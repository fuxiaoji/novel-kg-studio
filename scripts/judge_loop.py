"""Continuous judge loop: judge new answers as they appear and publish live accuracy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import load_cases  # noqa: E402
from goldonly_baseline import load_anno  # noqa: E402
from judge_three_groups import judge  # noqa: E402
from novel_kg_studio.llm import LLMClient  # noqa: E402

OUT = (
    Path(sys.argv[sys.argv.index("--out-root") + 1])
    if "--out-root" in sys.argv
    else ROOT / "outputs" / "four_datasets" / "dqa_qwen"
)
NOVEL_DIR = OUT / "novels"
CACHE = OUT / "judge_cache"
ANSWER_MODEL = "qwen2.5:7b-32k"
MTAG = f"|{ANSWER_MODEL}"
GROUPS = [
    ("graph_v4_m", "graph_", "r4"),
    ("graph_v4_u", "graphnm_", "r4"),
    ("graph_v5a_m", "graph_", "r5a"),
    ("graph_v5a_u", "graphnm_", "r5a"),
    ("graph_v5b_m", "graph_", "r5b"),
    ("graph_v5b_u", "graphnm_", "r5b"),
    ("graph_v7_m", "graph_", "r7"),
    ("graph_v7_u", "graphnm_", "r7"),
    ("ensemble_m", "ens_m_", "ens"),
    ("ensemble_u", "ens_u_", "ens"),
    ("tail", "tail_", "k3"),
    ("compress", "comp_", "k3"),
    ("full", "fullnovel_", "kfull"),
    ("gold_m", "masked", "gold"),
    ("gold_u", "unmasked", "gold"),
]
ENSEMBLE_VARIANTS = [("v4", "r4"), ("v5a", "r5a"), ("v5b", "r5b"), ("v7", "r7")]


def merged_cases() -> dict[str, dict]:
    by_novel: dict[str, dict] = {}
    for c in load_cases("detectiveqa"):
        nid = str(c["meta"]["novel_id"])
        merged = by_novel.setdefault(nid, {"questions": []})
        seen = {q["question"] for q in merged["questions"]}
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                merged["questions"].append(q)
    return by_novel


def key_for(nid: str, qi: int, kind: str) -> str:
    if kind == "r4":
        return hashlib.sha1(f"{nid}|{qi}|v1|r4{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "r5a":
        return hashlib.sha1(f"{nid}|{qi}|v1|r5a{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "r5b":
        return hashlib.sha1(f"{nid}|{qi}|v1|r5b{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "r7":
        return hashlib.sha1(f"{nid}|{qi}|v1|r7{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "k3":
        return hashlib.sha1(f"{nid}|{qi}|v1{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "ens":
        return hashlib.sha1(f"{nid}|{qi}|v1{MTAG}".encode("utf-8")).hexdigest()[:10]
    if kind == "kfull":
        return hashlib.sha1(f"fullnovel|{nid}|{qi}|v1".encode("utf-8")).hexdigest()[:10]
    return hashlib.sha1(f"{nid}|{qi}|v1".encode("utf-8")).hexdigest()[:10]


def letter_of(answer: str) -> str | None:
    """Extract the option letter (A-D) from a model answer, preferring explicit markers."""
    txt = str(answer or "").strip()
    if not txt:
        return None
    patterns = [
        r"Answer\s*[:：]\s*\(?([A-D])\)?",
        r"Letter\s*[:：]\s*\(?([A-D])\)?",
        r"(?:^|\s)\(?([A-D])\)?\s*[.:、]\s*(?:[A-Za-z])",
        r"(?:^|\s)([A-D])\s*[.:、]",
    ]
    for pat in patterns:
        m = re.search(pat, txt)
        if m:
            return m.group(1)
    return None


def build_ensemble_answers(by_novel: dict[str, dict], targets: list[str]) -> None:
    """Offline majority vote over v4/v5a/v5b/v7 answers (tie -> later variant, i.e. v7)."""
    for nid in targets:
        questions = by_novel.get(nid, {}).get("questions", [])
        q_total = len(questions)
        for variant, prefix in [("m", "graph_"), ("u", "graphnm_")]:
            for qi in range(q_total):
                answers: list[tuple[str, str]] = []
                for _, salt in ENSEMBLE_VARIANTS:
                    k = hashlib.sha1(f"{nid}|{qi}|v1|{salt}{MTAG}".encode("utf-8")).hexdigest()[:10]
                    p = NOVEL_DIR / nid / f"{prefix}{k}.json"
                    if not p.exists():
                        continue
                    try:
                        a = json.loads(p.read_text(encoding="utf-8")).get("answer", "")
                    except Exception:
                        continue
                    L = letter_of(a)
                    if L:
                        answers.append((L, str(a)))
                if len(answers) < 2:
                    continue
                cnt = Counter(L for L, _ in answers)
                mx = max(cnt.values())
                tied = [L for L in cnt if cnt[L] == mx]
                if len(tied) == 1:
                    winner = tied[0]
                else:
                    winner = next(L for L, _ in reversed(answers) if L in tied)
                full = next(a for L, a in reversed(answers) if L == winner)
                out = NOVEL_DIR / nid / f"ens_{variant}_{key_for(nid, qi, 'ens')}.json"
                payload = {"answer": full, "vote": {"letters": [L for L, _ in answers], "winner": winner}}
                out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    client = LLMClient(model="deepseek-v4-flash", temperature=0.0, max_tokens=2000, retries=3, reasoning_effort="low")
    targets_path = OUT / "large15_targets.json"
    if not targets_path.exists():
        targets_path = OUT / "targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8")) if targets_path.exists() else ["103", "104", "117"]
    by_novel = merged_cases()
    while True:
        try:
            build_ensemble_answers(by_novel, targets)
            counts = {g: [0, 0] for g, _, _ in GROUPS}
            per_novel: dict[str, dict] = {}
            debug_missing_full: list[str] = []
            for nid in targets:
                questions = by_novel.get(nid, {}).get("questions", [])
                per_novel[nid] = {"answered": 0, "total": len(questions)}
                for qi, q in enumerate(questions):
                    qid = q["qid"]
                    for g, prefix, kind in GROUPS:
                        k = key_for(nid, qi, kind)
                        if g in ("gold_m", "gold_u"):
                            continue
                        path = NOVEL_DIR / nid / f"{prefix}{k}.json"
                        if not path.exists():
                            continue
                        answer = json.loads(path.read_text(encoding="utf-8")).get("answer", "")
                        ckey = hashlib.sha1(f"{qid}|{g}|{answer}".encode("utf-8")).hexdigest()[:12]
                        cp = CACHE / f"j_{ckey}.json"
                        if g == "full" and not cp.exists():
                            debug_missing_full.append(f"{nid}-q{qi}-{ckey}")
                        if not cp.exists():
                            try:
                                judge(client, q["question"], q.get("gold_text") or "", answer, CACHE, ckey)
                            except Exception:
                                pass
                        verdict = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else None
                        if verdict is not None:
                            counts[g][1] += 1
                            counts[g][0] += 1 if verdict.get("correct") else 0
                            per_novel[nid]["answered"] += 1
            # gold-only groups (masked/unmasked under goldonly/{nid}/)
            for g, variant in [("gold_m", "masked"), ("gold_u", "unmasked")]:
                for nid in targets:
                    for anno in load_anno(nid):
                        k = hashlib.sha1(f"{nid}|{anno['source']}|{anno['qi']}|{variant}|v1|{ANSWER_MODEL}".encode("utf-8")).hexdigest()[:10]
                        path = OUT / "goldonly" / nid / f"{variant}_{k}.json"
                        if not path.exists():
                            continue
                        answer = json.loads(path.read_text(encoding="utf-8")).get("answer", "")
                        qid = f"{nid}_{anno['source']}_{anno['qi']}"
                        ckey = hashlib.sha1(f"{qid}|{g}|{answer}".encode("utf-8")).hexdigest()[:12]
                        cp = CACHE / f"j_{ckey}.json"
                        if not cp.exists():
                            try:
                                judge(client, anno["question"], anno["gold_text"] or "", answer, CACHE, ckey)
                            except Exception:
                                pass
                        verdict = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else None
                        if verdict is not None:
                            counts[g][1] += 1
                            counts[g][0] += 1 if verdict.get("correct") else 0
            payload = {
                "updated": time.strftime("%H:%M:%S"),
                "debug_missing_full": debug_missing_full,
                "groups": {
                    g: {"correct": c[0], "total": c[1], "accuracy": round(c[0] / c[1] * 100, 1) if c[1] else None}
                    for g, c in counts.items()
                },
                "per_novel": per_novel,
            }
            (OUT / "live_accuracy.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (OUT / "live_accuracy.js").write_text("window.LIVE_ACC = " + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[judge] iteration error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
