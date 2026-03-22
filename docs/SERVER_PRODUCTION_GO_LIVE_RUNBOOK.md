# Server Production Go-Live Runbook

## 1. Scope

This runbook is for **server-only production deployment**:

1. `myopia-server` hosts backend API only.
2. Doctor/Ops clients call backend over network.
3. PostgreSQL is the only persistent DB.

## 2. Required Baseline

1. Linux host with `systemd`.
2. Python virtualenv prepared at `/opt/myopia_app/.venv`.
3. Project deployed at `/opt/myopia_app`.
4. PostgreSQL reachable from server host.

## 3. Production Env File

Create `/etc/myopia/server.env` based on:

1. [server.env.example](/Users/liuruixi/Documents/Code/myopia_app/deploy/env/server.env.example)

Minimum required checks:

1. `MYOPIA_DATABASE_URL` points to production DB.
2. `MYOPIA_MODEL_DIR` points to deployed models.
3. `MYOPIA_ALLOWED_ORIGINS` uses real origins, not `*`.
4. `MYOPIA_AUTH_SECRET` is strong random (replace default).
5. `MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES=0`.

## 4. DB Migration (must run before service start)

```bash
cd /opt/myopia_app/backend
. ../.venv/bin/activate
MYOPIA_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' ../.venv/bin/alembic -c alembic.ini upgrade head
MYOPIA_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' ../.venv/bin/alembic -c alembic.ini current
```

Expected:

1. current revision is `0004_pred_family_results (head)`.

## 5. Systemd Service Install

Copy service file:

1. [myopia-server.service](/Users/liuruixi/Documents/Code/myopia_app/deploy/systemd/myopia-server.service)

Install commands:

```bash
sudo cp /opt/myopia_app/deploy/systemd/myopia-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now myopia-server
sudo systemctl status myopia-server --no-pager
```

## 6. Initial Admin Bootstrap

Run once (or password reset when needed):

```bash
cd /opt/myopia_app
./.venv/bin/python backend/scripts/bootstrap_admin.py \
  --username admin \
  --password '<strong-password>' \
  --display-name 'System Admin' \
  --database-url 'postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' \
  --reset-password-if-exists \
  --activate-if-exists
```

## 7. Go-Live Verification

API health:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/model-info
```

Security gates:

```bash
# Should not expose public clinical routes by default:
curl -i http://127.0.0.1:8000/v1/patients

# Clinical routes require token:
curl -i http://127.0.0.1:8000/v1/clinical/patients
```

Expected:

1. `/v1/patients` is not available (404).
2. `/v1/clinical/patients` returns 401 without token.

## 8. Rollback Plan

If startup fails after release:

1. Check logs: `journalctl -u myopia-server -n 200 --no-pager`
2. Fix env or model path issues in `/etc/myopia/server.env`.
3. Restart: `sudo systemctl restart myopia-server`
4. If migration-related incident, restore DB snapshot and redeploy previous revision.
