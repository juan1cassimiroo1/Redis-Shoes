"""Camada Wide-Column (Cassandra): histórico IMUTÁVEL de pedidos, modelado query-first.

Consultas que guiam o modelo (Q1..Q4):
  Q1  histórico de pedidos de um cliente, do mais recente ao mais antigo
  Q2  pedidos de uma loja em um mês (painel de analytics), do mais recente ao mais antigo
  Q3  um pedido pelo id
  Q4  linha do tempo de status de um pedido
Cada consulta tem sua própria tabela (desnormalização) e o pedido nunca sofre UPDATE: mudanças
de status são novos eventos em `eventos_pedido`.
"""
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "cql" / "schema.cql"


def mes_de(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


class InMemoryOrders:
    """Mesma interface do repositório Cassandra, para testes e modo local."""

    def __init__(self):
        self.by_id, self.events_ = {}, {}

    def save_order(self, o):
        self.by_id[o["pedido_id"]] = dict(o)

    def get_order(self, pedido_id):
        return self.by_id.get(pedido_id)

    def orders_by_user(self, uid, limit=50):
        r = [o for o in self.by_id.values() if o["usuario_id"] == uid]
        return sorted(r, key=lambda o: o["criado_em"], reverse=True)[:limit]

    def orders_by_store_month(self, loja, mes, limit=100):
        r = [o for o in self.by_id.values() if o["loja_id"] == loja and mes_de(o["criado_em"]) == mes]
        return sorted(r, key=lambda o: o["criado_em"], reverse=True)[:limit]

    def add_event(self, pedido_id, status):
        self.events_.setdefault(pedido_id, []).insert(0, {"ocorrido_em": datetime.now(timezone.utc).replace(tzinfo=None), "status": status})

    def events(self, pedido_id):
        return self.events_.get(pedido_id, [])

    def ping(self):
        return True


class CassandraOrders:
    def __init__(self, hosts, port=9042, keyspace="redisshoes", rf=1, retries=40):
        from cassandra import ConsistencyLevel
        from cassandra.cluster import Cluster
        self.CL = ConsistencyLevel
        last = None
        for _ in range(retries):                       # o Cassandra demora ~1 min para subir
            try:
                self.cluster = Cluster(hosts, port=port)
                self.session = self.cluster.connect()
                break
            except Exception as e:                      # noqa: BLE001
                last = e
                time.sleep(3)
        else:
            raise RuntimeError(f"Cassandra indisponível: {last}")
        self._schema(keyspace, rf)
        self._prepare()

    def _schema(self, keyspace, rf):
        s = self.session
        s.execute(f"CREATE KEYSPACE IF NOT EXISTS {keyspace} WITH replication = "
                  f"{{'class': 'SimpleStrategy', 'replication_factor': {rf}}}")
        s.set_keyspace(keyspace)
        texto = "\n".join(l for l in SCHEMA_FILE.read_text().splitlines() if not l.strip().startswith("--"))
        for stmt in texto.split(";"):
            if stmt.strip():
                s.execute(stmt.strip())

    def _prepare(self):
        p, cols = self.session.prepare, ("pedido_id, usuario_id, loja_id, canal, criado_em, total_centavos, "
                                         "cupom, desconto_cupom_centavos, itens")
        ph = ", ".join("?" * 9)
        self.ins_user = p(f"INSERT INTO pedidos_por_usuario ({cols}) VALUES ({ph})")
        self.ins_loja = p(f"INSERT INTO pedidos_por_loja (mes, {cols}) VALUES (?, {ph})")
        self.ins_id = p(f"INSERT INTO pedido_por_id ({cols}) VALUES ({ph})")
        self.ins_evt = p("INSERT INTO eventos_pedido (pedido_id, ocorrido_em, status) VALUES (?, ?, ?)")
        self.q_user = p("SELECT * FROM pedidos_por_usuario WHERE usuario_id = ? LIMIT ?")
        self.q_loja = p("SELECT * FROM pedidos_por_loja WHERE loja_id = ? AND mes = ? LIMIT ?")
        self.q_id = p("SELECT * FROM pedido_por_id WHERE pedido_id = ?")
        self.q_evt = p("SELECT * FROM eventos_pedido WHERE pedido_id = ?")

    @staticmethod
    def _itens_tuplas(o):
        return [(i["sku"], i["modelo"], i["cor"], i["tamanho"], i["qtd"], i["preco_base_centavos"],
                 i["desconto_pct"], i["preco_unit_centavos"]) for i in o["itens"]]

    @staticmethod
    def _row(r):
        d = r._asdict()
        d["pedido_id"] = str(d["pedido_id"])
        d["itens"] = [i._asdict() if hasattr(i, "_asdict") else i for i in d["itens"]]
        return d

    def save_order(self, o):
        from cassandra.query import BatchStatement, BatchType
        pid = uuid.UUID(o["pedido_id"])
        vals = (pid, o["usuario_id"], o["loja_id"], o["canal"], o["criado_em"], o["total_centavos"],
                o.get("cupom"), o.get("desconto_cupom_centavos", 0), self._itens_tuplas(o))
        b = BatchStatement(batch_type=BatchType.LOGGED, consistency_level=self.CL.QUORUM)
        b.add(self.ins_user, vals)
        b.add(self.ins_loja, (mes_de(o["criado_em"]),) + vals)
        b.add(self.ins_id, vals)
        self.session.execute(b)

    def get_order(self, pedido_id):
        r = self.session.execute(self.q_id, (uuid.UUID(pedido_id),)).one()
        return self._row(r) if r else None

    def orders_by_user(self, uid, limit=50):
        self.q_user.consistency_level = self.CL.QUORUM          # R + W > RF: lê o que acabou de gravar
        return [self._row(r) for r in self.session.execute(self.q_user, (uid, limit))]

    def orders_by_store_month(self, loja, mes, limit=100):
        self.q_loja.consistency_level = self.CL.ONE             # analytics tolera consistência eventual
        return [self._row(r) for r in self.session.execute(self.q_loja, (loja, mes, limit))]

    def add_event(self, pedido_id, status):
        self.session.execute(self.ins_evt, (uuid.UUID(pedido_id), datetime.now(timezone.utc).replace(tzinfo=None), status))

    def events(self, pedido_id):
        return [r._asdict() for r in self.session.execute(self.q_evt, (uuid.UUID(pedido_id),))]

    def ping(self):
        self.session.execute("SELECT now() FROM system.local")
        return True
