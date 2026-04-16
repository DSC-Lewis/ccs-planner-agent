#!/usr/bin/env bash
# One-shot: create the VM + open firewall + wait for sshd + bootstrap the app.
# Run from your laptop. Idempotent — if the VM already exists it'll use it.
#
# Usage:
#   export GCP_PROJECT=nvidia-test-410101
#   export CCS_ADMIN_KEY=$(openssl rand -hex 24)   # keep this — it's your login
#   bash deploy/provision.sh
set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${CCS_ADMIN_KEY:?set CCS_ADMIN_KEY (openssl rand -hex 24)}"
VM_NAME="${VM_NAME:-ccs-planner-agent}"
VM_ZONE="${VM_ZONE:-us-central1-a}"
REGION="${REGION:-us-central1}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

# ----------------------------------------------------------------------------
# 1. Create VM (if missing)
# ----------------------------------------------------------------------------
if gcloud compute instances describe "$VM_NAME" --zone="$VM_ZONE" --project="$GCP_PROJECT" >/dev/null 2>&1; then
    say "VM $VM_NAME already exists — skipping create"
else
    say "Creating VM $VM_NAME in $VM_ZONE"
    # Inject sshd host-key regeneration as a startup-script so we don't
    # trip over the Debian-12 first-boot bug.
    STARTUP=$(cat <<'STARTUP_EOF'
#!/bin/bash
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    ssh-keygen -A
    systemctl restart ssh
fi
STARTUP_EOF
)
    gcloud compute instances create "$VM_NAME" \
        --project="$GCP_PROJECT" --zone="$VM_ZONE" --machine-type=e2-micro \
        --network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default \
        --metadata=enable-osconfig=TRUE,startup-script="$STARTUP" \
        --maintenance-policy=MIGRATE --provisioning-model=STANDARD \
        --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append \
        --create-disk=auto-delete=yes,boot=yes,image=projects/debian-cloud/global/images/family/debian-12,mode=rw,size=10,type=pd-balanced \
        --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
        --labels=goog-ec-src=vm_add-gcloud --reservation-affinity=any
fi

# ----------------------------------------------------------------------------
# 2. Firewall
# ----------------------------------------------------------------------------
say "Ensuring firewall + network tag"
gcloud compute instances add-tags "$VM_NAME" --project="$GCP_PROJECT" --zone="$VM_ZONE" --tags=ccs-planner || true
if ! gcloud compute firewall-rules describe allow-ccs-planner-8000 --project="$GCP_PROJECT" >/dev/null 2>&1; then
    gcloud compute firewall-rules create allow-ccs-planner-8000 \
        --project="$GCP_PROJECT" --direction=INGRESS --action=ALLOW --rules=tcp:8000 \
        --target-tags=ccs-planner --source-ranges=0.0.0.0/0
fi

# ----------------------------------------------------------------------------
# 3. Wait for sshd
# ----------------------------------------------------------------------------
say "Waiting for sshd"
for i in $(seq 1 60); do
    if gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$GCP_PROJECT" \
        --command="echo up" >/dev/null 2>&1; then
        break
    fi
    sleep 5
done

# ----------------------------------------------------------------------------
# 4. Bootstrap
# ----------------------------------------------------------------------------
say "Running bootstrap.sh on VM (this takes ~3 min first time)"
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$GCP_PROJECT" \
    --command="curl -sSL https://raw.githubusercontent.com/DSC-Lewis/ccs-planner-agent/main/deploy/bootstrap.sh | bash -s -- '$CCS_ADMIN_KEY'"

# ----------------------------------------------------------------------------
# 5. Report
# ----------------------------------------------------------------------------
EXT_IP=$(gcloud compute instances describe "$VM_NAME" --project="$GCP_PROJECT" --zone="$VM_ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

cat <<EOF

🎉 CCS Planner deployed.

   URL       : http://$EXT_IP:8000
   Admin key : $CCS_ADMIN_KEY

   Mint a second user (for your teammate):
     curl -sX POST http://$EXT_IP:8000/api/users \\
       -H "X-API-Key: \$CCS_ADMIN_KEY" \\
       -H "Content-Type: application/json" \\
       -d '{"name":"teammate"}' | jq .

   Or log in at http://$EXT_IP:8000, click 👥 Users (admin-only), + Invite user.
EOF
