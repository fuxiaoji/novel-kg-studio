import json

base = r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr_local"
murder = json.load(open(base + r"\results_p2_murder.json", encoding="utf-8"))["results"]
objteam = json.load(open(base + r"\results.json", encoding="utf-8"))["results"]
merged = murder + objteam
print("murder:", len(murder), "objteam:", len(objteam), "total:", len(merged))
json.dump({"results": merged}, open(base + r"\results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("merged -> results.json")
