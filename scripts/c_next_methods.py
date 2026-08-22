"""Next-round option-C methods: citation repair, permutation consistency and a bounded C3."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from c_option_methods import (
    LETTERS,
    NEGATIVE_RE,
    EvidenceContext,
    _call_json,
    _evidence_text,
    _options_text,
    normalize_letter,
    normalize_payload,
    question_type,
    rrf_option_evidence,
)

VERSION = "c-next-v1"


def answer_schema() -> str:
    return (
        '{"selected_letter":"A|B|C|D","confidence":"high|medium|low",'
        '"evidence":{"A":{"support":["c_1"],"contradict":[],"decoy":[]},'
        '"B":{"support":[],"contradict":[],"decoy":[]},'
        '"C":{"support":[],"contradict":[],"decoy":[]},'
        '"D":{"support":[],"contradict":[],"decoy":[]}},'
        '"reason":"brief","needs_more":false,"followup_queries":[]}'
    )


def _norm_text(value: str) -> str:
    return re.sub(r"\W+", "", value, flags=re.UNICODE).lower()


def canonical_citation(value: Any, chunks: list[dict[str, Any]], valid_ids: set[str]) -> str | None:
    """Map IDs, decorated IDs, positions and quote strings back to a supplied chunk."""
    if isinstance(value, dict):
        for key in ("id", "chunk_id", "source_id"):
            if value.get(key):
                hit = canonical_citation(value[key], chunks, valid_ids)
                if hit:
                    return hit
        if value.get("start") is not None:
            try:
                pos = int(value["start"])
                containing = [c for c in chunks if int(c.get("start", -1)) <= pos < int(c.get("end", -1))]
                if containing:
                    return str(containing[-1]["id"])
            except (TypeError, ValueError):
                pass
        value = value.get("quote") or value.get("text") or ""
    text = str(value or "").strip()
    for match in re.findall(r"\bc_\d+\b", text, flags=re.I):
        cid = match.lower()
        if cid in valid_ids:
            return cid
    quote = _norm_text(text)
    if len(quote) < 18:
        return None
    best: tuple[float, str] | None = None
    for chunk in chunks:
        body = _norm_text(str(chunk.get("text", "")))
        if quote in body or (len(body) >= 24 and body in quote):
            return str(chunk["id"])
        # A long prefix is robust to the model appending a paraphrase.
        prefix = quote[: min(80, len(quote))]
        score = len(prefix) / max(len(quote), 1) if prefix and prefix in body else 0.0
        if score and (best is None or score > best[0]):
            best = (score, str(chunk["id"]))
    return best[1] if best else None


def canonicalize_payload(payload: Any, choices: list[str], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Canonicalize every evidence citation before applying the strict normalizer."""
    data = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    raw_ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    valid_ids = {str(c["id"]) for c in chunks}
    cleaned: dict[str, dict[str, list[str]]] = {}
    for letter in LETTERS:
        row = raw_ev.get(letter, {}) if isinstance(raw_ev.get(letter), dict) else {}
        cleaned[letter] = {}
        for kind in ("support", "contradict", "decoy"):
            values = row.get(kind, []) if isinstance(row.get(kind), list) else []
            ids = [canonical_citation(v, chunks, valid_ids) for v in values]
            cleaned[letter][kind] = list(dict.fromkeys(x for x in ids if x))[:4]
    data["evidence"] = cleaned
    return normalize_payload(data, choices, valid_ids)


def _answer_prompt(question: str, choices: list[str], package: dict[str, Any], extra: str = "") -> str:
    mode = "NEGATIVE: explicitly establish which option is false" if NEGATIVE_RE.search(question) else "POSITIVE"
    return (
        f"Question: {question}\nMode: {mode}\n\n{_options_text(choices)}\n\n"
        f"{_evidence_text(package, 12)}\n\n{extra}\n"
        "Evaluate every option independently. Quote supplied chunk IDs; missing evidence is not contradiction. "
        "Do not decide from option order or genre convention. You must return exactly one best-supported selected_letter, "
        "even when confidence is low; never return multiple letters or null. Return strict JSON only:\n" + answer_schema()
    )


