from __future__ import annotations

import json
from typing import Any

import plotly.graph_objects as go


def _figure_html(fig: go.Figure, div_id: str, include_js: str | bool) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=include_js, div_id=div_id)


SLIDER_JS = """
function wireTimeSlider(divId, state, sliderId, playId){
  var el = document.getElementById(divId);
  var slider = document.getElementById(sliderId);
  var play = document.getElementById(playId);
  function update(t){
    var eVis = state.edge_traces.map(function(x){ return x.t <= t; });
    var eIdx = state.edge_traces.map(function(x){ return x.idx; });
    Plotly.restyle(el, {visible: eVis}, eIdx);
    state.node_traces.forEach(function(nt){
      Plotly.restyle(el, {"marker.opacity": nt.times.map(function(v){ return v <= t ? 1 : 0.05; })}, [nt.idx]);
    });
  }
  slider.oninput = function(){ update(parseFloat(slider.value)); };
  var timer = null;
  play.onclick = function(){
    if(timer){ clearInterval(timer); timer = null; play.textContent = "播放"; return; }
    play.textContent = "暂停";
    timer = setInterval(function(){
      var v = parseFloat(slider.value) + 0.05;
      if(v > 1) v = 0;
      slider.value = v;
      update(v);
    }, 200);
  };
  update(1.0);
}
"""


