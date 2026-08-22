"""Download Qwen2.5-7B-Instruct Q4_K_M GGUF (2 parts) via curl with resume."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

HF = "https://hf-mirror.com/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"
DEST = Path(r"E:\desktop\coding\科研\models\Qwen2.5-7B-Instruct-GGUF")
FILES = [
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
]


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        out = DEST / name
        if out.exists() and out.stat().st_size > 0:
            print(f"skip {name} ({out.stat().st_size / 1e9:.2f} GB)")
            continue
        url = f"{HF}/{name}"
        for attempt in range(6):
            print(f"[dl] {name} attempt {attempt + 1}", flush=True)
            r = subprocess.run(
                ["curl.exe", "-L", "-s", "--retry", "3", "-C", "-", "-o", str(out), url],
                timeout=3600,
            )
            size = out.stat().st_size if out.exists() else 0
            print(f"[part] {name} rc={r.returncode} size={size / 1e9:.2f} GB", flush=True)
            if r.returncode == 0 and size > 0:
                break
            time.sleep(3)
        else:
            raise SystemExit(f"download failed for {name}")
    print("qwen download done", flush=True)


if __name__ == "__main__":
    main()
