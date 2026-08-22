"""DeepSeek V4 Flash baselines on the trusted 20 DetectiveQA novels.

Runs two independently cached policies:
1. question + options only
2. complete novel + question + options

The report includes overall accuracy and full-novel accuracy after excluding every
question answered correctly by the question-only policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c_option_methods import LETTERS, question_type  # noqa: E402
from novel_kg_studio.cache import load_json, save_json  # noqa: E402

NOVELS = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79", "15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]
MODEL = "deepseek-v4-flash"
PROMPT_VERSION = "v1_complete_novel_prefix_thinking_enabled"
SYSTEM = (
    "You answer DetectiveQA multiple-choice questions. Use only the information "
    "provided in the user message. Return exactly one option as strict JSON."
)


def options_block(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))


def question_prompt(q: dict[str, Any]) -> str:
    return (
        f"Question:\n{q['question']}\n\nOptions:\n{options_block(q)}\n\n"
        'Return strict JSON only: {"selected_letter":"A|B|C|D","reason":"brief"}'
    )


def full_prompt(case: dict[str, Any], q: dict[str, Any]) -> str:
    # Novel first maximizes reusable API prefix caching across a novel's questions.
    return (
        f"Complete novel:\n{case['text']}\n\n"
        f"Question:\n{q['question']}\n\nOptions:\n{options_block(q)}\n\n"
        'Return strict JSON only: {"selected_letter":"A|B|C|D","reason":"brief textual evidence"}'
    )


def extract_letter(content: str) -> str | None:
    text = str(content or "").strip()
    try:
        obj = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I))
        value = str(obj.get("selected_letter", "")).strip().upper()
        if value in LETTERS:
            return value
    except Exception:
        pass
    match = re.search(r'(?i)selected_letter["\s:=-]+([ABCD])\b', text)
    if not match:
        match = re.search(r"(?i)(?:answer|option|答案|选项)\s*(?:is|为|:|：)?\s*([ABCD])\b", text)
    if not match:
        match = re.search(r"(?i)^\s*([ABCD])(?:\b|[.、:：])", text)
    return match.group(1).upper() if match else None


class DeepSeekClient:
    def __init__(self, thinking: str) -> None:
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.thinking = thinking

    def complete(self, user: str) -> tuple[str, dict[str, Any]]:
        body = {
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": 32768,
            "thinking": {"type": self.thinking},
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error = ""
        for attempt in range(5):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    data = json.loads(response.read().decode("utf-8"))
                message = data["choices"][0]["message"] or {}
                content = message.get("content") or ""
                if content.strip():
                    return content, data.get("usage") or {}
                last_error = "empty content"
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code in (400, 401, 402, 403, 404):
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(last_error)


def valid(path: Path) -> bool:
    try:
        return load_json(path).get("selected_letter") in LETTERS
    except Exception:
        return False


def answer(client: DeepSeekClient, policy: str, case: dict[str, Any], qi: int, q: dict[str, Any], path: Path) -> dict[str, Any]:
    if valid(path):
        return load_json(path)
    prompt = question_prompt(q) if policy == "question_only" else full_prompt(case, q)
    content, usage = client.complete(prompt)
    letter = extract_letter(content)
    gold = LETTERS[q["gold_index"]]
    row = {
        "novel": str(case["meta"]["novel_id"]),
        "qi": qi,
        "qid": q["qid"],
        "question": q["question"],
        "choices": q["choices"],
        "question_type": question_type(q["question"]),
        "gold_letter": gold,
        "selected_letter": letter,
        "correct": letter == gold,
        "raw": content,
        "usage": usage,
        "model": MODEL,
        "thinking": client.thinking,
        "input_policy": policy,
        "prompt_hash": hashlib.sha256((SYSTEM + PROMPT_VERSION + policy).encode()).hexdigest()[:16],
    }
    save_json(path, row)
    return row


def run_question_only(cases: dict[str, Any], out: Path, thinking: str, workers: int) -> None:
    jobs = []
    for novel in NOVELS:
        case = cases[novel]
        for qi, q in enumerate(case["questions"]):
            jobs.append((case, qi, q, out / "answers" / "question_only" / novel / f"q{qi:02d}.json"))
    pending = [job for job in jobs if not valid(job[3])]
    completed = len(jobs) - len(pending)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(answer, DeepSeekClient(thinking), "question_only", *job) for job in pending]
        for future in as_completed(futures):
            try:
                row = future.result()
            except Exception as exc:
                print(f"[question_only retry-later] {type(exc).__name__}: {exc}", flush=True)
                continue
            completed += 1
            print(f"[question_only {completed}/{len(jobs)}] {row['novel']} q{row['qi']} {row['selected_letter']}", flush=True)


def run_full(cases: dict[str, Any], out: Path, thinking: str, workers: int) -> None:
    def one_novel(novel: str) -> int:
        client = DeepSeekClient(thinking)
        case = cases[novel]
        count = 0
        for qi, q in enumerate(case["questions"]):
            path = out / "answers" / "full_novel" / novel / f"q{qi:02d}.json"
            row = answer(client, "full_novel", case, qi, q, path)
            count += 1
            print(f"[full_novel] {novel} q{qi} {row['selected_letter']}", flush=True)
        return count

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one_novel, novel) for novel in NOVELS]
        for future in as_completed(futures):
            future.result()


def collect(cases: dict[str, Any], out: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"question_only": [], "full_novel": []}
    for policy in result:
        for novel in NOVELS:
            for qi, _ in enumerate(cases[novel]["questions"]):
                path = out / "answers" / policy / novel / f"q{qi:02d}.json"
                if not valid(path):
                    raise RuntimeError(f"missing or invalid answer: {path}")
                result[policy].append(load_json(path))
    return result["question_only"], result["full_novel"]


def usage_total(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    return {key: sum(int((row.get("usage") or {}).get(key, 0) or 0) for row in rows) for key in keys}


def analyze(cases: dict[str, Any], out: Path) -> None:
    qonly, full = collect(cases, out)
    hard_ids = {(r["novel"], r["qi"]) for r in qonly if not r["correct"]}
    hard_full = [r for r in full if (r["novel"], r["qi"]) in hard_ids]
    q_correct = sum(r["correct"] for r in qonly)
    f_correct = sum(r["correct"] for r in full)
    h_correct = sum(r["correct"] for r in hard_full)
    summary = {
        "model": MODEL,
        "thinking": qonly[0]["thinking"],
        "trusted_novels": NOVELS,
        "total_questions": len(qonly),
        "question_only": {"correct": q_correct, "total": len(qonly), "accuracy": q_correct / len(qonly)},
        "full_novel": {"correct": f_correct, "total": len(full), "accuracy": f_correct / len(full)},
        "hard_definition": "questions answered incorrectly by this DeepSeek question-only run",
        "hard_full_novel": {"correct": h_correct, "total": len(hard_full), "accuracy": h_correct / len(hard_full)},
        "question_only_usage": usage_total(qonly),
        "full_novel_usage": usage_total(full),
    }
    save_json(out / "summary.json", summary)
    lines = [
        "# DeepSeek V4 Flash：20本小说双基线",
        "",
        f"- 题目+选项：{q_correct}/{len(qonly)} = {q_correct / len(qonly):.1%}",
        f"- 全小说+题目+选项：{f_correct}/{len(full)} = {f_correct / len(full):.1%}",
        f"- 排除题目-only答对题后：{h_correct}/{len(hard_full)} = {h_correct / len(hard_full):.1%}",
        "",
        "硬集由本次 DeepSeek V4 Flash 的题目-only预测动态定义，不沿用Qwen硬集。",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_deepseek_v4flash_full_vs_question20")
    parser.add_argument("--thinking", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--question-workers", type=int, default=8)
    parser.add_argument("--novel-workers", type=int, default=3)
    parser.add_argument("--phase", choices=("all", "question", "full", "analyze"), default="all")
    args = parser.parse_args()
    cases = merged_cases(NOVELS)
    if sum(len(cases[n]["questions"]) for n in NOVELS) != 164:
        raise RuntimeError("trusted 20-novel question count is not 164")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.phase in ("all", "question"):
        run_question_only(cases, args.out, args.thinking, args.question_workers)
    if args.phase in ("all", "full"):
        run_full(cases, args.out, args.thinking, args.novel_workers)
    if args.phase in ("all", "analyze"):
        analyze(cases, args.out)


if __name__ == "__main__":
    main()
