import json, collections

data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
rows = data["results"]
methods = ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]
stat = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    for m in methods:
        if m not in r:
            continue
        note = r[m]["note"]
        if note.startswith("parsed"):
            stat[m]["parsed"] += 1
            stat[m]["correct"] += 1 if r[m]["correct"] else 0
        else:
            stat[m]["unparsed"] += 1
print(f"{'method':6} {'parsed':>7} {'correct':>8} {'unparsed':>8}")
for m in methods:
    s = stat[m]
    print(f"{m:6} {s['parsed']:7d} {s['correct']:8d} {s['unparsed']:8d}")

# sample unparsed answers
print("\nsample unparsed v5.1 answers:")
cnt = 0
for r in rows:
    if "v5.1" in r and not r["v5.1"]["note"].startswith("parsed"):
        print("Q:", r["question"][:50])
        print("A:", repr(r["v5.1"]["answer"])[:220])
        cnt += 1
        if cnt >= 5:
            break
print("\nsample unparsed v7 answers:")
cnt = 0
for r in rows:
    if "v7" in r and not r["v7"]["note"].startswith("parsed"):
        print("Q:", r["question"][:50])
        print("A:", repr(r["v7"]["answer"])[:220])
        cnt += 1
        if cnt >= 5:
            break
