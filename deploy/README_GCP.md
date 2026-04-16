# Deploy to GCP Compute Engine (e2-micro, Debian 12)

Target topology:

```
┌── internet ──────────────────────────────────┐
│  TCP 8000 (can tighten via firewall source)  │
└──────────────┬───────────────────────────────┘
               ▼
    ┌──────────────────────────────┐
    │  e2-micro · Debian 12        │
    │  us-central1-a · 10 GB PD    │  <-- free tier eligible
    │                              │
    │   docker compose up          │
    │     └─ ccs-planner-agent     │
    │         (:8000, bind-mount   │
    │          /opt/.../data)       │
    └──────────────────────────────┘
```

## 1 · Prerequisites (one-time, on your laptop)

```bash
gcloud auth login
gcloud config set project nvidia-test-410101

# Export the variables the deploy scripts read.
cp deploy/gcp-env.example ~/.ccs-deploy-env
# edit the file; generate an admin key with:
openssl rand -hex 24
# Then:
source ~/.ccs-deploy-env
```

## 2 · Create the VM

You already have the `gcloud compute instances create instance-20260416-224642 ...`
command. Run it as-is — the rest of this guide assumes the VM exists under
that name in `us-central1-a`.

## 3 · Open the firewall + tag the VM (one-time)

```bash
bash deploy/firewall.sh
```

This:
1. Adds the network tag `ccs-planner` to your VM.
2. Creates firewall rule `allow-ccs-planner-8000` permitting TCP/8000 from
   `$SRC_RANGES` (default `0.0.0.0/0` — tighten before going live).

Output includes the external IP. Keep it handy.

## 4 · Bootstrap the app on the VM

```bash
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$GCP_PROJECT"
# Then inside the VM:
curl -sSL https://raw.githubusercontent.com/DSC-Lewis/ccs-planner-agent/main/deploy/bootstrap.sh \
  | bash -s -- "$CCS_ADMIN_KEY"
```

The bootstrap script is **idempotent** — rerun it any time to pull the
latest image or reset a broken state.

What it does:
1. Installs Docker Engine + compose plugin from Docker's apt repo.
2. Clones `https://github.com/DSC-Lewis/ccs-planner-agent.git` to
   `/opt/ccs-planner-agent`.
3. Writes `/opt/ccs-planner-agent/.env` with your admin key + paths.
4. Writes `/opt/ccs-planner-agent/docker-compose.override.yml` that
   bind-mounts `/opt/ccs-planner-agent/data` into the container so the
   SQLite file survives container restarts.
5. Runs `docker compose up --build -d`.
6. Curls `/api/health` to verify the container is live.

## 5 · Open the UI

```
http://<external-ip>:8000
```

Log in with the admin key you passed to the bootstrap script.

## 6 · Rolling updates

After you merge a PR into `main`, SSH in and:

```bash
bash /opt/ccs-planner-agent/deploy/update.sh
```

Or from your laptop:

```bash
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$GCP_PROJECT" \
    --command="bash /opt/ccs-planner-agent/deploy/update.sh"
```

Downtime: ~3-5 seconds while the container restarts.

## 7 · Ops runbook

### Check logs
```bash
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" \
    --command="cd /opt/ccs-planner-agent && sudo docker compose logs --tail 200 -f"
```

### SSH and inspect SQLite
```bash
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE"
# inside the VM:
sudo sqlite3 /opt/ccs-planner-agent/data/ccs.db '.tables'
```

### Backup the DB
```bash
gcloud compute scp --zone="$VM_ZONE" \
    "$VM_NAME":/opt/ccs-planner-agent/data/ccs.db \
    ./ccs-backup-$(date +%F).db
```

### Stop the app (keep the VM)
```bash
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" \
    --command="cd /opt/ccs-planner-agent && sudo docker compose down"
```

### Stop the VM (save $) but keep disk
```bash
gcloud compute instances stop "$VM_NAME" --zone="$VM_ZONE"
```

### Start again later
```bash
gcloud compute instances start "$VM_NAME" --zone="$VM_ZONE"
# The container auto-starts because the compose service has
# `restart: unless-stopped`.
```

### Delete everything (app + VM + disk)
```bash
gcloud compute instances delete "$VM_NAME" --zone="$VM_ZONE"
gcloud compute firewall-rules delete allow-ccs-planner-8000
```

## 8 · Cost estimate

| Item | Spec | Monthly USD |
|---|---|---|
| e2-micro compute | 744 hrs | **$0** under the always-free tier in us-central1 |
| pd-balanced disk | 10 GB | ~$1.00 (pd-balanced is NOT free-tier; switch to pd-standard to save ~$0.60) |
| External IP (ephemeral, attached) | 1 | $0 while attached |
| Egress (within 1 GB/mo) | — | $0 |
| **Total** | | **~$1/month** |

> If you delete the VM while keeping the disk, the disk still bills.
> If you stop the VM, compute bills stop but disk + IP still bill.

## 9 · Hardening checklist (before non-test traffic)

- [ ] Tighten `SRC_RANGES` from `0.0.0.0/0` to your office CIDR.
- [ ] Put Caddy or an HTTPS load balancer in front for TLS.
- [ ] Enable automatic snapshots on the PD (already attached in your
      VM create command via `disk-resource-policy=default-schedule-1`).
- [ ] Rotate `CCS_ADMIN_KEY` periodically; `ensure_admin` on next
      bootstrap will update the hash.
- [ ] Set `CCS_CORS_ORIGINS` to your explicit origin list instead of `*`.
