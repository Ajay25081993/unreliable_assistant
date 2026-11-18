"""FastAPI service. Run: uvicorn api:app --workers N"""
import os, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ValidationError
from core.agent import Agent
from core.models import QueryRequest
from core import telemetry

AGENT: Agent | None = None

@asynccontextmanager
async def lifespan(app):
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("THREADS", 256))
    global AGENT
    AGENT = Agent(); yield

app = FastAPI(title="Unreliable Assistant (enterprise)", lifespan=lifespan)

@app.get("/healthz")
def healthz(): return dict(status="ok", audit_chain_ok=AGENT.store.verify_audit()[0])

@app.get("/metrics", response_class=PlainTextResponse)
def metrics(): return telemetry.prometheus_text() + f"cache_hit_ratio {AGENT.cache.ratio()}\n"

@app.post("/api/v1/agent/query")
def query(body: dict, request: Request, x_tenant_id: str | None = Header(default=None)):
    try: req = QueryRequest.model_validate(body)
    except ValidationError as e: raise HTTPException(422, e.errors(include_url=False, include_input=False, include_context=False))
    if x_tenant_id and x_tenant_id != req.tenant_id:          # header/body mismatch => reject (tenant spoofing)
        raise HTTPException(403, "tenant mismatch")
    return AGENT.handle(req, traceparent=request.headers.get("traceparent"))

class Decision(BaseModel):
    tenant_id: str; reviewer: str; approve: bool

@app.get("/api/v1/hitl/pending")
def pending(tenant_id: str): return [dict(id=r[0], requester=r[2], tool=r[3], args=r[4], status=r[5]) for r in AGENT.store.pending(tenant_id)]

@app.post("/api/v1/hitl/{approval_id}/decide")
def decide(approval_id: str, d: Decision):
    r = AGENT.store.decide(approval_id, d.tenant_id, d.reviewer, d.approve)
    if r is None: raise HTTPException(404, "no pending approval")
    if r == "SELF_APPROVAL_DENIED": raise HTTPException(403, r)
    return dict(status=r)
