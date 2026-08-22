"""Append a research-progress panel (gold nodes in 3D, leaderboard, LLM demos, method explainers) to dashboard.html."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from judge_loop import merged_cases  # noqa: E402

OUT = ROOT / "outputs" / "demo"
DASH = OUT / "dashboard.html"
DQA = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
NOVEL_TXT = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"
ANNO = ROOT.parent / "datasets" / "external" / "detectiveqa" / "anno_data_en" / "AIsup_anno" / "103.json"


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    nodes = graph["nodes"]
    edges = graph["edges"]
    novel = NOVEL_TXT.read_text(encoding="utf-8")
    para_pat = re.compile(r"(?ms)^\[(\d+)\]\s*(.*?)(?=^\[\d+\]\s*|\Z)")
    paras = {int(m.group(1)): m.group(2).strip() for m in para_pat.finditer(novel)}
    anno = json.loads(ANNO.read_text(encoding="utf-8"))[0]
    gold_paras: dict[int, str] = {}
    for q in anno["questions"]:
        for cp in q.get("clue_position") or []:
            if cp >= 0 and cp in paras:
                gold_paras[cp] = paras[cp]
    gold_norm = [norm(t) for t in gold_paras.values()]
    # gold nodes: evidence contained in any gold paragraph
    gold_ids = set()
    for n in nodes:
        if any(ev and any(g in norm(ev) or norm(ev) in g for g in gold_norm) for ev in n.get("evidence", [])):
            gold_ids.add(n["id"])
    by_id = {n["id"]: n for n in nodes}
    # central dense region: degree >= 75th percentile AND within 0.45 radius of centroid
    degs = sorted(n.get("degree", 0) for n in nodes)
    p75 = degs[int(len(degs) * 0.75)]
    cx = sum(n.get("fx", 0) for n in nodes) / len(nodes)
    cy = sum(n.get("fy", 0) for n in nodes) / len(nodes)
    cz = sum(n.get("fz", 0) for n in nodes) / len(nodes)
    gold_center = []
    for nid in gold_ids:
        n = by_id[nid]
        d = ((n.get("fx", 0) - cx) ** 2 + (n.get("fy", 0) - cy) ** 2 + (n.get("fz", 0) - cz) ** 2) ** 0.5
        if n.get("degree", 0) >= p75 and d <= 0.45:
            gold_center.append(nid)
    dense_total = []
    for n in nodes:
        d = ((n.get("fx", 0) - cx) ** 2 + (n.get("fy", 0) - cy) ** 2 + (n.get("fz", 0) - cz) ** 2) ** 0.5
        if n.get("degree", 0) >= p75 and d <= 0.45:
            dense_total.append(n["id"])
    # pick a showcase gold node (highest degree among gold)
    show_id = max(gold_ids, key=lambda i: by_id[i].get("degree", 0)) if gold_ids else None
    show = by_id.get(show_id, {})
    show_para = ""
    if show.get("evidence"):
        ev0 = show["evidence"][0]
        idx = novel.find(ev0)
        if idx >= 0:
            show_para = novel[max(0, idx - 150) : idx + 350].replace("\n", " ")
    show_neighbors = []
    for e in edges:
        if e["source"] == show_id and e["target"] in by_id:
            show_neighbors.append((e["type"], by_id[e["target"]].get("name", "?"), e.get("evidence", "")[:80]))
        elif e["target"] == show_id and e["source"] in by_id:
            show_neighbors.append((e["type"], by_id[e["source"]].get("name", "?"), e.get("evidence", "")[:80]))
    # answers for 103 Q0 from v4 / v5.2 / v7
    cases = merged_cases()
    q0 = cases["103"]["questions"][0]
    def load_ans(tag: str) -> str:
        k = hashlib.sha1(f"103|0|v1|{tag}".encode("utf-8")).hexdigest()[:10]
        p = DQA / "novels" / "103" / f"graph_{k}.json"
        return json.loads(p.read_text(encoding="utf-8")).get("answer", "") if p.exists() else "(missing)"
    ans_v4 = load_ans("r4")
    ans_v52 = load_ans("r5b")
    ans_v7 = load_ans("r7")
    trace_v5 = []
    tp = DQA / "novels" / "103" / f"trace_graph_{hashlib.sha1('103|0|v1|r5b'.encode()).hexdigest()[:10]}.json"
    if tp.exists():
        tr = json.loads(tp.read_text(encoding="utf-8"))
        for s in tr.get("steps", [])[:6]:
            act = json.dumps(s.get("action", {}), ensure_ascii=False)[:140]
            trace_v5.append(act)
    # leaderboard data
    def acc(fn: str) -> tuple[int, int]:
        p = DQA / fn
        if not p.exists():
            return 0, 0
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = d["results"]
        return sum(1 for r in rows if r.get("graph", {}).get("correct")), len(rows)
    lb = [
        ("v4 一次性检索+原文展开", acc("results_v4_59_judged.json")),
        ("v5.1 agentic 图推理", acc("results_graph_r5_judged.json")),
        ("v5.2 agentic+关系遍历", acc("results_graph_r5b_judged.json")),
        ("v7 按选项抽证+作答", acc("results_graph_r7_judged.json")),
        ("投票集成(3变体)", acc("results_ensemble_v457_judged.json")),
    ]
    go = json.loads((DQA / "results_goldonly_7novels_judged.json").read_text(encoding="utf-8")).get("summary", {})
    # build appended HTML
    gold_triples = "[[%s]]" % ",".join(f"[{by_id[i].get('fx',0)},{by_id[i].get('fy',0)},{by_id[i].get('fz',0)}]" for i in list(gold_ids)[:300])
    node_triples = "[[%s]]" % ",".join(f"[{n.get('fx',0)},{n.get('fy',0)},{n.get('fz',0)}]" for n in nodes[:1500])
    edge_lines = []
    drawn = set()
    for e in edges:
        if e["source"] in gold_ids or e["target"] in gold_ids:
            if (e["source"], e["target"]) in drawn:
                continue
            drawn.add((e["source"], e["target"]))
            a, b = by_id.get(e["source"]), by_id.get(e["target"])
            if a and b:
                edge_lines.append((a.get("fx",0), a.get("fy",0), a.get("fz",0), b.get("fx",0), b.get("fy",0), b.get("fz",0)))
        if len(edge_lines) >= 400:
            break
    edges_js = "[[%s]]" % ",".join(f"[{e[0]},{e[1]},{e[2]},{e[3]},{e[4]},{e[5]}]" for e in edge_lines[:300])
    lb_rows = "\n".join(f"<tr><td>{name}</td><td>{ok}/{total}</td><td>{ok/total*100:.1f}%</td></tr>" for name, (ok, total) in lb if total)
    neigh_rows = "\n".join(f"<tr><td>{t}</td><td>{nm}</td><td>{ev}</td></tr>" for t, nm, ev in show_neighbors[:8])
    trace_rows = "\n".join(f"<tr><td>step {i}</td><td>{a}</td></tr>" for i, a in enumerate(trace_v5))
    html = f"""
