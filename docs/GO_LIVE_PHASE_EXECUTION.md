# Go-Live 分阶段执行清单

> 更新时间：2026-03-22

## 0. 冻结基线（已完成）

1. `myopia-server` 当前基线提交：`758bd59`
2. `myopia-client` 当前基线提交：`10c4fd1`
3. 两仓已打标签并推送：`go-live-freeze-2026-03-22`

## 1. Phase 1 - 冻结功能范围（已完成）

1. 仅允许阻断级修复（P0/P1）。
2. 禁止新增功能与大改界面。
3. 上线期间以 tag 为唯一回滚锚点。

## 2. Phase 2 - 生产配置预检（进行中）

新增预检脚本：

1. [preflight_server_env.py](/Users/liuruixi/Documents/Code/myopia-server/backend/scripts/preflight_server_env.py)

执行命令：

```bash
cd /opt/myopia_app/backend
. ../.venv/bin/activate
python scripts/preflight_server_env.py --env-file /etc/myopia/server.env
```

通过标准：

1. 无 `[error]`。
2. `MYOPIA_ALLOWED_ORIGINS` 不含 `*`。
3. `MYOPIA_AUTH_SECRET` 非占位符且长度足够。
4. `MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES=0`。

## 3. Phase 3 - 数据库与迁移

```bash
cd /opt/myopia_app/backend
. ../.venv/bin/activate
MYOPIA_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' ../.venv/bin/alembic -c alembic.ini upgrade head
MYOPIA_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' ../.venv/bin/alembic -c alembic.ini current
```

通过标准：

1. Alembic 到 `head`。
2. 数据库连接正常。

## 4. Phase 4 - 服务部署与启动

```bash
sudo cp /opt/myopia_app/deploy/systemd/myopia-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now myopia-server
sudo systemctl status myopia-server --no-pager
```

通过标准：

1. 服务状态 `active (running)`。
2. 日志无连续异常重启。

## 5. Phase 5 - 首次安装初始化

1. 打开：`http://<server>:8000/setup`
2. 创建初始 admin。
3. 检查：`GET /v1/setup/status` 返回 `setup_required=false`。

## 6. Phase 6 - 客户端联调验收

1. doctor/ops 配置 API 地址。
2. 验证登录、患者、就诊、预测、ops 管理功能。
3. 验证安装态提示：未初始化时提示跳转 `/setup`。

## 7. Phase 7 - 上线前回归

```bash
cd /opt/myopia_app/backend
. ../.venv/bin/activate
python scripts/release_check_backend.py --model-dir ../models --data-dir ../../all --device cpu
python scripts/contract_check_backend.py --model-dir ../models --data-dir ../../all --device cpu
```

## 8. Phase 8 - 小流量上线与观测（24~72h）

1. 先小范围用户。
2. 监控错误率、接口延迟、数据库连接数。
3. 若异常，优先回滚到 tag：`go-live-freeze-2026-03-22`。

## 9. Phase 9 - 稳定后一周

1. 仅修复线上阻断问题。
2. 冻结后再启动“运维一键部署脚本包”迭代。

## 10. 参考文档

1. [SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md](/Users/liuruixi/Documents/Code/myopia-server/docs/SERVER_PRODUCTION_GO_LIVE_RUNBOOK.md)
2. [server.env.example](/Users/liuruixi/Documents/Code/myopia-server/deploy/env/server.env.example)
