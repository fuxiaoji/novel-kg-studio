import subprocess, os
from pathlib import Path
base = Path(r"E:\desktop\coding\科研\datasets\external\musr\official")
files = ["murder_mystery.json", "object_placements.json", "team_allocation.json"]
for f in files:
    out = base / f
    if out.exists() and out.stat().st_size > 100000:
        print("skip", f, flush=True)
        continue
    url = f"https://raw.githubusercontent.com/Zayne-sprague/MuSR/main/datasets/{f}"
    print("dl", f, flush=True)
    r = subprocess.run(["curl.exe", "-L", "-s", "--retry", "4", "-C", "-", "-o", str(out), url], timeout=1800)
    print("done", f, r.returncode, out.stat().st_size if out.exists() else 0, flush=True)
print("all done", flush=True)
