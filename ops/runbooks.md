# Incident runbooks (Gate 11)

Mitigations are implemented in `Agent.apply_runbook(name)` and can be triggered from the workbench (Tab 5).
Alert rules: `ops/alerts.yml`.

## 1. Latency Breach
**Trigger:** p95 end-to-end > 550 ms for 30 s. **Mitigate:** extend semantic-cache TTL (cosine >= 0.92 threshold is already
on), drop BM25/dense `top_k` 50 -> 25. **Verify:** p95 falls below 450 ms; recall KPI-10 still passes.

## 2. Hallucination Surge
**Trigger:** RAGAS faithfulness < 0.90 over 5 min. **Mitigate:** raise cross-encoder cutoff 0.42 -> 0.55 and enforce strict
extractive-quote mode. **Verify:** KPI-20/24; review audit rows with `event=veto`.

## 3. MCP Server Hang
**Trigger:** JSON-RPC timeout > 150 ms on > 1% of calls. **Mitigate:** trip the bulkhead, fail over to the cached
read-replica index, restart the MCP worker. **Verify:** `/healthz`, breaker state (Tab 5), `breaker_trips` metric.

## 4. Cache Stampede / Leak
**Trigger:** tenant key collision or Redis CPU > 85%. **Mitigate:** enforce `SHA256(tenant_id:user_id:norm_query)` with
`tenant:{id}:` prefix and jittered TTL (300 s +/- 45 s); single-flight hot keys. **Verify:** KPI-28 probe = 0 leaks.
