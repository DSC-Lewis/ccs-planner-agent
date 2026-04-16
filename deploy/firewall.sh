#!/usr/bin/env bash
# Open port 8000 to the internet for the ccs-planner VM and tag it.
# Run ONCE on your laptop after creating the VM. Safe to rerun.
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT, e.g. export GCP_PROJECT=nvidia-test-410101}"
: "${VM_NAME:?Set VM_NAME, e.g. export VM_NAME=instance-20260416-224642}"
VM_ZONE="${VM_ZONE:-us-central1-a}"
TAG="${TAG:-ccs-planner}"
SRC_RANGES="${SRC_RANGES:-0.0.0.0/0}"  # tighten to your office CIDR for prod

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

# ----------------------------------------------------------------------------
# 1. Make sure the VM carries our network tag (idempotent)
# ----------------------------------------------------------------------------
say "Tagging VM $VM_NAME with $TAG"
gcloud compute instances add-tags "$VM_NAME" \
    --project="$GCP_PROJECT" \
    --zone="$VM_ZONE" \
    --tags="$TAG" || true

# ----------------------------------------------------------------------------
# 2. Create (or update) the firewall rule
# ----------------------------------------------------------------------------
RULE="allow-${TAG}-8000"
if gcloud compute firewall-rules describe "$RULE" --project="$GCP_PROJECT" >/dev/null 2>&1; then
    say "Firewall rule $RULE already exists — refreshing source ranges"
    gcloud compute firewall-rules update "$RULE" \
        --project="$GCP_PROJECT" \
        --source-ranges="$SRC_RANGES"
else
    say "Creating firewall rule $RULE (tcp:8000, sources: $SRC_RANGES)"
    gcloud compute firewall-rules create "$RULE" \
        --project="$GCP_PROJECT" \
        --direction=INGRESS \
        --action=ALLOW \
        --rules=tcp:8000 \
        --target-tags="$TAG" \
        --source-ranges="$SRC_RANGES"
fi

EXT_IP="$(gcloud compute instances describe "$VM_NAME" \
    --project="$GCP_PROJECT" --zone="$VM_ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"

cat <<EOF

✅ Firewall is open.

   External IP : $EXT_IP
   URL         : http://$EXT_IP:8000
   Source CIDR : $SRC_RANGES
                 (set SRC_RANGES=<your-cidr> to restrict before re-run)
EOF
