"""MCP server `graphify-vault`: depth<=2 subgraph traversal over graph.json."""
import _common, sys
from mcp.server.mcpserver import MCPServer
from core.graphify import Graph
from core.models import SubgraphIn

mcp = MCPServer("graphify-vault")
G = Graph()

@mcp.tool()
def subgraph(anchor: str, depth: int = 2) -> dict:
    """Minimal connected subgraph around an anchor node (hard-capped at depth 2)."""
    a = SubgraphIn(anchor=anchor, depth=depth)
    s = G.subgraph(a.anchor, a.depth); s["hops"] = {k: v for k, v in s.get("hops", {}).items()}
    return s

if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run("stdio")
