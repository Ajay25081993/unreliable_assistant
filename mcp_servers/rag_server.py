"""MCP server `hybrid-rag`: BM25 -> bi-encoder -> RRF -> cross-encoder. Run: python mcp_servers/rag_server.py [--http PORT]"""
import _common, sys, re
from mcp.server.mcpserver import MCPServer
from core.rag import HybridRetriever
from core.models import RetrieveIn

mcp = MCPServer("hybrid-rag")
R = HybridRetriever()

@mcp.tool()
def hybrid_retrieve(query: str, k1: float = 1.25, b: float = 0.75, rrf_k: int = 60, threshold: float = 0.42, top_k: int = 50) -> dict:
    """3-stage hybrid retrieval; returns stage outputs, reranked top-4 and an abstain flag."""
    a = RetrieveIn(query=query, k1=k1, b=b, rrf_k=rrf_k, threshold=threshold, top_k=top_k)
    return R.retrieve(**a.model_dump())

@mcp.resource("rag://chunks/{chunk_id}")
def chunk(chunk_id: str) -> str:
    """Raw chunk text by id."""
    if not re.fullmatch(r"[a-z0-9\-]{3,64}", chunk_id): raise ValueError("invalid chunk id")
    return R.by_id[chunk_id]["text"]

if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run("stdio")
