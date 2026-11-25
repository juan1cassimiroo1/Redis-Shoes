"""Camada Chave-Valor (Redis): carrinho, favoritos, estoque, ranking, cache, pub/sub e lock.

Convenção de chaves: o trecho entre chaves {…} é um *hash tag*. No Redis Cluster só ele é
usado no cálculo do hash slot (CRC16 mod 16384), então todas as chaves de um mesmo usuário
(ou de uma mesma loja) caem no mesmo slot/nó — o que permite scripts Lua e transações
multi-chave sem cruzar shards.
"""
import json
import time
import uuid

import redis

from . import config
from .errors import EstoqueInsuficiente

# Reserva TODOS os itens ou NENHUM — atômico porque o Redis executa o script em uma única
# thread, sem intercalar outros comandos. Retorna 0 (ok) ou o índice (1-based) do item sem saldo.
LUA_RESERVE = """
for i, k in ipairs(KEYS) do
  local s = tonumber(redis.call('GET', k) or '0')
  if s < tonumber(ARGV[i]) then return i end
end
for i, k in ipairs(KEYS) do
  redis.call('DECRBY', k, ARGV[i])
end
return 0
"""

# Libera o lock apenas se ainda for nosso (evita apagar o lock de outro checkout).
LUA_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


def k_stock(loja, sku): return f"estoque:{{{loja}}}:{sku}"
def k_cart_qty(uid): return f"carrinho:{{{uid}}}:qtd"
def k_cart_snap(uid): return f"carrinho:{{{uid}}}:snap"
def k_cart_coupon(uid): return f"carrinho:{{{uid}}}:cupom"
def k_fav(uid): return f"favoritos:{{{uid}}}"
def k_favby(sku): return f"favoritadores:{{{sku}}}"
def k_lock(uid): return f"lock:checkout:{{{uid}}}"
def k_rank_sold(loja): return f"ranking:vendidos:{{{loja}}}"
RANK_FAV = "ranking:favoritados"
CHANNEL_PROMO = "promocoes"


