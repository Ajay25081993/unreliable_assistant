"""Agent runtime: triage -> bounded ReAct loop -> subagents (retrieval / execution / verification) -> post hooks."""
import re, time, uuid, threading
from collections import deque, defaultdict
from dataclasses import dataclass, field
import numpy as np
from .models import QueryRequest, Route, TOOLS
from .security import pre_hook, redact_pii, screen_retrieved
from .store import Store, TTLCache, _norm
from .graphify import Graph, ntok
from .rag import HybridRetriever, HashedNgramEncoder, tok
from .harness import ToolExecutionHarness, Gateway, CircuitBreaker, POOLS
from .tools import StateTools
from .telemetry import span, start_trace, current_trace_id, inc

LIMITS = dict(max_iterations=5, per_step_ms=120, total_ms=450)
ABSTAIN_MSG = "I can't find this in the knowledge base, so I won't guess. Please rephrase, or contact the quality team."
IDS = re.compile(r"(?i)\b(sku-\d{3}|lot-\d{5})\b")

# ---------------- triage ----------------
DOMAIN_KW = {"catalog": r"\b(sku|shelf|storage|stor(ed|age)|product|vaccine|insulin|saline|reagent|antibiotic)\b",
             "quality": r"\b(excursion|recall|quarantine|hold|lot|risk)\b",
             "governance": r"\b(pii|personal data|audit|approval|reviewer|hitl)\b",
             "operations": r"\b(cold chain|expir\w*|label\w*|temperature)\b",
             "sales": r"\b(return\w*)\b", "procurement": r"\b(supplier)\b"}
GREET = re.compile(r"(?i)^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon)|help|what can you do|who are you)\b")

def triage(q: str):
    """Deterministic classifier (temperature=0). Returns dict(route,intent,domain,risk_tier,complexity)."""
    ql = q.lower(); ids = IDS.findall(q)
    domain = next((d for d, rx in DOMAIN_KW.items() if re.search(rx, ql)), "general")
    cx = "multi" if (len(ids) > 1 or "excursion" in ql or " and " in ql) else "simple"
    if GREET.match(q) and not ids:
        return dict(route=Route.DIRECT, intent="smalltalk", domain="general", risk_tier=1, complexity="simple")
    if re.search(r"\b(place|put|apply|set|freeze|impose|hold)\b.*\b(hold|on hold|lot)\b|\bhold (on )?lot\b", ql) and re.search(r"(?i)lot[\s\-_]?\d+", q):
        return dict(route=Route.TX, intent="hold", domain="quality", risk_tier=3, complexity=cx)
    if re.search(r"(?i)\b(status|state)\b.*\blot[\s\-_]?\d+", q) or re.search(r"(?i)\blot[\s\-_]?\d+.*\b(status|state)\b", q):
        return dict(route=Route.TX, intent="status", domain="quality", risk_tier=1, complexity=cx)
    if re.search(r"(?i)\b(compute|calculate|assess)\b.*\brisk\b.*sku", ql) or re.search(r"(?i)\brisk score\b.*sku", ql):
        return dict(route=Route.TX, intent="risk", domain="quality", risk_tier=2, complexity=cx)
    if domain != "general" or ids:
        intent = "summarize" if re.search(r"summar|overview", ql) else "compare" if re.search(r"which|longer|shorter|compare", ql) else "lookup"
        return dict(route=Route.RAG, intent=intent, domain=domain, risk_tier=1, complexity=cx)
    return dict(route=Route.OOS, intent="out_of_scope", domain="general", risk_tier=1, complexity="simple")

# ---------------- context control ----------------
@dataclass(frozen=True)
class GoalAnchorBlock:
    intent: str
    entities: tuple
    constraints: tuple = ("no autonomous Tier3 execution", "cite sources", "tenant-scoped", "abstain if ungrounded")
    def render(self): return f"[GOAL] {self.intent} | entities={list(self.entities)} | policy={list(self.constraints)}"