<section id="research">
  <h2>研究进展（7 本 59 题 · DeepSeek 外部裁判）</h2>
  <table border="1" cellpadding="6" style="border-collapse:collapse;font-size:13px">
    <tr><th>方法</th><th>正确/59</th><th>正确率</th></tr>
    {lb_rows}
    <tr><td><b>金标全喂（上限）</b></td><td>{go.get('masked','?')}</td><td>-</td></tr>
  </table>
  <p class="hint">结论：7B(Q3) 金标上限 ≈51%；最佳自动方法（投票集成）44.1%，已达上限的 87%。代理循环/加chunk/32k 均未带来增益，按选项抽证+投票有效。</p>
</section>
<section id="gold3d">
  <h2>金标节点示例（小说 103 · 全图 3D 力导向）</h2>
  <p class="hint">红色 = 金标节点（其证据落在官方金标线索段落 533/539/620/1220 等）；蓝色 = 其余节点；红线 = 与金标节点相连的关系边。</p>
  <div id="fig-gold3d" style="height:600px"></div>
  <h3>统计</h3>
  <table border="1" cellpadding="6" style="border-collapse:collapse;font-size:13px">
    <tr><td>总节点</td><td>{len(nodes)}</td><td>总关系边</td><td>{len(edges)}</td></tr>
    <tr><td>金标节点</td><td>{len(gold_ids)}</td><td>占比</td><td>{len(gold_ids)/len(nodes)*100:.1f}%</td></tr>
    <tr><td>金标节点位于中心稠密区（度≥{p75} 且距质心≤0.45）</td><td colspan="3">{len(gold_center)} / {len(gold_ids)}</td></tr>
    <tr><td>全部节点位于中心稠密区</td><td colspan="3">{len(dense_total)} / {len(nodes)} = {len(dense_total)/len(nodes)*100:.1f}%</td></tr>
  </table>
  <h3>示例金标节点：{show.get('name','?')}（{show.get('type','?')}）</h3>
  <p><b>描述：</b>{show.get('description','')}</p>
  <p><b>证据片段：</b>{' | '.join(show.get('evidence', [])[:2])[:240]}</p>
  <p><b>出自原文段落：</b>{show_para[:380]}</p>
  <p><b>显著度：</b>{show.get('salience','?')}　<b>度：</b>{show.get('degree','?')}</p>
  <table border="1" cellpadding="6" style="border-collapse:collapse;font-size:12px">
    <tr><th>关系类型</th><th>邻居</th><th>边证据</th></tr>
    {neigh_rows}
  </table>
