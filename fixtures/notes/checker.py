import os, sys
ws = os.environ.get("SKILLHEX_WORKSPACE", ".")
p = os.path.join(ws, "answer.txt")
if not os.path.exists(p):
    print("answer.txt missing"); sys.exit(1)
v = open(p).read().strip()
print(f"answer.txt = {v!r}")
sys.exit(0 if v == "7" else 1)
