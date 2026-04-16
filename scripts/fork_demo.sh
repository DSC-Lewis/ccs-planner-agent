#!/usr/bin/env bash
# Manual -> Fork -> Automatic demo: prove the brief carries across agents.
set -euo pipefail
HOST="${1:-http://127.0.0.1:8778}"

say() { printf "\n\033[1;34m==> %s\033[0m\n" "$1"; }
j()  { python3 -m json.tool; }

# ---------- Manual Agent ----------
say "[1/3] Build Plan 1 via MANUAL agent"
M=$(curl -sf -X POST "$HOST/api/sessions" -H 'Content-Type: application/json' -d '{"mode":"manual"}')
MID=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['session']['id'])" "$M")
echo "manual session id: $MID"

step(){ curl -sf -X POST "$HOST/api/sessions/$1/advance" -H 'Content-Type: application/json' -d "$2"; }

step "$MID" '{"survey_id":"tw_2025","client_id":"internal_pitch"}'                                 >/dev/null
step "$MID" '{"project_name":"fork demo","start_date":"2026-02-16","weeks":4}'                     >/dev/null
step "$MID" '{"target_ids":["all_adults","ta_30_54_a"]}'                                            >/dev/null
step "$MID" '{"planning_type":"Comm"}'                                                              >/dev/null
step "$MID" '{"comms":{"brand_strength":6,"parent_brand":5,"competitor_clutter":5,"new_creative":5,"message_complexity":5,"kpi_ids":["brand_consideration","attitude_measures","brand_knowledge_scores"]}}' >/dev/null
step "$MID" '{"channel_ids":["tv_advertising","youtube_video_ads","meta_video_ads"]}'              >/dev/null
step "$MID" '{}'                                                                                    >/dev/null
M_FINAL=$(step "$MID" '{"weekly_budgets":{"tv_advertising":[2500,2500,2500,2500],"youtube_video_ads":[125000,125000,125000,125000],"meta_video_ads":[100000,100000,100000,100000]}}')

python3 - <<EOF
import json
r = json.loads('''$M_FINAL''')
b = r['session']['brief']
s = r['plan']['summary']
print(f"  Brief: {b['survey_id']} / {b['client_id']} / {b['project_name']}")
print(f"         {b['start_date']} -> {b['end_date']} ({b['weeks']}w) | Comm planning")
print(f"         targets = {b['target_ids']}")
print(f"         channels = {b['channel_ids']}")
print(f"  Plan 1 saved: id={r['plan']['id']}")
print(f"         budget={s['total_budget_twd']:,} reach={s['net_reach_pct']}% freq={s['frequency']}")
EOF

# ---------- Fork ----------
say "[2/3] Fork same Brief into AUTOMATIC agent"
F=$(curl -sf -X POST "$HOST/api/sessions/$MID/fork" -H 'Content-Type: application/json' -d '{"target_mode":"automatic"}')
AID=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['session']['id'])" "$F")
echo "automatic session id: $AID (new session, brief carried over)"

python3 - <<EOF
import json
r = json.loads('''$F''')
sess = r['session']
b = sess['brief']
ai = sess['automatic_input']
print(f"  Resume point : {sess['step']}  (survey/client/TA/comms/channels are SKIPPED)")
print(f"  Brief copied : survey={b['survey_id']} client={b['client_id']} project={b['project_name']}")
print(f"                 weeks={b['weeks']} targets={b['target_ids']}")
print(f"                 planning={b['planning_type']} kpis={b['comms']['kpi_ids']}")
print(f"                 channels={b['channel_ids']}")
print(f"  Auto seed    : mandatory={ai['mandatory_channel_ids']}")
prov = [h for h in sess['history'] if h['step']=='__forked_from__'][0]['payload']
print(f"  Provenance   : forked from session {prov['source_session_id']} "
      f"(mode={prov['source_mode']}, plan={prov['source_plan_id']})")
EOF

# ---------- Automatic Agent ----------
say "[3/3] Finish AUTOMATIC agent"
step "$AID" '{"criterion_id":"net_reach","strategy_id":"global_plan"}' >/dev/null
step "$AID" '{"total_budget_twd":6000000,"mandatory_channel_ids":["tv_advertising","meta_video_ads"],"optional_channel_ids":["youtube_video_ads"]}' >/dev/null
step "$AID" '{"constraints":{"tv_advertising":{"min_budget":500000,"max_budget":2000000}}}' >/dev/null
A_FINAL=$(step "$AID" '{}')

python3 - <<EOF
import json
r = json.loads('''$A_FINAL''')
p = r['plan']; s = p['summary']
print(f"  Plan 2 saved: id={p['id']}  kind={p['kind']}")
print(f"         budget={s['total_budget_twd']:,} impressions={s['total_impressions']:,}")
print(f"         reach={s['net_reach_pct']}% freq={s['frequency']}")
for a in p['allocations']:
    print(f"           - {a['channel_id']:22s} budget={a['total_budget_twd']:>12,.0f} "
          f"reach={a['net_reach_pct']:5.2f}% freq={a['frequency']}")
EOF

# ---------- Compare ----------
say "Compare Plan 1 (Manual) vs Plan 2 (Automatic) — same Brief"
P1=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['plan']['id'])" "$M_FINAL")
P2=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['plan']['id'])" "$A_FINAL")
CMP=$(curl -sf -X POST "$HOST/api/plans/compare" -H 'Content-Type: application/json' -d "[\"$P1\",\"$P2\"]")
python3 - <<EOF
import json
d = json.loads('''$CMP''')['delta']
print(f"  Δ budget     : {d['total_budget_twd']:+,.0f} TWD")
print(f"  Δ impressions: {d['total_impressions']:+,}")
print(f"  Δ net reach  : {d['net_reach_pct']:+.2f} pp")
print(f"  Δ frequency  : {d['frequency']:+.2f}")
EOF

echo -e "\n\033[1;32m✓ Manual->Fork->Automatic handoff verified\033[0m"
