"""State layer: tenant-isolated semantic cache (Redis stand-in), SQLite (Postgres stand-in) with
session_facts, approvals, lots, and a SHA-256 hash-chained audit log."""
import hashlib, json, random, re, sqlite3, threading, time
from pathlib import Path
from .security import redact_pii

DB_PATH = str(Path(__file__).resolve().parent.parent / "state.db")
_norm = lambda q: re.sub(r"\s+", " ", q.lower().strip())

def cache_key(tenant, user, query):
    return f"tenant:{tenant}:" + hashlib.sha256(f"{tenant}:{user}:{_norm(query)}".encode()).hexdigest()

class TTLCache:
    """In-process Redis stand-in. TTL 300s ± 45s jitter. Entries carry tenant_id for defense in depth."""
    def __init__(self, ttl=300, jitter=45, clock=time.time):
        self.d, self.ttl, self.jitter, self.clock = {}, ttl, jitter, clock
        self.hits = self.misses = 0
        self.lock = threading.Lock()
        self.down = False   # chaos switch (FMEA #1)
    def get(self, tenant, user, query):
        if self.down: return None
        k = cache_key(tenant, user, query)
        with self.lock:
            e = self.d.get(k)
            if e and e[0] > self.clock() and e[1]["tenant_id"] == tenant:
                self.hits += 1; return e[1]
            if e and e[0] <= self.clock(): self.d.pop(k, None)
            self.misses += 1
        return None
    def set(self, tenant, user, query, value):
        if self.down: return
        exp = self.clock() + self.ttl + random.uniform(-self.jitter, self.jitter)
        with self.lock: self.d[cache_key(tenant, user, query)] = (exp, dict(value, tenant_id=tenant))
    def ratio(self):
        n = self.hits + self.misses
        return self.hits / n if n else 0.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_facts(tenant_id TEXT, session_id TEXT, fact TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, tenant_id TEXT, requester TEXT, tool TEXT, args TEXT,
  status TEXT, reviewer TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS lots(tenant_id TEXT, lot_id TEXT, status TEXT, PRIMARY KEY(tenant_id, lot_id));
CREATE TABLE IF NOT EXISTS idem(tenant_id TEXT, key TEXT, result TEXT, PRIMARY KEY(tenant_id, key));
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, ts REAL, payload TEXT,
  prev_hash TEXT, hash TEXT);
"""
GENESIS = "0" * 64

class _Result:
    def __init__(self, rows, lastrowid): self.rows, self.lastrowid = rows, lastrowid
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows
    def __iter__(self): return iter(self.rows)

class LockedDB:
    """SQLite connection shared across threads; every statement is serialized (found by the KPI-28 concurrency probe)."""
    def __init__(self, path, lock):
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None); self.lock = lock
        self.c.execute("PRAGMA journal_mode=WAL"); self.c.executescript(SCHEMA)
    def execute(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args); return _Result(cur.fetchall(), cur.lastrowid)

class Store:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.db = LockedDB(path, self.lock)
    # ---- audit chain ----
    def append_audit(self, tenant, event: dict):
        payload = json.dumps(_scrub(event), sort_keys=True, default=str)   # zero raw PII
        with self.lock:
            row = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            h = hashlib.sha256((prev + payload).encode()).hexdigest()
            cur = self.db.execute("INSERT INTO audit(tenant_id,ts,payload,prev_hash,hash) VALUES(?,?,?,?,?)",
                                  (tenant, time.time(), payload, prev, h))
            return cur.lastrowid, h
    def verify_audit(self):
        with self.lock:
            prev = GENESIS
            for seq, payload, p, h in self.db.execute("SELECT seq,payload,prev_hash,hash FROM audit ORDER BY seq"):
                if p != prev or hashlib.sha256((prev + payload).encode()).hexdigest() != h:
                    return False, seq
                prev = h
        return True, None
    def audit_rows(self, tenant=None, limit=50):
        q, a = "SELECT seq,tenant_id,payload,hash FROM audit", ()
        if tenant: q += " WHERE tenant_id=?"; a = (tenant,)
        return self.db.execute(q + " ORDER BY seq DESC LIMIT ?", a + (limit,)).fetchall()
    # ---- session memory ----
    def add_fact(self, tenant, session, fact):
        self.db.execute("INSERT INTO session_facts VALUES(?,?,?,?)", (tenant, session, redact_pii(fact)[0], time.time()))
    def facts(self, tenant, session):
        return [r[0] for r in self.db.execute(
            "SELECT fact FROM session_facts WHERE tenant_id=? AND session_id=? ORDER BY ts", (tenant, session))]
    # ---- lots / approvals ----
    def lot_status(self, tenant, lot):
        r = self.db.execute("SELECT status FROM lots WHERE tenant_id=? AND lot_id=?", (tenant, lot)).fetchone()
        return r[0] if r else "UNKNOWN"
    def set_lot(self, tenant, lot, status):
        self.db.execute("INSERT OR REPLACE INTO lots VALUES(?,?,?)", (tenant, lot, status))
    def idem_get(self, tenant, key):
        r = self.db.execute("SELECT result FROM idem WHERE tenant_id=? AND key=?", (tenant, key)).fetchone()
        return json.loads(r[0]) if r else None
    def idem_put(self, tenant, key, result):
        self.db.execute("INSERT OR REPLACE INTO idem VALUES(?,?,?)", (tenant, key, json.dumps(result)))
    def create_approval(self, aid, tenant, requester, tool, args):
        self.db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)",
                        (aid, tenant, requester, tool, json.dumps(args), "PENDING_APPROVAL", None, time.time()))
    def pending(self, tenant=None):
        q, a = "SELECT id,tenant_id,requester,tool,args,status FROM approvals WHERE status='PENDING_APPROVAL'", ()
        if tenant: q += " AND tenant_id=?"; a = (tenant,)
        return self.db.execute(q, a).fetchall()
    def decide(self, aid, tenant, reviewer, approve: bool):
        with self.lock:
            r = self.db.execute("SELECT requester,tool,args,status FROM approvals WHERE id=? AND tenant_id=?",
                                (aid, tenant)).fetchone()
            if not r or r[3] != "PENDING_APPROVAL": return None
            if r[0] == reviewer: return "SELF_APPROVAL_DENIED"    # policy-hitl-approval
            st = "APPROVED" if approve else "REJECTED"
            self.db.execute("UPDATE approvals SET status=?,reviewer=? WHERE id=?", (st, reviewer, aid))
            if approve:
                args = json.loads(r[2]); self.set_lot(tenant, args["lot_id"], "ON_HOLD")
            self.append_audit(tenant, dict(event="hitl_decision", approval_id=aid, status=st, reviewer=reviewer))
            return st

def _scrub(o):
    if isinstance(o, str): return redact_pii(o)[0]
    if isinstance(o, dict): return {k: _scrub(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_scrub(v) for v in o]
    return o
