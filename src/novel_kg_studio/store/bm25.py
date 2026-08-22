from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


class BM25Index:
    """Pure-Python BM25 over a fixed document list."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.term_counts: list[Counter] = []
        self.doc_len = np.zeros(len(docs), dtype=float)
        df: Counter = Counter()
        for i, doc in enumerate(docs):
            terms = tokenize(doc)
            counts = Counter(terms)
            self.term_counts.append(counts)
            self.doc_len[i] = len(terms)
            for term in set(terms):
                df[term] += 1
        n = len(docs)
        self.idf = {term: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0) for term, freq in df.items()}
        self.avgdl = float(self.doc_len.mean()) if n else 0.0

    def score(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.term_counts), dtype=float)
        if not self.term_counts:
            return scores
        for term in set(tokenize(query)):
            weight = self.idf.get(term)
            if weight is None:
                continue
            for i, counts in enumerate(self.term_counts):
                freq = counts.get(term, 0)
                if freq:
                    denom = freq + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                    scores[i] += weight * freq * (self.k1 + 1) / denom
        return scores

