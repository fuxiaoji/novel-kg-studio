"""Option-C pilot: 10 new novels with Qwen build at 1500-char chunks + pass2 v3 prompt.

Full suite per novel: v4/v5a/v5b/v7 x masked/unmasked (8 rounds) + gold + tail + compress.
Fresh build only (--no-build-resume), separate output root (dqa_qwen_c) so the main
30-novel run is untouched. Answers/baselines are also fresh in the new root.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

os.chdir(ROOT)

NOVELS_C = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79"]
VARIANTS = ["v4", "v5a", "v5b", "v7"]
ANSWER_MODEL = "qwen2.5:7b-32k"
BUILD_MODEL = "qwen2.5:7b-c4"
OUT_C = ROOT / "outputs" / "four_datasets" / "dqa_qwen_c"
OUT_C.mkdir(parents=True, exist_ok=True)
(OUT_C / "targets.json").write_text(json.dumps(NOVELS_C), encoding="utf-8")
(OUT_C / "large15_targets.json").write_text(json.dumps(NOVELS_C), encoding="utf-8")

from detectiveqa_three_groups import main as three_main  # noqa: E402
from goldonly_baseline import main as goldonly_main  # noqa: E402


def run_three(novel: str, retrieval: str, masked: bool) -> None:
    argv = [
        "detectiveqa_three_groups.py",
        "--novels", novel,
        "--workers", "8",
        "--answer-workers", "3",
        "--groups", "graph", "tail", "compress",
        "--retrieval", retrieval,
        "--answer-model", ANSWER_MODEL,
        "--build-model", BUILD_MODEL,
        "--build-chunk-size", "1500",
        "--build-overlap", "100",
        "--no-build-resume",
        "--pass2-prompt", "v3",
        "--out-root", str(OUT_C),
    ]
    if not masked:
        argv.append("--no-mask-graph")
    argv += ["--out", str(OUT_C / "results_10novels_c.json")]
    sys.argv = argv
    three_main()


def run_gold(novel: str) -> None:
    sys.argv = [
        "goldonly_baseline.py",
        "--backend", "qwen",
        "--variants", "masked", "unmasked",
        "--novels", novel,
        "--model", ANSWER_MODEL,
        "--out-root", str(OUT_C),
        "--out", str(OUT_C / "results_goldonly_c.json"),
    ]
    goldonly_main()


def _novel_complete(nid: str) -> bool:
    from goldonly_baseline import load_anno

    d = OUT_C / "novels" / nid
    tag = f"|{ANSWER_MODEL}"
    variants = [
        ("v4_m", "graph_", "r4"),
        ("v4_u", "graphnm_", "r4"),
        ("v5a_m", "graph_", "r5a"),
        ("v5a_u", "graphnm_", "r5a"),
        ("v5b_m", "graph_", "r5b"),
        ("v5b_u", "graphnm_", "r5b"),
        ("v7_m", "graph_", "r7"),
        ("v7_u", "graphnm_", "r7"),
    ]
    # question count from merged cases
    from eval_four_datasets import load_cases

    seen: set[str] = set()
    q_total = 0
    for c in load_cases("detectiveqa"):
        if str(c["meta"].get("novel_id")) != nid:
            continue
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                q_total += 1
    if q_total <= 0:
        return False
    for _, prefix, salt in variants:
        got = 0
        for qi in range(q_total):
            k = hashlib.sha1(f"{nid}|{qi}|v1|{salt}{tag}".encode("utf-8")).hexdigest()[:10]
            p = d / f"{prefix}{k}.json"
            if p.exists():
                try:
                    a = json.loads(p.read_text(encoding="utf-8")).get("answer", "")
                    if str(a).strip():
                        got += 1
                except Exception:
                    pass
        if got < q_total:
            return False
    for variant in ("masked", "unmasked"):
        for anno in load_anno(nid):
            k = hashlib.sha1(f"{nid}|{anno['source']}|{anno['qi']}|{variant}|v1|{ANSWER_MODEL}".encode("utf-8")).hexdigest()[:10]
            p = OUT_C / "goldonly" / nid / f"{variant}_{k}.json"
            if not p.exists():
                return False
            try:
                a = json.loads(p.read_text(encoding="utf-8")).get("answer", "")
                if not str(a).strip():
                    return False
            except Exception:
                return False
    return True


if __name__ == "__main__":
    for nid in NOVELS_C:
        marker = OUT_C / "novels" / nid / "done_10novels_c.txt"
        if marker.exists() and _novel_complete(nid):
            print(f"=== C novel {nid}: already complete, skip ===", flush=True)
            continue
        for variant in VARIANTS:
            for masked in (True, False):
                tag = "masked" if masked else "unmasked"
                print(f"=== C novel {nid}: {variant} {tag} ===", flush=True)
                run_three(nid, variant, masked)
        print(f"=== C novel {nid}: gold-only ===", flush=True)
        run_gold(nid)
        if _novel_complete(nid):
            marker.write_text("done", encoding="utf-8")
            print(f"=== C novel {nid}: ALL ROUNDS DONE ===", flush=True)
        else:
            print(f"=== WARNING C novel {nid}: incomplete, not marked ===", flush=True)
    print("run_10novels_c all done", flush=True)
