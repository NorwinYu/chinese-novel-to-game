"""Ranking what a character remembers, for one turn of conversation.

The candidates are already filtered to what the character knows at this point of the story (the ledger
decides that); this module only orders them by relevance to what was just said. Two scorers:

  bm25   jieba words with the book's user dictionary; always available, no download
  embed  a local sentence-embedding model through fastembed, when N2G_EMBED_MODEL names one
         (e.g. BAAI/bge-small-zh-v1.5). Vectors for the whole ledger are computed once and cached in
         build/embed/. N2G_EMBED_CACHE is where the model files live (default .cache/embed).

With both, the score is the mean of the two (each scaled to 0..1) plus a small bonus for recent
chapters, so the character leans on what happened lately when nothing else decides."""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path

from ..config import ROOT

_STOP = set("的了是在我你他她它们这那和与就也都还又着过把被对说道吗呢吧啊呀么什么怎么一个不没有")


class Tokenizer:
    def __init__(self, book: Path):
        import jieba
        self.jieba = jieba.Tokenizer()
        self.jieba.setLogLevel(60) if hasattr(self.jieba, "setLogLevel") else None
        userdict = book / "build" / "userdict.txt"
        if userdict.exists():
            self.jieba.load_userdict(str(userdict))

    def __call__(self, text: str) -> list[str]:
        return [w for w in self.jieba.lcut(text) if w.strip() and w not in _STOP and not all(c in "，。！？、；：“”‘’（）《》…—" for c in w)]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = sum(self.lens) / max(1, len(docs))
        df = Counter(w for d in self.docs for w in d)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - k + 0.5) / (k + 0.5)) for w, k in df.items()}
        self.k1, self.b = k1, b

    def score(self, query: list[str], i: int) -> float:
        d, s = self.docs[i], 0.0
        for w in set(query):
            tf = d.get(w)
            if tf:
                s += self.idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
        return s


class Embedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding
        cache = os.environ.get("N2G_EMBED_CACHE") or str(ROOT / ".cache" / "embed")
        self.name = model
        self.model = TextEmbedding(model, cache_dir=cache)

    def docs(self, texts: list[str]):
        import numpy as np
        return np.array(list(self.model.embed(texts)), dtype="float32")

    def query(self, text: str):
        import numpy as np
        return np.array(list(self.model.query_embed([text]))[0], dtype="float32")


class Memory:
    """Every ledger fact and event of one book, indexed once; `rank` scores a subset for a query."""

    def __init__(self, book: Path, items: list[dict]):
        self.items = items
        self.pos = {it["id"]: k for k, it in enumerate(items)}
        self.tok = Tokenizer(book)
        self.bm25 = BM25([self.tok(it["text"]) for it in items])
        self.vecs = self.embedder = None
        model = os.environ.get("N2G_EMBED_MODEL", "").strip()
        if model:
            self.embedder = Embedder(model)
            self.vecs = self._vectors(book, model)

    def _vectors(self, book: Path, model: str):
        import numpy as np
        d = book / "build" / "embed"
        d.mkdir(parents=True, exist_ok=True)
        stem = model.replace("/", "__")
        ids_path, vec_path = d / f"{stem}.ids.json", d / f"{stem}.npy"
        ids = [it["id"] for it in self.items]
        if ids_path.exists() and vec_path.exists() and json.loads(ids_path.read_text(encoding="utf-8")) == ids:
            return np.load(vec_path)
        vecs = self.embedder.docs([it["text"] for it in self.items])
        np.save(vec_path, vecs)
        ids_path.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        return vecs

    @property
    def backend(self) -> str:
        return f"bm25+{self.embedder.name}" if self.embedder else "bm25"

    def rank(self, query: str, ids: list[str], chapter: int, k: int = 12) -> list[dict]:
        if not ids:
            return []
        idx = [self.pos[i] for i in ids if i in self.pos]
        q = self.tok(query)
        lex = [self.bm25.score(q, i) for i in idx]
        top = max(lex) or 1.0
        scores = [x / top for x in lex]
        if self.vecs is not None:
            qv = self.embedder.query(query)
            sims = (self.vecs[idx] @ qv).tolist()
            lo, hi = min(sims), max(sims)
            scores = [(a + (s - lo) / ((hi - lo) or 1)) / 2 for a, s in zip(scores, sims)]
        span = max(1, chapter + 1)
        scores = [s + 0.1 * (1 - (chapter - self.items[i]["chapter"]) / span) for s, i in zip(scores, idx)]
        order = sorted(range(len(idx)), key=lambda j: -scores[j])[:k]
        return [self.items[idx[j]] | {"score": round(scores[j], 3)} for j in order]
