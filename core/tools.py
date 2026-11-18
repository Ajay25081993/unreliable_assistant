"""Tool implementations shared by the in-process gateway and the MCP servers (single source of truth)."""
import re, math
from .graphify import Graph
from .store import Store

DEMO_LOTS = {"LOT-88392": "RELEASED", "LOT-00044": "RELEASED", "LOT-00123": "QUARANTINE", "LOT-00007": "RELEASED"}
_RISK = {"A": 0.9, "B": 0.5, "C": 0.2}

class ToolError(Exception):
    def __init__(self, code, msg): self.code, self.msg = code, msg

def ensure_tenant(store: Store, tenant):
    for lot, st in DEMO_LOTS.items():
        if store.lot_status(tenant, lot) == "UNKNOWN": store.set_lot(tenant, lot, st)

class StateTools:
    def __init__(self, store: Store, graph: Graph):
        self.store, self.graph = store, graph
    def get_lot_status(self, tenant, a):
        ensure_tenant(self.store, tenant)
        st = self.store.lot_status(tenant, a["lot_id"])
        if st == "UNKNOWN": raise ToolError(-32602, f"{a['lot_id']} not found")
        return dict(lot_id=a["lot_id"], status=st, tenant_id=tenant)
    def compute_risk(self, tenant, a):
        n = self.graph.nodes.get(a["sku"].lower())
        if not n: raise ToolError(-32602, f"{a['sku']} not found")
        cls = re.search(r"class (\w)", n["text"])[1]
        return dict(sku=a["sku"], risk_class=cls, risk_score=round(_RISK[cls] * min(1, math.log10(a["qty"] + 1) / 5), 3))
    def place_lot_hold(self, tenant, a):          # Tier 3: never mutates; creates PENDING_APPROVAL
        raise ToolError(-32005, "Tier 3 requires approval")   # replaced by harness HITL path (kept as safety net)
