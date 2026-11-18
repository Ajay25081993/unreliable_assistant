"""Real MCP JSON-RPC round trip over stdio against the hybrid-rag server."""
import asyncio, sys, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_rag_server_roundtrip():
    async def go():
        p = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_servers" / "rag_server.py")])
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                assert "hybrid_retrieve" in [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("hybrid_retrieve", {"query": "shelf life of SKU-003"})
                assert "sku-003" in res.content[0].text
                bad = await s.call_tool("hybrid_retrieve", {"query": "x", "top_k": 5000})
                assert bad.is_error
    asyncio.run(go())