def method_c4_citation(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    """C4 with tolerant citation localization and a real grounded-evidence gate."""
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=14)
    prompt = (
        f"Question: {q['question']}\n\n{_options_text(q['choices'])}\n\n{_evidence_text(package, 14)}\n\n"
        "For every option extract explicit support, explicit contradiction and likely decoys. "
        "Use chunk IDs; if necessary return an exact quote or {id, start, quote}. Missing evidence is not contradiction. "
        "Return only the evidence object from this schema:\n" + answer_schema()
    )
    extracted, extraction_error = _call_json(client, "Extract grounded option evidence without choosing by majority.", prompt, 1900)
    extraction = canonicalize_payload(extracted, q["choices"], package["chunks"])
    grounded_count = len(extraction["evidence_ids"])
    answer_prompt = _answer_prompt(
        q["question"],
        q["choices"],
        package,
        "Canonical evidence table:\n" + json.dumps(extraction["evidence"], ensure_ascii=False),
    )
    raw, answer_error = _call_json(client, "Choose only from localized source evidence.", answer_prompt)
    final = canonicalize_payload(raw, q["choices"], package["chunks"])
    final.update(
        {
            "method": "c4_citation_fixed",
            "retrieval": package,
            "extraction_raw": extracted,
            "extraction": extraction,
            "grounded_count": grounded_count,
            "fallback_used": grounded_count == 0,
            "raw": raw,
            "error": "; ".join(x for x in (extraction_error, answer_error) if x),
        }
    )
    return final


def _remap_payload(payload: Any, order: list[int], original_choices: list[str]) -> dict[str, Any]:
    """Map a response over permuted display letters back to original option letters."""
    if not isinstance(payload, dict):
        return {}
    mapped = copy.deepcopy(payload)
    shown_letter = normalize_letter(payload.get("selected_letter"))
    if shown_letter is not None and shown_letter in LETTERS:
        mapped["selected_letter"] = LETTERS[order[LETTERS.index(shown_letter)]]
    raw_ev = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    remapped_ev = {}
    for shown_idx, original_idx in enumerate(order):
        remapped_ev[LETTERS[original_idx]] = raw_ev.get(LETTERS[shown_idx], {})
    mapped["evidence"] = remapped_ev
    return mapped


def permutation_order(q: dict[str, Any]) -> list[int]:
    digest = hashlib.sha1((q.get("qid", "") + q["question"]).encode("utf-8")).digest()
    order = list(range(min(4, len(q["choices"]))))
    # Stable Fisher-Yates, forced away from identity.
    for i in range(len(order) - 1, 0, -1):
        j = digest[i] % (i + 1)
        order[i], order[j] = order[j], order[i]
    if order == list(range(len(order))):
        order = order[1:] + order[:1]
    return order


def _evidence_score(result: dict[str, Any], letter: str | None, negative: bool = False) -> int:
    row = result.get("evidence", {}).get(letter, {}) if letter else {}
    support = len(row.get("support", []))
    contradict = len(row.get("contradict", []))
    return (3 * contradict - 3 * support if negative else 3 * support - 3 * contradict) - len(row.get("decoy", []))


def method_c1_permutation(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=12)
    order = permutation_order(q)
    runs = []
    for label, shown_order in (("original", list(range(4))), ("permuted", order)):
        shown_choices = [q["choices"][i] for i in shown_order]
        raw, error = _call_json(
            client,
            "Answer independently of option position and cite localized source evidence.",
            _answer_prompt(q["question"], shown_choices, package),
        )
        remapped = _remap_payload(raw, shown_order, q["choices"])
        normalized = canonicalize_payload(remapped, q["choices"], package["chunks"])
        runs.append({"label": label, "order": shown_order, "raw": raw, "normalized": normalized, "error": error})
    letters = [r["normalized"]["selected_letter"] for r in runs]
    consistent = letters[0] is not None and letters[0] == letters[1]
    negative = bool(NEGATIVE_RE.search(q["question"]))
    if consistent:
        chosen = runs[0]["normalized"] if _evidence_score(runs[0]["normalized"], letters[0], negative) >= _evidence_score(runs[1]["normalized"], letters[1], negative) else runs[1]["normalized"]
    else:
        valid_runs = [r["normalized"] for r in runs if r["normalized"]["selected_letter"] is not None]
        pool = valid_runs or [r["normalized"] for r in runs]
        chosen = max(pool, key=lambda r: (_evidence_score(r, r["selected_letter"], negative), len(r["evidence_ids"])))
    chosen = copy.deepcopy(chosen)
    if not consistent:
        chosen["confidence"] = "low"
        chosen["needs_more"] = True
    chosen.update(
        {
            "method": "c1_option_permutation",
            "retrieval": package,
            "permutation_runs": runs,
            "permutation_consistent": consistent,
            "error": "; ".join(r["error"] for r in runs if r["error"]),
        }
    )
    return chosen


def _merge_chunks(base: list[dict[str, Any]], additions: list[dict[str, Any]], limit: int = 18) -> list[dict[str, Any]]:
    merged = {str(c["id"]): c for c in base}
    for chunk in additions:
        merged.setdefault(str(chunk["id"]), chunk)
    return list(merged.values())[:limit]