def build_dashboard(
    *,
    fig_deletion: go.Figure,
    fig_reason: go.Figure,
    fig_text_ruler: go.Figure,
    fig_time_text: go.Figure,
    fig_time_ruler: go.Figure,
    fig_view_a: go.Figure,
    state_a: dict[str, Any],
    fig_view_b: go.Figure,
    state_b: dict[str, Any],
    fig_density: go.Figure,
    rag_html: str,
    llm_process_html: str = "",
    diagnostics_html: str = "",
    stats: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    parts.append(
        """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Novel KG Studio</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#222}
header{background:#1f2d3d;color:#fff;padding:14px 24px}
header h1{margin:0;font-size:20px}
header p{margin:4px 0 0;font-size:13px;opacity:.85}
nav{background:#fff;border-bottom:1px solid #e2e5e9;padding:8px 24px;position:sticky;top:0;z-index:5}
nav a{margin-right:16px;color:#2c7be5;text-decoration:none;font-size:14px}
section{background:#fff;margin:16px 24px;padding:16px 20px;border:1px solid #e2e5e9;border-radius:8px}
section h2{margin:0 0 6px;font-size:16px;color:#1f2d3d}
section p.hint{margin:0 0 10px;font-size:13px;color:#666}
button{cursor:pointer}
</style>
</head>
<body>
<header>
  <h1>Novel KG Studio — 长篇小说 → 知识图谱 + RAG + 3D 动态可视化</h1>
  <p>两遍 LLM 建图：先过滤文学性内容并按时间排序，再建人物/线索/地点/时间/事件图谱</p>
</header>
<nav>
  <a href="#step1">① 删减</a>
  <a href="#ruler-text">② 文本尺密度</a>
  <a href="#ruler-time-text">③ 时间×文本尺</a>
  <a href="#ruler-time">④ 时间尺密度</a>
  <a href="#view3d">⑤ 全图 3D</a>
  <a href="#rag">⑥ RAG 检索 3D</a>
  <a href="#llm-proc">⑦ LLM 解题示例</a>
  <a href="#workflow-info">⑧ 工作流诊断</a>
</nav>
"""
    )

    stats_html = ""
    if stats:
        kept_ratio = f"{100.0 * float(stats.get('kept_ratio', 0)):.0f}%"
        stats_html = (
            f'<p class="hint">小说字符数 {stats.get("novel_chars")}；第一遍保留 {stats.get("num_kept")} 条/'
            f'{stats.get("kept_chars")} 字符（{kept_ratio}），删除 {stats.get("num_dropped")} 条；'
            f'图谱节点 {stats.get("num_nodes")}、关系 {stats.get("num_edges")}。</p>'
        )

    parts.append(f"<section id=\"step1\"><h2>① 第一步：保留 vs 删除</h2>{stats_html}")
    parts.append(_figure_html(fig_deletion, "fig-deletion", include_js="cdn"))
    parts.append(_figure_html(fig_reason, "fig-reason", include_js=False))
    parts.append("</section>")

    parts.append("<section id=\"ruler-text\"><h2>② 事件在文本尺（0–1）上的分布</h2>")
    parts.append("<p class=\"hint\">尺子代表整本小说文本均匀铺开（0–1 米），柱高为该位置的事件量；低谷即稀疏区。</p>")
    parts.append(_figure_html(fig_text_ruler, "fig-text-ruler", include_js=False))
    parts.append("</section>")

    parts.append("<section id=\"ruler-time-text\"><h2>③ 时间在文本尺（0–1）上的分布（故事时间轴）</h2>")
    parts.append("<p class=\"hint\">一条水平时间带铺在文本尺（0–1）上：颜色=故事时间（第 N 小时，从故事开始算）。刻度线标出每个整小时在文本尺上的起始位置，虚线=第几天；悬停色带可读任意文本位置的精确小时数。</p>")
    parts.append(_figure_html(fig_time_text, "fig-time-text", include_js=False))
    parts.append("</section>")

    parts.append("<section id=\"ruler-time\"><h2>④ 线索节点在小说时间尺（0–1）上的分布</h2>")
    parts.append("<p class=\"hint\">尺子代表小说历时均匀铺开（0–1 米），柱高为该时间段的线索节点数；高峰即稠密区。</p>")
    parts.append(_figure_html(fig_time_ruler, "fig-time-ruler", include_js=False))
    parts.append("</section>")

    parts.append("<section id=\"view3d\"><h2>⑤ 全图 3D 动态视图</h2>")
    parts.append("<p class=\"hint\">拖动滑块或点击播放，按小说时间显示图谱；拖动 3D 场景可旋转查看。</p>")
    parts.append(_figure_html(fig_view_a, "fig-view-a", include_js=False))
    parts.append("<div style=\"margin:4px 0\"><input id=\"slider-a\" type=\"range\" min=\"0\" max=\"1\" step=\"0.05\" value=\"1\" style=\"width:70%\"> <button id=\"play-a\">播放</button></div>")
    parts.append(_figure_html(fig_view_b, "fig-view-b", include_js=False))
    parts.append("<div style=\"margin:4px 0\"><input id=\"slider-b\" type=\"range\" min=\"0\" max=\"1\" step=\"0.05\" value=\"1\" style=\"width:70%\"> <button id=\"play-b\">播放</button></div>")
    parts.append(_figure_html(fig_density, "fig-density", include_js=False))
    parts.append("</section>")

    parts.append("<section id=\"rag\"><h2>⑥ RAG 检索：题目 → 一阶线索 → 二阶扩散</h2>")
    parts.append("<p class=\"hint\">检索在图谱上进行：先 BM25 命中与题目相关的线索节点（一阶），再沿关系边扩散到相关线索（二阶）。</p>")
    parts.append(rag_html)
    parts.append("</section>")

    parts.append("<section id=\"llm-proc\"><h2>⑦ LLM 解题示例：查询理解 → 检索 → 作答</h2>")
    parts.append("<p class=\"hint\">完整展示 LLM 如何运用这套框架解题：先对问题做指代消解与查询扩展，再在图谱上检索一/二阶线索，最后基于可见线索作答（含掩码位置）。</p>")
    parts.append(llm_process_html or "<p class=\"hint\">尚未生成问题集结果，先运行 scripts/run_question_set.py。</p>")
    parts.append("</section>")

    if diagnostics_html:
        parts.append("<section id=\"workflow-info\"><h2>⑧ 工作流诊断</h2>")
        parts.append(diagnostics_html)
        parts.append("</section>")

    parts.append(
        "<script>"
        + SLIDER_JS
        + "</script><script>"
        + f"wireTimeSlider('fig-view-a', {json.dumps(state_a, ensure_ascii=False)}, 'slider-a', 'play-a');"
        + f"wireTimeSlider('fig-view-b', {json.dumps(state_b, ensure_ascii=False)}, 'slider-b', 'play-b');"
        + "</script>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)