class RedisStore:
    def __init__(self, master: redis.Redis, replica: redis.Redis | None = None):
        self.m = master
        self.r = replica or master      # leituras tolerantes a atraso (AP)
        self._reserve = master.register_script(LUA_RESERVE)
        self._unlock = master.register_script(LUA_UNLOCK)

    @classmethod
    def from_urls(cls, master_url, replica_url=""):
        m = redis.Redis.from_url(master_url, decode_responses=True)
        r = redis.Redis.from_url(replica_url, decode_responses=True) if replica_url else None
        return cls(m, r)

    def ping(self):
        return self.m.ping()

    # ------------------------------------------------------------------ estoque (CP)
    def get_stock(self, loja, sku) -> int:
        return int(self.m.get(k_stock(loja, sku)) or 0)

    def set_stock(self, loja, sku, qtd: int):
        self.m.set(k_stock(loja, sku), int(qtd))

    def reserve(self, loja, itens: dict):
        """Baixa de estoque all-or-nothing. Levanta EstoqueInsuficiente(sku) se faltar saldo."""
        skus = list(itens)
        res = self._reserve(keys=[k_stock(loja, s) for s in skus], args=[itens[s] for s in skus])
        if res != 0:
            sku = skus[int(res) - 1]
            raise EstoqueInsuficiente(f"sem saldo para {sku}", sku=sku,
                                      disponivel=self.get_stock(loja, sku))

    def release(self, loja, itens: dict):
        """Compensação (saga): devolve o saldo reservado."""
        pipe = self.m.pipeline(transaction=True)
        for sku, q in itens.items():
            pipe.incrby(k_stock(loja, sku), int(q))
        pipe.execute()

    # ------------------------------------------------------------------ carrinho (AP, soft state)
    def cart_add(self, uid, sku, qtd: int, snapshot: dict):
        pipe = self.m.pipeline(transaction=True)
        pipe.hincrby(k_cart_qty(uid), sku, int(qtd))
        pipe.hset(k_cart_snap(uid), sku, json.dumps(snapshot, ensure_ascii=False))
        pipe.expire(k_cart_qty(uid), config.CART_TTL_SECONDS)     # TTL deslizante de 7 dias
        pipe.expire(k_cart_snap(uid), config.CART_TTL_SECONDS)
        pipe.execute()

    def cart_set_qty(self, uid, sku, qtd: int):
        if qtd <= 0:
            return self.cart_remove(uid, sku)
        if not self.m.hexists(k_cart_qty(uid), sku):
            return False
        self.m.hset(k_cart_qty(uid), sku, int(qtd))
        return True

    def cart_remove(self, uid, sku):
        pipe = self.m.pipeline(transaction=True)
        pipe.hdel(k_cart_qty(uid), sku)
        pipe.hdel(k_cart_snap(uid), sku)
        return pipe.execute()[0] > 0

    def cart_get(self, uid) -> dict:
        pipe = self.m.pipeline(transaction=True)
        pipe.hgetall(k_cart_qty(uid))
        pipe.hgetall(k_cart_snap(uid))
        pipe.get(k_cart_coupon(uid))
        pipe.ttl(k_cart_qty(uid))
        qtd, snap, cupom, ttl = pipe.execute()
        itens = {s: {"qtd": int(q), **json.loads(snap.get(s, "{}"))} for s, q in qtd.items()}
        return {"itens": itens, "cupom": cupom, "ttl": ttl}

    def cart_set_coupon(self, uid, code):
        self.m.set(k_cart_coupon(uid), code, ex=config.CART_TTL_SECONDS)

    def cart_clear(self, uid):
        self.m.delete(k_cart_qty(uid), k_cart_snap(uid), k_cart_coupon(uid))

    # ------------------------------------------------------------------ favoritos (sem TTL)
    def fav_add(self, uid, sku) -> bool:
        pipe = self.m.pipeline(transaction=False)       # chaves em slots diferentes no Cluster
        pipe.zadd(k_fav(uid), {sku: time.time()}, nx=True)
        pipe.sadd(k_favby(sku), uid)
        added, _ = pipe.execute()
        if added:
            self.m.zincrby(RANK_FAV, 1, sku)
        return bool(added)

    def fav_remove(self, uid, sku) -> bool:
        pipe = self.m.pipeline(transaction=False)
        pipe.zrem(k_fav(uid), sku)
        pipe.srem(k_favby(sku), uid)
        removed, _ = pipe.execute()
        if removed:
            self.m.zincrby(RANK_FAV, -1, sku)
        return bool(removed)

    def fav_list(self, uid) -> list:
        return self.m.zrevrange(k_fav(uid), 0, -1)

    def fav_ttl(self, uid) -> int:
        return self.m.ttl(k_fav(uid))      # -1 = sem expiração

    def fav_users_of(self, skus) -> set:
        if not skus:
            return set()
        pipe = self.r.pipeline(transaction=False)
        for s in skus:
            pipe.smembers(k_favby(s))
        out = set()
        for members in pipe.execute():
            out |= set(members)
        return out

    # ------------------------------------------------------------------ ranking
    def sold_incr(self, loja, sku, qtd):
        self.m.zincrby(k_rank_sold(loja), int(qtd), sku)

    def sold_top(self, loja, n=10):
        return [(s, int(v)) for s, v in self.r.zrevrange(k_rank_sold(loja), 0, n - 1, withscores=True)]

    def fav_top(self, n=10):
        return [(s, int(v)) for s, v in self.r.zrevrange(RANK_FAV, 0, n - 1, withscores=True)]

    # ------------------------------------------------------------------ cache-aside / pub-sub / lock
    def cache_get(self, key):
        v = self.r.get(key)
        return json.loads(v) if v else None

    def cache_set(self, key, value, ttl=config.PRODUCT_CACHE_TTL):
        self.m.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)

    def cache_del(self, key):
        self.m.delete(key)

    def publish_promo(self, payload: dict) -> int:
        return self.m.publish(CHANNEL_PROMO, json.dumps(payload, ensure_ascii=False))

    def acquire_lock(self, uid):
        token = uuid.uuid4().hex
        ok = self.m.set(k_lock(uid), token, nx=True, ex=config.CHECKOUT_LOCK_TTL)
        return token if ok else None

    def release_lock(self, uid, token):
        self._unlock(keys=[k_lock(uid)], args=[token])
