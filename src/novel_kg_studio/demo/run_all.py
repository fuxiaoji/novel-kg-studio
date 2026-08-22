from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.pipeline import run_pass1, run_pass2
from novel_kg_studio.pipeline.consolidate import consolidate_person_nodes
from novel_kg_studio.pipeline.coref import repair_graph
from novel_kg_studio.pipeline.pass2_graph import pass2_cache_dir
from novel_kg_studio.pipeline.merge import build_graph
from novel_kg_studio.schema import DroppedSpan, KeptSpan
from novel_kg_studio.schema import norm_text
from novel_kg_studio.store import GraphStore
from novel_kg_studio.viz import (
    build_dashboard,
    compute_force_layout,
    deletion_ruler,
    dropped_reason_bar,
    llm_process_panel,
    rag_panel_html,
    text_ruler_density,
    time_over_text_ruler,
    time_ruler_density,
    view_density,
    view_force,
    view_text_time_type,
)

ROOT = Path(__file__).resolve().parents[3]


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_spans(path: Path, cls: type) -> list:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(cls(**json.loads(line)))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Novel KG Studio end-to-end on a long novel.")
    parser.add_argument("--config", default=str(ROOT / "config" / "demo.yaml"))
    parser.add_argument("--no-resume", action="store_true", help="Ignore cached LLM results")
    parser.add_argument("--max-chunks", type=int, default=None, help="Limit chunks per pass (debug)")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--skip-llm", action="store_true", help="Reuse cached pass results without calling the LLM")
    parser.add_argument("--question", default="", help="Override the RAG demo question")
    parser.add_argument("--novel", default="", help="Novel id (103/104/117) from config.novels; overrides paths/output")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.novel:
        novel_cfg = (config.get("novels") or {}).get(args.novel)
        if novel_cfg is None:
            raise ValueError(f"Unknown novel id: {args.novel}")
        config["novel_path"] = novel_cfg["novel_path"]
        config["output_dir"] = novel_cfg["output_dir"]
        anno_path = _resolve(ROOT, str(novel_cfg["anno_path"]))
        if anno_path.exists():
            import json as _json

            anno = _json.loads(anno_path.read_text(encoding="utf-8"))
            questions = anno[0]["questions"] if isinstance(anno, list) else anno.get("questions") or []
            if questions:
                config["question"] = str(questions[0].get("question") or config.get("question") or "")
    novel_path = _resolve(ROOT, str(config["novel_path"]))
    novel_text = novel_path.read_text(encoding="utf-8")
    question = str(args.question or config.get("question") or "")
    out_dir = _resolve(ROOT, str(config["output_dir"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    resume = not args.no_resume
    workers = args.workers or int(config.get("pass1_workers") or 8)
    model_cfg = dict(config.get("model") or {})
    retrieval_cfg = dict(config.get("retrieval") or {})
    viz_cfg = dict(config.get("viz") or {})
    edge_cap = int(viz_cfg.get("max_edge_traces") or 1500)

    started = time.time()
    client = None
    if not args.skip_llm:
        client = LLMClient(
            model=str(model_cfg.get("name") or "deepseek-chat"),
            temperature=float(model_cfg.get("temperature") or 0.0),
            max_tokens=int(model_cfg.get("max_tokens_pass2") or 4000),
            retries=int(model_cfg.get("retries") or 3),
        )

    if args.skip_llm:
        kept = _load_spans(out_dir / "pass1" / "kept.jsonl", KeptSpan)
        dropped = _load_spans(out_dir / "pass1" / "dropped.jsonl", DroppedSpan)
        stats1 = load_json(out_dir / "pass1" / "stats.json") or {}
        size = int((config.get("chunking") or {}).get("size") or 1500)
        cache_dir = pass2_cache_dir(out_dir, kept, size)
        records = [r for r in (load_json(p) for p in sorted(cache_dir.glob("pass2_*.json"))) if r]
    else:
        kept, dropped, stats1 = run_pass1(
            novel_text,
            config=config,
            client=client,
            out_dir=out_dir,
            resume=resume,
            workers=workers,
            max_chunks=args.max_chunks,
        )
        _write_jsonl(out_dir / "pass1" / "kept.jsonl", [s.to_dict() for s in kept])
        _write_jsonl(out_dir / "pass1" / "dropped.jsonl", [d.to_dict() for d in dropped])
        save_json(out_dir / "pass1" / "stats.json", stats1)
        records = run_pass2(
            kept,
            config=config,
            client=client,
            out_dir=out_dir,
            resume=resume,
            workers=workers,
            max_chunks=args.max_chunks,
        )

    kept_by_seq = {s.seq: s for s in kept}
    nodes, edges, merge_stats = build_graph(records, kept_by_seq, max(len(novel_text), 1))

    coref_cfg = dict(config.get("coref") or {})
    consolidation_cfg = dict(config.get("entity_consolidation") or {})
    coref_enabled = bool(coref_cfg.get("enabled", True))
    consolidation_enabled = bool(consolidation_cfg.get("enabled", True))
    if client is None and (coref_enabled or consolidation_enabled):
        try:
            client = LLMClient(
                model=str(model_cfg.get("name") or "deepseek-chat"),
                temperature=float(model_cfg.get("temperature") or 0.0),
                max_tokens=int(model_cfg.get("max_tokens_pass2") or 4000),
                retries=int(model_cfg.get("retries") or 3),
            )
        except Exception as exc:
            print(f"[postprocess] skipped (no LLM client): {exc}")
            client = None
    if coref_enabled and client is not None:
        raw_graph = {"nodes": nodes, "edges": edges}
        save_json(out_dir / "graph_raw.json", raw_graph)
        repaired, coref_stats = repair_graph(
            raw_graph,
            [s.to_dict() for s in kept],
            client,
            out_dir,
            batch_size=int(coref_cfg.get("batch_size") or 8),
            window=int(coref_cfg.get("window") or 1),
            resume=resume,
        )
        nodes, edges = repaired["nodes"], repaired["edges"]
        merge_stats["coref"] = coref_stats

    if consolidation_enabled and client is not None:
        consolidated, consolidation_stats = consolidate_person_nodes(
            {"nodes": nodes, "edges": edges},
            client,
            out_dir,
            cap=int(consolidation_cfg.get("cap") or 150),
            resume=resume,
        )
        nodes, edges = consolidated["nodes"], consolidated["edges"]
        merge_stats["consolidation"] = consolidation_stats

    kept_dicts = [s.to_dict() for s in kept]
    kept_norm = [(row["seq"], norm_text(row["text"])) for row in kept_dicts]
    for node in nodes:
        source_ids: list[int] = []
        for evidence in node.get("evidence", []):
            head = norm_text(evidence)[:40]
            if not head:
                continue
            for seq, row_norm in kept_norm:
                if head in row_norm:
                    source_ids.append(seq)
                    break
        node["source_sentence_ids"] = sorted(set(source_ids))[:10]

    layout = compute_force_layout(nodes, edges)
    for node in nodes:
        coords = layout.get(node["id"])
        if coords is not None:
            node["fx"], node["fy"], node["fz"] = (float(x) for x in coords)
        else:
            node["fx"], node["fy"], node["fz"] = 0.0, 0.0, 0.0

    graph_payload = {
        "novel_chars": len(novel_text),
        "pass1_stats": stats1,
        "merge_stats": merge_stats,
        "nodes": nodes,
        "edges": edges,
    }
    save_json(out_dir / "graph.json", graph_payload)

    store = GraphStore(nodes, edges)
    retrieval = store.retrieve(question, k1=int(retrieval_cfg.get("k1") or 8), k2=int(retrieval_cfg.get("k2") or 12))
    save_json(out_dir / "retrieval.json", retrieval.to_dict())

    fig_deletion = deletion_ruler(kept, dropped, max(len(novel_text), 1))
    fig_reason = dropped_reason_bar(stats1)
    fig_text_ruler = text_ruler_density(kept)
    fig_time_text = time_over_text_ruler(kept)
    fig_time_ruler = time_ruler_density(nodes)
    gold_ids: set[str] = set()
    coverage_text = ""
    gold_analysis_path = out_dir / "gold_analysis.json"
    if gold_analysis_path.exists():
        gold_data = json.loads(gold_analysis_path.read_text(encoding="utf-8"))
        gold_ids = set(gold_data.get("gold_node_ids") or [])
        per_question = gold_data.get("per_question") or []
        if per_question:
            avg1 = sum(r.get("coverage_first", 0) for r in per_question) / len(per_question)
            avg12 = sum(r.get("coverage_first_second", 0) for r in per_question) / len(per_question)
            avg123 = sum(r.get("coverage_first_second_third", 0) for r in per_question) / len(per_question)
            coverage_text = (
                f"金标覆盖：一阶 {avg1:.0%}，一阶+二阶 {avg12:.0%}，+三阶 {avg123:.0%}（官方推理词命中率，7 题均值）；"
            )
    fig_view_a, state_a = view_text_time_type(nodes, edges, edge_cap=edge_cap, gold_ids=gold_ids)
    fig_view_b, state_b = view_force(nodes, edges, layout, edge_cap=edge_cap, gold_ids=gold_ids)
    fig_density = view_density(nodes)
    rag_html = rag_panel_html(
        nodes,
        edges,
        k1=int(retrieval_cfg.get("k1") or 8),
        k2=int(retrieval_cfg.get("k2") or 12),
        preset=question,
    )
    llm_process_html = ""
    diagnostics_html = ""
    question_results_path = out_dir / "question_set_results.json"
    if question_results_path.exists():
        question_rows = json.loads(question_results_path.read_text(encoding="utf-8"))
        llm_process_html = llm_process_panel(question_rows)
        total_third = sum(r["counts"]["third"] for r in question_rows)
        total_info = sum(round(r["third_order_informative_ratio"] * r["counts"]["third"]) for r in question_rows)
        avg_gain = sum(
            r["gold_coverage"]["first_second_third"] - r["gold_coverage"]["first_second"] for r in question_rows
        ) / max(len(question_rows), 1)
        coref_stats = (merge_stats.get("coref") or {})
        diagnostics_html = (
            "<p class=\"hint\">"
            + coverage_text
            + f"指代消解：代词边 {coref_stats.get('pronoun_edges')} 条，重挂 {coref_stats.get('moved_edges')} 条，"
            f"未处理 {coref_stats.get('unmoved_edges')} 条；"
            f"三阶扩散评估：共 {total_third} 个三阶节点，信息型 {total_info} 个（{total_info / max(total_third, 1):.0%}），"
            f"金标覆盖增益平均 {avg_gain:.3f}（结论：默认不需要三阶扩散）。"
            "</p>"
        )
    dashboard_stats = {**stats1, **merge_stats}
    html = build_dashboard(
        fig_deletion=fig_deletion,
        fig_reason=fig_reason,
        fig_text_ruler=fig_text_ruler,
        fig_time_text=fig_time_text,
        fig_time_ruler=fig_time_ruler,
        fig_view_a=fig_view_a,
        state_a=state_a,
        fig_view_b=fig_view_b,
        state_b=state_b,
        fig_density=fig_density,
        rag_html=rag_html,
        llm_process_html=llm_process_html,
        diagnostics_html=diagnostics_html,
        stats=dashboard_stats,
    )
    dashboard_path = out_dir / "dashboard.html"
    dashboard_path.write_text(html, encoding="utf-8")

    elapsed = time.time() - started
    print("=" * 60)
    print(f"novel chars: {len(novel_text)}")
    print(f"pass1: kept={stats1.get('num_kept')} dropped={stats1.get('num_dropped')} "
          f"kept_ratio={float(stats1.get('kept_ratio', 0)):.2f} failed_chunks={stats1.get('failed_chunks')}")
    print(f"graph: nodes={merge_stats.get('num_nodes')} edges={merge_stats.get('num_edges')}")
    print(f"rag first_order={len(retrieval.first_order)} second_order={len(retrieval.second_order)}")
    print(f"dashboard: {dashboard_path} ({dashboard_path.stat().st_size / 1024:.0f} KB)")
    print(f"elapsed: {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
