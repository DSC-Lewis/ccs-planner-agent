#!/usr/bin/env bash
# End-to-end smoke test. Expects the server to already be running.
set -euo pipefail
HOST="${1:-http://127.0.0.1:8777}"

say() { printf "\n\033[1;34m== %s ==\033[0m\n" "$1"; }

say "Health"
curl -sf "$HOST/api/health" | python3 -m json.tool

say "Reference (surveys)"
curl -sf "$HOST/api/reference/surveys" | python3 -m json.tool | head -20

# ---------- MANUAL ----------
say "Start MANUAL session"
MANUAL=$(curl -sf -X POST "$HOST/api/sessions" -H 'Content-Type: application/json' -d '{"mode":"manual"}')
MID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session']['id'])" "$MANUAL")
echo "session id: $MID"

step() {
  local sid=$1 payload=$2
  curl -sf -X POST "$HOST/api/sessions/$sid/advance" \
       -H 'Content-Type: application/json' -d "$payload"
}

step "$MID" '{"survey_id":"tw_2025","client_id":"internal_pitch"}' >/dev/null
step "$MID" '{"project_name":"smoke test","start_date":"2026-02-16","weeks":4}' >/dev/null
step "$MID" '{"target_ids":["all_adults","ta_30_54_a"]}' >/dev/null
step "$MID" '{"planning_type":"Reach"}' >/dev/null
step "$MID" '{"channel_ids":["tv_advertising","youtube_video_ads","meta_video_ads"]}' >/dev/null
step "$MID" '{}' >/dev/null  # calibration

say "Save Manual weekly budgets"
MANUAL_FINAL=$(step "$MID" '{"weekly_budgets":{
  "tv_advertising":[2500,2500,2500,2500],
  "youtube_video_ads":[125000,125000,125000,125000],
  "meta_video_ads":[100000,100000,100000,100000]
}}')
python3 - <<EOF
import json
r = json.loads('''$MANUAL_FINAL''')
s = r['plan']['summary']
print(f"Plan 1 · Manual")
print(f"  total_budget_twd : {s['total_budget_twd']:>12,.0f}")
print(f"  total_impressions: {s['total_impressions']:>12,.0f}")
print(f"  total_grp        : {s['total_grp']:>12.2f}")
print(f"  net_reach_pct    : {s['net_reach_pct']:>12.2f}%")
print(f"  frequency        : {s['frequency']:>12.2f}")
EOF

# ---------- AUTOMATIC ----------
say "Start AUTOMATIC session"
AUTO=$(curl -sf -X POST "$HOST/api/sessions" -H 'Content-Type: application/json' -d '{"mode":"automatic"}')
AID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session']['id'])" "$AUTO")
echo "session id: $AID"

step "$AID" '{"survey_id":"tw_2025","client_id":"internal_pitch"}' >/dev/null
step "$AID" '{"project_name":"smoke auto","start_date":"2026-02-16","weeks":4}' >/dev/null
step "$AID" '{"target_ids":["all_adults"]}' >/dev/null
step "$AID" '{"planning_type":"Reach"}' >/dev/null
step "$AID" '{"channel_ids":["tv_advertising","youtube_video_ads","meta_video_ads"]}' >/dev/null
step "$AID" '{"criterion_id":"net_reach","strategy_id":"global_plan"}' >/dev/null
step "$AID" '{
  "total_budget_twd":6000000,
  "mandatory_channel_ids":["tv_advertising","meta_video_ads"],
  "optional_channel_ids":["youtube_video_ads"]
}' >/dev/null
step "$AID" '{"constraints":{"tv_advertising":{"min_budget":500000,"max_budget":2000000}}}' >/dev/null

say "Run Auto optimization"
AUTO_FINAL=$(step "$AID" '{}')
python3 - <<EOF
import json
r = json.loads('''$AUTO_FINAL''')
p = r['plan']
s = p['summary']
print(f"Plan 2 · Automatic ({p['meta'].get('criterion_id')} / {p['meta'].get('strategy_id')})")
print(f"  total_budget_twd : {s['total_budget_twd']:>12,.0f}")
print(f"  total_impressions: {s['total_impressions']:>12,.0f}")
print(f"  net_reach_pct    : {s['net_reach_pct']:>12.2f}%")
print(f"  frequency        : {s['frequency']:>12.2f}")
print()
print(f"  Channel allocation:")
for a in p['allocations']:
    print(f"    {a['channel_id']:22s} {a['total_budget_twd']:>12,.0f} ({a['net_reach_pct']:5.2f}% reach, freq {a['frequency']:.2f})")
EOF

say "Compare Plan 1 vs Plan 2"
MPLAN=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['plan']['id'])" "$MANUAL_FINAL")
APLAN=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['plan']['id'])" "$AUTO_FINAL")
curl -sf -X POST "$HOST/api/plans/compare" -H 'Content-Type: application/json' \
    -d "[\"$MPLAN\",\"$APLAN\"]" \
  | python3 -c "import json,sys; d=json.loads(sys.stdin.read())['delta']; \
import pprint; pprint.pprint(d)"

echo -e "\n\033[1;32m✓ Smoke test complete\033[0m"
