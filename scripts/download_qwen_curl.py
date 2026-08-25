from __future__ import annotations
import subprocess, sys
from pathlib import Path
DEST = Path(r"E:\desktop\coding\科研\models\Qwen2.5-7B-Instruct-GGUF")
DEST.mkdir(parents=True, exist_ok=True)
files = [
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
]
for name in files:
    out = DEST / name
    if out.exists() and out.stat().st_size > 0:
        print("skip", name, flush=True)
        continue
    url = f"https://hf-mirror.com/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/{name}"
    print("download", name, flush=True)
    r = subprocess.run(["curl.exe", "-L", "-s", "--retry", "4", "-C", "-", "-o", str(out), url], timeout=3600)
    print("done", name, r.returncode, out.stat().st_size if out.exists() else 0, flush=True)
print("all done", flush=True)
