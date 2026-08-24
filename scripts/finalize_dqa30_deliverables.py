"""Verify frozen inputs and hash compact paper deliverables."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "config" / "dqa30_frozen_graphs.json"
OUT = ROOT / "paper" / "DELIVERABLE_MANIFEST.json"


def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def main() -> None:
    frozen=json.loads(FROZEN.read_text(encoding="utf-8")); drift=[]
    for item in frozen["records"]:
        path=Path(item["graph_path"])
        stat=path.stat(); actual=sha(path)
        if actual!=item["sha256"] or stat.st_size!=item["bytes"] or stat.st_mtime_ns!=item["mtime_ns"]:
            drift.append({"novel":item["novel"],"path":str(path),"expected_sha256":item["sha256"],"actual_sha256":actual,"expected_bytes":item["bytes"],"actual_bytes":stat.st_size,"expected_mtime_ns":item["mtime_ns"],"actual_mtime_ns":stat.st_mtime_ns})
    if drift: raise RuntimeError("frozen graph drift detected: "+json.dumps(drift,ensure_ascii=False))
    patterns=("paper/*.tex","paper/*.bib","paper/*.md","paper/*.pdf","paper/generated/*.csv","paper/generated/*.json","paper/generated/*.jsonl","paper/generated/*.tex","paper/generated/*.pdf","paper/generated/*.svg","config/dqa30_frozen_graphs.*","scripts/*dqa30*.py")
    paths=sorted({path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file() and path!=OUT})
    manifest={"protocol":"dqa30-final-deliverables-v1","graph_integrity":{"records":len(frozen["records"]),"unchanged":True,"manifest_sha256":sha(FROZEN)},"files":[{"path":str(path.relative_to(ROOT)),"bytes":path.stat().st_size,"sha256":sha(path)} for path in paths]}
    OUT.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"graphs_unchanged":30,"deliverables":len(paths),"manifest":str(OUT)},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
