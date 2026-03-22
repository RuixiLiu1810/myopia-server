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

## 首次安装向导（类似 WordPress）

默认开启首次安装模式：当数据库里没有 `admin` 用户时，服务会进入安装态。

1. 打开 `http://127.0.0.1:8000/setup`
2. 在向导页面先执行：`写入 Env -> 执行预检 -> 执行迁移`
3. 再填写初始管理员用户名/密码并提交
4. 初始化完成后使用 `/v1/auth/login` 正常登录

安装态接口：

- `GET /v1/setup/status`
- `GET /v1/setup/diagnostics`
- `POST /v1/setup/env/write`
- `POST /v1/setup/run/preflight`
- `POST /v1/setup/run/migrate`

安装态下默认会锁住非安装接口（返回 `503`），可通过环境变量关闭：

- `MYOPIA_SETUP_ENFORCE_LOCK=0`

## 关键环境变量

- `MYOPIA_DATABASE_URL`
- `MYOPIA_MODEL_DIR`
- `MYOPIA_AUTH_SECRET`
- `MYOPIA_ALLOWED_ORIGINS`
- `MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES=0`
- `MYOPIA_SETUP_ENABLED=1`
- `MYOPIA_SETUP_ENFORCE_LOCK=1`
- `MYOPIA_INSTALL_MARKER_FILE`（可选）
- `MYOPIA_SETUP_ENV_FILE`（默认 `/etc/myopia/server.env`）
- `MYOPIA_SETUP_COMMAND_TIMEOUT_SECONDS`（默认 `240`）

## 上线

按：`docs/SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md`

分阶段执行清单：`docs/GO_LIVE_PHASE_EXECUTION.md`
