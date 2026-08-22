import sys, json

sys.path.insert(0, r"E:\desktop\coding\科研\novel-kg-studio\src")
from novel_kg_studio.llm import LLMClient

client = LLMClient(model="deepseek-v4-flash", temperature=0.0, max_tokens=4000, retries=2, reasoning_effort="low")
lines = open(r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\cases\musr.jsonl", encoding="utf-8").readlines()
case = json.loads(lines[500])
q = case["questions"][0]
print("question:", q["question"])
print("choices:", q["choices"], "gold:", q["gold_index"])
text = case["text"]
opt = "Options:\n" + "\n".join(f"{chr(65+i)}. {c.strip()}" for i, c in enumerate(q["choices"]))
prompt = (
    f"Question: {q['question']}\n\n{opt}\n\nNarrative:\n{text}\n\n"
    "Reason step by step using ONLY the narrative, then end your reply with exactly a line "
    "`ANSWER: N` where N is the number of your chosen option (1-based)."
)
for i in range(2):
    try:
        out = client.complete(
            "You are a careful reasoning judge solving a MuSR-style story question.", prompt, max_tokens=4000
        )
        print(f"attempt {i}: len={len(out)} tail={out[-220:]!r}")
    except Exception as e:
        print(f"attempt {i} FAIL: {e!r}")
