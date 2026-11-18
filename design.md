# design.md — The Unreliable Assistant → Enterprise Grade

**Status: LOCKED (Gate 1).** Changes require an ADR amendment in §1.7.

## 1.1 System Charter & Hard Boundaries

**Scope.** A multi-tenant agentic Q&A / workflow assistant over an enterprise knowledge base (policies, SOPs,
product catalog). It answers grounded questions (3-stage hybrid RAG), and proposes transactional actions
(lot holds, SKU updates) through a validated, RBAC-tiered tool harness.

**Non-goals / hard boundaries**
- **Zero autonomous clinical, financial or legal execution.** Any `TIER_3_MUTATE` call is frozen as
  `PENDING_APPROVAL` until an authorized human approves (HITL).
- No raw PII is persisted (logs, audit chain, cache, memory) — redaction happens before anything is stored.
- No cross-tenant data access: `tenant_id` is part of every cache key, DB row and audit event.
- No unbounded loops: ReAct is a bounded state machine (§1.7 ADR-4).
- No free-form web access.

**Personas.** *Operator* (asks questions, needs cited answers), *Reviewer* (approves/rejects Tier 3),
*Platform Engineer* (watches KPIs, runbooks, load tests), *Red-teamer* (adversarial testing).

## 1.2 MCP Tool & Data Contracts

Transport: JSON-RPC 2.0 over MCP (stdio for `hybrid-rag`, `graphify-vault`; streamable-http for `state-store`).
Pydantic v2 models live in `core/models.py` (strict mode, `extra="forbid"`).

| Server | Tool / Resource | Input (regex constraints) | Output | Tier |
|---|---|---|---|---|
| hybrid-rag | `hybrid_retrieve` | `query: str(1..1000)`, `k1: float 0.5..3`, `b: float 0..1`, `rrf_k: int 1..200`, `threshold: float 0..1`, `top_k: int 1..100` | `RetrievalResult{stage1[], stage2[], fused[], reranked[≤4], abstain: bool, top_score}` | T1 |
| hybrid-rag | resource `rag://chunks/{id}` | `id` matches `^[a-z0-9\-]{3,64}$` | chunk text + metadata | T1 |
| graphify-vault | `subgraph` | `anchor: str`, `depth: int 0..2` | `{nodes[], edges[], text, tokens}` | T1 |
| state-store | `get_lot_status` | `lot_id` `^LOT-\d{5}$` | `{lot_id,status,tenant_id}` | T1 |
| state-store | `compute_risk` | `sku` `^SKU-\d{3}$`, `qty: int 1..100000` | `{sku,risk_score}` | T2 |
| state-store | `place_lot_hold` | `lot_id` `^LOT-\d{5}$`, `reason: str(1..200)`, `idempotency_key: UUIDv4` | `{status:"PENDING_APPROVAL", approval_id}` | **T3** |
| state-store | `append_audit` | `event: dict` | `{seq, hash}` | internal |

**Error codes** (JSON-RPC `error.code`): `-32602` invalid params (schema), `-32001` RBAC denied,
`-32002` duplicate idempotency key (returns original result), `-32003` breaker open (`HALTED_BREAKER`),
`-32004` tool timeout, `-32005` pending approval, `-32006` tenant mismatch.

**Auto-repair (deterministic, pre-validation):** `SKU99`→`SKU-099`, `sku 7`→`SKU-007`, `lot 44`→`LOT-00044`,
`LOT44`→`LOT-00044`. Repair is regex-only; never LLM-driven.

**HTTP API.** `POST /api/v1/agent/query` `{tenant_id, user_id, query, session_id?}` →
`{tenant_id, answer, route, citations[], faithfulness, abstained, trace_id, latency_ms, halted?}`.
Also `/healthz`, `/metrics`, `/api/v1/hitl/pending`, `/api/v1/hitl/{id}/decide`.

## 1.3 Distributed Topology & Data Flow

