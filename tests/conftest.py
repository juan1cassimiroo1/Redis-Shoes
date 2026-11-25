import shutil
import socket
import subprocess
import time

import mongomock
import pytest
import redis

from app.catalog import Catalog
from app.orders import InMemoryOrders
from app.redis_store import RedisStore
from app.seed import seed
from app.shop import Shop


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def redis_url():
    if not shutil.which("redis-server"):
        pytest.skip("redis-server não instalado")
    port = _free_port()
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL)
    url = f"redis://127.0.0.1:{port}/0"
    for _ in range(50):
        try:
            redis.Redis.from_url(url).ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield url
    proc.terminate()


@pytest.fixture()
def shop(redis_url):
    store = RedisStore.from_urls(redis_url)
    store.m.flushall()
    catalog = Catalog(mongomock.MongoClient()["t"])
    catalog.ensure_indexes()
    s = Shop(store, catalog, InMemoryOrders())
    seed(s, estoque_inicial=10)
    return s
