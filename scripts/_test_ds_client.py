import sys
sys.path.insert(0, r"E:\desktop\coding\科研\novel-kg-studio\scripts")
sys.path.insert(0, r"E:\desktop\coding\科研\novel-kg-studio\src")
from eval_four_datasets import UrllibClient

c = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
try:
    out = c.complete("You are a judge.", "Reply with OK", max_tokens=50)
    print("complete OK:", out[:50])
except Exception as e:
    print("complete FAIL:", type(e).__name__, repr(e)[:300])
try:
    j = c.complete_json("You are a judge.", 'Return strict JSON {"correct": true}', max_tokens=50)
    print("json OK:", j)
except Exception as e:
    print("json FAIL:", type(e).__name__, repr(e)[:300])
