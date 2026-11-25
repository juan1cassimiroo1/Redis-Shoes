import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import config
from app.errors import (CarrinhoVazio, CupomInvalido, EstoqueInsuficiente, PersistenciaIndisponivel)

SKU = "RBK-CLA-BRA-38"      # R$ 699,90
SKU2 = "ADI-UB1-PRE-40"     # R$ 999,99
LOJA = config.LOJA_ONLINE


# ---------------------------------------------------------------- carrinho (Chave-Valor)
def test_carrinho_ttl_7_dias_e_incremento(shop):
    shop.cart_add("u1", SKU, 1)
    v = shop.cart_add("u1", SKU, 2)
    assert v["itens"][0]["qtd"] == 3
    ttl = shop.store.m.ttl("carrinho:{u1}:qtd")
    assert 604700 < ttl <= 604800
    assert v["total_centavos"] == 3 * 69990


def test_carrinho_remover_e_alterar(shop):
    shop.cart_add("u1", SKU, 1)
    shop.cart_add("u1", SKU2, 1)
    shop.store.cart_set_qty("u1", SKU, 4)
    shop.store.cart_remove("u1", SKU2)
    v = shop.cart_view("u1")
    assert [(i["sku"], i["qtd"]) for i in v["itens"]] == [(SKU, 4)]


def test_cupom(shop):
    shop.cart_add("u1", SKU, 1)
    v = shop.cart_set_coupon("u1", "bemvindo10")
    assert v["desconto_cupom_centavos"] == 6999 and v["total_centavos"] == 62991
    with pytest.raises(CupomInvalido):
        shop.cart_set_coupon("u1", "NAOEXISTE")


# ---------------------------------------------------------------- estoque (consistência)
def test_reserva_all_or_nothing(shop):
    shop.store.set_stock(LOJA, SKU2, 0)
    with pytest.raises(EstoqueInsuficiente) as e:
        shop.store.reserve(LOJA, {SKU: 2, SKU2: 1})
    assert e.value.extra["sku"] == SKU2
    assert shop.store.get_stock(LOJA, SKU) == 10        # nada foi baixado


def test_sem_overselling_sob_concorrencia(shop):
    shop.store.set_stock(LOJA, SKU, 25)
    ok, falhas = [], []

    def comprar(i):
        uid = f"c{i}"
        shop.cart_add(uid, SKU, 1)
        try:
            ok.append(shop.checkout(uid)["pedido_id"])
        except EstoqueInsuficiente:
            falhas.append(uid)

    with ThreadPoolExecutor(60) as ex:
        list(ex.map(comprar, range(100)))
    assert len(ok) == 25 and len(falhas) == 75
    assert shop.store.get_stock(LOJA, SKU) == 0
    assert len(shop.orders.by_id) == 25


def test_pdv_e_online_compartilham_saldo(shop):
    shop.store.set_stock("LJ-BSB-01", SKU, 3)
    shop.pdv_sale("LJ-BSB-01", {SKU: 2})
    assert shop.store.get_stock("LJ-BSB-01", SKU) == 1
    shop.cart_add("u1", SKU, 2)
    with pytest.raises(EstoqueInsuficiente):
        shop.checkout("u1", loja_id="LJ-BSB-01")


# ---------------------------------------------------------------- checkout / saga
def test_checkout_ok_limpa_carrinho_e_grava_historico(shop):
    shop.cart_add("u1", SKU, 2)
    p = shop.checkout("u1", cupom="BEMVINDO10")
    assert p["total_centavos"] == 2 * 69990 - 13998
    assert shop.store.get_stock(LOJA, SKU) == 8
    assert shop.store.cart_get("u1")["itens"] == {}
    assert shop.orders.orders_by_user("u1")[0]["pedido_id"] == p["pedido_id"]
    assert shop.store.sold_top(LOJA)[0] == (SKU, 2)
    with pytest.raises(CarrinhoVazio):
        shop.checkout("u1")