def method_c3_discriminative(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    """Bounded C3: one contrastive pass and at most one targeted follow-up pass."""
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=14)
    raw1, error1 = _call_json(
        client,
        "Compare claims option by option; do not pursue a single early hypothesis.",
        _answer_prompt(
            q["question"],
            q["choices"],
            package,
            "Stage 1: identify the strongest supported option and its strongest competitor. Request at most two discriminative follow-up queries if evidence cannot separate them.",
        ),
        2100,
    )
    first = canonicalize_payload(raw1, q["choices"], package["chunks"])
    selected = first["selected_letter"]
    selected_support = first.get("evidence", {}).get(selected, {}).get("support", []) if selected else []
    explicit_competitor_contradiction = any(
        first["evidence"][letter]["contradict"] for letter in LETTERS if letter != selected
    )
    decisive = bool(selected_support and explicit_competitor_contradiction)
    trace = [{"stage": 1, "raw": raw1, "normalized": first, "visible_evidence_ids": [c["id"] for c in package["chunks"]], "error": error1}]
    final = first
    if not decisive:
        queries = raw1.get("followup_queries", [])[:2] if isinstance(raw1, dict) and isinstance(raw1.get("followup_queries"), list) else []
        if not queries:
            candidates = [selected] if selected else []
            candidates += [letter for letter in LETTERS if letter != selected][:_ensure_two(selected)]
            queries = [q["question"] + " " + q["choices"][LETTERS.index(letter)] for letter in candidates[:2]]
        additions = []
        for query in queries[:2]:
            additions.extend(
                {"id": ctx.chunks[i].id, "start": ctx.chunks[i].start, "end": ctx.chunks[i].end, "text": ctx.chunks[i].text}
                for i in ctx.top_chunks(str(query), 4)
            )
        chunks = _merge_chunks(package["chunks"], additions)
        second_package = {**package, "chunks": chunks}
        raw2, error2 = _call_json(
            client,
            "Resolve only by explicit evidence that distinguishes the leading options.",
            _answer_prompt(
                q["question"],
                q["choices"],
                second_package,
                "Stage 1 canonical evidence:\n" + json.dumps(first["evidence"], ensure_ascii=False) + "\nMake the final contrastive decision.",
            ),
            2200,
        )
        second = canonicalize_payload(raw2, q["choices"], chunks)
        # A follow-up may be less parseable than a grounded first pass. Never
        # discard an earlier localized answer merely because exploration failed.
        if second["selected_letter"] is not None and (second["evidence_ids"] or not first["evidence_ids"]):
            final = second
        trace.append({"stage": 2, "queries": queries[:2], "raw": raw2, "normalized": second, "retained_stage": 2 if final is second else 1, "visible_evidence_ids": [c["id"] for c in chunks], "error": error2})
        package = second_package
    final = copy.deepcopy(final)
    final.update(
        {
            "method": "c3_two_stage_discriminative",
            "retrieval": package,
            "trace": trace,
            "stopped_after_stage": len(trace),
            "error": "; ".join(str(t.get("error", "")) for t in trace if t.get("error")),
        }
    )
    return final


def method_c3_first_stage(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    """The empirically safer C3 variant: one contrastive retrieval/decision pass only."""
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=14)
    raw, error = _call_json(
        client,
        "Compare every option independently and make one contrastive decision.",
        _answer_prompt(
            q["question"],
            q["choices"],
            package,
            "For each option state localized support and contradiction, then select exactly one best-supported answer. This is the final pass; do not request another search.",
        ),
        2200,
    )
    final = canonicalize_payload(raw, q["choices"], package["chunks"])
    normalized_snapshot = copy.deepcopy(final)
    final.update(
        {
            "method": "c3_single_stage_contrastive",
            "retrieval": package,
            "trace": [{"stage": 1, "raw": raw, "normalized": normalized_snapshot, "visible_evidence_ids": [c["id"] for c in package["chunks"]], "error": error}],
            "stopped_after_stage": 1,
            "raw": raw,
            "error": error,
        }
    )
    return final


def _ensure_two(selected: str | None) -> int:
    return 1 if selected else 2


