"""Live HTTP smoke for the intent-boundary hotfix — exact spec queries.

Usage:
  python scripts/live_smoke_hotfix.py            # localhost:8000
  BASE_URL=https://... python scripts/live_smoke_hotfix.py
"""
import json, os, sys, urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/chat"
SID = "live-hotfix-1"

def chat(message, session_id=SID):
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

steps = [
    # (label, message, session_id, expected_intent, must_not_contain)
    ("establish order", "Where is order ORD-1001?", SID, "ORDER_STATUS", None),
    ("1. return policy", "What is the return policy?", SID, "STORE_POLICY", "หมายเลขคำสั่งซื้อ"),
    ("2. policies follow", "What policies does SiamCart follow?", SID, "STORE_POLICY", "หมายเลขคำสั่งซื้อ"),
    ("3. research part of", "What research is SiamCart part of?", SID, "RESEARCH_INFO", "หมายเลขคำสั่งซื้อ"),
    ("4. what is SiamCart", "What is SiamCart?", SID, "RESEARCH_INFO", "หมายเลขคำสั่งซื้อ"),
    ("5. where is my order (fresh)", "Where is my order?", "live-hotfix-fresh", "UNKNOWN", None),
    ("6. where order ORD-1001", "Where is order ORD-1001?", SID, "ORDER_STATUS", None),
]

ok = True
for label, msg, sid, exp_intent, must_not in steps:
    code, d = chat(msg, sid)
    intent = d.get("intent", "?")
    clarify = d.get("requires_clarification")
    src = d.get("response_source")
    resp = d.get("response", "")[:110].replace("\n", " ")
    status = "OK" if (code == 200 and intent == exp_intent and (must_not is None or must_not not in d.get("response", ""))) else "FAIL"
    if status == "FAIL":
        ok = False
    print(f"[{status}] {label:28} HTTP={code} intent={intent:14} clarify={clarify} src={src}")
    print(f"         resp: {resp}")
    if code == 200:
        print(f"         llm_enabled={d.get('llm_enabled')} active_order={d.get('active_order_id')} "
              f"retrieval_source={d.get('policy_evidence', {}).get('retrieval_source')}")
print("\nALL OK" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
