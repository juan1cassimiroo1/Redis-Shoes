-- RedisShoes — esquema Cassandra (query-first). O keyspace é criado pela aplicação com o RF desejado:
--   CREATE KEYSPACE redisshoes WITH replication = {'class':'NetworkTopologyStrategy','datacenter1':3};

CREATE TYPE IF NOT EXISTS item_pedido (
  sku                  text,
  modelo               text,
  cor                  text,
  tamanho              text,
  qtd                  int,
  preco_base_centavos  bigint,
  desconto_pct         int,
  preco_unit_centavos  bigint      -- preço efetivamente cobrado (snapshot imutável)
);

-- Q1: histórico do cliente (mais recente primeiro)
CREATE TABLE IF NOT EXISTS pedidos_por_usuario (
  usuario_id               text,
  criado_em                timestamp,
  pedido_id                uuid,
  loja_id                  text,
  canal                    text,
  total_centavos           bigint,
  cupom                    text,
  desconto_cupom_centavos  bigint,
  itens                    list<frozen<item_pedido>>,
  PRIMARY KEY ((usuario_id), criado_em, pedido_id)
) WITH CLUSTERING ORDER BY (criado_em DESC, pedido_id ASC);

-- Q2: painel da loja. Partition key composta (loja_id, mes) = bucket mensal, que impede partições
-- ilimitadas e distribui as lojas pelo anel via Murmur3 + vnodes (consistent hashing).
CREATE TABLE IF NOT EXISTS pedidos_por_loja (
  loja_id                  text,
  mes                      text,
  criado_em                timestamp,
  pedido_id                uuid,
  usuario_id               text,
  canal                    text,
  total_centavos           bigint,
  cupom                    text,
  desconto_cupom_centavos  bigint,
  itens                    list<frozen<item_pedido>>,
  PRIMARY KEY ((loja_id, mes), criado_em, pedido_id)
) WITH CLUSTERING ORDER BY (criado_em DESC, pedido_id ASC);

-- Q3: pedido por id
CREATE TABLE IF NOT EXISTS pedido_por_id (
  pedido_id                uuid PRIMARY KEY,
  usuario_id               text,
  loja_id                  text,
  canal                    text,
  criado_em                timestamp,
  total_centavos           bigint,
  cupom                    text,
  desconto_cupom_centavos  bigint,
  itens                    list<frozen<item_pedido>>
);

-- Q4: eventos append-only (o pedido em si nunca é alterado)
CREATE TABLE IF NOT EXISTS eventos_pedido (
  pedido_id    uuid,
  ocorrido_em  timestamp,
  status       text,
  PRIMARY KEY ((pedido_id), ocorrido_em)
) WITH CLUSTERING ORDER BY (ocorrido_em DESC);
