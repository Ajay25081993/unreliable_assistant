"""Signed MCP server manifests (HMAC-SHA256 over file digests + tool names). Dev key is a default; set
MCP_SIGNING_KEY in real deployments (asymmetric signing is the production recommendation)."""
import hashlib, hmac, json, os, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVERS = {"hybrid-rag": ("mcp_servers/rag_server.py", ["hybrid_retrieve"]),
           "graphify-vault": ("mcp_servers/graphify_server.py", ["subgraph"]),
           "state-store": ("mcp_servers/state_server.py", ["get_lot_status", "compute_risk", "place_lot_hold", "append_audit"])}
def _key(): return os.environ.get("MCP_SIGNING_KEY", "dev-only-key").encode()
def _sig(body): return hmac.new(_key(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
def build():
    out = {}
    for n, (f, tools) in SERVERS.items():
        body = dict(name=n, file=f, sha256=hashlib.sha256((ROOT / f).read_bytes()).hexdigest(), tools=tools)
        out[n] = dict(body, signature=_sig(body))
    return out
def verify(path=ROOT / "mcp_servers" / "manifest.json"):
    m = json.loads(pathlib.Path(path).read_text()); res = {}
    for n, e in m.items():
        body = {k: v for k, v in e.items() if k != "signature"}
        ok = hmac.compare_digest(e["signature"], _sig(body)) and hashlib.sha256((ROOT / e["file"]).read_bytes()).hexdigest() == e["sha256"]
        res[n] = ok
    return res
if __name__ == "__main__":
    (ROOT / "mcp_servers" / "manifest.json").write_text(json.dumps(build(), indent=1)); print(verify())
