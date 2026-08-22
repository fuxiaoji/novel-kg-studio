"""Evaluate MiniMax M2/M2.5 on the ten DetectiveQA novels with a maximal tail window.

The API key is read only from MINIMAX_API_KEY and is never persisted.  For each
model/novel pair the longest question is used to calibrate a fixed tail that
keeps input + requested output below MiniMax's 204,800-token context limit.
Putting the novel before the question also makes repeated questions eligible
for MiniMax prefix caching.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openai import BadRequestError, OpenAI, RateLimitError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]

from c_option_methods import LETTERS, normalize_letter  # noqa: E402
from run_c_improvements import NOVELS, merged_cases  # noqa: E402
from novel_kg_studio.llm import extract_json  # noqa: E402

MODELS = ["MiniMax-M2", "MiniMax-M2.5"]
CONTEXT_LIMIT = 204_800
MAX_COMPLETION = 2_048
TARGET_PROMPT_TOKENS = CONTEXT_LIMIT - MAX_COMPLETION - 512
VERSION = "minimax-tail-204800-v1"
SYSTEM = (
    "You are a meticulous detective-fiction reader. Answer only from the supplied novel. "
    "Evaluate every option against explicit events and explanations. Do not use outside knowledge."
)


def options_block(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q.get("choices") or []))


def build_user_prompt(tail: str, q: dict[str, Any]) -> str:
    # Keep the large, repeated prefix byte-identical so provider caching can work.
    return (
        "NOVEL TEXT (a suffix ending at the original end of the novel):\n"
        + tail
        + "\n\nEND NOVEL TEXT\n\nQUESTION:\n"
        + str(q["question"])
        + "\n\nOPTIONS:\n"
        + options_block(q)
        + "\n\nSelect exactly one option. Return only strict JSON in this form: "
        + '{"selected_letter":"A|B|C|D","confidence":"high|medium|low","reason":"brief evidence-based reason"}'
    )


def initial_tail_chars(text: str) -> int:
    """Conservative first estimate; calibration expands it using API token usage."""
    return min(len(text), TARGET_PROMPT_TOKENS * 3)


def parse_answer(content: str, choices: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        parsed = extract_json(content)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        pass
    letter = normalize_letter(payload.get("selected_letter")) or normalize_letter(content)
    return {
        "selected_letter": letter,
        "selected_text": choices[LETTERS.index(letter)] if letter in LETTERS and len(choices) >= 4 else None,
        "confidence": str(payload.get("confidence") or "low"),
        "reason": str(payload.get("reason") or ""),
    }


def is_context_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("context", "204800", "token limit", "too long", "maximum length"))


class MiniMaxClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=900.0, max_retries=0)

    def answer(self, model: str, tail: str, q: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": build_user_prompt(tail, q)},
                    ],
                    temperature=1.0,
                    top_p=0.95,
                    max_completion_tokens=MAX_COMPLETION,
                    extra_body={"reasoning_split": True},
                )
                message = response.choices[0].message
                content = message.content or ""
                dumped = response.model_dump()
                message_dump = dumped.get("choices", [{}])[0].get("message", {})
                usage = dumped.get("usage") or {}
                normalized = parse_answer(content, q.get("choices") or [])
                normalized.update(
                    {
                        "content": content,
                        "reasoning_content": message_dump.get("reasoning_content") or "",
                        "reasoning_details": message_dump.get("reasoning_details") or [],
                        "finish_reason": dumped.get("choices", [{}])[0].get("finish_reason"),
                        "usage": usage,
                        "request_id": dumped.get("id"),
                        "elapsed_seconds": round(time.time() - started, 3),
                        "attempts": attempt + 1,
                    }
                )
                return normalized
            except BadRequestError:
                raise
            except RateLimitError as exc:
                last_exc = exc
                time.sleep(min(60, 8 * (attempt + 1)))
            except Exception as exc:
                if getattr(exc, "status_code", None) == 402 or "insufficient balance" in str(exc).lower():
                    raise
                last_exc = exc
                time.sleep(min(30, 3 * (attempt + 1)))
        raise RuntimeError(f"MiniMax request failed after retries: {last_exc}")


def usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def cached_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("prompt_tokens_details") or {}
    value = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    return int(value) if isinstance(value, (int, float)) else 0


def estimated_cost_yuan(rows: list[dict[str, Any]]) -> float:
    cost = 0.0
    for row in rows:
        usage = row.get("usage") or {}
        prompt = usage_value(usage, "prompt_tokens")
        cached = min(prompt, cached_tokens(usage))
        output = usage_value(usage, "completion_tokens")
        cost += (prompt - cached) * 2.1e-6 + cached * 0.21e-6 + output * 8.4e-6
    return cost


class Dashboard:
    def __init__(self, root: Path, total: int) -> None:
        self.root = root
        self.total = total
        self.started = time.time()
        self.rows: list[dict[str, Any]] = []
        self.current = "准备开始"
        self.lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)
        self._write_html()
        self.publish()

    def _write_html(self) -> None:
        html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MiniMax 长上下文实验</title>
<style>body{font-family:system-ui,"Microsoft YaHei";margin:28px;max-width:1050px;color:#202124}.bar{height:22px;background:#e5e7eb;border-radius:12px;overflow:hidden}.fill{height:100%;background:#2563eb}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}.muted{color:#666}</style>
<h1>MiniMax 204,800 上下文尾窗口实验</h1><div id="app">正在加载……</div>
<script src="progress.js"></script><script>
function draw(){const p=window.MM_PROGRESS||{},pct=p.total?100*p.completed/p.total:0;const rows=Object.entries(p.by_model||{}).map(([k,v])=>`<tr><td>${k}</td><td>${v.done}</td><td>${v.correct}</td><td>${v.done?(100*v.correct/v.done).toFixed(1):'-'}%</td><td>${v.unparsed}</td><td>${(v.prompt_tokens||0).toLocaleString()}</td><td>¥${(v.estimated_cost_yuan||0).toFixed(2)}</td></tr>`).join('');document.getElementById('app').innerHTML=`<p><b>${p.completed||0} / ${p.total||0}</b>（${pct.toFixed(1)}%）</p><div class="bar"><div class="fill" style="width:${pct}%"></div></div><p>当前：${p.current||''}</p><p>速度：${(p.per_hour||0).toFixed(1)} 题/小时；预计剩余：${p.eta||'-'}</p><p class="muted">更新时间：${p.updated||''}</p><table><tr><th>模型</th><th>完成</th><th>正确</th><th>正确率</th><th>未解析</th><th>输入token</th><th>估算费用</th></tr>${rows}</table>`}
draw();setInterval(()=>{const s=document.createElement('script');s.src='progress.js?t='+Date.now();s.onload=draw;document.head.appendChild(s)},3000)
</script></html>"""
        (self.root / "progress.html").write_text(html, encoding="utf-8")

    def add(self, row: dict[str, Any], current: str) -> None:
        with self.lock:
            self.rows.append(row)
            self.current = current
            self.publish()

    def publish(self) -> None:
        elapsed = max(time.time() - self.started, 0.001)
        by_model: dict[str, dict[str, Any]] = {}
        for model in MODELS:
            rows = [r for r in self.rows if r.get("model") == model]
            by_model[model] = {
                "done": len(rows),
                "correct": sum(bool(r.get("correct")) for r in rows),
                "unparsed": sum(not r.get("selected_letter") for r in rows),
                "prompt_tokens": sum(usage_value(r.get("usage") or {}, "prompt_tokens") for r in rows),
                "completion_tokens": sum(usage_value(r.get("usage") or {}, "completion_tokens") for r in rows),
                "cached_tokens": sum(cached_tokens(r.get("usage") or {}) for r in rows),
                "estimated_cost_yuan": estimated_cost_yuan(rows),
            }
        rate = len(self.rows) / elapsed * 3600
        remaining = max(self.total - len(self.rows), 0)
        eta_h = remaining / rate if rate else 0
        payload = {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "completed": len(self.rows),
            "total": self.total,
            "current": self.current,
            "per_hour": rate,
            "eta": "已完成" if not remaining else f"{eta_h:.1f} 小时",
            "by_model": by_model,
        }
        (self.root / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.root / "progress.js").write_text("window.MM_PROGRESS=" + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8")


def answer_path(root: Path, model: str, novel: str, qi: int) -> Path:
    return root / "answers" / model / novel / f"q{qi:02d}.json"


def load_cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return row if row.get("version") == VERSION and row.get("model") in MODELS else None


def calibrate_and_answer(
    client: MiniMaxClient,
    model: str,
    novel: str,
    text: str,
    qi: int,
    q: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    chars = initial_tail_chars(text)
    result: dict[str, Any] | None = None
    for _ in range(5):
        tail = text[-chars:]
        try:
            result = client.answer(model, tail, q)
        except BadRequestError as exc:
            if is_context_error(exc) and chars > 10_000:
                chars = max(10_000, int(chars * 0.88))
                continue
            raise
        prompt_tokens = usage_value(result.get("usage") or {}, "prompt_tokens")
        if chars >= len(text) or not prompt_tokens:
            break
        expanded = min(len(text), int(chars * TARGET_PROMPT_TOKENS / prompt_tokens * 0.995))
        if expanded <= chars + 1_000:
            break
        chars = expanded
    if result is None:
        raise RuntimeError(f"Unable to calibrate {model}/{novel}")
    return chars, result


def finalize_row(model: str, novel: str, qi: int, q: dict[str, Any], text: str, tail_chars: int, result: dict[str, Any]) -> dict[str, Any]:
    gold_index = q.get("gold_index")
    gold_letter = LETTERS[gold_index] if isinstance(gold_index, int) and 0 <= gold_index < 4 else None
    selected = normalize_letter(result.get("selected_letter"))
    row = dict(result)
    row.update(
        {
            "version": VERSION,
            "model": model,
            "novel": novel,
            "qi": qi,
            "qid": q.get("qid"),
            "question": q.get("question"),
            "choices": q.get("choices"),
            "gold_index": gold_index,
            "gold_letter": gold_letter,
            "gold_text": q.get("gold_text"),
            "correct": bool(selected and selected == gold_letter),
            "context_limit": CONTEXT_LIMIT,
            "max_completion_tokens": MAX_COMPLETION,
            "novel_chars": len(text),
            "tail_chars": tail_chars,
            "tail_start_char": len(text) - tail_chars,
            "novel_char_coverage": tail_chars / max(len(text), 1),
            "prompt_hash": hashlib.sha256(build_user_prompt(text[-tail_chars:], q).encode("utf-8")).hexdigest(),
        }
    )
    return row


def write_summary(root: Path, rows: list[dict[str, Any]]) -> None:
    summary: dict[str, Any] = {"version": VERSION, "context_limit": CONTEXT_LIMIT, "models": {}}
    for model in MODELS:
        mr = [r for r in rows if r.get("model") == model]
        elapsed = [float(r["elapsed_seconds"]) for r in mr if isinstance(r.get("elapsed_seconds"), (int, float))]
        summary["models"][model] = {
            "correct": sum(bool(r.get("correct")) for r in mr),
            "total": len(mr),
            "accuracy": sum(bool(r.get("correct")) for r in mr) / len(mr) if mr else None,
            "unparsed": sum(not r.get("selected_letter") for r in mr),
            "prompt_tokens": sum(usage_value(r.get("usage") or {}, "prompt_tokens") for r in mr),
            "completion_tokens": sum(usage_value(r.get("usage") or {}, "completion_tokens") for r in mr),
            "cached_tokens": sum(cached_tokens(r.get("usage") or {}) for r in mr),
            "estimated_cost_yuan": estimated_cost_yuan(mr),
            "average_elapsed_seconds": statistics.mean(elapsed) if elapsed else None,
        }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    with (root / "question_matrix.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["model", "novel", "qi", "qid", "question", "gold_letter", "selected_letter", "correct", "confidence", "tail_chars", "novel_char_coverage", "prompt_tokens", "cached_tokens", "completion_tokens", "elapsed_seconds"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            usage = row.get("usage") or {}
            writer.writerow({**{f: row.get(f) for f in fields}, "prompt_tokens": usage_value(usage, "prompt_tokens"), "cached_tokens": cached_tokens(usage), "completion_tokens": usage_value(usage, "completion_tokens")})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=MODELS, choices=MODELS)
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_minimax_tail_204800")
    parser.add_argument("--base-url", default="https://api.minimaxi.com/v1")
    args = parser.parse_args()

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MINIMAX_API_KEY is required; it is never written to disk")
    cases = merged_cases()
    novels = [str(n) for n in args.novels]
    questions_by_novel = {n: cases[n]["questions"][: args.max_questions or None] for n in novels}
    total = sum(len(questions_by_novel[n]) for n in novels) * len(args.models)
    dashboard = Dashboard(args.out_root, total)
    client = MiniMaxClient(api_key, args.base_url)
    all_rows: list[dict[str, Any]] = []

    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "models": args.models,
        "novels": novels,
        "context_limit": CONTEXT_LIMIT,
        "max_completion_tokens": MAX_COMPLETION,
        "tail_policy": "largest suffix calibrated from provider prompt token usage",
        "api_base": args.base_url,
        "api_key_persisted": False,
    }
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # Keep both models adjacent for a novel. Besides fair ordering, this gives
    # the provider's shared prefix cache the best chance to reuse the long text.
    for novel in novels:
        for model in args.models:
            text = cases[novel]["text"]
            questions = questions_by_novel[novel]
            pending = [(qi, q) for qi, q in enumerate(questions) if load_cached(answer_path(args.out_root, model, novel, qi)) is None]
            cached_rows = [load_cached(answer_path(args.out_root, model, novel, qi)) for qi in range(len(questions))]
            for row in cached_rows:
                if row:
                    all_rows.append(row)
                    dashboard.add(row, f"复用缓存 {model} / 小说 {novel}")
            if not pending:
                continue

            cal_dir = args.out_root / "calibration" / model
            cal_dir.mkdir(parents=True, exist_ok=True)
            cal_path = cal_dir / f"{novel}.json"
            calibration = None
            if cal_path.exists():
                try:
                    candidate = json.loads(cal_path.read_text(encoding="utf-8"))
                    if candidate.get("model") == model and candidate.get("novel") == novel and candidate.get("novel_chars") == len(text):
                        saved_chars = int(candidate.get("tail_chars", 0))
                        if 0 < saved_chars <= len(text):
                            calibration = candidate
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    calibration = None

            if calibration is not None:
                tail_chars = int(calibration["tail_chars"])
                ordered = [(qi, q, None) for qi, q in pending]
            else:
                # Calibrate with the longest question/options so every later request fits.
                cal_qi, cal_q = max(pending, key=lambda item: len(build_user_prompt("", item[1])))
                dashboard.current = f"校准 {model} / 小说 {novel}"
                dashboard.publish()
                tail_chars, raw = calibrate_and_answer(client, model, novel, text, cal_qi, cal_q)
                calibration = {
                    "model": model,
                    "novel": novel,
                    "novel_chars": len(text),
                    "tail_chars": tail_chars,
                    "tail_start_char": len(text) - tail_chars,
                    "coverage": tail_chars / max(len(text), 1),
                    "calibration_qi": cal_qi,
                    "prompt_tokens": usage_value(raw.get("usage") or {}, "prompt_tokens"),
                }
                cal_path.write_text(json.dumps(calibration, ensure_ascii=False, indent=1), encoding="utf-8")
                ordered = [(cal_qi, cal_q, raw)] + [(qi, q, None) for qi, q in pending if qi != cal_qi]
            tail = text[-tail_chars:]

            for qi, q, result in ordered:
                if result is None:
                    result = client.answer(model, tail, q)
                row = finalize_row(model, novel, qi, q, text, tail_chars, result)
                path = answer_path(args.out_root, model, novel, qi)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
                all_rows.append(row)
                dashboard.add(row, f"{model} / 小说 {novel} / q{qi}: {row.get('selected_letter') or '未解析'}")
                print(f"[{model}/{novel}] q{qi} -> {row.get('selected_letter')} correct={row['correct']} prompt={usage_value(row.get('usage') or {}, 'prompt_tokens')} cache={cached_tokens(row.get('usage') or {})}", flush=True)
                write_summary(args.out_root, all_rows)

    write_summary(args.out_root, all_rows)
    print(f"complete: {len(all_rows)}/{total} -> {args.out_root / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
