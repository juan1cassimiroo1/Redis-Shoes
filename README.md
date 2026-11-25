# RedisShoes — Marco 2 (Projeto Incremental · Bancos de Dados NoSQL · CEUB)

Sistema de persistência poliglota da loja fictícia RedisShoes, evoluindo o Marco 1:

| Família | Tecnologia | Papel no sistema |
|---|---|---|
| Chave-Valor | **Redis** (master + réplica) | carrinho (Hash + TTL 7 dias), favoritos (Sorted Set + Set), estoque (String + Lua atômico), rankings (Sorted Set), cache, Pub/Sub, lock |
| Documentos | **MongoDB** | catálogo semiestruturado, promoções, cupons |
| Wide-Column | **Cassandra** | histórico imutável de pedidos (query-first) |

## Rodar com Docker (completo, 3 bancos reais)
```bash
docker compose up --build        # Cassandra leva ~1 min para ficar pronto; a API aguarda
# API + Swagger: http://localhost:8000/docs
python scripts/demo.py           # roteiro da apresentação
python scripts/oversell_naive.py # prova do overselling: GET+SET vs. Lua atômico
```

## Rodar sem Docker (Redis real + Mongo simulado + pedidos em memória)
```bash
pip install -r requirements.txt
./scripts/run_local.sh           # precisa de redis-server instalado
python scripts/demo.py
BACKENDS=local python -m pytest -q
```

## Endpoints principais (usuário via cabeçalho `X-Usuario-Id`)
`POST /carrinho/itens` · `GET /carrinho` · `PUT /carrinho/cupom` · `PUT /favoritos/{sku}` · `POST /checkout` ·
`POST /pdv/{loja}/venda` · `GET /pedidos` · `GET /painel/lojas/{loja}/pedidos?mes=AAAA-MM` ·
`POST /promocoes` · `GET /ranking/vendidos` · `POST /admin/falha-pedidos?ativo=true` (injeção de falha)

## Estrutura
```
app/redis_store.py   Chave-Valor (Lua, TTL, hash tags)      app/catalog.py   Documentos (Mongo)
app/orders.py        Wide-Column (Cassandra) + versão em memória
app/shop.py          checkout em saga: reserva → grava → commit | compensa
cql/schema.cql       esquema Cassandra               tests/   14 testes (Redis real)
```
Preços são inteiros em centavos. Autenticação real está fora do escopo (cabeçalho simula o usuário).
