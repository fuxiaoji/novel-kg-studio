"""Native Ollama chat client with thinking explicitly disabled."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from novel_kg_studio.llm import extract_json


class NativeOllamaNoThinkClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", num_ctx: int = 32768) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx

    def complete(self, system: str, user: str, *, max_tokens: int = 800) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "think": False,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.0, "num_ctx": self.num_ctx, "num_predict": max_tokens},
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str((payload.get("message") or {}).get("content") or "")

    def complete_json(self, system: str, user: str, *, max_tokens: int = 800) -> Any:
        raw = self.complete(system, user, max_tokens=max_tokens)
        try:
            return extract_json(raw)
        except ValueError:
            return {"raw": raw}
