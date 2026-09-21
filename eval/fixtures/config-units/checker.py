import os, sys
ws = os.environ.get("SKILLHEX_WORKSPACE", ".")
p = os.path.join(ws, "answer.txt")
if not os.path.exists(p):
    print("answer.txt missing"); sys.exit(1)
v = open(p).read().strip().rstrip("s").strip()
print(f"answer.txt = {v!r}")
try:
    ok = abs(float(v) - float(os.environ.get("SKILLHEX_EXPECTED", "2.5"))) < 1e-9
except ValueError:
    ok = False
sys.exit(0 if ok else 1)
