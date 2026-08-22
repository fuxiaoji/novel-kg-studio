import json, os, glob

base = r"E:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\musr"
for domain in ["murder_mystery", "object_placements", "team_allocation"]:
    print("=" * 60)
    print(domain)
    dirs = sorted(glob.glob(os.path.join(base, f"musr_{domain}_*")))
    for d in dirs:
        name = os.path.basename(d)
        graph = os.path.exists(os.path.join(d, "graph.json"))
        fulls = len(glob.glob(os.path.join(d, "full_*.json")))
        graph_ans = len(glob.glob(os.path.join(d, "graph_*.json")))
        print(f"  {name}: graph={graph} full_ans={fulls} graph_ans={graph_ans}")
    print(f"  total stories: {len(dirs)}")
