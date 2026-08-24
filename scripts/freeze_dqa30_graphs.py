"""Freeze the 30 reused DetectiveQA graphs without modifying graph artifacts.

The manifest is the sole authority for later evaluation.  It records byte-level
hashes, timestamps, graph sizes, and the known construction cohort.  Running the
script again verifies drift unless --refresh is explicitly supplied.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "config" / "dqa30_frozen_graphs.json"

COHORTS = {
    "legacy20_a": {
        "novels": ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79"],
        "root": ROOT / "outputs" / "four_datasets" / "dqa_qwen_c",
        "graph_builder": "legacy-qwen2.5-7b-c4-pipeline",
        "builder_evidence": "experiment lineage; no per-batch build manifest",
    },
    "legacy20_b": {
        "novels": ["15", "16", "25", "29", "81", "82", "83", "84", "87", "90"],
        "root": ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10",
        "graph_builder": "legacy-qwen2.5-7b-c4-pipeline",
        "builder_evidence": "scripts/build_c_next10_graphs.py defaults; no per-batch build manifest",
    },
    "pass2_v4_9b10": {
        "novels": ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"],
        "root": ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "batch03",
        "graph_builder": "qwen3.5:9b-pass2-v4",
        "builder_evidence": "outputs/four_datasets/dqa30_attention/batch03/build_manifest.json",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def graph_metrics(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    ids = {str(node.get("id")) for node in nodes}
    degree = {node_id: 0 for node_id in ids}
    valid_edges = 0
    self_loops = 0
    dangling = 0
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source not in ids or target not in ids:
            dangling += 1
            continue
        valid_edges += 1
        if source == target:
            self_loops += 1
            degree[source] += 2
        else:
            degree[source] += 1
            degree[target] += 1
    isolates = sum(value == 0 for value in degree.values())
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "valid_edges": valid_edges,
        "dangling_edges": dangling,
        "self_loops": self_loops,
        "isolates_recomputed": isolates,
        "isolate_rate_recomputed": isolates / len(nodes) if nodes else None,
        "edge_node_ratio": len(edges) / len(nodes) if nodes else None,
        "embedded_quality": graph.get("quality"),
    }


def build_manifest() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cohort, spec in COHORTS.items():
        for novel in spec["novels"]:
            if novel in seen:
                raise RuntimeError(f"duplicate novel in frozen set: {novel}")
            seen.add(novel)
            path = spec["root"] / "novels" / novel / "graph.json"
            if not path.is_file():
                raise FileNotFoundError(path)
            graph = json.loads(path.read_text(encoding="utf-8"))
            stat = path.stat()
            records.append(
                {
                    "novel": novel,
                    "cohort": "old20" if cohort.startswith("legacy20") else "new10",
                    "source_batch": cohort,
                    "graph_path": str(path.resolve()),
                    "graph_builder": spec["graph_builder"],
                    "builder_evidence": spec["builder_evidence"],
                    "sha256": sha256(path),
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    **graph_metrics(graph),
                }
            )
    if len(records) != 30:
        raise RuntimeError(f"expected 30 graphs, found {len(records)}")
    return {
        "protocol": "dqa30-frozen-reuse-v1",
        "guard": "Graphs are read-only inputs. Gold annotations are evaluation-only.",
        "cohort_warning": "The old20 and new10 graphs were built by different graph pipelines; pooled results are descriptive.",
        "excluded_partial_rebuilds": [
            str((ROOT / "outputs" / "four_datasets" / "dqa60_single9" / "batch01" / "novels" / novel).resolve())
            for novel in ("26", "27", "28")
        ],
        "records": records,
    }


def verify(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old = {row["novel"]: row for row in previous.get("records", [])}
    errors = []
    for row in current["records"]:
        prior = old.get(row["novel"])
        if prior is None:
            errors.append(f"{row['novel']}: absent from existing manifest")
            continue
        for key in ("graph_path", "sha256", "bytes", "mtime_ns"):
            if prior.get(key) != row.get(key):
                errors.append(f"{row['novel']}: {key} changed")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refresh", action="store_true", help="replace an existing manifest after reporting drift")
    args = parser.parse_args()
    current = build_manifest()
    if args.out.exists():
        previous = json.loads(args.out.read_text(encoding="utf-8"))
        drift = verify(previous, current)
        if drift and not args.refresh:
            raise RuntimeError("frozen graph drift detected:\n" + "\n".join(drift))
        current["previous_manifest_verified"] = not drift
        current["refresh_drift"] = drift
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = args.out.with_suffix(".csv")
    fields = [
        "novel", "cohort", "source_batch", "graph_builder", "graph_path", "sha256", "bytes", "mtime_ns",
        "nodes", "edges", "valid_edges", "dangling_edges", "self_loops", "isolates_recomputed",
        "isolate_rate_recomputed", "edge_node_ratio",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in current["records"])
    print(json.dumps({"manifest": str(args.out), "csv": str(csv_path), "graphs": len(current["records"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
