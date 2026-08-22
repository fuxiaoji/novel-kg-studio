"""Probe which model names the DeepSeek API accepts."""

from __future__ import annotations

import os

from openai import OpenAI


def main() -> None:
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    for model in ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK only."}],
                max_tokens=8,
            )
            print(f"{model}: OK -> {response.choices[0].message.content!r}")
        except Exception as exc:
            print(f"{model}: ERROR -> {str(exc)[:180]}")


if __name__ == "__main__":
    main()
