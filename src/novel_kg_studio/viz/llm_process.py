"""Dashboard panel: step-by-step LLM problem-solving process over the question set."""

from __future__ import annotations

import json
from typing import Any


def llm_process_panel(rows: list[dict[str, Any]]) -> str:
    data = json.dumps(rows, ensure_ascii=False)
    html = """
<div id="llm-proc-wrap">
  <div style="margin:8px 0">
    <span style="font-size:13px">选择示例问题：</span>
    <select id="llm-proc-select" style="min-width:320px;padding:5px"></select>
    <button id="llm-proc-prev" style="padding:5px 10px">← 上一个</button>
    <button id="llm-proc-next" style="padding:5px 10px">下一个 →</button>
  </div>
  <div id="llm-proc-steps"></div>
</div>
<script>
(function(){
  var ROWS = __DATA__;
  var idx = 0;
  var sel = document.getElementById("llm-proc-select");
  ROWS.forEach(function(r, i){
    var o = document.createElement("option");
    o.value = i;
    o.textContent = (i + 1) + ". " + r.question;
    sel.appendChild(o);
  });
  function esc(s){ return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function card(title, body){
    return '<div style="border:1px solid #d8dee4;border-radius:6px;padding:8px 12px;margin:8px 0;background:#fafbfc">'
      + '<b style="font-size:13px">' + title + '</b><div style="margin-top:5px;font-size:13px;line-height:1.55">' + body + '</div></div>';
  }
  function chips(arr){
    if(!arr || !arr.length) return "（无）";
    return arr.map(function(x){ return esc(x); }).join(" → ");
  }
  function render(){
    var r = ROWS[idx];
    if(!r) return;
    var mask = Math.round((r.mask || 1) * 100);
    var cov = r.gold_coverage || {};
    var info = Math.round((r.third_order_informative_ratio || 0) * 100);
    var steps = "";
    steps += card("① 输入问题（掩码：只允许看到文本位置 ≤ " + mask + "%）", esc(r.question));
    steps += card("② LLM 查询理解 / 指代消解", esc(r.interpretation)
      + "<br>实体目标：" + chips(r.entity_targets));
    steps += card("③ LLM 扩展查询", esc(r.expanded_query));
    steps += card("④ 一阶线索（BM25+扩展+目标加权）", chips(r.first_order));
    steps += card("⑤ 二阶图谱扩散（沿关系边）", chips(r.second_order));
    steps += card("⑥ 三阶评估（信息型 " + info + "%，金标覆盖 2阶→3阶：" + (cov.first_second || 0) + " → " + (cov.first_second_third || 0) + "）",
      chips((r.third_order || []).slice(0, 10)));
    steps += card("⑦ LLM 基于可见线索作答", esc(r.answer));
    document.getElementById("llm-proc-steps").innerHTML = steps;
  }
  sel.onchange = function(){ idx = parseInt(sel.value); render(); };
  document.getElementById("llm-proc-prev").onclick = function(){
    idx = (idx + ROWS.length - 1) % ROWS.length; sel.value = idx; render();
  };
  document.getElementById("llm-proc-next").onclick = function(){
    idx = (idx + 1) % ROWS.length; sel.value = idx; render();
  };
  render();
})();
</script>
"""
    return html.replace("__DATA__", data)

