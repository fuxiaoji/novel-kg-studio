import pandas as pd, os

base = r"."
for f in ["murder_mystery.csv", "object_placements.csv", "team_allocation.csv"]:
    p = os.path.join(base, f)
    df = pd.read_csv(p)
    print("=" * 70)
    print(f, df.shape)
    print("cols:", list(df.columns))
    for c in df.columns:
        if c != "narrative":
            v = df[c].iloc[0]
            print(f"  {c}: type={type(v).__name__} sample={repr(v)[:400]}")
    print("narrative chars:", df["narrative"].str.len().describe().round(1).to_dict())
    print("answer_index dist:", df["answer_index"].value_counts().to_dict())
    print("n_options dist:", df["choices"].apply(lambda x: len(eval(x)) if isinstance(x, str) else -1).value_counts().to_dict())
