"""ToolExecutionHarness: auto-repair -> Pydantic strict validation -> RBAC tier -> UUIDv4 idempotency ->
circuit breaker -> bulkheaded dispatch. Tier 3 mutations are only ever recorded as PENDING_APPROVAL."""
import hashlib, json, re, threading, time, uuid
from collections import Counter
from pydantic import ValidationError
from .models import TOOLS, ROLE_MAX_TIER, ERR, Tier
from .telemetry import span, inc
from .tools import StateTools, ToolError
from .store import Store

POOLS = dict(rag=threading.BoundedSemaphore(64), db=threading.BoundedSemaphore(128), llm=threading.BoundedSemaphore(256))
SERVER_POOL = {"hybrid-rag": "rag", "graphify-vault": "rag", "state-store": "db"}
TIER_N = {Tier.T1: 1, Tier.T2: 2, Tier.T3: 3}

def repair_args(args: dict) -> tuple[dict, bool]:
    """Deterministic regex auto-repair. Returns (args, changed)."""
    out, ch = dict(args), False
    for k, v in args.items():
        if not isinstance(v, str): continue
        n = v.strip()
        if k == "sku" or k.endswith("_sku"):
            m = re.fullmatch(r"(?i)sku[\s\-_]?0*(\d{1,3})", n)
            if m: n = f"SKU-{int(m[1]):03d}"
        if k == "lot_id":
            m = re.fullmatch(r"(?i)lot[\s\-_]?0*(\d{1,5})", n)
            if m: n = f"LOT-{int(m[1]):05d}"
        if n != v: out[k], ch = n, True
    return out, ch

def args_hash(tool, args):
    a = {k: v for k, v in args.items() if k != "idempotency_key"}
    return hashlib.sha256((tool + json.dumps(a, sort_keys=True)).encode()).hexdigest()[:16]

class CircuitBreaker:
    """Trips when the same (tenant, tool, canonical_args_hash) fails >= max_identical times; half-opens after `cooldown` s."""
    def __init__(self, max_identical=2, cooldown=30.0):
        self.max, self.cooldown, self.fail, self.open, self.lock = max_identical, cooldown, Counter(), {}, threading.Lock()
    def allow(self, key):
        with self.lock:
            t = self.open.get(key)
            if t is None: return True
            if time.time() - t > self.cooldown: self.open.pop(key); self.fail.pop(key, None); return True
            return False
    def record(self, key, ok):
        with self.lock:
            if ok: self.fail.pop(key, None); return
            self.fail[key] += 1
            if self.fail[key] >= self.max and key not in self.open: self.open[key] = time.time(); inc("breaker_trips")
    def reset(self):
        with self.lock: self.fail.clear(); self.open.clear()

class Gateway:
    """MCP loopback: wraps every call in a real JSON-RPC 2.0 envelope (serialize + parse) so the overhead is
    measurable (KPI-30), then dispatches to the server implementation."""
    def __init__(self, tools: StateTools, retriever, graph):
        self.state, self.retriever, self.graph = tools, retriever, graph
        self.rpc_ms = []
    def call(self, server, tool, tenant, args):
        t0 = time.perf_counter()
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args, "_meta": {"tenant": tenant}}})
        msg = json.loads(req)
        t1 = time.perf_counter()
        a, tn = msg["params"]["arguments"], msg["params"]["name"]
        if tn == "hybrid_retrieve": res = self.retriever.retrieve(**a)
        elif tn == "subgraph": res = self.graph.subgraph(a["anchor"], a["depth"])
        else: res = getattr(self.state, tn)(tenant, a)
        t2 = time.perf_counter()
        out = json.loads(json.dumps({"jsonrpc": "2.0", "id": 1, "result": res}, default=list))["result"]
        t3 = time.perf_counter()
        self.rpc_ms.append(((t1 - t0) + (t3 - t2)) * 1000)
        if len(self.rpc_ms) > 5000: del self.rpc_ms[:2500]
        return out

