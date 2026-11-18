"""Graphify: builds graph.json (python modules, pydantic schemas, Obsidian notes) and serves depth<=2 subgraphs.
CLI: python -m core.graphify build ./core ./knowledge_vault --output graph.json"""
import ast, json, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent

def ntok(s: str) -> int:           # coarse token estimate (~1.3 tok/word); same counter used on both sides of KPI-26
    return int(len(re.findall(r"\S+", s)) * 1.3) + 1

def build(py_dirs, vault_dir, output):
    nodes, edges = {}, []
    for d in py_dirs:
        for p in sorted(pathlib.Path(d).resolve().rglob("*.py")):
            mid = f"mod:{p.stem}"
            nodes[mid] = dict(id=mid, kind="module", path=str(p.relative_to(ROOT)))
            try: tree = ast.parse(p.read_text())
            except SyntaxError: continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef) and any(getattr(b, "id", "") in ("Strict", "BaseModel") for b in n.bases):
                    cid = f"schema:{n.name}"; nodes[cid] = dict(id=cid, kind="schema"); edges.append((mid, cid, "defines"))
                if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module:
                    edges.append((mid, f"mod:{n.module}", "imports"))
    for p in sorted(pathlib.Path(vault_dir).resolve().glob("*.md")):
        raw = p.read_text(); m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
        meta = dict(l.split(": ", 1) for l in m.group(1).splitlines()) if m else {}
        body = m.group(2) if m else raw
        nid = meta.get("id", p.stem)
        nodes[nid] = dict(id=nid, kind="note", domain=meta.get("domain"), risk_tier=meta.get("risk_tier"),
                          text=_compact(nid, body))
        for l in re.findall(r"\[\[([^\]]+)\]\]", body): edges.append((nid, l, "links"))
    edges = [e for e in edges if e[1] in nodes]
    g = dict(nodes=list(nodes.values()), edges=[list(e) for e in edges])
    pathlib.Path(output).write_text(json.dumps(g, indent=1))
    return g

def _compact(nid, body):
    body = re.sub(r"^# .*\n", "", body).strip()
    m = re.match(r"(SKU-\d+) is an? (.+?)\. Its shelf life is (\d+) days\. Its storage temperature is (\S+?)\. Its risk class is (\w)\.", body)
    if m: return f"{m[1]}|{m[2]}|shelf {m[3]}d|{m[4]}|class {m[5]}"
    body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
    return " ".join(re.split(r"(?<=\.)\s+", body)[:3])

class Graph:
    def __init__(self, path=ROOT / "graph.json"):
        g = json.loads(pathlib.Path(path).read_text())
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.adj = {}
        for a, b, k in g["edges"]:
            self.adj.setdefault(a, set()).add(b)   # directed: follow outgoing links only (avoids hub fan-out)
    def resolve_anchor(self, query):
        m = re.search(r"(?i)\b(sku-\d{3}|lot-\d{5})\b", query)
        if m and m[1].lower() in self.nodes: return m[1].lower()
        q = query.lower()
        for nid, n in self.nodes.items():
            if n["kind"] == "note" and nid.startswith("policy-") and nid[7:].replace("-", " ") in q: return nid
        return None
    def subgraph(self, anchor, depth=2):
        depth = max(0, min(2, depth))                       # hard cap
        if anchor not in self.nodes: return dict(anchor=anchor, nodes=[], edges=[], text="", tokens=0)
        seen, frontier = {anchor: 0}, [anchor]
        for d in range(1, depth + 1):
            nxt = []
            for u in frontier:
                for v in sorted(self.adj.get(u, ())):
                    if v not in seen and len(seen) < 8 and self.nodes[v]["kind"] == "note": seen[v] = d; nxt.append(v)
            frontier = nxt
        text = "\n".join(self.nodes[n]["text"] for n in seen if "text" in self.nodes[n])
        es = [(a, b) for a in seen for b in self.adj.get(a, ()) if b in seen]
        return dict(anchor=anchor, nodes=sorted(seen, key=seen.get), edges=es, text=text, tokens=ntok(text), hops=seen)

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "build":
        out = a[a.index("--output") + 1] if "--output" in a else "graph.json"
        dirs = [x for x in a[1:] if not x.startswith("--") and x != out]
        g = build(dirs[:-1] or ["core"], dirs[-1] if dirs else "knowledge_vault", out)
        print(f"graph: {len(g['nodes'])} nodes, {len(g['edges'])} edges -> {out}")
