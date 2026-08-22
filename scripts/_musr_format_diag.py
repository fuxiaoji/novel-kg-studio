import json, re, collections

data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
rows = [r for r in data["results"] if r["domain"] == "murder_mystery"]

def extract(answer):
    mm = list(re.finditer(r"(?i)\bANSWER\s*[:=]?\s*(\d+)", answer))
    if mm:
        n = int(mm[-1].group(1))
        if 1 <= n <= 2:
            return ("num", n - 1)
    m = re.search(r"\b([A-D])\b\s*[).:]?\s*$", answer.strip(), re.M)
    if m:
        return ("letter", ord(m.group(1)) - ord("A"))
    m2 = re.search(r"\bANSWER\s*[:=]?\s*([A-Z])", answer)
    if m2:
        return ("initial", m2.group(1).upper())
    return ("none", None)

for m in ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]:
    forms = collections.Counter()
    correct = collections.Counter()
    total = collections.Counter()
    for r in rows:
        if m not in r:
            continue
        kind, idx = extract(r[m]["answer"])
        forms[kind] += 1
        if idx is not None:
            total[kind] += 1
            correct[kind] += 1 if idx == r["gold_index"] else 0
    print(f"\n{m}:")
    for k in ["num", "letter", "initial", "none"]:
        print(f"   {k:8} forms={forms[k]:3d}  parsed_correct={correct[k]}/{total[k]}")
