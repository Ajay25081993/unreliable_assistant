# The Unreliable Assistant → Enterprise Grade

Build of `plan.md` (18 gates). Contract: [design.md](design.md). Diagram: `docs/workflow_orchestration.png`.

```bash
pip install -r requirements.txt
python scripts/build_vault.py                       # vault + 250-q golden set
python -m core.graphify build ./core ./knowledge_vault --output graph.json
python -m core.manifest                             # sign MCP manifests
uvicorn api:app --port 8000                         # API
python mcp_servers/state_server.py                  # streamable-http :8081 (rag/graphify servers are stdio)
streamlit run app.py                                # 6-tab workbench
python -m pytest -q tests                           # unit + real MCP stdio round trip
python -m eval.run_kpis                             # 36-KPI scorecard -> eval/results/scorecard.json
k6 run load_tests/k6_50k_load.js                    # full 50K run (needs a real cluster); -e SMOKE=1 for local smoke
```

## What is real vs. stand-in
- Real: MCP servers (verified over stdio/HTTP), harness, bounded ReAct, breaker, HITL, hash-chained audit, PII/injection plugins, BM25/RRF, graph pruning.
- Stand-ins (design ADR-2/5): hashed-ngram encoder and lexical cross-scorer instead of ONNX BGE models; rule-based extractive generator instead of an LLM; SQLite/in-process cache instead of Postgres/Redis; RAGAS *proxies* instead of LLM-judged RAGAS.
- Not run here: JMeter plan, docker-compose/OTel/Grafana stack, 50K-VU load. KPI-31..36 are `NOT_MEASURED` until a real run is ingested.
- Datasets, golden set, red-team and triage labels are synthetic and were authored alongside the code; scores are in-sample.
