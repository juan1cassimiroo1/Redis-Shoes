"""Roteiro de demonstração (10–12 min). Requer a API em execução (docker compose up ou run_local.sh).

    python scripts/demo.py [http://localhost:8000]
"""
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
c = httpx.Client(base_url=BASE, timeout=30)
H = lambda u: {"X-Usuario-Id": u}                       # noqa: E731
brl = lambda cts: f"R$ {cts / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731


def titulo(t):
    print(f"\n{'=' * 4} {t} {'=' * (70 - len(t))}")


SKU, SKU2 = "RBK-CLA-BRA-38", "ADI-UB1-PRE-40"

titulo("0. Saúde das três famílias")
print(c.get("/saude").json())

titulo("1. Catálogo semiestruturado (MongoDB) — atributos diferentes por marca")
for p in c.get("/produtos", params={"q": "boost"}).json() or c.get("/produtos").json()[:2]:
    print(p)
print("Reebok :", c.get("/produtos/reebok-classic").json()["atributos"])
print("Adidas :", c.get("/produtos/adidas-ultraboost").json()["atributos"])
print("cache  :", c.get("/produtos/adidas-ultraboost").json()["_cache"], "(2ª leitura vem do Redis)")

titulo("2. Carrinho em tempo real (Redis Hash + TTL 7 dias) e cupom")
c.post("/carrinho/itens", json={"sku": SKU, "qtd": 1}, headers=H("ana"))
c.post("/carrinho/itens", json={"sku": SKU2, "qtd": 2}, headers=H("ana"))
v = c.put("/carrinho/cupom", json={"codigo": "BEMVINDO10"}, headers=H("ana")).json()
for i in v["itens"]:
    print(f'  {i["qtd"]}x {i["modelo"]} {i["cor"]} {i["tamanho"]}  {brl(i["preco_unit_centavos"])}')
print(f'  subtotal {brl(v["subtotal_centavos"])} | cupom -{brl(v["desconto_cupom_centavos"])} | total {brl(v["total_centavos"])}')
print(f'  expira em {v["expira_em_segundos"]}s (~{v["expira_em_segundos"] / 86400:.1f} dias)')

titulo("3. Favoritos (sem TTL) + promoção que notifica quem favoritou")
c.put(f"/favoritos/{SKU}", headers=H("ana"))
c.put(f"/favoritos/{SKU}", headers=H("beto"))
print("  TTL dos favoritos:", c.get("/favoritos", headers=H("ana")).json()["ttl"], "(-1 = nunca expira)")
r = c.post("/promocoes", json={"nome": "Semana Reebok", "percentual": 20,
                               "escopo": {"tipo": "marca", "valor": "Reebok"}}).json()
print("  usuários notificados:", r["usuarios_a_notificar"])

titulo("4. Checkout: reserva atômica (Redis) → histórico imutável (Cassandra) → limpa carrinho")
est0 = c.get(f"/estoque/CD01/{SKU2}").json()["quantidade"]
p = c.post("/checkout", json={}, headers=H("ana")).json()
print(f'  pedido {p["pedido_id"]}  total {brl(p["total_centavos"])}')
print(f'  estoque {SKU2}: {est0} -> {c.get(f"/estoque/CD01/{SKU2}").json()["quantidade"]}')
print("  carrinho após compra:", c.get("/carrinho", headers=H("ana")).json()["itens"])

titulo("5. Histórico imutável: preço congelado mesmo com nova promoção")
c.post("/promocoes", json={"nome": "Relâmpago", "percentual": 50, "escopo": {"tipo": "sku", "valor": SKU2}})
print("  preço vivo hoje   :", brl(c.get(f"/sku/{SKU2}/preco").json()["preco_final_centavos"]))
h = c.get(f'/pedidos/{p["pedido_id"]}').json()
print("  preço no pedido   :", brl(h["itens"][1]["preco_unit_centavos"]) if len(h["itens"]) > 1 else brl(h["itens"][0]["preco_unit_centavos"]))
print("  eventos do pedido :", [e["status"] for e in h["eventos"]])

titulo("6. Falha no Cassandra: compensação (saga) — estoque devolvido, carrinho preservado")
c.post("/carrinho/itens", json={"sku": SKU, "qtd": 2}, headers=H("caio"))
e0 = c.get(f"/estoque/CD01/{SKU}").json()["quantidade"]
c.post("/admin/falha-pedidos", params={"ativo": True})
r = c.post("/checkout", json={}, headers=H("caio"))
print(f"  HTTP {r.status_code}: {r.json()['detalhe']}")
print(f'  estoque {e0} -> {c.get(f"/estoque/CD01/{SKU}").json()["quantidade"]} | itens no carrinho:',
      len(c.get("/carrinho", headers=H("caio")).json()["itens"]))
c.post("/admin/falha-pedidos", params={"ativo": False})
print("  tentando de novo:", c.post("/checkout", json={}, headers=H("caio")).status_code)

titulo("7. Estoque único: balcão da loja física e e-commerce baixam o MESMO saldo")
c.put("/estoque/LJ-BSB-01/ASC-GK30-CIN-42", json={"quantidade": 3})
c.post("/pdv/LJ-BSB-01/venda", json={"itens": {"ASC-GK30-CIN-42": 2}})
c.post("/carrinho/itens", json={"sku": "ASC-GK30-CIN-42", "qtd": 2}, headers=H("duda"))
r = c.post("/checkout", json={"loja_id": "LJ-BSB-01"}, headers=H("duda"))
print(f'  balcão vendeu 2 de 3; site tentou 2 -> HTTP {r.status_code} ({r.json().get("erro")}, disponível={r.json().get("disponivel")})')

titulo("8. Pico de drop: 100 compradores simultâneos disputando 30 pares")
SKU3 = "NKE-PG41-AZU-40"
c.put(f"/estoque/CD01/{SKU3}", json={"quantidade": 30})


def comprar(i):
    u = f"drop{i}"
    httpx.post(f"{BASE}/carrinho/itens", json={"sku": SKU3, "qtd": 1}, headers=H(u), timeout=60)
    return httpx.post(f"{BASE}/checkout", json={}, headers=H(u), timeout=60).status_code


with ThreadPoolExecutor(50) as ex:
    codes = list(ex.map(comprar, range(100)))
print(f"  aceitos={codes.count(201)}  recusados por falta de estoque={codes.count(409)}  outros={len(codes) - codes.count(201) - codes.count(409)}")
print("  estoque final:", c.get(f"/estoque/CD01/{SKU3}").json()["quantidade"])

titulo("9. Painel da loja (Cassandra, partição loja+mês) e rankings (Redis Sorted Set)")
mes = p["criado_em"][:7]
pn = c.get(f"/painel/lojas/CD01/pedidos", params={"mes": mes, "limite": 500}).json()
print(f'  CD01 em {mes}: {pn["quantidade"]} pedidos, faturamento {brl(pn["faturamento_centavos"])}')
print("  mais vendidos :", c.get("/ranking/vendidos", params={"n": 3}).json())
print("  mais favoritados:", c.get("/ranking/favoritados", params={"n": 3}).json())