```
Client ─► NGINX/Envoy ─► FastAPI worker (stateless, N replicas)
   Ingress: hook.pre [PII redactor → injection shield] ─► Redis semantic cache
      key = SHA256(tenant_id:user_id:norm_query), TTL 300s ± 45s jitter
   miss ─► Triage (temp 0, ≤25 ms) ─► DIRECT_ANSWER | HYBRID_RAG | TRANSACTION_EXEC | REJECT_OOS
   HYBRID_RAG ─► Retrieval subagent ─► MCP hybrid-rag (BM25 → Bi-Encoder → RRF → Cross-Encoder/ONNX)
                                      └► MCP graphify-vault (depth ≤ 2 subgraph)
        score < 0.42 ─► deterministic abstention (<90 ms, no LLM)
   TRANSACTION_EXEC ─► Execution subagent ─► ToolExecutionHarness ─► MCP state-store ─► Postgres
        Tier 3 ─► HITL gate (PENDING_APPROVAL)
   Verification subagent (NLI claim→chunk, RAGAS) can veto ─► hook.post ─► audit chain + OTel
   OTel Collector ─► Prometheus + Tempo + Grafana
```
Bulkheads: `rag_pool=64`, `db_pool=128`, `llm_pool=256`. Reference implementation substitutes
SQLite for Postgres and an in-process TTL store for Redis behind the same interfaces (`core/store.py`).
The diagram is `docs/workflow_orchestration.png`.

## 1.4 End-to-End Latency Budget (200–600 ms)

| Stage | p95 budget (ms) | p99 (ms) |
|---|--:|--:|
| Edge LB + TLS + routing | 15 | 25 |
| hook.pre (PII + injection) | 15 | 22 |
| cache.lookup (Redis) | 5 | 10 |
| triage.classify | 25 | 35 |
| BM25 | 12 | 18 |
| Bi-encoder + ANN | 25 | 35 |
| RRF | 3 | 5 |
| Cross-encoder rerank | 45 | 60 |
| MCP JSON-RPC overhead (3 hops × ≤4) | 12 | 18 |
| LLM generate (TTFT-bounded, prefix cached) | 180 | 260 |
| hook.post (async RAGAS sampling; sync NLI veto) | 40 | 60 |
| Graphify subgraph | 10 | 15 |
| Headroom / queueing | 63 | 41 |
| **Sum (additive worst case)** | **450** | **600** |

Abstention path: 15+5+25+12+25+3+45+12 ≈ **142 ms worst-case** budget sum but typical <90 ms (no LLM).
Cache-hit path ≈ 40 ms. The p95 ≤ 450 / p99 ≤ 600 claim is a **budget**; it is only *verified* by running
`load_tests/` against a real deployment (see KPI-31..36 status in the scorecard — they stay `NOT MEASURED`
unless real k6/JMeter results are ingested).

## 1.5 KPI & SLA Gate Matrix (36)

| KPI | Metric | Threshold |
|---|---|---|
| 01 | Pre-hook latency p95 | ≤ 15 ms |
| 02 | PII redaction recall | ≥ 99.8% |
| 03 | Injection block rate | ≥ 99.5% |
| 04 | Triage routing accuracy | ≥ 97.0% |
| 05 | Triage latency p95 | ≤ 25 ms |
| 06 | False-positive refusal on benign | ≤ 1.0% |
| 07 | BM25 latency p95 | ≤ 12 ms |
| 08 | Bi-encoder+ANN latency p95 | ≤ 25 ms |
| 09 | Rerank latency p95 | ≤ 45 ms |
| 10 | Hybrid Recall@50 | ≥ 0.92 |
| 11 | Context Precision@4 | ≥ 0.88 |
| 12 | MRR@4 | ≥ 0.85 |
| 13 | nDCG@4 | ≥ 0.86 |
| 14 | Tool schema first-pass validation | ≥ 96.0% |
| 15 | Auto-repair success | ≥ 90.0% |
| 16 | Infinite-loop escape rate | 100% |
| 17 | Mean ReAct steps / task | ≤ 2.2 |
| 18 | Context drift score | ≥ 0.85 |
| 19 | Tier 3 HITL enforcement | 100% |
| 20 | RAGAS faithfulness | ≥ 0.92 |
| 21 | RAGAS answer relevancy | ≥ 0.90 |
| 22 | RAGAS context entity recall | ≥ 0.85 |
| 23 | Unanswerable abstention accuracy | ≥ 98.0% |
| 24 | Hallucination rate | ≤ 0.5% |
| 25 | TTFT p95 | ≤ 180 ms |
| 26 | Prompt token reduction (Graphify) | ≥ 45.0% |
| 27 | Semantic cache hit ratio | ≥ 35.0% |
| 28 | Cross-tenant cache isolation | 100% (0 leaks) |
| 29 | Cost / 1k queries | ≤ $0.45 |
| 30 | MCP serialization overhead p95 | ≤ 4 ms |
| 31 | E2E p50 @50K VU | ≤ 240 ms |
| 32 | E2E p95 @50K VU | ≤ 450 ms |
| 33 | E2E p99 @50K VU | ≤ 590 ms |
| 34 | 5xx rate @50K VU | ≤ 0.05% |
| 35 | Sustained throughput | ≥ 8,500 RPS |
| 36 | 1-hr soak RSS growth | ≤ 3.0% |

