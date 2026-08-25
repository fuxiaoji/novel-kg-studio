import os, time
from pathlib import Path
from llama_cpp import Llama
base = Path(os.environ["QWEN_DIR"])
model_path = base / "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
t0 = time.time()
print("loading...", flush=True)
llm = Llama(model_path=str(model_path), n_ctx=2048, n_threads=8, verbose=False)
print(f"loaded in {time.time()-t0:.0f}s", flush=True)
out = llm.create_chat_completion(
    messages=[{"role":"user","content":"Reply with exactly: QWEN-OK"}],
    max_tokens=32, temperature=0.0,
)
print("reply:", out["choices"][0]["message"]["content"][:60], flush=True)
print("inference time:", round(time.time()-t0,1), flush=True)
