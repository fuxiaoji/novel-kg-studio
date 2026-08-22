import json, os

path = r"official\murder_mystery.json"
data = json.load(open(path, encoding="utf-8"))
print("top-level type:", type(data).__name__, "len:", len(data))
item = data[0]
print("item keys:", list(item.keys()))
print("context len:", len(item.get("context", "")))
print("context head:", repr(item.get("context", "")[:300]))
qs = item.get("questions", [])
print("num questions:", len(qs))
q0 = qs[0]
print("question keys:", list(q0.keys()))
for k, v in q0.items():
    if k == "intermediate_data":
        print(f"  {k}: type={type(v).__name__}")
        if isinstance(v, dict):
            print("   keys:", list(v.keys()))
            print("   sample:", json.dumps(v, ensure_ascii=False)[:2000])
        elif isinstance(v, list):
            print("   len:", len(v))
            print("   sample:", json.dumps(v[0], ensure_ascii=False)[:2000])
        else:
            print("   value:", str(v)[:2000])
    elif k == "intermediate_trees":
        print(f"  {k}: type={type(v).__name__}")
        if isinstance(v, list):
            print("   len:", len(v))
            for i, t in enumerate(v[:2]):
                print(f"   tree[{i}]:", json.dumps(t, ensure_ascii=False)[:1500])
    else:
        print(f"  {k}: {repr(v)[:500]}")
