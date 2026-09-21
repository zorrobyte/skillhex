import os, sys
ws = os.environ.get("SKILLHEX_WORKSPACE", ".")
p = os.path.join(ws, "answer.txt")
if not os.path.exists(p):
    print("answer.txt missing"); sys.exit(1)
v = open(p).read().strip().replace(",", "")
print(f"answer.txt = {v!r}")
try:
    ok = abs(float(v) - 275.50) < 0.005
except ValueError:
    ok = False
sys.exit(0 if ok else 1)
