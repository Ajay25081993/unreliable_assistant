"""3-stage hybrid retrieval: BM25 -> dense bi-encoder -> RRF -> cross-encoder rerank.
Encoder/Reranker are interfaces; the reference build uses deterministic CPU stand-ins (design.md ADR-2)."""
import math, re, pathlib, hashlib
import numpy as np
from collections import Counter, defaultdict
from .telemetry import span

VAULT = pathlib.Path(__file__).resolve().parent.parent / "knowledge_vault"
STOP = set("has have had can will would should many much who whom when where why summarize summary explain describe tell happened happen action actions required require longer a an the of is are was were what which who how does do to in on for and or be it its if by with as at from that this".split())

def stem(w):  # tiny suffix stemmer (Porter stand-in); also normalizes trailing 'e'
    if w.endswith("ss"): return w
    for suf in ("ing", "ed", "es", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]; break
    return w.rstrip("e") if len(w) > 4 else w

def tok(s):
    return [stem(w) for w in re.findall(r"[a-z0-9]+(?:-[0-9]+)?", s.lower()) if w not in STOP]

def load_chunks(vault=VAULT):
    chunks = []
    for p in sorted(vault.glob("*.md")):
        raw = p.read_text()
        m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
        meta = dict(l.split(": ", 1) for l in m.group(1).splitlines()) if m else {}
        body = (m.group(2) if m else raw).strip()
        cid = meta.get("id", p.stem)
        chunks.append(dict(id=cid, text=body, meta=meta, links=re.findall(r"\[\[([^\]]+)\]\]", body),
                           hash=hashlib.sha1(body.encode()).hexdigest()[:12]))
    return chunks

class BM25:
    def __init__(self, docs_tokens):
        self.dt = docs_tokens; self.N = len(docs_tokens)
        self.dl = np.array([len(d) for d in docs_tokens], float); self.avg = self.dl.mean() if self.N else 1
        self.tf = [Counter(d) for d in docs_tokens]
        self.inv = defaultdict(list)                     # inverted index
        for i, c in enumerate(self.tf):
            for t in c: self.inv[t].append(i)
    def scores(self, q, k1=1.25, b=0.75):
        s = np.zeros(self.N)
        for t in set(q):
            ids = self.inv.get(t)
            if not ids: continue
            idf = math.log(1 + (self.N - len(ids) + 0.5) / (len(ids) + 0.5))
            for i in ids:
                f = self.tf[i][t]
                s[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.dl[i] / self.avg))
        return s

class HashedNgramEncoder:
    """Deterministic dense encoder: hashed word + char-trigram features, L2-normalized (cosine-ready)."""
    DIM = 512
    def encode(self, texts):
        M = np.zeros((len(texts), self.DIM), np.float32)
        for r, t in enumerate(texts):
            ws = tok(t)
            feats = [(w, 1.0) for w in ws] + [(w[i:i + 3], 0.3) for w in ws for i in range(max(1, len(w) - 2))]
            for f, wt in feats:
                h = int(hashlib.md5(f.encode()).hexdigest()[:8], 16)
                M[r, h % self.DIM] += wt * (1 if (h >> 20) & 1 else -1) if False else wt   # unsigned: keeps cosine >= 0
        n = np.linalg.norm(M, axis=1, keepdims=True); n[n == 0] = 1
        return M / n

class OverlapCrossEncoder:
    """Query-document joint scorer in [0,1] (sigmoid). Uses IDF-weighted query-term coverage with exact-ID boost.
    Stand-in for bge-reranker-v2-m3; same interface: score(query, [docs]) -> list[float]."""
    def __init__(self, idf): self.idf = idf
    def score(self, query, docs):
        qids = set(re.findall(r"(?:sku|lot)-\d+", query.lower()))
        q = [t for t in set(tok(query)) if t not in qids]
        unk = max(self.idf.values()) if self.idf else 3.0
        wt = {t: self.idf.get(t, unk) for t in q}
        tot = sum(wt.values()); out = []
        for d in docs:
            dt = set(tok(d)); dl = d.lower()
            idw = unk * len(qids)
            hit = sum(unk for i in qids if i in dl)
            cov = (sum(wt[t] for t in q if t in dt) + hit) / (tot + idw) if (tot + idw) else 0.5
            cov *= 0.7 ** sum(1 for t in q if t not in self.idf)   # out-of-vocabulary terms => likely unanswerable
            idm = any(i in dl for i in qids)
            is_entity = bool(re.match(r"\s*(?:sku|lot)-\d+", dl)) or bool(re.search(r"(?:sku|lot)-\d+ is a", dl))
            if qids and is_entity and not idm: cov *= 0.3     # wrong entity
            x = 8.0 * (cov - 0.55)
            out.append(1 / (1 + math.exp(-x)))
        return out

def rrf(rank_lists, k=60, pool=40):
    sc = defaultdict(float)
    for rl in rank_lists:
        for r, d in enumerate(rl): sc[d] += 1.0 / (k + r + 1)
    fused = sorted(sc, key=lambda d: -sc[d])[:pool]
    return [(d, sc[d]) for d in fused]

class HybridRetriever:
    def __init__(self, chunks=None, encoder=None):
        self.chunks = chunks or load_chunks()
        self.by_id = {c["id"]: c for c in self.chunks}
        self.toks = [tok(c["text"] + " " + c["id"].replace("-", " ") + " " + c["id"]) for c in self.chunks]
        self.bm25 = BM25(self.toks)
        self.enc = encoder or HashedNgramEncoder()
        self.emb = self.enc.encode([c["text"] + " " + c["id"] for c in self.chunks])
        df = Counter(t for d in self.toks for t in set(d))
        N = len(self.chunks)
        self.idf = {t: math.log(1 + N / n) for t, n in df.items()}
        self.ce = OverlapCrossEncoder(self.idf)
        self.degraded = False

    def retrieve(self, query, k1=1.25, b=0.75, rrf_k=60, threshold=0.42, top_k=50, rerank_k=4, pool=40):
        qt = tok(query)
        with span("mcp.rag.bm25"):
            s1 = self.bm25.scores(qt, k1, b)
            o1 = [int(i) for i in np.argsort(-s1)[:top_k] if s1[i] > 0]
        with span("mcp.rag.biencoder"):
            qe = self.enc.encode([query])[0]
            s2 = self.emb @ qe
            o2 = [int(i) for i in np.argsort(-s2)[:top_k] if s2[i] > 0.05]
        with span("mcp.rag.rrf"):
            fused = rrf([o1, o2], rrf_k, pool)
        with span("mcp.rag.rerank"):
            ids = [i for i, _ in fused]
            ce = self.ce.score(query, [self.chunks[i]["text"] + " " + self.chunks[i]["id"] for i in ids]) if ids else []
            ranked = sorted(zip(ids, ce), key=lambda x: -x[1])[:rerank_k]
        top = ranked[0][1] if ranked else 0.0
        keep = [(i, s) for i, s in ranked if s > threshold]
        fmt = lambda i: self.chunks[i]["id"]
        return dict(
            stage1=[(fmt(i), round(float(s1[i]), 4)) for i in o1[:10]],
            stage2=[(fmt(i), round(float(s2[i]), 4)) for i in o2[:10]],
            fused=[(fmt(i), round(s, 5)) for i, s in fused[:10]],
            reranked=[dict(id=fmt(i), score=round(s, 4), text=self.chunks[i]["text"]) for i, s in keep],
            recall_pool=[fmt(i) for i in set(o1) | set(o2)],
            top_score=round(top, 4), abstain=not keep)
