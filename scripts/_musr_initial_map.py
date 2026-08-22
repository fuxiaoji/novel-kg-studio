import json, re, collections

data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
rows = [r for r in data["results"] if r["domain"] == "murder_mystery"]

def initial_of(answer):
    m = re.search(r"\bANSWER\s*[:=]?\s*([A-Z])", answer)
    return m.group(1).upper() if m else None

for m in ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]:
    rec = 0
    total = 0
    amb = 0
    for r in rows:
        if m not in r:
            continue
        init = initial_of(r[m]["answer"])
        if not init:
            continue
        choices = r["choices"]
        match = [i for i, c in enumerate(choices) if c.strip().startswith(init)]
        if len(match) == 1:
            total += 1
            rec += 1 if match[0] == r["gold_index"] else 0
        elif len(match) > 1:
            amb += 1
    print(f"{m}: initial-form {total+amb} (unique={total}, ambiguous={amb}) -> recoverable correct {rec}/{total}")
