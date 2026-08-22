"""One-token calibrated choice probabilities from the local Ollama OpenAI endpoint."""

from __future__ import annotations

import json
import math
import urllib.request
from typing import Any


class QwenChoiceLogprobClient:
    def __init__(self, model: str = "qwen2.5:7b-32k", base_url: str = "http://127.0.0.1:11434/v1") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def classify(self, system: str, user: str, labels: str = "ABC") -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": 20,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
        choice = payload["choices"][0]
        content = str((choice.get("message") or {}).get("content") or "").strip().upper()
        alternatives = ((choice.get("logprobs") or {}).get("content") or [{}])[0].get("top_logprobs") or []
        logits = {label: -30.0 for label in labels}
        for item in alternatives:
            token = str(item.get("token") or "").strip().upper()
            if token in logits:
                logits[token] = max(logits[token], float(item.get("logprob") or -30.0))
        if content in logits and logits[content] <= -29.0:
            logits[content] = 0.0
        peak = max(logits.values())
        weights = {label: math.exp(value - peak) for label, value in logits.items()}
        total = sum(weights.values())
        probabilities = {label: value / total for label, value in weights.items()}
        return {
            "selected_token": content,
            "logprobs": logits,
            "probabilities": probabilities,
            "usage": payload.get("usage") or {},
        }
