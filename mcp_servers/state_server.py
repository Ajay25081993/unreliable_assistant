"""MCP server `state-store` (streamable-http on :8081 by default). Tenant comes from the `tenant_id` argument that the
host injects; Tier 3 tools only create PENDING_APPROVAL records."""
import _common, sys, uuid
from mcp.server.mcpserver import MCPServer
from core.store import Store
from core.graphify import Graph
from core.tools import StateTools, ToolError, ensure_tenant
from core.models import GetLotStatusIn, ComputeRiskIn, PlaceLotHoldIn

mcp = MCPServer("state-store")
S = Store(); T = StateTools(S, Graph())

@mcp.tool()
def get_lot_status(tenant_id: str, lot_id: str) -> dict:
    """Tier 1. Lot status for a tenant."""
    a = GetLotStatusIn(lot_id=lot_id)
    try: return T.get_lot_status(tenant_id, a.model_dump())
    except ToolError as e: raise ValueError(f"{e.code}: {e.msg}")

@mcp.tool()
def compute_risk(tenant_id: str, sku: str, qty: int) -> dict:
    """Tier 2. Deterministic risk score."""
    a = ComputeRiskIn(sku=sku, qty=qty)
    try: return T.compute_risk(tenant_id, a.model_dump())
    except ToolError as e: raise ValueError(f"{e.code}: {e.msg}")

@mcp.tool()
def place_lot_hold(tenant_id: str, lot_id: str, reason: str, idempotency_key: str) -> dict:
    """Tier 3. Never executes: records PENDING_APPROVAL and returns the approval id (idempotent)."""
    a = PlaceLotHoldIn(lot_id=lot_id, reason=reason, idempotency_key=idempotency_key)
    prior = S.idem_get(tenant_id, a.idempotency_key)
    if prior: return prior["result"]
    aid = str(uuid.uuid4()); S.create_approval(aid, tenant_id, "mcp", "place_lot_hold", a.model_dump())
    S.append_audit(tenant_id, dict(event="tier3_pending", approval_id=aid))
    r = dict(ok=True, result=dict(status="PENDING_APPROVAL", approval_id=aid)); S.idem_put(tenant_id, a.idempotency_key, r)
    return r["result"]

@mcp.tool()
def append_audit(tenant_id: str, event: dict) -> dict:
    """Append to the SHA-256 hash-chained audit log (PII scrubbed)."""
    seq, h = S.append_audit(tenant_id, event); return dict(seq=seq, hash=h)

if __name__ == "__main__":
    if "--stdio" in sys.argv: mcp.run("stdio")
    else: mcp.run("streamable-http", host="127.0.0.1", port=8081)
