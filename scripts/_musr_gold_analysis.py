import json
from collections import Counter

data = json.load(open(r"official\murder_mystery.json", encoding="utf-8"))

def walk(node, out, path=()):
    out.append((node, path))
    for i, c in enumerate(node.get("children") or []):
        walk(c, out, path + (i,))

stats = Counter()
leaf_stats = Counter()
explicit_total = explicit_verbatim = explicit_fuzzy = 0
samples = []
for item in data:
    ctx = item["context"]
    q = item["questions"][0]
    for tree in q.get("intermediate_trees") or []:
        nodes = []
        for root in tree.get("root_structure") or []:
            walk(root, nodes)
        stats["trees"] += 1
        for node, _ in nodes:
            ft = node.get("fact_type")
            leaf_stats[ft] += 1
            if ft == "explicit":
                explicit_total += 1
                text = (node.get("value") or "").strip()
                # verbatim containment (case-insensitive, collapse spaces)
                norm_ctx = " ".join(ctx.split()).lower()
                norm_text = " ".join(text.split()).lower()
                if norm_text and norm_text in norm_ctx:
                    explicit_verbatim += 1
                else:
                    # fuzzy: all words appear within a window
                    words = [w for w in norm_text.split() if len(w) > 3]
                    if words and all(w in norm_ctx for w in words):
                        explicit_fuzzy += 1
                        if len(samples) < 12:
                            samples.append(text)

print("total items:", len(data))
print("stats:", dict(stats))
print("leaf fact_type:", dict(leaf_stats))
print(f"explicit leaves: total={explicit_total} verbatim={explicit_verbatim} ({explicit_verbatim/explicit_total:.1%}) all-words-fuzzy={explicit_fuzzy} ({explicit_fuzzy/explicit_total:.1%})")
print("fuzzy-but-not-verbatim samples:")
for s in samples:
    print("  -", s[:160])
