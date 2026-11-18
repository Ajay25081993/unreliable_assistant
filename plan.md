<div align="center">

# The Unreliable Assistant → Enterprise Grade

### Hackathon Mid-Check · 18-Gate Production Engineering Plan

MCP · Plugins · 3-Stage Hybrid RAG · 50K VU k6/JMeter Load Testing · 36-KPI Governance

`Source: Unreliable_Assistant_MidCheck_Enterprise_Blueprint_v2`

</div>

---

## Table of Contents

1. [Mission Mandate](#1-mission-mandate)
2. [Gate Overview & Build Order](#2-gate-overview--build-order)
3. [Gate 0 — Enterprise Stack, MCP Servers & Plugins](#3-gate-0--enterprise-stack-mcp-servers--plugins)
4. [Gate 1 — `design.md` (HARD GATE)](#4-gate-1--designmd-hard-gate)
5. [Gate 2 — `workflow_orchestration.png`](#5-gate-2--workflow_orchestrationpng)
6. [Gates 3 & 8 — 3-Stage Hybrid RAG & RAGAS](#6-gates-3--8--3-stage-hybrid-rag--ragas)
7. [Gate 4 — Harness & Loop Engineering](#7-gate-4--harness--loop-engineering)
8. [Gate 5 — Context Retention & Drift Control](#8-gate-5--context-retention--drift-control)
9. [Gates 6 & 7 — Triage & Subagent Topology](#9-gates-6--7--triage--subagent-topology)
10. [Gate 9 — Enterprise Design Patterns](#10-gate-9--enterprise-design-patterns)
11. [Gates 10 & 11 — Observability & Incident Response](#11-gates-10--11--observability--incident-response)
12. [Gate 12 — 36 Stage-Level KPIs](#12-gate-12--36-stage-level-kpis)
13. [Gates 13 & 14 — Governance & Red Teaming](#13-gates-13--14--governance--red-teaming)
14. [Gates 15 & 16 — Scalability & 50K Load Testing](#14-gates-15--16--scalability--50k-load-testing)
15. [Gate 17 — Token Optimization](#15-gate-17--token-optimization-obsidian--graphify)
16. [Gate 18 — Streamlit Workbench](#16-gate-18--streamlit-developer-workbench)
17. [Deliverables Checklist](#17-deliverables-checklist)

---

## 1. Mission Mandate

Inherit **The Unreliable Assistant**, a fragile prototype with:

| Defect | Symptom |
|---|---|
| Hallucinated facts | Ungrounded answers |
| Runaway tool loops | Unbounded agent iterations |
| Cross-user cache leaks | Tenant data exposure |
| Context drift | Goal lost amid tool noise |
| Latency collapse | Fails under traffic |

> **Rule:** Before writing a single line of application code, author **`design.md`** (Gate 1) and generate **`workflow_orchestration.png`** (Gate 2).

**Target outcome:** an enterprise-grade, MCP-enabled, plugin-extensible agentic architecture that is:

- verified against **50,000 concurrent virtual users**
- delivering **200–600 ms** latency
- audited across **36 stage-level KPIs**

---

## 2. Gate Overview & Build Order

| Phase | Gate(s) | Focus | Primary Artifact |
|:-:|---|---|---|
| **Pre-build** | 1 | Design contract (HARD GATE) | `design.md` |
| **Pre-build** | 2 | Workflow diagram | `docs/workflow_orchestration.png` |
| Foundation | 0 | Stack, MCP, plugins | `mcp_config.json` |
| Core | 3, 8 | 3-stage hybrid RAG + RAGAS | `mcp_servers/rag_server.py` |
| Core | 4, 5 | Harness, loop, context | `ToolExecutionHarness` |
| Core | 6, 7 | Triage + subagents | Triage agent, 3 subagents |
| Hardening | 9 | Reliability patterns | Breaker, bulkheads, abstention, HITL |
| Hardening | 10, 11 | Observability + incidents | OTel pipeline, runbooks |
| Verification | 12 | 36 KPIs | CI/CD + Tab 6 scorecard |
| Verification | 13, 14 | Governance + red team | Audit chain, 100-prompt suite |
| Verification | 15, 16 | 50K load testing | `k6_50k_load.js`, `jmeter_50k_plan.jmx` |
| Optimization | 17 | Token reduction | `knowledge_vault/`, `graph.json` |
| Delivery | 18 | Developer workbench | `app.py` (6 tabs) |

---

## 3. Gate 0 — Enterprise Stack, MCP Servers & Plugins

Standardize **all** tool integrations through the **Model Context Protocol (MCP)** over **JSON-RPC 2.0**:

- **stdio** — local workers
- **Streamable HTTP / SSE** — distributed microservices
- Modular **lifecycle plugins** for deterministic interception

### 3.1 Stack Matrix

| Layer / Capability | Selected Tool / Protocol | MCP Server / Plugin Binding | Architectural Justification |
|---|---|---|---|
| Agent Runtime & API | Python 3.11 + FastAPI + Uvicorn (async) | MCP Host Client (`mcp.ClientSession`) | Non-blocking async I/O sustaining 50,000 concurrent connections |
| Sparse & Dense Retrieval | Rank-BM25 + FAISS-IVF-PQ + BGE-Large | MCP Server `mcp-hybrid-rag` (Resources + Tools) | Exposes `rag://chunks/{id}` resources and `hybrid_retrieve` tool over JSON-RPC |
| Precision Reranker | ONNX Runtime (`bge-reranker-v2-m3` FP16) | In-process Triton / ONNX microservice | Sub-35 ms cross-encoder inference via INT8/FP16 quantization and dynamic batching |
| State & Session Cache | PostgreSQL (pgvector) + Redis Cluster | MCP Server `mcp-state-store` | ACID long-term audit persistence + sub-millisecond tenant-isolated Redis caching |
| Token Compression | Obsidian Vault + Graphify AST/Link Engine | MCP Server `mcp-graphify-vault` | Traverses 1-hop/2-hop `[[wikilinks]]` and code dependency graphs to cut tokens 55% |
| Lifecycle Plugins | Pluggy / Entry-Point Hook Registry | Pre-Hook, Post-Hook, PII, OTel, HITL Plugins | Hot-swappable deterministic interceptors decoupled from core LLM reasoning |

### 3.2 MCP Configuration & Plugin Registration (`mcp_config.json`)

```json
{
  "mcpServers": {
    "hybrid-rag":     {"command": "uv", "args": ["run", "mcp_servers/rag_server.py"], "transport": "stdio", "timeout_ms": 150},
    "state-store":    {"url": "http://localhost:8081/mcp", "transport": "streamable-http", "pool_size": 200},
    "graphify-vault": {"command": "uv", "args": ["run", "mcp_servers/graphify_server.py"], "transport": "stdio"}
  },
  "plugins": [
    {"name": "pii_redactor",      "stage": "PRE_EXECUTION",  "priority": 10, "fail_closed": true},
    {"name": "injection_shield",  "stage": "PRE_EXECUTION",  "priority": 20, "fail_closed": true},
    {"name": "circuit_breaker",   "stage": "TOOL_DISPATCH",  "priority": 10, "max_identical_calls": 2},
    {"name": "ragas_auditor",     "stage": "POST_EXECUTION", "priority": 10, "min_faithfulness": 0.92},
    {"name": "otel_exporter",     "stage": "ALL_SPANS",      "endpoint": "http://localhost:4317"}
  ]
}
```

---

## 4. Gate 1 — `design.md` (HARD GATE)

> ⛔ **No implementation code is permitted** until `design.md` is authored, reviewed and **locked in the repository root**.

Seven mandatory sections:

| § | Section | Required Content |
|:-:|---|---|
| 1.1 | **System Charter & Hard Boundaries** | Explicit scope; non-goals (**zero autonomous clinical / financial / legal execution without HITL**); user personas |
| 1.2 | **MCP Tool & Data Contracts** | Exact JSON-RPC / MCP tool schemas; Pydantic v2 input/output models; regex constraints; error codes |
| 1.3 | **Distributed Topology & Data Flow** | Complete architecture diagram: API Gateway, Redis cache, MCP servers, ONNX reranker, Postgres |
| 1.4 | **End-to-End Latency Budget (200–600 ms)** | Breakdown proving **p95 ≤ 450 ms** and **p99 ≤ 600 ms** at 50,000 concurrent VUs |
| 1.5 | **KPI & SLA Gate Matrix** | All **36** stage-level KPIs with hard pass/fail thresholds |
| 1.6 | **Failure Mode & Effects Analysis (FMEA)** | **12-row** table mapping every component failure (Redis down, reranker timeout, MCP crash, LLM 429, …) to deterministic fallbacks |
| 1.7 | **Architecture Decision Records (ADRs)** | ADRs for: hybrid retrieval weights, ONNX quantization, Redis key isolation, bounded ReAct loops |

---

## 5. Gate 2 — `workflow_orchestration.png`

Generate a **high-DPI (300 DPI)** diagram at `docs/workflow_orchestration.png` using Graphviz (`diagrams` / `graphviz` Python library) **before** writing application logic, and **render it in Streamlit Tab 1**.

The diagram must explicitly show four zones:

| Zone | Required Flow |
|---|---|
| **Ingress & Security Layer** | Ingress → Pre-Execution Plugins (**PII Redactor → Injection Classifier**) → **Redis Semantic Cache Check** |
| **Triage & Subagent Routing** | Triage Agent (`temperature=0.0`, **< 30 ms**) branching into **Direct Response**, **Retrieval Subagent**, **Execution Subagent**, **Audit Subagent** |
| **MCP & 3-Stage RAG Pipeline** | MCP JSON-RPC calls to `mcp-hybrid-rag` (**BM25 + Bi-Encoder → RRF → Cross-Encoder**), `mcp-graphify-vault`, `mcp-state-store` |
| **Control Plane & Egress** | Bounded ReAct Loop (`max_steps=5`) → Circuit Breaker → **Tier 3 HITL Gate** → Post-Execution **RAGAS & OTel Export** |

---

## 6. Gates 3 & 8 — 3-Stage Hybrid RAG & RAGAS

Implement the full retrieval stack in **`mcp_servers/rag_server.py`** with strict per-stage latency budgets.

| Stage | Engine | Hyperparameters & Optimization | Budget | Output |
|---|---|---|:-:|---|
| **1 · Sparse Lexical** | BM25Okapi + PorterStemmer + Inverted Index | `k1=1.25`, `b=0.75`, `top_k=50`, pre-tokenized | **< 12 ms** | 50 lexical candidates + raw BM25 scores |
| **2 · Dense Semantic** | Bi-Encoder (`bge-large-en-v1.5` ONNX) + FAISS HNSW | chunk=512 tok, overlap=64 tok, `efSearch=64`, `top_k=50` | **< 25 ms** | 50 semantic candidates + cosine similarity |
| **Rank Fusion** | Reciprocal Rank Fusion (RRF) | smoothing `k=60`, dedup by `chunk_hash`, pool=40 | **< 3 ms** | Top 40 unified, scale-normalized candidates |
| **3 · Cross-Encoder** | `bge-reranker-v2-m3` (ONNX FP16, batch=32) | `rerank_k=4`, sigmoid threshold **> 0.42**, `max_len=512` | **< 45 ms** | Top 4 verified context chunks + confidence |

### 6.1 RAGAS Continuous Evaluation (Step 8)

Automated RAGAS scoring over a **250-question Golden Dataset**, stratified:

| Query Type | Share | ≈ Count |
|---|:-:|:-:|
| Factoid | 25% | 62–63 |
| Complex Reasoning | 25% | 62–63 |
| Multi-Hop | 20% | 50 |
| Summary | 15% | 37–38 |
| Negative / Unanswerable | 15% | 37–38 |

**Release gates**

| Metric | Threshold |
|---|:-:|
| Faithfulness | ≥ 0.92 |
| Answer Relevancy | ≥ 0.90 |
| Context Precision | ≥ 0.88 |
| Context Recall | ≥ 0.85 |
| Context Entity Recall | ≥ 0.85 |

---

## 7. Gate 4 — Harness & Loop Engineering

### 7.1 Tool Routing Harness — `ToolExecutionHarness`

Every MCP tool call passes through four ordered steps:

1. **Pydantic v2 strict validation**
2. **Deterministic regex auto-repair** — e.g. `SKU99` → `SKU-099`, `lot 44` → `LOT-00044`
3. **RBAC permission-tier check** — `TIER_1_READ`, `TIER_2_COMPUTE`, `TIER_3_MUTATE`
4. **`UUIDv4` idempotency enforcement**

### 7.2 Bounded Loop Engineering

Deterministic state machine:

```
INIT → TRIAGE → REASONING → TOOL_CALL → OBSERVATION → VERIFY → DONE / HALTED
```

| Hard Limit | Value |
|---|:-:|
| `max_iterations` | 5 |
| `per_step_timeout` | 120 ms |
| `total_loop_budget` | 450 ms |
| Recursive self-calls without state mutation | **0** |

---

## 8. Gate 5 — Context Retention & Drift Control

ReAct framework safeguards:

1. **Immutable Goal Anchoring** — prepend an immutable `GoalAnchorBlock` (original user intent, normalized entity IDs, active policy constraints) at **index 0** of every ReAct prompt, so tool-output noise cannot cause goal drift.
2. **Observation Compression** — pass every tool response through an extractive compressor that strips boilerplate JSON keys and truncates observations to **≤ 180 tokens**.
3. **Sliding Window + Dual Memory** — keep only the **last 3 ReAct turns** in hot Redis/RAM; persist verified facts in PostgreSQL (`session_facts` table) keyed by `session_id`.

---

## 9. Gates 6 & 7 — Triage & Subagent Topology

### 9.1 Triage Agent (Step 6)

- Ultra-fast distilled classifier: **≤ 25 ms** inference, `temperature = 0.0`
- Tags each query with **`(intent, domain, risk_tier, complexity)`**
- Routes directly to: `DIRECT_ANSWER` · `HYBRID_RAG` · `TRANSACTION_EXEC` · `REJECT_OOS`

### 9.2 Subagents

| Subagent | Core Responsibility | Bound MCP Servers | Isolation & Guardrail Rule |
|---|---|---|---|
| **Retrieval** | Query rewriting, 3-stage hybrid RAG, citation grounding | `mcp-hybrid-rag`, `mcp-graphify-vault` | Read-only; cannot invoke state-mutating tools |
| **Execution** | Schema validation, form submission, workflow transitions | `mcp-state-store` | No RAG / external web access; Tier 3 triggers HITL freeze |
| **Verification** | Claim-to-chunk NLI check, RAGAS triad audit, OTel logging | `mcp-state-store` (audit log only) | Independent model context; **vetoes ungrounded drafts** |

---

## 10. Gate 9 — Enterprise Design Patterns

| # | Pattern | Specification |
|:-:|---|---|
| 1 | **Idempotent Circuit Breaker** | Trips when `(tool_name, canonical_args_hash)` fails **≥ 2 times**; transitions to `HALTED_BREAKER`; returns a structured user remediation prompt |
| 2 | **Bulkhead Isolation** | Isolated thread/process pools per MCP server: `rag_pool=64`, `db_pool=128`, `llm_pool=256` — a slow reranker never starves health checks or triage |
| 3 | **Deterministic Abstention Short-Circuit** | Cross-Encoder top score **< 0.42** → bypass LLM, emit deterministic abstention in **< 90 ms** (zero hallucination risk, ~80% token savings) |
| 4 | **Asynchronous HITL Gate** | Tier 3 mutations write a `PENDING_APPROVAL` record to Postgres and **pause execution** until an authorized reviewer approves via UI/API |

---

## 11. Gates 10 & 11 — Observability & Incident Response

### 11.1 Full-Trace Instrumentation (Step 10)

Propagate **W3C TraceContext (`traceparent`)** on every hop, exporting
**OpenTelemetry Collector → Prometheus + Tempo + Grafana**.

Required spans:

`hook.pre` · `cache.lookup` · `triage.classify` · `mcp.rag.bm25` · `mcp.rag.biencoder` · `mcp.rag.rrf` · `mcp.rag.rerank` · `agent.react.step` · `llm.generate` · `hook.post.ragas`

### 11.2 Incident Runbooks

| Scenario | Automated Alert Trigger | Immediate Mitigation & Runbook |
|---|---|---|
| **Latency Breach (> 600 ms)** | p95 e2e latency > 550 ms for 30 s | Enable aggressive semantic cache (cosine ≥ 0.92); drop BM25/Dense `top_k` 50 → 25 |
| **Hallucination Surge** | RAGAS Faithfulness < 0.90 (5 min window) | Raise Cross-Encoder cutoff 0.42 → 0.55; enforce strict extractive-quote mode |
| **MCP Server Hang** | JSON-RPC timeout > 150 ms (> 1% rate) | Trip MCP bulkhead circuit; fail over to cached read-replica index; restart MCP worker |
| **Cache Stampede / Leak** | Tenant key collision **or** Redis CPU > 85% | Enforce `SHA256(tenant_id:user_id:norm_query)` prefix + jittered TTL (**300 s ± 45 s**) |

---

## 12. Gate 12 — 36 Stage-Level KPIs

All 36 KPIs across 6 stages must be **continuously measured and passed in CI/CD and Streamlit Tab 6** (scorecard).

### Stage 1 — Ingress & Triage

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-01 | Pre-Hook Guardrail Latency (p95) | ≤ 15 ms | OTel span `hook.pre` |
| KPI-02 | PII Redaction Recall (Email/Phone/SSN/Obfuscated) | ≥ 99.8% | Presidio / Regex audit |
| KPI-03 | Prompt Injection Block Rate (Direct + Indirect) | ≥ 99.5% | Red Team benchmark |
| KPI-04 | Triage Routing Accuracy | ≥ 97.0% | Labeled intent set |
| KPI-05 | Triage Classification Latency (p95) | ≤ 25 ms | OTel span `triage` |
| KPI-06 | False-Positive Refusal Rate on Benign Queries | ≤ 1.0% | Benign control set |

### Stage 2 — Hybrid Retrieval

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-07 | Stage 1 BM25 Latency (p95) | ≤ 12 ms | OTel span `rag.bm25` |
| KPI-08 | Stage 2 Bi-Encoder + FAISS Latency (p95) | ≤ 25 ms | OTel span `rag.dense` |
| KPI-09 | Stage 3 Cross-Encoder Rerank Latency (p95) | ≤ 45 ms | OTel span `rag.rerank` |
| KPI-10 | Hybrid Context Recall@50 | ≥ 0.92 | RAGAS / IR eval |
| KPI-11 | Post-Rerank Context Precision@4 | ≥ 0.88 | RAGAS evaluator |
| KPI-12 | Mean Reciprocal Rank (MRR@4) | ≥ 0.85 | IR benchmark script |
| KPI-13 | nDCG@4 | ≥ 0.86 | IR benchmark script |

### Stage 3 — Agent & Loop

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-14 | Tool Schema First-Pass Validation Rate | ≥ 96.0% | Pydantic telemetry |
| KPI-15 | Auto-Repair Success Rate on Malformed Args | ≥ 90.0% | Harness metrics |
| KPI-16 | Infinite Loop Escape Rate (Circuit Breaker) | 100.0% | Chaos loop suite |
| KPI-17 | Mean ReAct Steps per Completed Task | ≤ 2.2 steps | OTel trace count |
| KPI-18 | Context Drift Score (Goal Cosine Alignment) | ≥ 0.85 | Embedding drift audit |
| KPI-19 | Tier 3 HITL Gate Enforcement Rate | 100.0% | Audit-log integrity |

### Stage 4 — Generation & RAGAS

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-20 | RAGAS Faithfulness | ≥ 0.92 | Post-Hook RAGAS |
| KPI-21 | RAGAS Answer Relevancy | ≥ 0.90 | Post-Hook RAGAS |
| KPI-22 | RAGAS Context Entity Recall | ≥ 0.85 | Post-Hook RAGAS |
| KPI-23 | Unanswerable Query Abstention Accuracy | ≥ 98.0% | Negative test set |
| KPI-24 | Unsupported Claim / Hallucination Rate | ≤ 0.5% | NLI claim verifier |
| KPI-25 | Time-To-First-Token (TTFT p95) | ≤ 180 ms | FastAPI stream metric |

### Stage 5 — Token & Graphify

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-26 | Prompt Token Reduction via Graphify + Obsidian | ≥ 45.0% | Token counter hook |
| KPI-27 | Semantic Cache Hit Ratio (steady state) | ≥ 35.0% | Redis hit/miss metric |
| KPI-28 | Cross-Tenant Cache Isolation Integrity | 100.0% (0 leaks) | Concurrency probe |
| KPI-29 | Cost per 1,000 Enterprise Queries | ≤ $0.45 | OTel token-cost calc |
| KPI-30 | MCP JSON-RPC Serialization Overhead (p95) | ≤ 4 ms | MCP client timer |

### Stage 6 — Scale & Load (50K)

| KPI | Metric | Threshold | Tool |
|---|---|:-:|---|
| KPI-31 | End-to-End Latency (p50) @ 50K VUs | ≤ 240 ms | k6 + JMeter summary |
| KPI-32 | End-to-End Latency (p95) @ 50K VUs | ≤ 450 ms | k6 + JMeter summary |
| KPI-33 | End-to-End Latency (p99) @ 50K VUs | ≤ 590 ms (< 600 ms) | k6 + JMeter summary |
| KPI-34 | HTTP 5xx Error Rate @ 50K VUs | ≤ 0.05% | k6 / JMeter threshold |
| KPI-35 | Sustained Throughput | ≥ 8,500 RPS | k6 / JMeter counter |
| KPI-36 | Memory Leak / RSS Growth over 1-hr soak | ≤ 3.0% delta | Prometheus Node Exporter |

---

## 13. Gates 13 & 14 — Governance & Red Teaming

### 13.1 Governance & Security-in-Build (Step 13)

- **SHA-256 hash-chained immutable audit logs** (`prev_hash + event_payload`)
- **Strict tenant isolation** — `tenant_id` in every SQLite/Postgres query and every Redis key
- **Zero raw PII persistence**
- **Signed MCP server manifests**

### 13.2 Red-Team Suite (Step 14)

Automated **100-prompt** adversarial suite covering:

| # | Attack Class |
|:-:|---|
| 1 | Direct jailbreaks & system-prompt extraction |
| 2 | Indirect prompt injection hidden inside retrieved Obsidian/PDF chunks |
| 3 | Multi-turn crescendo social engineering |
| 4 | Unicode homoglyph & `name[at]domain` PII smuggling |
| 5 | Tool-argument overflow / SQLi fuzzing |

---

## 14. Gates 15 & 16 — Scalability & 50K Load Testing

**Target:** 200–600 ms end-to-end at **50,000 concurrent VUs**.

**Deployment topology**

- Horizontally scaled **stateless FastAPI workers** behind **NGINX / Envoy**
- **Redis Cluster** for semantic caching
- **ONNX-quantized** Bi-Encoder / Cross-Encoder workers with dynamic micro-batching (`batch=32`, `max_wait=8 ms`)
- **vLLM / TensorRT-LLM** with continuous batching and prefix caching

### 14.1 Grafana k6 — `load_tests/k6_50k_load.js`

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 10000 },  // Ramp to 10K VUs
    { duration: '3m', target: 50000 },  // Ramp to 50K concurrent VUs
    { duration: '5m', target: 50000 },  // Sustain 50K VUs peak
    { duration: '2m', target: 0 },      // Graceful ramp-down
  ],
  thresholds: {
    'http_req_duration': ['p(50)<250', 'p(95)<450', 'p(99)<600'], // Hard 200-600ms SLA
    'http_req_failed': ['rate<0.0005'],                           // < 0.05% error rate
    'checks': ['rate>0.999'],
  },
};

export default function () {
  const vuId = __VU;
  const payload = JSON.stringify({
    tenant_id: `tenant_${vuId % 50}`,
    user_id: `user_${vuId}`,
    query: 'Verify batch LOT-88392 safety protocol and return triage status'
  });
  const params = { headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': `tenant_${vuId % 50}` } };
  const res = http.post('http://localhost:8000/api/v1/agent/query', payload, params);
  check(res, {
    'status is 200': (r) => r.status === 200,
    'latency within 200-600ms SLA': (r) => r.timings.duration <= 600,
    'no cross-tenant leak': (r) => r.json('tenant_id') === `tenant_${vuId % 50}`,
  });
  sleep(1);
}
```

### 14.2 Apache JMeter — `load_tests/jmeter_50k_plan.jmx`

| Item | Specification |
|---|---|
| Topology | **1 Controller + 10 distributed worker injectors** |
| Load | 5,000 threads/worker = **50,000 VUs** |
| Ramp-up | 180 s |
| Loop count | Infinite, over a **600 s** duration |
| Mode | Non-GUI CLI |
| Assertions | Duration Assertion (600 ms); JSONPath Assertion (`$.faithfulness >= 0.92`) |
| Listeners / timers | Constant Throughput Timer; Backend Listener (InfluxDB/Prometheus) |
| Validation scope | JDBC/SQLite persistence **and** HTTP endpoints simultaneously |

```bash
jmeter -n -t jmeter_50k_plan.jmx -R worker1..worker10 -l results_50k.jtl -e -o dashboard_50k/
```

---

## 15. Gate 17 — Token Optimization (Obsidian & Graphify)

**Goal:** cut LLM input tokens by **45–65%** and lower TTFT.

1. **Atomic Obsidian Knowledge Vault** — convert all enterprise policies, SOPs and product catalogs into atomic Markdown notes in `knowledge_vault/`. Each note carries strict YAML frontmatter (`id`, `domain`, `risk_tier`, `version`) and explicit `[[wikilinks]]` to related rules.
2. **Graphify Topology Subgraph Pruning**
   - Build: `graphify build ./src ./knowledge_vault --output graph.json` (graph of Python modules, Pydantic schemas, Obsidian policy nodes)
   - Runtime: `mcp-graphify-vault` resolves the exact query anchor node and traverses **at most `depth=2`** edges
   - Inject only the minimal connected subgraph: **~320 tokens vs. ~1,400 tokens** for a raw chunk dump

---

## 16. Gate 18 — Streamlit Developer Workbench

Interactive control plane in **`app.py`** with six engineering tabs for live mid-check verification.

| Tab | Interactive Controls, Live Inspectors & Verification Widgets |
|---|---|
| **1 · Architecture & Workflow** | Renders `design.md` contract checklist; displays `workflow_orchestration.png`; live Triage routing console; ReAct step-by-step trace accordion; Tier 3 HITL Approve/Reject queue |
| **2 · 3-Stage Hybrid RAG Lab** | Side-by-side table: Stage 1 BM25 scores, Stage 2 Bi-Encoder cosine scores, RRF fused ranks, Stage 3 Cross-Encoder scores; sliders for `k1`, `b`, `RRF k`, `threshold` |
| **3 · MCP & Plugin Inspector** | Live status of `hybrid-rag`, `state-store`, `graphify-vault`; JSON-RPC payload inspector; hot-toggle switches for Pre/Post execution plugins |
| **4 · Memory & Graphify Tokens** | Interactive Graphify subgraph visualizer; Obsidian atomic-note viewer; live SQLite/Redis session-state browser; real-time Token Savings & Cost Reduction gauge |
| **5 · Red Team & Circuit Breaker** | One-click adversarial runner (direct/indirect injections, obfuscated PII, infinite SKU retry loop, cross-tenant cache probe); live Circuit Breaker trip/reset controls |
| **6 · 36-KPI Scorecard & 50K Load** | Live OTel span waterfall; RAGAS Triad gauges; k6/JMeter 50,000-VU latency histogram (p50/p95/p99 vs. 200–600 ms SLA); automated 36-KPI Pass/Fail release scorecard |

---

## 17. Deliverables Checklist

- [ ] `design.md` — 7 sections, locked in repo root *(Gate 1, before any code)*
- [ ] `docs/workflow_orchestration.png` — 300 DPI, 4 zones, rendered in Tab 1 *(Gate 2, before any code)*
- [ ] `mcp_config.json` — 3 MCP servers + 5 plugins
- [ ] `mcp_servers/rag_server.py` — BM25 → Dense → RRF → Cross-Encoder
- [ ] `mcp_servers/graphify_server.py` and state-store MCP server
- [ ] `ToolExecutionHarness` — validation, auto-repair, RBAC tiers, idempotency
- [ ] Bounded ReAct state machine (5 iterations / 120 ms step / 450 ms budget)
- [ ] `GoalAnchorBlock`, observation compressor, dual memory (`session_facts`)
- [ ] Triage agent + Retrieval / Execution / Verification subagents
- [ ] Circuit breaker, bulkheads, abstention short-circuit, async HITL gate
- [ ] OTel instrumentation (10 spans) + Prometheus / Tempo / Grafana
- [ ] 4 incident runbooks with automated alerts
- [ ] 250-question RAGAS golden dataset + 5 release gates
- [ ] 36-KPI CI/CD gate + scorecard
- [ ] Hash-chained audit log, tenant isolation, zero raw PII, signed MCP manifests
- [ ] 100-prompt red-team suite (5 classes)
- [ ] `load_tests/k6_50k_load.js` and `load_tests/jmeter_50k_plan.jmx`
- [ ] `knowledge_vault/` atomic notes + `graph.json`
- [ ] `app.py` — 6-tab Streamlit workbench
