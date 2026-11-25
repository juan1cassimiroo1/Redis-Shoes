"""Prova do problema: baixa de estoque ingênua (GET → decide → SET) vs. script Lua atômico.

    python scripts/oversell_naive.py [redis_url] [estoque] [compradores]
"""
import sys
import threading
import time

import redis

sys.path.insert(0, ".")
from app.redis_store import RedisStore  # noqa: E402

url = sys.argv[1] if len(sys.argv) > 1 else "redis://localhost:6379/0"
ESTOQUE = int(sys.argv[2]) if len(sys.argv) > 2 else 20
COMPRADORES = int(sys.argv[3]) if len(sys.argv) > 3 else 100
r = redis.Redis.from_url(url, decode_responses=True)
store = RedisStore(r)


def rodar(nome, comprar):
    r.set("estoque:{TESTE}:TENIS", ESTOQUE)
    vendas = []
    ts = [threading.Thread(target=lambda: vendas.append(comprar())) for _ in range(COMPRADORES)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok = sum(1 for v in vendas if v)
    print(f"{nome:<22} estoque inicial={ESTOQUE}  vendas aceitas={ok:>3}  saldo final={r.get('estoque:{TESTE}:TENIS')}"
          f"  {'-> OVERSELLING' if ok > ESTOQUE else '-> correto'}")


def ingenuo():
    saldo = int(r.get("estoque:{TESTE}:TENIS"))
    if saldo < 1:
        return False
    time.sleep(0.002)                       # validações/regras entre a leitura e a escrita
    r.set("estoque:{TESTE}:TENIS", saldo - 1)
    return True


def atomico():
    try:
        store.reserve("TESTE", {"TENIS": 1})
        return True
    except Exception:
        return False


rodar("GET + SET (ingênuo)", ingenuo)
rodar("Lua atômico (Marco 2)", atomico)
r.delete("estoque:{TESTE}:TENIS")
