import json, collections, re

data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
rows = [r for r in data["results"] if r["domain"] == "murder_mystery"]
for m in ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]:
    notes = collections.Counter()
    for r in rows:
        if m in r:
            notes[r[m]["note"][:40]] += 1
    print(f"\n{m}:")
    for k, v in notes.most_common(8):
        print(f"   {v}x {k}")

print("\n=== basic answer tails (first 10) ===")
for r in rows[:10]:
    a = r["basic"]["answer"]
    print(f"note={r['basic']['note'][:35]!r} tail={a[-90:]!r}")