def compress_observation(obs, max_tokens=180):
    """Extractive compressor: strip JSON boilerplate keys, drop long text bodies, truncate to <=180 tokens."""
    def flat(o):
        if isinstance(o, dict):
            return " ".join(f"{k}={flat(v)}" if k in ("id", "status", "lot_id", "sku", "risk_score", "risk_class", "score", "top_score", "abstain", "approval_id")
                            else flat(v) for k, v in o.items() if k not in ("text", "stage1", "stage2", "fused", "recall_pool", "edges", "hops"))
        if isinstance(o, (list, tuple)): return " ".join(flat(x) for x in o[:6])
        return str(o)
    words = flat(obs).split()
    return " ".join(words[: int(max_tokens / 1.3)])

class Memory:
    """Sliding window (last 3 ReAct turns, hot) + verified facts in SQLite session_facts."""
    def __init__(self, store: Store): self.store, self.hot = store, defaultdict(lambda: deque(maxlen=3)); self.lock = threading.Lock()
    def add_turn(self, tenant, session, turn):
        with self.lock: self.hot[(tenant, session)].append(turn)
    def window(self, tenant, session):
        with self.lock: return list(self.hot[(tenant, session)])
    def add_fact(self, tenant, session, fact): self.store.add_fact(tenant, session, fact)

# ---------------- generation + verification ----------------
SKU_RX = re.compile(r"(SKU-\d{3}) is an? (.+?)\. Its shelf life is (\d+) days\. Its storage temperature is (\S+?)\. Its risk class is (\w)\.")

def parse_sku(text):
    m = SKU_RX.search(text)
    return dict(sku=m[1], cat=m[2], shelf=int(m[3]), temp=m[4], cls=m[5]) if m else None

def _sentences(t): return [s.strip() for s in re.split(r"(?<=\.)\s+", re.sub(r"\[\[|\]\]", "", t)) if s.strip()]

class RuleBasedGenerator:
    """Deterministic extractive generator (LLM stand-in; see design ADR-2). Interface: generate(query, ctx)->(answer, cites)."""
    def generate(self, query, ctx, strict=False):
        ql = query.lower(); qids = [i.upper() for i in IDS.findall(query)]
        skus = {}
        for cid, t in ctx:
            p = parse_sku(t)
            if p: skus[p["sku"]] = (cid, p)
        want = [s for s in qids if s.startswith("SKU") and s in skus]
        if len(want) >= 2 and re.search(r"longer|shorter|which|compare", ql):
            a, b = skus[want[0]][1], skus[want[1]][1]
            if a["shelf"] == b["shelf"]: return f"{a['sku']} and {b['sku']} have the same shelf life ({a['shelf']} days).", [skus[want[0]][0], skus[want[1]][0]]
            lo = (a, b) if (a["shelf"] > b["shelf"]) == ("longer" in ql or "shorter" not in ql) else (b, a)
            return f"{lo[0]['sku']} has the {'shorter' if 'shorter' in ql else 'longer'} shelf life ({lo[0]['shelf']} days vs {lo[1]['shelf']} days).", [skus[want[0]][0], skus[want[1]][0]]
        if want and "excursion" in ql:
            cid, p = skus[want[0]]
            for pid, t in ctx:
                for s in _sentences(t):
                    if f"risk class {p['cls']} skus require".lower() in s.lower():
                        return f"{p['sku']} is risk class {p['cls']}. {s}", [cid, pid]
        if want and re.search(r"shelf|expir|last", ql):
            cid, p = skus[want[0]]; return f"{p['sku']} has a shelf life of {p['shelf']} days.", [cid]
        if want and re.search(r"stor|temperature|keep", ql):
            cid, p = skus[want[0]]; return f"{p['sku']} storage temperature is {p['temp']}.", [cid]
        if want and re.search(r"class|risk", ql):
            cid, p = skus[want[0]]; return f"{p['sku']} is risk class {p['cls']}.", [cid]
        if not ctx: return None, []
        cid, t = ctx[0]
        sents = _sentences(re.sub(r"^#.*\n", "", t).strip())
        if re.search(r"summar|overview|explain|describe", ql): return " ".join(sents[:3]), [cid]
        qt = set(tok(query))
        best = sorted(sents, key=lambda s: -len(qt & set(tok(s))))[: 1 if strict else 2]
        return " ".join(best), [cid]

