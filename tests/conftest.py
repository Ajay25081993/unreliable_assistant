import sys, pathlib, tempfile, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.agent import Agent
from core.store import Store
from core.models import QueryRequest

@pytest.fixture
def agent(): return Agent(store=Store(tempfile.mktemp(suffix=".db")))
def Q(q, t="t1", u="u1", **k): return QueryRequest(tenant_id=t, user_id=u, query=q, **k)
