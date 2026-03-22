# Myopia Server

后端服务仓：负责 API、鉴权、数据库、模型推理与部署。

## 目录

- `backend/`
- `apps/server/`、`apps/shared/`
- `run_server.py`
- `launcher_server.py`
- `deploy/`
- `docs/`

## 本地启动

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r ../requirements.txt
../.venv/bin/alembic -c alembic.ini upgrade head
cd ..
python run_server.py --host 0.0.0.0 --port 8000
```

## 关键环境变量

- `MYOPIA_DATABASE_URL`
- `MYOPIA_MODEL_DIR`
- `MYOPIA_AUTH_SECRET`
- `MYOPIA_ALLOWED_ORIGINS`
- `MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES=0`

## 上线

按：`docs/SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md`