def _facts(t):
    t = t.lower()
    return set(re.findall(r"\d+(?:-\d+)?c?", t)) | set(re.findall(r"(?:sku|lot)-\d+", t)) | {f"class {m}" for m in re.findall(r"class (\w)\b", t)} \
        | {f"tier {m}" for m in re.findall(r"tier (\d)", t)}

def verify_claims(answer, ctx):
    """NLI stand-in (claim -> chunk). A claim is supported iff every checkable fact in it (numbers, SKU/LOT ids,
    risk classes, tiers) appears in the context AND >=60% of its content tokens appear in the context.
    Returns (faithfulness, unsupported_claims)."""
    pool = set(); facts = set()
    for _, t in ctx: pool |= set(tok(t)); facts |= _facts(t)
    claims = [c for c in _sentences(re.sub(r"\[[^\]]*\]", "", answer)) if c]
    if not claims: return 1.0, []
    bad = []
    for c in claims:
        ct = tok(c); sup = sum(1 for t in ct if t in pool) / max(1, len(ct))
        if sup < 0.6 or not _facts(c) <= facts: bad.append(c)
    return round(1 - len(bad) / len(claims), 4), bad

_enc = HashedNgramEncoder()
def answer_relevancy(q, a):
    """Proxy for RAGAS answer relevancy: share of the question's content terms (stemmed, IDs included) that the answer
    addresses. Not the LLM-regenerated-question method; documented in the scorecard."""
    qt = set(tok(q)); at = set(tok(a))
    return len(qt & at) / len(qt) if qt else 1.0

# ---------------- planners (ReAct "reasoning") ----------------
class RagPlanner:
    def __init__(self, params): self.p = params
    def next(self, ctx, obs):
        if not obs: return ("hybrid_retrieve", dict(query=ctx["query"], **self.p))
        if len(obs) == 1 and not obs[0]["abstain"] and ctx["entities"]:
            return ("subgraph", dict(anchor=ctx["entities"][0].lower(), depth=2))
        if len(obs) == 1 and not obs[0]["abstain"]:
            a = Graph_anchor(ctx["graph"], ctx["query"], obs[0])
            if a: return ("subgraph", dict(anchor=a, depth=2))
        return None

def Graph_anchor(graph, query, ret):
    a = graph.resolve_anchor(query)
    if a: return a
    return ret["reranked"][0]["id"] if ret["reranked"] else None

class TxPlanner:
    def next(self, ctx, obs):
        q = ctx["query"]; t = ctx["triage"]
        lot = re.search(r"(?i)\blot[\s\-_]?(\d+)", q); sku = re.search(r"(?i)\bsku[\s\-_]?(\d+)", q)
        if t["intent"] == "status": return ("get_lot_status", dict(lot_id=lot[0])) if not obs and lot else None
        if t["intent"] == "risk":
            qty = re.search(r"(?i)(?:qty|quantity|units?)\D{0,3}(\d+)", q)
            return ("compute_risk", dict(sku=sku[0], qty=int(qty[1]) if qty else 1)) if not obs and sku else None
        if t["intent"] == "hold" and lot:
            if not obs: return ("get_lot_status", dict(lot_id=lot[0]))
            if len(obs) == 1 and obs[0].get("status") in ("RELEASED", "QUARANTINE"):
                why = re.search(r"(?i)(?:because|reason:?|due to)\s+(.+)", q)
                return ("place_lot_hold", dict(lot_id=lot[0], reason=(why[1] if why else "requested via assistant")[:200]))
        return None

class LoopingPlanner:   # chaos: a broken planner that retries the same bad call forever
    def next(self, ctx, obs): return ("get_lot_status", dict(lot_id="LOT-ZZZZZ"))

ALLOWED = {"retrieval": {"hybrid_retrieve", "subgraph"}, "execution": {"get_lot_status", "compute_risk", "place_lot_hold"},
           "verification": set()}

