"""30-novel DetectiveQA large-scale run.

Experiment groups (graph variants): v4 / v5a(v5.1 agentic) / v5b(v5.2 relation traversal) / v7,
each masked + unmasked (8 answer rounds). Baselines: tail window, chunk-compress, gold-only.

- answer model: qwen2.5:7b-32k (32k context)
- graph build model: qwen2.5:7b-c4 (4k context; small chunks only per chunk-size experiment)
- tail/compress answers are model-keyed and shared across the 8 rounds (computed once)
- gold-only round: masked + unmasked
- resume-safe: per-novel done marker + per-answer file caches; run can be restarted anytime
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

os.chdir(ROOT)

# 7 already-built novels + 23 new ones (spread across sizes)
NOVELS = [
    "103", "104", "117", "100", "106", "108", "121",  # built
    "25", "26", "27", "28", "30", "31", "33", "40", "53", "56",
    "79", "82", "84", "87", "90", "93", "97", "99", "105", "109", "114", "116", "118",
]
VARIANTS = ["v4", "v5a", "v5b", "v7"]
ANSWER_MODEL = "qwen2.5:7b-32k"
BUILD_MODEL = "qwen2.5:7b-c4"
OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
(OUT / "targets.json").write_text(json.dumps(NOVELS), encoding="utf-8")
(OUT / "large15_targets.json").write_text(json.dumps(NOVELS), encoding="utf-8")

from detectiveqa_three_groups import main as three_main  # noqa: E402
from goldonly_baseline import main as goldonly_main  # noqa: E402
from goldonly_baseline import load_anno  # noqa: E402


def _merged_questions(nid: str) -> int:
    from eval_four_datasets import load_cases

    seen: set[str] = set()
    n = 0
    for c in load_cases("detectiveqa"):
        if str(c["meta"].get("novel_id")) != nid:
            continue
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                n += 1
    return n


def _novel_complete(nid: str) -> bool:
    """True only when every variant round AND gold round has a non-empty cached answer."""
    import hashlib
    import json as _json

    d = OUT / "novels" / nid
    q_total = _merged_questions(nid)
    if q_total <= 0:
        return False
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
    tag = f"|{ANSWER_MODEL}"
    for _, prefix, salt in variants:
        got = 0
        for qi in range(q_total):
            k = hashlib.sha1(f"{nid}|{qi}|v1|{salt}{tag}".encode("utf-8")).hexdigest()[:10]
            p = d / f"{prefix}{k}.json"
            if p.exists():
                try:
                    ans = _json.loads(p.read_text(encoding="utf-8")).get("answer", "")
                    if str(ans).strip():
                        got += 1
                except Exception:
                    pass
        if got < q_total:
            return False
    for variant in ("masked", "unmasked"):
        expected = 0
        for anno in load_anno(nid):
            expected += 1
            k = hashlib.sha1(f"{nid}|{anno['source']}|{anno['qi']}|{variant}|v1|{ANSWER_MODEL}".encode("utf-8")).hexdigest()[:10]
            p = OUT / "goldonly" / nid / f"{variant}_{k}.json"
            if not p.exists():
                return False
            try:
                ans = json.loads(p.read_text(encoding="utf-8")).get("answer", "")
                if not str(ans).strip():
                    return False
            except Exception:
                return False
    return True


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
    ]
    if not masked:
        argv.append("--no-mask-graph")
    argv += ["--out", str(OUT / "results_30novels.json")]
    sys.argv = argv
    three_main()


def run_gold(novel: str) -> None:
    sys.argv = [
        "goldonly_baseline.py",
        "--backend", "qwen",
        "--variants", "masked", "unmasked",
        "--novels", novel,
        "--model", ANSWER_MODEL,
        "--out", str(OUT / "results_goldonly_30novels.json"),
    ]
    goldonly_main()


if __name__ == "__main__":
    for nid in NOVELS:
        marker = OUT / "novels" / nid / "done_30novels.txt"
        if marker.exists() and _novel_complete(nid):
            print(f"=== novel {nid}: already complete, skip ===", flush=True)
            continue
        if marker.exists():
            print(f"=== novel {nid}: stale done marker (incomplete answers), re-running ===", flush=True)
            marker.unlink()
        for variant in VARIANTS:
            for masked in (True, False):
                tag = "masked" if masked else "unmasked"
                print(f"=== novel {nid}: {variant} {tag} ===", flush=True)
                run_three(nid, variant, masked)
        print(f"=== novel {nid}: gold-only ===", flush=True)
        run_gold(nid)
        if _novel_complete(nid):
            marker.write_text("done", encoding="utf-8")
            print(f"=== novel {nid}: ALL ROUNDS DONE ===", flush=True)
        else:
            print(f"=== WARNING novel {nid}: incomplete answers after rounds, NOT marked done (will retry on next run) ===", flush=True)
    (OUT / "targets.json").write_text(json.dumps(NOVELS), encoding="utf-8")
    print("30-novel run all done", flush=True)