def _candidate_chunks(result: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = result.get("retrieval", {}).get("chunks", [])
    return [c for c in chunks if isinstance(c, dict) and c.get("id") and c.get("text")]


def run_evidence_verifier(
    client: Any,
    q: dict[str, Any],
    candidates: list[dict[str, Any]],
    tail_letter: str | None = None,
) -> dict[str, Any]:
    """Per-instance gate and anonymized verifier over full, canonical source paragraphs."""
    started = time.time()
    chunks_by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for chunk in _candidate_chunks(candidate):
            chunks_by_id.setdefault(str(chunk["id"]), chunk)
    chunks = list(chunks_by_id.values())[:24]
    valid = set(chunks_by_id)
    compact = []
    for candidate in candidates:
        letter = normalize_letter(candidate.get("selected_letter"))
        ids = [str(x) for x in candidate.get("evidence_ids", []) if str(x) in valid]
        compact.append(
            {
                "selected_letter": letter,
                "evidence_ids": ids,
                "evidence": candidate.get("evidence", {}),
                "permutation_consistent": candidate.get("permutation_consistent"),
            }
        )
    perm = next((c for c in candidates if c.get("method") == "c1_option_permutation"), None)
    grounded_letters = [normalize_letter(c.get("selected_letter")) for c in candidates if c.get("evidence_ids")]
    direct_letter = normalize_letter(perm.get("selected_letter")) if perm else None
    gated_direct = bool(
        perm
        and perm.get("permutation_consistent")
        and direct_letter
        and perm.get("evidence_ids")
        and grounded_letters.count(direct_letter) >= 2
    )
    raw: dict[str, Any] = {}
    error = ""
    if gated_direct:
        final = canonicalize_payload(perm, q["choices"], chunks)
        final["reason"] = "Permutation-consistent answer accepted because an independent grounded candidate agrees."
    else:
        order = permutation_order(q)
        shown_choices = [q["choices"][i] for i in order]
        displayed_compact = []
        for candidate in compact:
            item = dict(candidate)
            letter = candidate.get("selected_letter")
            if letter is not None and letter in LETTERS:
                item["selected_letter"] = LETTERS[order.index(LETTERS.index(letter))]
            raw_evidence = candidate.get("evidence", {})
            item["evidence"] = {
                LETTERS[shown_idx]: raw_evidence.get(LETTERS[original_idx], {})
                for shown_idx, original_idx in enumerate(order)
            }
            displayed_compact.append(item)
        displayed_tail = LETTERS[order.index(LETTERS.index(tail_letter))] if tail_letter is not None and tail_letter in LETTERS else None
        evidence_rows = []
        for chunk in chunks:
            evidence_rows.append(f"[{chunk['id']} @ {chunk.get('start', '')}]\n{chunk.get('text', '')}")
        prompt = (
            f"Question: {q['question']}\n\n{_options_text(shown_choices)}\n\n"
            "Anonymized candidate claims (letters use the DISPLAYED ordering and are hints only; verify them):\n"
            + json.dumps(displayed_compact, ensure_ascii=False)
            + f"\nUngrounded tail-window candidate: {displayed_tail or 'none'}\n\nFull source paragraphs:\n"
            + "\n\n".join(evidence_rows)
            + "\n\nFor each displayed option, decide whether a paragraph directly entails or contradicts it. "
            "Ignore candidate counts and confidence labels. Prefer a located quote over agreement. Missing evidence is not contradiction. "
            "Return displayed letters using strict JSON:\n"
            + answer_schema()
        )
        raw, error = _call_json(client, "Act as an evidence verifier, not a voter.", prompt, 2400)
        remapped = _remap_payload(raw, order, q["choices"])
        final = canonicalize_payload(remapped, q["choices"], chunks)
        if final["selected_letter"] is None or not final["evidence_ids"]:
            # Deterministic fallback scores only canonical evidence, never self-reported confidence.
            scores = Counter()
            for candidate in compact:
                letter = candidate["selected_letter"]
                if letter:
                    scores[letter] += 2 * len(candidate["evidence_ids"])
            winner = scores.most_common(1)[0][0] if scores else (tail_letter or "A")
            source = next((c for c in candidates if normalize_letter(c.get("selected_letter")) == winner and c.get("evidence_ids")), {})
            final = canonicalize_payload(source, q["choices"], chunks)
            final["selected_letter"] = winner
            final["selected_text"] = q["choices"][LETTERS.index(winner)]
            final["confidence"] = "low"
            final["needs_more"] = True
            final["reason"] = "Verifier lacked a localized final citation; used canonical-evidence score fallback."
    final = copy.deepcopy(final)
    final.update(
        {
            "method": "evidence_instance_gate",
            "candidates": compact,
            "tail_letter": tail_letter,
            "gated_direct": gated_direct,
            "raw": raw,
            "error": error,
            "question_type": question_type(q["question"]),
            "elapsed_seconds": round(time.time() - started, 3),
            "prompt_version": VERSION,
            "prompt_hash": hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12],
        }
    )
    return final


METHODS = {
    "c4fix": method_c4_citation,
    "c1perm": method_c1_permutation,
    "c3disc": method_c3_discriminative,
    "c3first": method_c3_first_stage,
}


def run_next_method(method: str, client: Any, q: dict[str, Any], graph: dict[str, Any], novel_text: str, mask_char: int | None) -> dict[str, Any]:
    started = time.time()
    ctx = EvidenceContext.build(graph, novel_text, mask_char)
    result = METHODS[method](client, q, ctx)
    result["question_type"] = question_type(q["question"])
    result["masked_at"] = mask_char
    result["mask_policy"] = ctx.mask_policy
    result["elapsed_seconds"] = round(time.time() - started, 3)
    result["prompt_version"] = VERSION
    result["prompt_hash"] = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
    return result
