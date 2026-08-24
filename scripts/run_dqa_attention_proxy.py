"""Batched embedding entry point for the attention-proxy pilot."""

from __future__ import annotations

import json
import urllib.request

import numpy as np

import run_dqa_attention_proxy_pilot as pilot


def _batched_embed(texts: list[str]) -> np.ndarray:
    outputs = []
    for start in range(0, len(texts), 24):
        batch = [str(text)[:4000] for text in texts[start : start + 24]]
        body = {"model": "bge-m3", "input": batch}
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/embed",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            outputs.extend(json.loads(response.read().decode("utf-8"))["embeddings"])
    matrix = np.asarray(outputs, dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
    return matrix


pilot.embed = _batched_embed


if __name__ == "__main__":
    pilot.main()
