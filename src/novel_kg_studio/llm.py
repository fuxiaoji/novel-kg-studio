from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from json_repair import repair_json
from openai import OpenAI


class LLMClient:
    """OpenAI-compatible chat client (DeepSeek) with JSON extraction + retries."""

    def __init__(
        self,
        model: str = "deepseek-chat",
        temperature: float = 0.0,
        max_tokens: int = 4000,
        retries: int = 3,
        reasoning_effort: str | None = None,
        thinking: str | None = None,
    ) -> None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable is required")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retries = retries
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=180.0,
        )

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                kwargs: dict[str, Any] = {}
                if self.reasoning_effort:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                if self.thinking:
                    kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    **kwargs,
                )
                content = response.choices[0].message.content or ""
                if not content.strip():
                    raise RuntimeError("empty completion")
                return content
            except Exception as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after {self.retries} attempts: {last_error!r}")

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                raw = self.complete(system, user, max_tokens=max_tokens)
                return extract_json(raw)
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    user = f"{user}\n\nYour previous response was not valid JSON: {exc}\nReturn strict JSON only."
        raise RuntimeError(f"JSON extraction failed after retries: {last_error!r}")


def extract_json(text: str, _depth: int = 0) -> Any:
    """Extract the first JSON object/array from an LLM response."""
    if _depth > 8:
        raise ValueError("extract_json recursion too deep")
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        if start < 0:
            continue
        end = raw.rfind(close_ch)
        if end <= start:
            continue
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            continue
    try:
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, (dict, list)):
            return repaired
    except Exception:
        pass

    merged: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == raw:
            continue
        try:
            item = extract_json(line, _depth + 1)
        except ValueError:
            continue
        if isinstance(item, dict):
            merged.update(item)
        elif isinstance(item, list):
            for element in item:
                if isinstance(element, dict):
                    merged.update(element)
    if merged:
        return merged
    raise ValueError(f"Could not find valid JSON in response: {raw[:200]!r}")
