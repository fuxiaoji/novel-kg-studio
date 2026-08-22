import json, sys
sys.path.insert(0, r"E:\desktop\coding\科研\novel-kg-studio\scripts")
sys.path.insert(0, r"E:\desktop\coding\科研\novel-kg-studio\src")
from eval_four_datasets import UrllibClient

client = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
data = json.load(open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local\results.json", encoding="utf-8"))
row = next(r for r in data["results"] if "v5.1" in r and not r["v5.1"]["note"].startswith("parsed"))
print("q:", row["question"][:60])
print("gold:", row["gold_index"], row["gold_text"])
print("answer head:", row["v5.1"]["answer"][:200])
choices = "\n".join(f"{i+1}. {c}" for i, c in enumerate(row.get("choices", [])))
prompt = (
    "Question: " + row["question"] + "\n"
    "Choices (1-based):\n" + choices + "\n"
    "Gold answer (index): " + str(row.get("gold_index")) + " -> " + str(row.get("gold_text", "")) + "\n"
    "Model answer: " + row["v5.1"]["answer"][:2500] + "\n"
    "Decide whether the model answer selects the gold option.\n"
    'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
)
try:
    payload = client.complete_json("You are a strict but fair answer judge for MuSR.", prompt, max_tokens=800)
    print("verdict:", payload)
except Exception as e:
    print("FAIL:", type(e).__name__, repr(e)[:500])
