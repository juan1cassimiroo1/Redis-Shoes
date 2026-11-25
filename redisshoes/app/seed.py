"""Carga inicial: catálogo (Mongo), estoque (Redis), cupom e promoção."""
from . import config

PRODUTOS = [
    ("reebok-classic", "Reebok", "Classic Leather", "casual", "RBK-CLA", [("branco", 699_90), ("preto", 719_90)],
     {"cabedal": "couro", "solado": "borracha"}),
    ("adidas-ultraboost", "Adidas", "Ultraboost 1.0", "corrida", "ADI-UB1", [("preto", 999_99), ("cinza", 999_99)],
     {"tecnologia": "Boost", "drop_mm": 10, "peso_g": 310}),
    ("allstar-chuck70", "Converse", "All Star Chuck 70 Lace", "casual", "CNV-C70", [("rosa", 479_99), ("preto", 499_99)],
     {"cano": "alto", "material": "lona"}),
    ("nike-pegasus", "Nike", "Pegasus 41", "corrida", "NKE-PG41", [("azul", 799_90), ("preto", 799_90)],
     {"tecnologia": "ReactX", "drop_mm": 10}),
    ("asics-gelkayano", "Asics", "Gel-Kayano 30", "corrida", "ASC-GK30", [("cinza", 1199_90)],
     {"pisada": "pronada", "amortecimento": "GEL"}),
]
TAMANHOS = ["36", "38", "40", "42"]


def seed(shop, estoque_inicial=20, lojas=(config.LOJA_ONLINE, "LJ-BSB-01")):
    for pid, marca, modelo, cat, pref, cores, attrs in PRODUTOS:
        variantes = [{"sku": f"{pref}-{cor[:3].upper()}-{t}", "cor": cor, "tamanho": t, "preco_base_centavos": preco}
                     for cor, preco in cores for t in TAMANHOS]
        shop.upsert_product({"_id": pid, "marca": marca, "modelo": modelo, "categoria": cat,
                             "atributos": attrs, "variantes": variantes})
        for v in variantes:
            for loja in lojas:
                shop.store.set_stock(loja, v["sku"], estoque_inicial)
    shop.catalog.create_coupon("BEMVINDO10", 10)
    return {"produtos": len(PRODUTOS), "lojas": list(lojas)}
