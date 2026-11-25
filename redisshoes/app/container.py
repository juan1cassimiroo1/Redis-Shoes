"""Monta as três conexões conforme BACKENDS=real|local."""
from . import config
from .catalog import Catalog
from .orders import CassandraOrders, InMemoryOrders
from .redis_store import RedisStore
from .shop import Shop


def build_shop() -> Shop:
    store = RedisStore.from_urls(config.REDIS_URL, config.REDIS_REPLICA_URL)
    if config.BACKENDS == "local":
        import mongomock
        db = mongomock.MongoClient()[config.MONGO_DB]
        orders = InMemoryOrders()
    else:
        from pymongo import MongoClient
        db = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=5000)[config.MONGO_DB]
        orders = CassandraOrders(config.CASSANDRA_HOSTS, config.CASSANDRA_PORT,
                                 config.CASSANDRA_KEYSPACE, config.CASSANDRA_RF)
    catalog = Catalog(db)
    catalog.ensure_indexes()
    return Shop(store, catalog, orders)
