# Reproducibility guide

All commands are run from `D:\desktop\coding\科研\novel-kg-studio` with the recovered virtual environment. None of these commands rebuilds a graph.

## 1. Freeze and verify graph inputs

```powershell
.\.venv_recovered\Scripts\python.exe scripts\freeze_dqa30_graphs.py
```

This must reproduce `config/dqa30_frozen_graphs.json` with 30 records and unchanged SHA-256, byte size, and modification time. The incomplete batch01 replacements for novels 26--28 are excluded.

## 2. Missing old20 non-graph baselines

```powershell
.\.venv_recovered\Scripts\python.exe scripts\run_dqa30_missing_baselines.py --summary-workers 1
```

The runner is resumable at the per-question level. It executes only B2 whole-book compression and B3 ordinary vector RAG for old20. B1 and Q0 are reused from their frozen local 9B records.

## 3. Pure-graph aggregation and statistics

```powershell
.\.venv_recovered\Scripts\python.exe scripts\aggregate_dqa30_pure_graph_results.py
.\.venv_recovered\Scripts\python.exe scripts\archive_dqa30_answer_records.py
```

G1--G3 are option-order probes over graph evidence, G4 is their deterministic graph-only majority, and G5 is the archived tight graph expansion with `baseline_access=false`. Historical tail-fallback composites are not counted as pure graph methods.

Expected invariants:

- 234 unique questions and 30 novels;
- nine method records per question;
- no invalid selected option and no denominator reduction;
- Q0-hard membership is defined only from the frozen Q0 prediction;
- 15 planned graph-vs-baseline paired comparisons with Holm correction.

## 4. Gold-density audit and figures

```powershell
.\.venv_recovered\Scripts\python.exe scripts\analyze_dqa30_dense_regions.py
.\.venv_recovered\Scripts\python.exe scripts\plot_dqa30_paper_figures.py --manifest paper\generated\novel103_plot_manifest.json
```

Gold annotations are loaded only by the post-hoc audit. The primary density statistic is membership in the undirected simple-graph 2-core. The secondary visual statistic uses degree at least two and normalized spring-layout radius at most 0.45 over five fixed seeds.

## 5. Citation scan

```powershell
.\.venv_recovered\Scripts\python.exe C:\Users\fwj\.codex\skills\citation-verifier\scripts\scan_citations.py paper
```

Every claim-bearing citation should then be checked against the publisher, ACL Anthology, NeurIPS/OpenReview, Microsoft GraphRAG documentation, or the official DetectiveQA repository.

## 6. Compile the paper

The machine has a bundled Tectonic executable:

```powershell
& C:\Users\fwj\.codex\.tmp\bundled-marketplaces\openai-bundled\plugins\latex\bin\tectonic.exe -X compile paper\manuscript.tex --outdir paper\build
```

The manuscript uses the supplied engineering template's A4 single-column geometry and Times New Roman typography. Generated figures and tables are read from `paper/generated`.

## 7. Final integrity check

Run the freeze command again, compare all graph hashes to the initial manifest, then hash the deliverables. Do not stage the graph output directories in Git. Commit scripts, frozen manifests, compact generated tables, paper source, bibliography, and reports only.