class Agent:
    def __init__(self, store=None, retriever=None, graph=None):
        self.store = store or Store()
        self.graph = graph or Graph()
        self.retriever = retriever or HybridRetriever()
        self.gw = Gateway(StateTools(self.store, self.graph), self.retriever, self.graph)
        self.breaker = CircuitBreaker(2)
        self.harness = ToolExecutionHarness(self.gw, self.store, self.breaker)
        self.cache, self.memory, self.gen = TTLCache(), Memory(self.store), RuleBasedGenerator()
        self.plugins = dict(pii_redactor=True, injection_shield=True, circuit_breaker=True, ragas_auditor=True, otel_exporter=True)
        self.params = dict(k1=1.25, b=0.75, rrf_k=60, threshold=0.42, top_k=50)
        self.strict_extractive = False
        self.sem = defaultdict(list); self.sem_lock = threading.Lock()
        self.price = dict(inp=0.25 / 1e6, out=1.25 / 1e6)          # modeled $/token (configurable assumption)
        self.metrics = defaultdict(float)

    def apply_runbook(self, name):
        """Incident mitigations (ops/runbooks.md). Returns a description of what changed."""
        if name == "latency":
            self.params.update(top_k=25); self.cache.ttl = 600; return "top_k 50->25, cache TTL 600s"
        if name == "hallucination":
            self.params.update(threshold=0.55); self.strict_extractive = True; return "cross-encoder cutoff 0.42->0.55, strict extractive mode"
        if name == "mcp_hang":
            self.breaker.reset(); return "breaker reset; in-process index already serves as the cached replica"
        if name == "cache":
            self.cache.ttl, self.cache.jitter = 300, 45; return "TTL 300s +/- 45s jitter enforced"
        if name == "reset":
            self.params.update(top_k=50, threshold=0.42); self.strict_extractive = False; self.cache.ttl = 300; return "defaults restored"
        raise ValueError(name)

    # ---- semantic cache layer (same tenant+user, same entity IDs, cos>=0.92) ----
    def _sem_get(self, tenant, user, q):
        ids = frozenset(i.lower() for i in IDS.findall(q)); e = _enc.encode([_norm(q)])[0]
        with self.sem_lock: cands = list(self.sem[(tenant, user)])
        for emb, qids, qtext in cands:
            if qids == ids and float(emb @ e) >= 0.92:
                r = self.cache.get(tenant, user, qtext)
                if r: return r
        return None
    def _sem_put(self, tenant, user, q):
        with self.sem_lock:
            lst = self.sem[(tenant, user)]; lst.append((_enc.encode([_norm(q)])[0], frozenset(i.lower() for i in IDS.findall(q)), _norm(q)))
            del lst[:-200]

    def handle(self, req: QueryRequest, traceparent=None, planner=None, force_sub=None):
        t0 = time.perf_counter(); tid = start_trace(traceparent)
        tenant, user, sess = req.tenant_id, req.user_id, req.session_id or f"{req.user_id}-default"
        out = dict(tenant_id=tenant, trace_id=tid, answer="", route=None, citations=[], faithfulness=None, abstained=False,
                   halted=None, cache_hit=False, steps=[], pii_redacted=0, tokens=dict(prompt=0, baseline=0, output=0))
        def done(**kw):
            out.update(kw); out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            inc("requests_total"); return out
        # --- hook.pre ---
        with span("hook.pre"):
            clean, info = pre_hook(req.query, self.plugins)
        out["pii_redacted"] = info["pii"]
        if info["blocked"]:
            self.store.append_audit(tenant, dict(event="blocked", user=user, query=clean, hits=info["hits"][:2]))
            inc("blocked_total")
            return done(route="BLOCKED", answer="Request blocked by security policy.", halted="BLOCKED_SECURITY")
        # --- cache ---
        with span("cache.lookup"):
            hit = self.cache.get(tenant, user, clean) or self._sem_get(tenant, user, clean)
        if hit and hit.get("tenant_id") == tenant:
            inc("cache_hit"); res = {k: v for k, v in hit.items() if k not in ("trace_id", "latency_ms")}
            out.update(res); out.update(cache_hit=True, trace_id=tid)
            return done()
        # --- triage ---
        with span("triage.classify"):
            tri = triage(clean)
        out["triage"] = {**tri, "route": tri["route"].value}; out["route"] = tri["route"].value
        ents = [i.upper() for i in IDS.findall(clean)]
        anchor = GoalAnchorBlock(tri["intent"], tuple(ents))
        if tri["route"] == Route.DIRECT:
            return done(answer="Hello! I can answer questions about products (SKUs) and quality policies, check lot status, and request lot holds for human approval.")
        if tri["route"] == Route.OOS:
            return done(answer="That's outside what I can help with. I cover product catalog, quality and governance policies.", abstained=True)
        ctx = dict(query=clean, entities=ents, triage=tri, graph=self.graph)
        sub = force_sub or ("retrieval" if tri["route"] == Route.RAG else "execution")
        pl = planner or (RagPlanner(self.params) if sub == "retrieval" else TxPlanner())
        loop = self._react(ctx, pl, sub, tenant, user, req.role, sess, anchor)
        out["steps"] = loop["trace"]; out["goal_anchor"] = anchor.render()
        if loop["halted"]:
            return done(halted=loop["halted"], answer=loop["message"], abstained=True)
        if sub == "execution":
            return done(**self._finish_tx(loop, tenant, sess, clean, tri))
        # --- retrieval answer path ---
        ret = next((o for o in loop["obs"] if "reranked" in o), None)
        if ret is None or ret["abstain"]:                         # deterministic abstention short-circuit (no LLM)
            inc("abstain_total")
            r = dict(answer=ABSTAIN_MSG, abstained=True, faithfulness=1.0, citations=[])
            self._finalize(tenant, user, clean, dict(r, route=out['route']), cacheable=True)
            return done(**r)
        chunks = []
        for c in ret["reranked"]:
            if screen_retrieved(c["text"]): chunks.append((c["id"], c["text"]))
            else: self.store.append_audit(tenant, dict(event="indirect_injection_dropped", chunk=c["id"]))
        sg = next((o for o in loop["obs"] if "nodes" in o), None)
        seen = {c for c, _ in chunks}
        for n in (sg["nodes"] if sg else []):
            if n in self.retriever.by_id and n not in seen and len(chunks) < 6:
                t = self.retriever.by_id[n]["text"]
                if screen_retrieved(t): chunks.append((n, t)); seen.add(n)
        # order: entity-matching notes first
        qids = {e.lower() for e in ents}
        chunks.sort(key=lambda c: (c[0] not in qids,))
        with span("llm.generate"):
            with POOLS["llm"]:
                ans, cites = self.gen.generate(clean, chunks, strict=self.strict_extractive)
        if ans is None:
            r = dict(answer=ABSTAIN_MSG, abstained=True, faithfulness=1.0, citations=[])
            self._finalize(tenant, user, clean, dict(r, route=out['route']), cacheable=True); return done(**r)
        # --- verification subagent (may veto) + hook.post ---
        with span("hook.post.ragas"):
            faith, bad = verify_claims(ans, chunks) if self.plugins["ragas_auditor"] else (1.0, [])
        if faith < 0.92:
            inc("veto_total")
            self.store.append_audit(tenant, dict(event="veto", unsupported=bad[:3]))
            r = dict(answer="I couldn't verify that answer against the sources, so I'm not providing it.", abstained=True, faithfulness=faith, citations=[])
            return done(**r)
        ans, _ = redact_pii(ans)
        base = ntok(" ".join(self.retriever.chunks[i]["text"] for i in range(min(10, len(self.retriever.chunks)))))   # naive 10-chunk dump
        used = ntok(anchor.render() + (sg["text"] if sg else " ".join(t for _, t in chunks[:2])) + clean)
        outt = ntok(ans)
        r = dict(answer=f"{ans} [{', '.join(dict.fromkeys(cites))}]", citations=list(dict.fromkeys(cites)), faithfulness=faith,
                 abstained=False, tokens=dict(prompt=used, baseline=base + ntok(anchor.render()) + ntok(clean), output=outt),
                 relevancy=round(answer_relevancy(clean, ans), 4),
                 cost_usd=round(used * self.price["inp"] + outt * self.price["out"], 8))
        self.memory.add_turn(tenant, sess, dict(q=clean, a=ans))
        for e in ents: self.memory.add_fact(tenant, sess, f"user asked about {e}")
        self.store.append_audit(tenant, dict(event="answer", user=user, query=clean, route=out["route"], cites=r["citations"], faith=faith))
        self._finalize(tenant, user, clean, dict(r, route=out['route']), cacheable=True)
        return done(**r)

    def _finalize(self, tenant, user, q, r, cacheable):
        if cacheable: self.cache.set(tenant, user, q, r); self._sem_put(tenant, user, q)

    def _finish_tx(self, loop, tenant, sess, q, tri):
        last = loop["obs"][-1] if loop["obs"] else None
        if not last: return dict(answer="I couldn't determine what to do with that request.", abstained=True)
        if "approval_id" in last:
            self.memory.add_fact(tenant, sess, f"hold requested approval={last['approval_id']}")
            return dict(answer=f"Lot hold requested. Status: PENDING_APPROVAL (id {last['approval_id']}). A reviewer must approve before anything changes.",
                        citations=["policy-hitl-approval"], faithfulness=1.0, hitl=last["approval_id"])
        if "risk_score" in last:
            self.memory.add_fact(tenant, sess, f"{last['sku']} risk {last['risk_score']}")
            return dict(answer=f"{last['sku']} is risk class {last['risk_class']} with a risk score of {last['risk_score']}.", faithfulness=1.0)
        if "status" in last:
            self.memory.add_fact(tenant, sess, f"{last['lot_id']} status {last['status']}")
            return dict(answer=f"{last['lot_id']} status is {last['status']}.", faithfulness=1.0)
        return dict(answer="Done.", faithfulness=1.0)

    # ---- bounded ReAct state machine ----
    def _react(self, ctx, planner, sub, tenant, user, role, sess, anchor):
        trace, obs_raw, obs_c = [], [], []
        state = "INIT"; t_loop = time.perf_counter(); last_action = None
        state = "TRIAGE"
        halted = msg = None
        for i in range(LIMITS["max_iterations"]):
            ts = time.perf_counter()
            with span("agent.react.step", step=i):
                state = "REASONING"
                prompt = [anchor.render()] + [f"[OBS] {o}" for o in obs_c[-3:]]     # anchor is ALWAYS index 0; last 3 turns
                assert prompt[0].startswith("[GOAL]")
                action = planner.next(ctx, obs_raw)
                if action is None: state = "VERIFY"; trace.append(dict(step=i, state=state, action="final")); break
                tool, args = action
                if last_action == (tool, repr(args)) and (not obs_raw or obs_raw[-1] == obs_raw[-1]) and len(obs_raw) >= 1 and obs_c and "ERR" not in obs_c[-1]:
                    halted, msg = "HALTED_LOOP", "Detected a repeated step with no state change; stopping."; break
                last_action = (tool, repr(args))
                state = "TOOL_CALL"
                if tool not in ALLOWED[sub]:
                    r = dict(ok=False, error=dict(code=-32001, message=f"{sub} subagent may not call {tool}"))
                else:
                    r = self.harness.call(tool, args, tenant, user, role)
                state = "OBSERVATION"
                if r.get("halted_breaker"):
                    halted, msg = "HALTED_BREAKER", r["error"]["message"]
                    trace.append(dict(step=i, state=state, tool=tool, ok=False, error=r["error"], ms=round((time.perf_counter()-ts)*1000, 2))); break
                if not r["ok"]:
                    obs_c.append("ERR " + r["error"]["message"][:120]); obs_raw.append(dict(error=r["error"]))
                    trace.append(dict(step=i, state=state, tool=tool, ok=False, error=r["error"], ms=round((time.perf_counter()-ts)*1000, 2)))
                    halted, msg = "HALTED_ERROR", f"I couldn't complete that: {r['error']['message']}"; break
                res = r["result"]; obs_raw.append(res); obs_c.append(compress_observation(res))
                el = (time.perf_counter() - ts) * 1000
                trace.append(dict(step=i, state=state, tool=tool, args=args if tool != "hybrid_retrieve" else "query", obs=obs_c[-1], ms=round(el, 2)))
                if el > LIMITS["per_step_ms"]: halted, msg = "HALTED_STEP_TIMEOUT", "A step exceeded its time budget."; break
                if (time.perf_counter() - t_loop) * 1000 > LIMITS["total_ms"]: halted, msg = "HALTED_BUDGET", "The loop budget was exhausted."; break
        else:
            halted, msg = "HALTED_MAX_ITER", "Reached the maximum number of reasoning steps."
        return dict(trace=trace, obs=obs_raw, halted=halted, message=msg, state="HALTED" if halted else "DONE")
