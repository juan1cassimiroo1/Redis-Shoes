"""Camada de Documentos (MongoDB): catálogo semiestruturado, promoções e cupons.

Preços são inteiros em centavos (evita erro de ponto flutuante e Decimal no BSON).
"""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Catalog:
    def __init__(self, db):
        self.db = db
        self.produtos = db["produtos"]
        self.promocoes = db["promocoes"]
        self.cupons = db["cupons"]

    def ensure_indexes(self):
        self.produtos.create_index("variantes.sku", unique=True)          # multikey: busca por SKU
        self.produtos.create_index([("marca", 1), ("categoria", 1)])
        self.promocoes.create_index([("ativa", 1), ("inicio", 1), ("fim", 1)])
        self.cupons.create_index("codigo", unique=True)

    # ---------------------------------------------------------------- produtos
    def upsert_product(self, doc: dict):
        doc = dict(doc)
        pid = doc.pop("_id", None) or doc.pop("id")
        doc.setdefault("criado_em", _now())
        self.produtos.replace_one({"_id": pid}, {"_id": pid, **doc}, upsert=True)
        return pid

    def get_product(self, pid):
        return self.produtos.find_one({"_id": pid})

    def search(self, q=None, marca=None, categoria=None, limit=20):
        filtro = {}
        if q:
            filtro["$or"] = [{"modelo": {"$regex": q, "$options": "i"}},
                             {"marca": {"$regex": q, "$options": "i"}}]
        if marca:
            filtro["marca"] = {"$regex": f"^{marca}$", "$options": "i"}
        if categoria:
            filtro["categoria"] = categoria
        return list(self.produtos.find(filtro).limit(limit))

    def find_by_sku(self, sku):
        prod = self.produtos.find_one({"variantes.sku": sku})
        if not prod:
            return None, None
        return prod, next(v for v in prod["variantes"] if v["sku"] == sku)

    def skus_in_scope(self, escopo: dict) -> list:
        tipo, valor = escopo.get("tipo"), escopo.get("valor")
        if tipo == "sku":
            return [valor]
        filtro = {} if tipo == "todos" else {tipo: valor}
        return [v["sku"] for p in self.produtos.find(filtro) for v in p["variantes"]]

    # ---------------------------------------------------------------- promoções e preço
    def create_promotion(self, nome, percentual: int, escopo: dict, inicio=None, fim=None):
        doc = {"nome": nome, "percentual": int(percentual), "escopo": escopo,
               "inicio": inicio or _now(), "fim": fim or datetime(2100, 1, 1), "ativa": True}
        return str(self.promocoes.insert_one(doc).inserted_id)

    def end_promotion(self, promo_id):
        from bson import ObjectId
        self.promocoes.update_one({"_id": ObjectId(promo_id)}, {"$set": {"ativa": False}})

    def _best_promo(self, prod, variant, now):
        melhor = None
        for pr in self.promocoes.find({"ativa": True, "inicio": {"$lte": now}, "fim": {"$gte": now}}):
            e = pr["escopo"]
            casa = (e["tipo"] == "todos"
                    or (e["tipo"] == "sku" and e["valor"] == variant["sku"])
                    or (e["tipo"] == "marca" and e["valor"].lower() == prod["marca"].lower())
                    or (e["tipo"] == "categoria" and e["valor"] == prod.get("categoria")))
            if casa and (melhor is None or pr["percentual"] > melhor["percentual"]):
                melhor = pr
        return melhor

    def price_for(self, sku, now=None):
        """Preço autoritativo (sempre lido do Mongo) com a melhor promoção vigente."""
        prod, var = self.find_by_sku(sku)
        if not prod:
            return None
        now = now or _now()
        promo = self._best_promo(prod, var, now)
        pct = promo["percentual"] if promo else 0
        base = var["preco_base_centavos"]
        return {"sku": sku, "produto_id": prod["_id"], "marca": prod["marca"], "modelo": prod["modelo"],
                "cor": var["cor"], "tamanho": var["tamanho"], "preco_base_centavos": base,
                "desconto_pct": pct, "preco_final_centavos": base * (100 - pct) // 100,
                "promocao_id": str(promo["_id"]) if promo else None}

    # ---------------------------------------------------------------- cupons
    def create_coupon(self, codigo, percentual, validade=None):
        self.cupons.replace_one({"codigo": codigo.upper()},
                                {"codigo": codigo.upper(), "percentual": int(percentual),
                                 "validade": validade or datetime(2100, 1, 1)}, upsert=True)

    def get_valid_coupon(self, codigo):
        c = self.cupons.find_one({"codigo": codigo.upper()})
        return c if c and c["validade"] >= _now() else None
