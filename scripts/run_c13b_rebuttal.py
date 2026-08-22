"""C13b: one rebuttal pipeline that tests a tail hypothesis against option-conditioned graph evidence."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c_combined20 import load_rows  # noqa: E402
from c13_option_rebuttal import VERSION as AUDIT_VERSION  # noqa: E402
from c8_graph_passage import LETTERS, normalize_letter  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c13b-tail-hypothesis-option-graph-rebuttal-v1"


def format_checks(checks: list[dict[str, Any]]) -> str:
    blocks = []
    for row in checks:
        blocks.append(
            f"{row['letter']}. {row['option']}\n"
            f"  grounded support quote: {row.get('support_quote') or '[none]'}\n"
            f"  grounded contradiction quote: {row.get('contradiction_quote') or '[none]'}\n"
            f"  retrieval auditor note: {str(row.get('analysis') or '')[:650] or '[none]'}"
        )
    return "\n\n".join(blocks)


def arbitrate(client: Any, old: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    tail = old["tail_unmasked"]
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(audit["choices"][:4]))
    prompt = (
        f"QUESTION\n{audit['question']}\n\nOPTIONS\n{options}\n\n"
        f"PROVISIONAL NARRATIVE-HYPOTHESIS\n{tail}. {audit['choices'][LETTERS.index(tail)]}\n\n"
        f"OPTION-CONDITIONED GRAPH EVIDENCE\n{format_checks(audit['checks'])}\n\n"
        "Test the provisional answer as a falsifiable claim. KEEP it unless the quoted novel evidence or a precise auditor "
        "observation gives a concrete different identity, number, place, time, action, or motive. UNKNOWN or missing evidence "
        "is never a rebuttal. Override only when the old option is actually contradicted and a specific alternative is better "
        "supported. Independently check option-number and option-name binding. For NOT/EXCEPT questions, follow the negative "
        "wording exactly. Return strict JSON only: "
        '{"action":"KEEP|OVERRIDE","selected_letter":"A|B|C|D","decisive_evidence":"short"}'
    )
    raw = client.complete_json(
        "You are a conservative hypothesis falsification judge. Lack of evidence never falsifies a claim.",
        prompt,
        max_tokens=360,
    )
    action = str(raw.get("action", "KEEP")).upper() if isinstance(raw, dict) else "KEEP"
    proposed = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    selected = proposed if action == "OVERRIDE" and proposed in LETTERS else tail
    return {
        "method": "c13b_tail_hypothesis_option_graph_rebuttal",
        "selected_letter": selected,
        "provisional_tail_letter": tail,
        "action": "OVERRIDE" if selected != tail else "KEEP",
        "decisive_evidence": str(raw.get("decisive_evidence", "")) if isinstance(raw, dict) else "",
        "audit_selected_letter": audit.get("selected_letter"),
        "checks": audit["checks"],
        "raw": raw,
        "prompt_version": VERSION,
        "audit_version": AUDIT_VERSION,
        "mask": "unmasked",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c13b_20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    selected = set(args.novels)
    rows = [row for row in load_rows() if row["novel"] in selected]
    client = OllamaClient(args.model, max_tokens=500, num_ctx=32768)
    lock = threading.Lock(); done = 0; started = time.time()

    def one(old: dict[str, Any]) -> dict[str, Any]:
        path = args.out / "answers" / old["novel"] / f"q{int(old['qi']):02d}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("prompt_version") == VERSION and normalize_letter(cached.get("selected_letter")) in LETTERS:
                    return cached
            except Exception:
                pass
        audit_path = BASE / "dqa_qwen_c13_20" / "answers" / old["novel"] / f"q{int(old['qi']):02d}.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("prompt_version") != AUDIT_VERSION:
            raise RuntimeError(f"stale C13 audit: {audit_path}")
        result = arbitrate(client, old, audit)
        result.update(
            {
                "novel": old["novel"], "batch": old["batch"], "qi": int(old["qi"]), "qid": old["qid"],
                "question": old["question"], "choices": audit["choices"], "gold_letter": old["gold_letter"],
                "correct": result["selected_letter"] == old["gold_letter"], "answer_model": args.model,
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    with ThreadPoolExecutor(max_workers=min(args.workers, len(rows) or 1)) as pool:
        futures = [pool.submit(one, row) for row in rows]
        for future in as_completed(futures):
            row = future.result()
            with lock:
                done += 1
                elapsed = max(time.time() - started, 0.01)
                progress = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": done, "total": len(rows), "current": f"c13b/{row['novel']}/q{row['qi']} -> {row['selected_letter']}", "per_hour": done / elapsed * 3600, "eta_minutes": (len(rows) - done) / max(done / elapsed, 1e-9) / 60}
                (args.out / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[{done}/{len(rows)}] c13b/{row['novel']}/q{row['qi']} -> {row['selected_letter']} ({row['action']})", flush=True)

    outputs = [json.loads(path.read_text(encoding="utf-8")) for path in (args.out / "answers").glob("*/*.json") if path.parent.name in selected]
    correct = sum(bool(row["correct"]) for row in outputs)
    print(json.dumps({"correct": correct, "total": len(outputs), "accuracy": correct / len(outputs), "overrides": sum(row["action"] == "OVERRIDE" for row in outputs)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
