from __future__ import annotations

import json
from typing import Any

from ..schema import TYPE_LAYER


def rag_panel_html(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, k1: int = 8, k2: int = 12, preset: str = "") -> str:
    compact_nodes = []
    for n in nodes:
        compact_nodes.append(
            {
                "id": n["id"],
                "name": n["name"],
                "type": n["type"],
                "aliases": n.get("aliases", [])[:3],
                "ev": n.get("evidence", [])[:2],
                "x": round(float(n.get("text_pos", 0.5)), 4),
                "y": round(float(n.get("time_pos", 0.5)), 4),
                "z": float(TYPE_LAYER.get(n["type"], 0)),
                "fx": round(float(n.get("fx", 0.0)), 4),
                "fy": round(float(n.get("fy", 0.0)), 4),
                "fz": round(float(n.get("fz", 0.0)), 4),
                "day": n.get("day"),
                "degree": n.get("degree", 0),
            }
        )
    compact_edges = [
        {
            "s": e["source"],
            "t": e["target"],
            "type": e["type"],
            "ev": str(e.get("evidence") or "")[:160],
            "conf": round(float(e.get("confidence") or 0.9), 3),
        }
        for e in edges
    ]
    data_json = json.dumps({"nodes": compact_nodes, "edges": compact_edges}, ensure_ascii=False)
    preset_escaped = str(preset or "").replace("\\", "\\\\").replace('"', '\\"')

    html = """
<div id="rag-wrap">
  <div style="margin:10px 0">
    <input id="rag-q" style="width:62%;padding:6px;font-size:14px" placeholder="输入题目，检索一阶/二阶线索">
    <button id="rag-run" style="padding:6px 14px">检索</button>
    <button id="rag-preset" style="padding:6px 14px">预设题目</button>
  </div>
  <div id="rag-info" style="margin:8px 0;font-size:13px;color:#333"></div>
  <div style="display:flex;gap:10px;flex-wrap:wrap">
    <div style="flex:1;min-width:430px"><b>坐标视图（文本×时间×类型）</b><div id="rag-txt" style="height:540px"></div></div>
    <div style="flex:1;min-width:430px"><b>力导向视图</b><div id="rag-force" style="height:540px"></div></div>
  </div>
  <div id="rag-detail" style="margin-top:10px;border:1px solid #ccc;border-radius:6px;padding:8px;min-height:60px;font-size:13px;background:#fafafa">
    点击节点查看线索证据（一阶=红点，二阶=橙点，边标签为关系类型）
  </div>
</div>
<script>
(function(){
  var G = __DATA__;
  var K1 = __K1__;
  var K2 = __K2__;
  var PRESET = "__PRESET__";
  var byId = {};
  var idxAll = {};
  G.nodes.forEach(function(n, i){ byId[n.id] = n; idxAll[n.id] = i; });
  var adj = {};
  G.edges.forEach(function(e){
    (adj[e.s] = adj[e.s] || []).push({n: e.t, e: e});
    (adj[e.t] = adj[e.t] || []).push({n: e.s, e: e});
  });
  function tok(s){ return (s || "").toLowerCase().match(/[a-z0-9\u4e00-\u9fff]+/g) || []; }
  var docTokens = G.nodes.map(function(n){
    return tok([n.name].concat(n.aliases || []).concat(n.ev || []).join(" "));
  });
  var df = {}, idf = {};
  docTokens.forEach(function(ts){
    var seen = {};
    ts.forEach(function(t){ if(!seen[t]){ seen[t] = 1; df[t] = (df[t] || 0) + 1; } });
  });
  Object.keys(df).forEach(function(t){
    idf[t] = Math.log((G.nodes.length - df[t] + 0.5) / (df[t] + 0.5) + 1);
  });
  function bm25(q){
    var qs = tok(q);
    var scores = new Array(G.nodes.length).fill(0);
    qs.forEach(function(t){
      var w = idf[t];
      if(!w) return;
      docTokens.forEach(function(ts, i){
        var f = 0;
        ts.forEach(function(x){ if(x === t) f++; });
        if(f) scores[i] += w * f * 2.5 / (f + 1.5);
      });
    });
    return scores;
  }
  function coords(n, mode){ return mode === "force" ? [n.fx, n.fy, n.fz] : [n.x, n.y, n.z]; }
  function render(divId, firstIds, secondIds, mode){
    var ids = firstIds.slice();
    secondIds.forEach(function(i){ if(ids.indexOf(i) < 0) ids.push(i); });
    var firstSet = {}, secondSet = {};
    firstIds.forEach(function(i){ firstSet[i] = 1; });
    secondIds.forEach(function(i){ secondSet[i] = 1; });
    var idxById = {};
    ids.forEach(function(i, pos){ idxById[G.nodes[i].id] = pos; });
    var traces = [];
    var types = ["person","location","time_anchor","clue_object","event","evidence_sentence"];
    var colors = {"person":"#4e79a7","location":"#59a14f","time_anchor":"#b6992d","clue_object":"#f28e2b","event":"#e15759","evidence_sentence":"#b07aa1"};
    types.forEach(function(tp){
      var pts = [], marks = [];
      ids.forEach(function(i){
        var n = G.nodes[i];
        if(n.type !== tp) return;
        pts.push(n);
        marks.push(firstSet[i] ? 1 : (secondSet[i] ? 2 : 0));
      });
      if(!pts.length) return;
      traces.push({
        type: "scatter3d", mode: "markers+text",
        x: pts.map(function(n){ return coords(n, mode)[0]; }),
        y: pts.map(function(n){ return coords(n, mode)[1]; }),
        z: pts.map(function(n){ return coords(n, mode)[2]; }),
        text: pts.map(function(n){ return n.name; }),
        textposition: "top center", textfont: {size: 9},
        customdata: pts.map(function(n){ return idxAll[n.id]; }),
        marker: {
          size: pts.map(function(n, i){ return marks[i] === 1 ? 12 : (marks[i] === 2 ? 8 : 6); }),
          color: pts.map(function(n, i){ return marks[i] === 1 ? "#d62728" : (marks[i] === 2 ? "#ff7f0e" : "#cccccc"); }),
          line: {width: 1, color: "#333"}
        },
        hovertemplate: "%{customdata}"
      });
    });
    G.edges.forEach(function(e){
      var ai = idxById[e.s], bi = idxById[e.t];
      if(ai === undefined || bi === undefined) return;
      var a = G.nodes[ids[ai]], b = G.nodes[ids[bi]];
      var ca = coords(a, mode), cb = coords(b, mode);
      traces.push({
        type: "scatter3d", mode: "lines",
        line: {color: "#999", width: 2},
        x: [ca[0], cb[0], null], y: [ca[1], cb[1], null], z: [ca[2], cb[2], null],
        hoverinfo: "skip"
      });
      traces.push({
        type: "scatter3d", mode: "text",
        x: [(ca[0] + cb[0]) / 2], y: [(ca[1] + cb[1]) / 2], z: [(ca[2] + cb[2]) / 2],
        text: [e.type], textfont: {size: 8, color: "#666"},
        hoverinfo: "skip"
      });
    });
    var layout = {
      height: 540, margin: {l: 0, r: 0, t: 0, b: 0},
      scene: {
        xaxis: {title: mode === "force" ? "fx" : "文本位置"},
        yaxis: {title: mode === "force" ? "fy" : "小说时间"},
        zaxis: {title: mode === "force" ? "fz" : "类型层"}
      }
    };
    Plotly.react(divId, traces, layout, {displayModeBar: false});
  }
  function showDetail(nodeIdx){
    var n = G.nodes[nodeIdx];
    if(!n) return;
    var html = "<b>" + n.name + "</b> (" + n.type + ")<br>" +
      "day=" + (n.day === null || n.day === undefined ? "?" : n.day) +
      " | degree=" + n.degree +
      " | 文本位置=" + n.x.toFixed(3) + " | 时间位置=" + n.y.toFixed(3) + "<br>" +
      (n.aliases && n.aliases.length ? "别名: " + n.aliases.join(", ") + "<br>" : "") +
      "证据:<br>" + (n.ev && n.ev.length ? n.ev.map(function(e){ return "&bull; " + e; }).join("<br>") : "（无）");
    document.getElementById("rag-detail").innerHTML = html;
  }
  function bindClick(){
    ["rag-txt", "rag-force"].forEach(function(divId){
      var el = document.getElementById(divId);
      el.on("plotly_click", function(ev){
        var pt = ev.points && ev.points[0];
        if(pt && pt.customdata !== undefined) showDetail(Number(pt.customdata));
      });
    });
  }
  function run(){
    var q = document.getElementById("rag-q").value.trim();
    if(!q) return;
    var scores = bm25(q);
    var order = G.nodes.map(function(n, i){ return i; }).sort(function(a, b){ return scores[b] - scores[a]; });
    var first = [];
    order.forEach(function(i){ if(scores[i] > 0 && first.length < K1) first.push(i); });
    var firstSet = {};
    first.forEach(function(i){ firstSet[i] = 1; });
    var secondScores = {};
    first.forEach(function(i){
      var n = G.nodes[i];
      (adj[n.id] || []).forEach(function(link){
        var j = idxAll[link.n];
        if(j === undefined || firstSet[j]) return;
        if(secondScores[j] === undefined || link.e.conf > secondScores[j]) secondScores[j] = link.e.conf;
      });
    });
    var keys = Object.keys(secondScores);
    keys.sort(function(a, b){ return secondScores[b] - secondScores[a]; });
    var second = keys.slice(0, K2).map(Number);
    render("rag-txt", first, second, "txt");
    render("rag-force", first, second, "force");
    document.getElementById("rag-info").textContent =
      "题目: " + q + " —— 一阶线索 " + first.length + " 条，二阶扩散线索 " + second.length + " 条（红=一阶，橙=二阶）";
    bindClick();
  }
  document.getElementById("rag-run").onclick = run;
  document.getElementById("rag-preset").onclick = function(){
    document.getElementById("rag-q").value = PRESET;
    run();
  };
})();
</script>
"""
    return html.replace("__DATA__", data_json).replace("__K1__", str(k1)).replace("__K2__", str(k2)).replace("__PRESET__", preset_escaped)

