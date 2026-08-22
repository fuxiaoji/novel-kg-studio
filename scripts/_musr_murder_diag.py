import json, collections

data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
rows = [r for r in data["results"] if r["domain"] == "murder_mystery"]
print("murder rows:", len(rows))
gold = collections.Counter(r["gold_index"] for r in rows)
print("gold dist (0=A,1=B):", dict(gold))

for m in ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]:
    ans_idx = []
    for r in rows:
        if m not in r:
            continue
        a = r[m]["answer"]
        import re
        mm = list(re.finditer(r"(?i)\bANSWER\s*[:=]?\s*(\d+)", a))
        idx = (int(mm[-1].group(1)) - 1) if mm else None
        ans_idx.append(idx)
    cnt = collections.Counter(ans_idx)
    print(f"{m}: answer dist {dict(cnt)}  parsed={sum(1 for x in ans_idx if x is not None)}/{len(ans_idx)}")

# correlation: when parsed, does model match gold more often on A or B?
print("\nparsed answers vs gold (basic):")
for r in rows:
    a = r["basic"]["answer"]
    mm = list(re.finditer(r"(?i)\bANSWER\s*[:=]?\s*(\d+)", a))
    if mm:
        idx = int(mm[-1].group(1)) - 1
        print(f"  gold={r['gold_index']} ans={idx} {'OK' if idx==r['gold_index'] else 'XX'} | {r['question'][:40]}")

# show a few full rows
print("\nsample row (v5.2 correct=?, note):")
for r in rows[:6]:
    print(f"  gold={r['gold_index']} {r['gold_text']} | v5.2={r['v5.2']['note']} | basic={r['basic']['note']} | q={r['question'][:35]}")