class ToolExecutionHarness:
    def __init__(self, gateway: Gateway, store: Store, breaker: CircuitBreaker | None = None, timeout_ms=150):
        self.gw, self.store, self.breaker, self.timeout_ms = gateway, store, breaker or CircuitBreaker(), timeout_ms
        self.stats = Counter()

    def call(self, tool, raw_args, tenant, user, role="operator"):
        """Returns dict(ok, result|error{code,message}, tier, repaired, halted_breaker?)."""
        if tool not in TOOLS: return self._err(ERR["INVALID"], f"unknown tool {tool}")
        model, tier, server = TOOLS[tool]
        args, repaired = repair_args(raw_args)
        if tier == Tier.T3 and "idempotency_key" not in args:        # derive a deterministic UUIDv4-formatted key
            h = hashlib.sha256(f"{tenant}:{tool}:{args_hash(tool, args)}".encode()).hexdigest()
            args["idempotency_key"] = str(uuid.UUID(h[:32], version=4))
        key = (tenant, tool, args_hash(tool, args))   # tenant-scoped: one tenant's failures must not trip another's breaker
        self.stats["calls"] += 1
        if not self.breaker.allow(key):
            return self._err(ERR["BREAKER"], "HALTED_BREAKER: this exact call failed repeatedly. Change the arguments "
                             "(e.g. check the ID format) or ask a human to investigate.", halted=True)
        try:
            with span("harness.validate"):
                v = model.model_validate(args).model_dump()
        except ValidationError as e:
            self.stats["invalid_first_pass" if not repaired else "invalid_after_repair"] += 1
            self.breaker.record(key, False)
            return self._err(ERR["INVALID"], "invalid arguments: " + "; ".join(f"{'.'.join(map(str,x['loc']))}: {x['msg']}" for x in e.errors()[:3]))
        if repaired: self.stats["repaired_ok"] += 1
        else: self.stats["first_pass_ok"] += 1
        if TIER_N[tier] > ROLE_MAX_TIER.get(role, 0) and not (tier == Tier.T3 and role in ("operator", "admin")):
            self.store.append_audit(tenant, dict(event="rbac_denied", tool=tool, user=user, role=role))
            return self._err(ERR["RBAC"], f"role {role} may not call {tier.value}")
        if tier == Tier.T3:
            return self._tier3(tool, v, tenant, user, key)
        pool = POOLS[SERVER_POOL[server]]
        if not pool.acquire(timeout=0.05): return self._err(ERR["TIMEOUT"], "bulkhead saturated")
        try:
            t0 = time.perf_counter()
            res = self.gw.call(server, tool, tenant, v)
            if (time.perf_counter() - t0) * 1000 > self.timeout_ms:
                self.breaker.record(key, False); return self._err(ERR["TIMEOUT"], "tool timeout")
        except ToolError as e:
            self.breaker.record(key, False)
            return self._err(e.code, e.msg)
        finally: pool.release()
        self.breaker.record(key, True)
        return dict(ok=True, result=res, tier=tier.value, repaired=repaired)

    def _tier3(self, tool, v, tenant, user, key):
        idem = self.store.idem_get(tenant, v["idempotency_key"])
        if idem: return dict(idem, duplicate=True)
        aid = str(uuid.uuid4())
        self.store.create_approval(aid, tenant, user, tool, v)
        self.store.append_audit(tenant, dict(event="tier3_pending", tool=tool, approval_id=aid, user=user, args=v))
        r = dict(ok=True, tier=Tier.T3.value, result=dict(status="PENDING_APPROVAL", approval_id=aid), hitl=True)
        self.store.idem_put(tenant, v["idempotency_key"], r)
        self.breaker.record(key, True); inc("hitl_pending")
        return r

    def _err(self, code, msg, halted=False):
        return dict(ok=False, error=dict(code=code, message=msg), halted_breaker=halted)