def test_falha_no_cassandra_compensa_estoque_e_preserva_carrinho(shop):
    shop.cart_add("u1", SKU, 2)
    shop.simular_falha_pedidos = True
    with pytest.raises(PersistenciaIndisponivel):
        shop.checkout("u1")
    assert shop.store.get_stock(LOJA, SKU) == 10
    assert shop.store.cart_get("u1")["itens"][SKU]["qtd"] == 2
    shop.simular_falha_pedidos = False
    assert shop.checkout("u1")["canal"] == "ONLINE"      # pode tentar de novo


def test_checkout_duplo_clique_e_bloqueado(shop):
    from app.errors import CheckoutEmAndamento
    tok = shop.store.acquire_lock("u1")
    shop.cart_add("u1", SKU, 1)
    with pytest.raises(CheckoutEmAndamento):
        shop.checkout("u1")
    shop.store.release_lock("u1", tok)
    shop.checkout("u1")


# ---------------------------------------------------------------- histórico imutável
def test_preco_no_historico_nao_muda_apos_promocao(shop):
    shop.cart_add("u1", SKU, 1)
    p = shop.checkout("u1")
    shop.create_promotion("Black Friday", 50, {"tipo": "todos"})
    assert shop.catalog.price_for(SKU)["preco_final_centavos"] == 34995      # preço vivo mudou
    h = shop.orders.get_order(p["pedido_id"])
    assert h["itens"][0]["preco_unit_centavos"] == 69990                      # histórico não


def test_painel_por_loja_e_mes(shop):
    shop.pdv_sale("LJ-BSB-01", {SKU: 1})
    shop.cart_add("u1", SKU2, 1)
    p = shop.checkout("u1")
    mes = p["criado_em"][:7]
    assert len(shop.orders.orders_by_store_month("LJ-BSB-01", mes)) == 1
    assert len(shop.orders.orders_by_store_month(LOJA, mes)) == 1


# ---------------------------------------------------------------- favoritos + promoções
def test_favoritos_sem_ttl_e_notificacao_de_promocao(shop):
    assert shop.store.fav_add("u1", SKU) is True
    assert shop.store.fav_add("u1", SKU) is False
    shop.store.fav_add("u2", SKU)
    shop.store.fav_add("u3", SKU2)
    assert shop.store.fav_ttl("u1") == -1
    ps = shop.store.m.pubsub()
    ps.subscribe("promocoes")
    ps.get_message(timeout=1)
    r = shop.create_promotion("Reebok -20%", 20, {"tipo": "marca", "valor": "Reebok"})
    assert r["usuarios_a_notificar"] == ["u1", "u2"]
    msg = None
    for _ in range(10):
        msg = ps.get_message(timeout=0.5)
        if msg and msg["type"] == "message":
            break
    assert json.loads(msg["data"])["percentual"] == 20


def test_cache_do_produto_e_invalidacao(shop):
    assert shop.get_product("reebok-classic")["_cache"] == "miss"
    assert shop.get_product("reebok-classic")["_cache"] == "hit"
    shop.upsert_product({"_id": "reebok-classic", "marca": "Reebok", "modelo": "Classic II", "categoria": "casual",
                         "variantes": [{"sku": "X-1", "cor": "branco", "tamanho": "38", "preco_base_centavos": 1}]})
    assert shop.get_product("reebok-classic")["modelo"] == "Classic II"


# ---------------------------------------------------------------- API
def test_api_fluxo_completo(shop, monkeypatch):
    from app.main import app
    with TestClient(app) as c:                       # lifespan cria um shop próprio...
        app.state.shop = shop                        # ...que substituímos pelo de teste
        h = {"X-Usuario-Id": "ana"}
        assert c.post("/carrinho/itens", json={"sku": SKU, "qtd": 1}, headers=h).status_code == 200
        assert c.put("/favoritos/" + SKU2, headers=h).json()["adicionado"] is True
        r = c.post("/checkout", json={}, headers=h)
        assert r.status_code == 201
        assert c.get("/pedidos", headers=h).json()[0]["pedido_id"] == r.json()["pedido_id"]
        assert c.post("/checkout", json={}, headers=h).status_code == 409
        assert c.get("/carrinho").status_code == 401
        assert c.get("/ranking/vendidos").json()[0]["sku"] == SKU
