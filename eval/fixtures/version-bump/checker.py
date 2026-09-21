import os, sys, json
ws = os.environ.get("SKILLHEX_WORKSPACE", ".")
p = os.path.join(ws, "answer.txt")
if not os.path.exists(p):
    print("answer.txt missing"); sys.exit(1)
v = open(p).read().strip().lstrip("v")
try:
    pkg = json.load(open(os.path.join(ws, "package.json"))).get("version")
except Exception as e:  # noqa: BLE001
    print(f"package.json unreadable: {e}"); sys.exit(1)
print(f"answer.txt = {v!r}, package.json version = {pkg!r}")
sys.exit(0 if v == "1.4.10" and pkg == "1.4.10" else 1)
