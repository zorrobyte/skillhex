import os, sys, json
ws = os.environ.get("SKILLHEX_WORKSPACE", ".")
p = os.path.join(ws, "answer.txt")
if not os.path.exists(p):
    print("answer.txt missing"); sys.exit(1)
v = open(p).read().strip().lstrip("v")
print(f"answer.txt = {v!r}")
sys.exit(0 if v == "1.4.10" else 1)
