"""Configuração por variáveis de ambiente (12-factor)."""
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")          # master (escritas)
REDIS_REPLICA_URL = os.getenv("REDIS_REPLICA_URL", "")                  # réplica (leituras eventuais)
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "redisshoes")
CASSANDRA_HOSTS = [h for h in os.getenv("CASSANDRA_HOSTS", "localhost").split(",") if h]
CASSANDRA_PORT = int(os.getenv("CASSANDRA_PORT", "9042"))
CASSANDRA_KEYSPACE = os.getenv("CASSANDRA_KEYSPACE", "redisshoes")
CASSANDRA_RF = int(os.getenv("CASSANDRA_RF", "1"))                      # produção: 3

# "real" usa Mongo/Cassandra de verdade; "local" usa mongomock + pedidos em memória
BACKENDS = os.getenv("BACKENDS", "real")
SEED_ON_START = os.getenv("SEED_ON_START", "0") == "1"
ENABLE_ADMIN = os.getenv("ENABLE_ADMIN", "1") == "1"

CART_TTL_SECONDS = 7 * 24 * 3600       # 1 semana (requisito do Marco 1)
PRODUCT_CACHE_TTL = 300
CHECKOUT_LOCK_TTL = 30
LOJA_ONLINE = "CD01"                    # centro de distribuição que atende o e-commerce
