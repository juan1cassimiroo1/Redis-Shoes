"""API REST (FastAPI). Documentação interativa em /docs."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config
from .container import build_shop
from .errors import ShopError
from .seed import seed


@asynccontextmanager
async def lifespan(app):
    app.state.shop = build_shop()
    if config.SEED_ON_START:
        seed(app.state.shop)
    yield


app = FastAPI(title="RedisShoes — Marco 2", version="2.0", lifespan=lifespan)
shop = lambda: app.state.shop            # noqa: E731


@app.exception_handler(ShopError)
async def shop_error(_, e: ShopError):
    return JSONResponse(status_code=e.status, content={"erro": e.code, "detalhe": str(e), **e.extra})


class ItemIn(BaseModel):
    sku: str
    qtd: int = Field(1, ge=1, le=20)


class QtdIn(BaseModel):
    qtd: int = Field(ge=0, le=20)


class CupomIn(BaseModel):
    codigo: str


class CheckoutIn(BaseModel):
    loja_id: str = config.LOJA_ONLINE
    cupom: str | None = None


class PdvIn(BaseModel):
    itens: dict[str, int]


class EstoqueIn(BaseModel):
    quantidade: int = Field(ge=0)


class PromoIn(BaseModel):
    nome: str
    percentual: int = Field(ge=1, le=90)
    escopo: dict            # {"tipo": "sku|marca|categoria|todos", "valor": "..."}


class CupomNovoIn(BaseModel):
    codigo: str
    percentual: int = Field(ge=1, le=90)


def uid_of(x_usuario_id: str | None):
    if not x_usuario_id:
        raise HTTPException(401, "informe o cabeçalho X-Usuario-Id")
    return x_usuario_id


# ------------------------------------------------------------------ saúde / admin
@app.get("/saude")
def saude():
    s = shop()
    return {"redis": bool(s.store.ping()), "orders": bool(s.orders.ping()),
            "mongo": s.catalog.db.command("ping")["ok"] == 1 if hasattr(s.catalog.db, "command") else True}


@app.post("/admin/seed")
def admin_seed():
    return seed(shop())


@app.post("/admin/falha-pedidos")
def falha(ativo: bool = True):
    if not config.ENABLE_ADMIN:
        raise HTTPException(403)
    shop().simular_falha_pedidos = ativo
    return {"simular_falha_pedidos": ativo}


# ------------------------------------------------------------------ catálogo (Documentos)
@app.get("/produtos")
def produtos(q: str | None = None, marca: str | None = None, categoria: str | None = None):
    return [{"id": p["_id"], "marca": p["marca"], "modelo": p["modelo"], "categoria": p.get("categoria"),
             "variantes": len(p["variantes"])} for p in shop().catalog.search(q, marca, categoria)]


@app.get("/produtos/{pid}")
def produto(pid: str):
    return shop().get_product(pid)


@app.put("/produtos/{pid}")
def produto_put(pid: str, doc: dict):
    return {"id": shop().upsert_product({**doc, "_id": pid})}


@app.get("/sku/{sku}/preco")
def preco(sku: str):
    info = shop().catalog.price_for(sku)
    if not info:
        raise HTTPException(404, "SKU inexistente")
    return info


@app.post("/promocoes")
def promocao(p: PromoIn):
    return shop().create_promotion(p.nome, p.percentual, p.escopo)


@app.post("/cupons")
def cupom_novo(c: CupomNovoIn):
    shop().catalog.create_coupon(c.codigo, c.percentual)
    return {"ok": True}


# ------------------------------------------------------------------ carrinho (Chave-Valor)
@app.get("/carrinho")
def carrinho(x_usuario_id: str | None = Header(None)):
    return shop().cart_view(uid_of(x_usuario_id))


@app.post("/carrinho/itens")
def carrinho_add(i: ItemIn, x_usuario_id: str | None = Header(None)):
    return shop().cart_add(uid_of(x_usuario_id), i.sku, i.qtd)


@app.patch("/carrinho/itens/{sku}")
def carrinho_qtd(sku: str, q: QtdIn, x_usuario_id: str | None = Header(None)):
    uid = uid_of(x_usuario_id)
    shop().store.cart_set_qty(uid, sku, q.qtd)
    return shop().cart_view(uid)


@app.delete("/carrinho/itens/{sku}")
def carrinho_del(sku: str, x_usuario_id: str | None = Header(None)):
    uid = uid_of(x_usuario_id)
    shop().store.cart_remove(uid, sku)
    return shop().cart_view(uid)


@app.put("/carrinho/cupom")
def carrinho_cupom(c: CupomIn, x_usuario_id: str | None = Header(None)):
    return shop().cart_set_coupon(uid_of(x_usuario_id), c.codigo)


# ------------------------------------------------------------------ favoritos
@app.get("/favoritos")
def favoritos(x_usuario_id: str | None = Header(None)):
    uid = uid_of(x_usuario_id)
    skus = shop().store.fav_list(uid)
    return {"usuario_id": uid, "skus": skus, "ttl": shop().store.fav_ttl(uid),
            "itens": [shop().catalog.price_for(s) for s in skus]}


@app.put("/favoritos/{sku}")
def fav_add(sku: str, x_usuario_id: str | None = Header(None)):
    if not shop().catalog.price_for(sku):
        raise HTTPException(404, "SKU inexistente")
    return {"adicionado": shop().store.fav_add(uid_of(x_usuario_id), sku)}


@app.delete("/favoritos/{sku}")
def fav_del(sku: str, x_usuario_id: str | None = Header(None)):
    return {"removido": shop().store.fav_remove(uid_of(x_usuario_id), sku)}


# ------------------------------------------------------------------ estoque (fonte única da verdade)
@app.get("/estoque/{loja}/{sku}")
def estoque(loja: str, sku: str):
    return {"loja": loja, "sku": sku, "quantidade": shop().store.get_stock(loja, sku)}


@app.put("/estoque/{loja}/{sku}")
def estoque_ajuste(loja: str, sku: str, e: EstoqueIn):
    shop().store.set_stock(loja, sku, e.quantidade)       # inventário/contagem física
    return {"loja": loja, "sku": sku, "quantidade": e.quantidade}


# ------------------------------------------------------------------ compra
@app.post("/checkout", status_code=201)
def checkout(c: CheckoutIn, x_usuario_id: str | None = Header(None)):
    return shop().checkout(uid_of(x_usuario_id), c.loja_id, c.cupom)


@app.post("/pdv/{loja}/venda", status_code=201)
def pdv(loja: str, v: PdvIn):
    return shop().pdv_sale(loja, v.itens)


# ------------------------------------------------------------------ histórico (Wide-Column)
@app.get("/pedidos")
def pedidos(x_usuario_id: str | None = Header(None), limite: int = Query(20, le=100)):
    return shop().orders.orders_by_user(uid_of(x_usuario_id), limite)


@app.get("/pedidos/{pedido_id}")
def pedido(pedido_id: str):
    o = shop().orders.get_order(pedido_id)
    if not o:
        raise HTTPException(404, "pedido inexistente")
    return {**o, "eventos": shop().orders.events(pedido_id)}


@app.get("/painel/lojas/{loja}/pedidos")
def painel(loja: str, mes: str, limite: int = Query(100, le=1000)):
    r = shop().orders.orders_by_store_month(loja, mes, limite)
    return {"loja": loja, "mes": mes, "quantidade": len(r),
            "faturamento_centavos": sum(o["total_centavos"] for o in r), "pedidos": r}


@app.get("/ranking/vendidos")
def ranking(loja: str = config.LOJA_ONLINE, n: int = 10):
    return [{"sku": s, "vendidos": v} for s, v in shop().store.sold_top(loja, n)]


@app.get("/ranking/favoritados")
def ranking_fav(n: int = 10):
    return [{"sku": s, "favoritos": v} for s, v in shop().store.fav_top(n)]