</section>
<section id="llm-demo">
  <h2>三版 LLM 答题过程（小说 103 Q0：How did the killer leave the scene?）</h2>
  <table border="1" cellpadding="6" style="border-collapse:collapse;font-size:13px">
    <tr><th>版本</th><th>机制</th><th>答案</th></tr>
    <tr><td>v4</td><td>一次性检索：图节点证据+原文段落展开，一次作答</td><td>{ans_v4[:90]}</td></tr>
    <tr><td>v5.2</td><td>agentic：每步输出 lookup/traverse/expand 动作，沿关系边扩散</td><td>{ans_v52[:90]}</td></tr>
    <tr><td>v7</td><td>按选项抽证：每个选项抽取支持/反驳证据句，再作答</td><td>{ans_v7[:90]}</td></tr>
  </table>
  <h3>v5.2 的思考轨迹（trace）</h3>
  <table border="1" cellpadding="6" style="border-collapse:collapse;font-size:12px">
    <tr><th>步</th><th>动作</th></tr>
    {trace_rows}
  </table>
</section>
<section id="methods">
  <h2>四个方法实现原理</h2>
  <ul style="font-size:13px;line-height:1.7">
    <li><b>v4</b>：检索规划(plan_search,HyDE) → 图打分取一/二阶节点 → BM25 取句子 → 把节点证据定位回原文段落展开（≤6 段）→ 一次 LLM 作答。</li>
    <li><b>v5.1/5.2 agentic</b>：每轮把当前笔记+未访问邻居（带关系类型）给 LLM，LLM 输出 JSON 动作(lookup 节点 / traverse 关系 / expand 原文) → 代码执行并把结果反馈进下一轮 → 最多 6 步 → 汇总作答。</li>
    <li><b>v7 按选项抽证</b>：实体定位 chunk（图节点名→所在原文块）→ 抽取器按每个选项引述支持/反驳的逐字句（做原文 grounding 校验）→ 基于分选项证据作答。</li>
    <li><b>投票集成</b>：v4/v5.2/v7 各自作答取选项字母，多数票胜出，平局取 v7；用多数方的完整答案文本送判。</li>
  </ul>
</section>
<script>
  if (typeof Plotly === 'undefined') {{
    document.getElementById('fig-gold3d').innerHTML = '<p style="color:#b03a2e">Plotly 未能加载（CDN 被拦截），3D 图暂不可用；统计信息仍可查看。</p>';
  }} else {{
  var goldPts = {gold_triples};
  var nodePts = {node_triples};
  var edgePts = {edges_js};
  var traces = [];
  traces.push({{type:'scatter3d', mode:'markers', x:nodePts.map(p=>p[0]), y:nodePts.map(p=>p[1]), z:nodePts.map(p=>p[2]), marker:{{size:2,color:'#9db4d6',opacity:0.45}}, name:'普通节点'}});
  traces.push({{type:'scatter3d', mode:'markers', x:goldPts.map(p=>p[0]), y:goldPts.map(p=>p[1]), z:goldPts.map(p=>p[2]), marker:{{size:7,color:'#e74c3c'}}, name:'金标节点'}});
  edgePts.forEach(function(e){{
    traces.push({{type:'scatter3d', mode:'lines', x:[e[0],e[3]], y:[e[1],e[4]], z:[e[2],e[5]], line:{{color:'rgba(231,76,60,0.35)',width:1}}, hoverinfo:'skip', showlegend:false}});
  }});
  Plotly.newPlot('fig-gold3d', traces, {{title:'全图力导向 · 金标节点高亮', showlegend:true, margin:{{l:0,r:0,t:40,b:0}}}}, {{displayModeBar:false}});
  }}
</script>
"""
    backup = OUT / "dashboard_backup.html"
    if not backup.exists():
        backup.write_bytes(DASH.read_bytes())
    content = DASH.read_text(encoding="utf-8")
    if 'id="research"' in content:
        content = content.split('<section id="research">')[0]
    if "</body>" in content:
        content = content.replace("</body>", html + "\n</body>")
    else:
        content = content.rstrip() + "\n" + html + "\n"
    DASH.write_text(content, encoding="utf-8")
    print("gold nodes:", len(gold_ids), "| center:", len(gold_center), "| show:", show.get("name"))
    print("dashboard updated:", DASH)


if __name__ == "__main__":
    main()