KPI 01–30 are measured locally by `eval/run_kpis.py`. KPI 31–36 require external load infrastructure
and are reported `NOT MEASURED` until a k6/JMeter summary is ingested (`eval/results/load_summary.json`).

## 1.6 Failure Mode & Effects Analysis

| # | Component failure | Detection | Deterministic fallback |
|--:|---|---|---|
| 1 | Redis down | cache op exception/timeout 5 ms | Bypass cache, serve uncached; never serve cross-tenant |
| 2 | Reranker timeout (>45 ms ×2) | span timer | Use RRF order, lower confidence, force extractive mode |
| 3 | MCP server crash/hang (>150 ms) | JSON-RPC timeout | Trip bulkhead; serve from cached read-replica index; restart worker |
| 4 | LLM 429 / 5xx | HTTP status | Backoff ≤1 retry, then extractive answer from top chunk |
| 5 | Postgres unavailable | conn error | Tier 1/2 reads from cache; Tier 3 refused (`-32005`), never executed |
| 6 | Embedding/ANN index missing | startup check | BM25-only retrieval, flagged degraded |
| 7 | Tool-call loop | breaker: same (tool,args_hash) fails ≥2 | `HALTED_BREAKER` + remediation prompt |
| 8 | ReAct budget exhausted | iteration/time counters | `HALTED` with partial verified facts |
| 9 | PII plugin error | plugin exception | **fail closed**: reject request |
| 10 | Injection shield error | plugin exception | **fail closed**: reject request |
| 11 | Audit chain tamper | hash verify mismatch | Alert, freeze Tier 3, readonly mode |
| 12 | Cache stampede / hot key | Redis CPU >85% | Jittered TTL, single-flight, tenant-prefixed keys |

## 1.7 Architecture Decision Records

**ADR-1 Hybrid retrieval weights.** BM25 (k1=1.25,b=0.75) and dense retrieval are fused with RRF (k=60) rather
than weighted score blending, because their score scales are incomparable and RRF needs no calibration.
Pool of 40 goes to the cross-encoder; top 4 kept; abstain below 0.42.

**ADR-2 ONNX quantization.** Production encoders/rerankers run as ONNX FP16/INT8 with micro-batching
(batch 32, 8 ms max wait) to meet <25/<45 ms. The reference build ships deterministic CPU stand-ins
(hashed n-gram embedder; lexical-overlap cross-scorer) behind the same `Encoder`/`Reranker` interface so the
pipeline, budgets and tests run without GPU/model downloads. Swapping in ONNX models is a drop-in change.

**ADR-3 Redis key isolation.** Key = `SHA256(tenant_id:user_id:norm_query)` with `tenant:{id}:` prefix and jittered TTL
300±45 s. A response is only returned if the stored entry's `tenant_id` equals the requester's (defense in depth).

**ADR-4 Bounded ReAct.** State machine `INIT→TRIAGE→REASONING→TOOL_CALL→OBSERVATION→VERIFY→DONE|HALTED`;
max 5 iterations, 120 ms/step, 450 ms total, zero recursive self-calls without state mutation. Prevents the
prototype's runaway loops by construction.

**ADR-5 Reference-vs-production honesty.** Local stand-ins must never be reported as production measurements.
Scorecard rows record `source` (`measured-local`, `external`, `not-measured`).
