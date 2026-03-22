# Phase C Deployment Baseline

This directory contains production-oriented templates for Phase C (deployment decoupling):

- `systemd/myopia-server.service`: backend API lifecycle
- `systemd/myopia-ops.service`: ops web lifecycle (no backend process control by default)
- `docker/Dockerfile`: container image template
- `docker/docker-compose.phase_c.yml`: backend + postgres + ops baseline
- `env/server.env.example`: backend environment template
- `env/ops.env.example`: ops environment template

## Target Topology

1. `run_server.py` is the only backend process manager.
2. `run_ops.py` is optional and should be internal/VPN only.
3. Doctor client (`run_doctor.py` or packaged desktop app) only calls backend API.

## Systemd Quick Start

1. Copy environment templates:
   - `/etc/myopia/server.env` from `env/server.env.example`
   - `/etc/myopia/ops.env` from `env/ops.env.example`
2. Copy service files from `systemd/` to `/etc/systemd/system/`.
3. Edit paths in service files to your real project path.
4. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myopia-server
sudo systemctl enable --now myopia-ops
```

## Docker Compose Quick Start

```bash
cd deploy/docker
docker compose -f docker-compose.phase_c.yml up -d --build
```

## Verification Checklist

1. `curl http://127.0.0.1:8000/healthz` returns 200.
2. `http://127.0.0.1:8788/ops/launcher` is reachable only from internal/VPN scope.
3. Restart `myopia-ops` does not affect `myopia-server`.
4. Restart `myopia-server` does not require restarting doctor client package.

## Production Go-Live

Use the server runbook for release:

1. [SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md](/Users/liuruixi/Documents/Code/myopia_app/docs/SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md)
