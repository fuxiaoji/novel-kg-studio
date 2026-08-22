"""Download Qwen2.5-7B-Instruct Q4_K_M GGUF (2 split parts) from hf-mirror."""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

HF = "https://hf-mirror.com/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"
DEST = Path(r"E:\desktop\coding\科研\models\Qwen2.5-7B-Instruct-GGUF")
FILES = [
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
]


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[skip] {dest.name} already present")
        return
    tmp = dest.with_suffix(".part")
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            print(f"[dl] {dest.name} attempt {attempt + 1}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            tmp.replace(dest)
            print(f"[ok] {dest.name} {dest.stat().st_size / 1e9:.2f} GB")
            return
        except Exception as exc:
            last_err = exc
            time.sleep(3.0 * (attempt + 1))
    print(f"[FAIL] {url}: {last_err!r}")
    sys.exit(1)


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        download(f"{HF}/{name}", DEST / name)
    print("qwen download done")


if __name__ == "__main__":
    main()
