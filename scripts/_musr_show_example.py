import json

lines = open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\cases\musr.jsonl", encoding="utf-8").readlines()
case = json.loads(lines[0])
q = case["questions"][0]
print("CASE:", case["case_id"])
print("QUESTION:", q["question"])
print("CHOICES:", q["choices"], "GOLD_INDEX:", q["gold_index"], "GOLD:", q["gold_text"])
trees = q["meta"]["reasoning_trees"]
print("num trees:", len(trees))
for t in trees:
    print("  root:", t["root"])
    expl = [l for l in t["leaves"] if l["fact_type"] == "explicit"]
    cs = [l for l in t["leaves"] if l["fact_type"] == "commonsense"]
    print(f"    explicit leaves: {len(expl)}  commonsense leaves: {len(cs)}")
    for l in expl[:4]:
        print("      -", l["text"][:150])

# graph stats
g = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr\musr_murder_mystery_0\graph.json", encoding="utf-8"))
print("\nGRAPH nodes:", len(g["nodes"]), "edges:", len(g["edges"]))
from collections import Counter
print("node types:", Counter(n.get("type") for n in g["nodes"]))
print("edge types:", Counter(e.get("type") for e in g["edges"]))
print("\nfirst 6 nodes:")
for n in g["nodes"][:6]:
    print("  ", n.get("type"), "|", n.get("name"), "|", (n.get("description") or "")[:80], "| ev:", (n.get("evidence") or [""])[0][:80])

# coverage sample
for idx in [0, 1, 2]:
    d = json.load(open(rf"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr\musr_murder_mystery_{idx}\graph.json", encoding="utf-8"))
    pass
