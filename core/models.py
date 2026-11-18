"""Pydantic v2 strict contracts (design.md §1.2)."""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

UUID4 = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
LOT = r"^LOT-\d{5}$"
SKU = r"^SKU-\d{3}$"

class Tier(str, Enum):
    T1 = "TIER_1_READ"; T2 = "TIER_2_COMPUTE"; T3 = "TIER_3_MUTATE"

class Route(str, Enum):
    DIRECT = "DIRECT_ANSWER"; RAG = "HYBRID_RAG"; TX = "TRANSACTION_EXEC"; OOS = "REJECT_OOS"

class QueryRequest(Strict):
    tenant_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    user_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    role: Literal["operator", "reviewer", "admin"] = "operator"

class GetLotStatusIn(Strict):
    lot_id: str = Field(pattern=LOT)
class ComputeRiskIn(Strict):
    sku: str = Field(pattern=SKU)
    qty: int = Field(ge=1, le=100000)
class PlaceLotHoldIn(Strict):
    lot_id: str = Field(pattern=LOT)
    reason: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(pattern=UUID4)
class SubgraphIn(Strict):
    anchor: str = Field(min_length=1, max_length=100)
    depth: int = Field(ge=0, le=2, default=2)
class RetrieveIn(Strict):
    query: str = Field(min_length=1, max_length=1000)
    k1: float = Field(default=1.25, ge=0.5, le=3)
    b: float = Field(default=0.75, ge=0, le=1)
    rrf_k: int = Field(default=60, ge=1, le=200)
    threshold: float = Field(default=0.42, ge=0, le=1)
    top_k: int = Field(default=50, ge=1, le=100)

TOOLS = {  # name -> (input model, tier, server)
    "get_lot_status": (GetLotStatusIn, Tier.T1, "state-store"),
    "compute_risk": (ComputeRiskIn, Tier.T2, "state-store"),
    "place_lot_hold": (PlaceLotHoldIn, Tier.T3, "state-store"),
    "subgraph": (SubgraphIn, Tier.T1, "graphify-vault"),
    "hybrid_retrieve": (RetrieveIn, Tier.T1, "hybrid-rag"),
}
ROLE_MAX_TIER = {"operator": 2, "reviewer": 2, "admin": 3}   # operators may *request* T3 via HITL path only
ERR = dict(INVALID=-32602, RBAC=-32001, DUP=-32002, BREAKER=-32003, TIMEOUT=-32004, PENDING=-32005, TENANT=-32006)
