"""Regras de negócio que orquestram as três famílias NoSQL."""
import uuid
from datetime import datetime, timezone

from . import config
from .errors import (CarrinhoVazio, CheckoutEmAndamento, CupomInvalido, PersistenciaIndisponivel,
                     ProdutoInexistente)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Shop:
    def __init__(self, store, catalog, orders):
        self.store, self.catalog, self.orders = store, catalog, orders
        self.simular_falha_pedidos = False      # injeção de falha para a demonstração (admin)

    # ------------------------------------------------------------ catálogo (Mongo + cache Redis)
    def get_product(self, pid):
        key = f"cache:produto:{pid}"
        cached = self.store.cache_get(key)              # lê da réplica: pode estar um pouco defasado (AP)
        if cached:
            return {**cached, "_cache": "hit"}
        prod = self.catalog.get_product(pid)
        if not prod:
            raise ProdutoInexistente(pid)
        prod["criado_em"] = str(prod.get("criado_em", ""))
        self.store.cache_set(key, prod)
        return {**prod, "_cache": "miss"}

    def upsert_product(self, doc):
        pid = self.catalog.upsert_product(doc)
        self.store.cache_del(f"cache:produto:{pid}")    # invalidação
        return pid

    # ------------------------------------------------------------ carrinho
    def cart_add(self, uid, sku, qtd):
        info = self.catalog.price_for(sku)
        if not info:
            raise ProdutoInexistente(sku)
        snap = {k: info[k] for k in ("marca", "modelo", "cor", "tamanho")}
        self.store.cart_add(uid, sku, qtd, snap)
        return self.cart_view(uid)

    def cart_view(self, uid):
        c = self.store.cart_get(uid)
        linhas, subtotal = [], 0
        for sku, it in c["itens"].items():
            info = self.catalog.price_for(sku) or {}
            unit = info.get("preco_final_centavos", 0)
            linhas.append({"sku": sku, "qtd": it["qtd"], "modelo": it.get("modelo"), "cor": it.get("cor"),
                           "tamanho": it.get("tamanho"), "preco_base_centavos": info.get("preco_base_centavos"),
                           "desconto_pct": info.get("desconto_pct", 0), "preco_unit_centavos": unit,
                           "total_linha_centavos": unit * it["qtd"],
                           "estoque_disponivel": self.store.get_stock(config.LOJA_ONLINE, sku)})
            subtotal += unit * it["qtd"]
        desc = 0
        if c["cupom"]:
            cup = self.catalog.get_valid_coupon(c["cupom"])
            desc = subtotal * cup["percentual"] // 100 if cup else 0
        return {"usuario_id": uid, "itens": linhas, "subtotal_centavos": subtotal, "cupom": c["cupom"],
                "desconto_cupom_centavos": desc, "total_centavos": subtotal - desc,
                "expira_em_segundos": c["ttl"] if c["ttl"] >= 0 else None}

    def cart_set_coupon(self, uid, codigo):
        if not self.catalog.get_valid_coupon(codigo):
            raise CupomInvalido(codigo)
        self.store.cart_set_coupon(uid, codigo.upper())
        return self.cart_view(uid)

    # ------------------------------------------------------------ favoritos e promoções
    def create_promotion(self, nome, percentual, escopo):
        promo_id = self.catalog.create_promotion(nome, percentual, escopo)
        skus = self.catalog.skus_in_scope(escopo)
        usuarios = sorted(self.store.fav_users_of(skus))
        self.store.publish_promo({"promocao_id": promo_id, "nome": nome, "percentual": percentual,
                                  "skus": skus, "usuarios_a_notificar": usuarios})
        return {"promocao_id": promo_id, "skus_afetados": skus, "usuarios_a_notificar": usuarios}

    # ------------------------------------------------------------ checkout (saga)
    def checkout(self, uid, loja_id=config.LOJA_ONLINE, cupom=None):
        token = self.store.acquire_lock(uid)
        if not token:
            raise CheckoutEmAndamento(uid)
        try:
            cart = self.store.cart_get(uid)
            if not cart["itens"]:
                raise CarrinhoVazio(uid)
            codigo = cupom or cart["cupom"]
            pct_cupom = 0
            if codigo:
                cup = self.catalog.get_valid_coupon(codigo)
                if not cup:
                    raise CupomInvalido(codigo)
                pct_cupom = cup["percentual"]
            itens, qtds = self._price_lines({s: i["qtd"] for s, i in cart["itens"].items()})
            return self._finalize(uid, loja_id, "ONLINE", itens, qtds, codigo, pct_cupom,
                                  on_commit=lambda: self.store.cart_clear(uid))
        finally:
            self.store.release_lock(uid, token)

    def pdv_sale(self, loja_id, qtds: dict):
        """Venda no balcão da loja física: baixa no MESMO saldo do e-commerce."""
        itens, qtds = self._price_lines(qtds)
        return self._finalize(f"pdv-{loja_id}", loja_id, "PDV", itens, qtds, None, 0)

    def _price_lines(self, qtds):
        itens = []
        for sku, q in qtds.items():
            info = self.catalog.price_for(sku)          # preço autoritativo, nunca do cache/carrinho
            if not info:
                raise ProdutoInexistente(sku)
            itens.append({"sku": sku, "modelo": f'{info["marca"]} {info["modelo"]}', "cor": info["cor"],
                          "tamanho": info["tamanho"], "qtd": q,
                          "preco_base_centavos": info["preco_base_centavos"],
                          "desconto_pct": info["desconto_pct"],
                          "preco_unit_centavos": info["preco_final_centavos"]})
        return itens, qtds

    def _finalize(self, uid, loja_id, canal, itens, qtds, cupom, pct_cupom, on_commit=None):
        subtotal = sum(i["preco_unit_centavos"] * i["qtd"] for i in itens)
        desc = subtotal * pct_cupom // 100
        pedido = {"pedido_id": str(uuid.uuid4()), "usuario_id": uid, "loja_id": loja_id, "canal": canal,
                  "criado_em": _utcnow(), "total_centavos": subtotal - desc, "cupom": cupom,
                  "desconto_cupom_centavos": desc, "itens": itens}
        # 1) Reserva atômica no Redis (CP): tudo ou nada.
        self.store.reserve(loja_id, qtds)
        try:
            # 2) Grava o histórico imutável no Cassandra.
            if self.simular_falha_pedidos:
                raise ConnectionError("falha simulada no Cassandra")
            self.orders.save_order(pedido)
        except Exception as e:                           # noqa: BLE001
            # 3) Compensação: devolve o estoque e MANTÉM o carrinho intacto.
            self.store.release(loja_id, qtds)
            raise PersistenciaIndisponivel("pedido não registrado; estoque devolvido, carrinho preservado") from e
        # 4) Commit: só agora o carrinho é removido e os rankings são atualizados.
        if on_commit:
            on_commit()
        for i in itens:
            self.store.sold_incr(loja_id, i["sku"], i["qtd"])
        try:
            self.orders.add_event(pedido["pedido_id"], "PAGO")
        except Exception:                                # noqa: BLE001  (evento é best-effort)
            pass
        return {**pedido, "criado_em": pedido["criado_em"].isoformat()}
