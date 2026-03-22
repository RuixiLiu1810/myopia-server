# Backend（独立部署）

后端目录与 notebook/实验文件解耦，仅提供 API 服务。

统一入口（在仓库根目录执行）：

```bash
python myopia_app/run_server.py     # 后端服务（推荐）
python myopia_app/run_doctor.py     # 医生端 UI（开发联调）
python myopia_app/run_ops.py        # 运维端 UI（内网/VPN）
```

运行时脚本总览见：[docs/RUNTIME_SCRIPT_MAP.md](/Users/liuruixi/Documents/Code/myopia_app/docs/RUNTIME_SCRIPT_MAP.md)。

## 目录

```text
backend/
├── myopia_backend/
│   ├── api.py                 # FastAPI 应用与路由
│   ├── db/                    # 数据库模型与会话层
│   ├── config.py              # 配置读取（环境变量）
│   ├── schemas.py             # 请求/响应模型
│   ├── model_defs.py          # 网络结构兼容类
│   ├── model_store.py         # 模型发现与加载
│   ├── preprocessing.py       # 输入预处理
│   └── inference_service.py   # 核心推理服务
├── alembic/                   # 数据库迁移脚本
│   └── versions/
├── alembic.ini
├── tests/
│   ├── test_inference_service.py  # 核心路由/输入单测
│   └── test_model_store.py        # 模型发现单测
├── scripts/
│   ├── run_backend.py         # 启动脚本
│   ├── bootstrap_admin.py     # 初始化管理员账号
│   ├── db_upgrade.py          # 数据库迁移执行脚本
│   ├── smoke_test_inference_api.py # 推理接口端到端冒烟
│   ├── smoke_test_assets_api.py    # 资产接口端到端冒烟
│   ├── smoke_test_clinical_api.py  # 临床接口端到端冒烟
│   ├── contract_check_backend.py # 前后端契约回归
│   ├── explain_routing.py     # 路由可解释性脚本
│   ├── run_unit_tests.py      # 单元测试运行脚本
│   ├── release_check_backend.py # 发布前一键检查
│   └── export_fen_family_state_dict.py # Fen/FenG .pth -> state_dict 转换
├── requirements.txt
└── .env.example
```

## 启动

```bash
cd myopia_app/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r ../requirements.txt
python scripts/run_backend.py \
  --model-dir ../models \
  --database-url postgresql+psycopg://myopia:myopia@127.0.0.1:5432/myopia \
  --default-device cpu \
  --storage-backend local \
  --local-storage-dir ../storage \
  --host 0.0.0.0 \
  --port 8000 \
  --max-visits 5 \
  --max-inline-image-bytes 8388608 \
  --max-inline-total-bytes 33554432
```

## 接口冒烟

```bash
. .venv/bin/activate
python scripts/smoke_test_inference_api.py --model-dir ../models --data-dir ../../all --device cpu
```

## Fen/FenG 格式迁移（先于多模型族接入）

将 `fen/` 与 `fenG/` 目录下的全模型 `.pth` 转换为 `models/` 下的 `*_state_dict.pt`：

```bash
cd myopia_app
. .venv/bin/activate
python backend/scripts/export_fen_family_state_dict.py
```

会生成转换清单：

```text
myopia_app/models/manifest_fen_family.json
```

资产接口冒烟：

```bash
. .venv/bin/activate
python scripts/smoke_test_assets_api.py --model-dir ../models --device cpu
```

临床接口冒烟：

```bash
. .venv/bin/activate
python scripts/smoke_test_clinical_api.py --model-dir ../models --device cpu
```

## 核心单元测试

```bash
. .venv/bin/activate
python scripts/run_unit_tests.py
```

## 发布前一键检查

```bash
. .venv/bin/activate
python scripts/release_check_backend.py --model-dir ../models --data-dir ../../all --device cpu
```

## 数据库迁移

```bash
cd myopia_app/backend
. .venv/bin/activate
python scripts/db_upgrade.py --revision head \
  --database-url postgresql+psycopg://myopia:myopia@127.0.0.1:5432/myopia
```

也可以使用环境变量：

```bash
export MYOPIA_DATABASE_URL=postgresql+psycopg://myopia:myopia@127.0.0.1:5432/myopia
python scripts/db_upgrade.py
```

## 初始化管理员（首次部署）

```bash
cd myopia_app/backend
. .venv/bin/activate
python scripts/bootstrap_admin.py \
  --username admin \
  --password 'ChangeMe123!' \
  --display-name 'System Admin' \
  --database-url postgresql+psycopg://myopia:myopia@127.0.0.1:5432/myopia
```

## 契约回归（前后端接口）

```bash
. .venv/bin/activate
python scripts/contract_check_backend.py --model-dir ../models --data-dir ../../all --device cpu
```

## 可解释性辅助

```bash
# 查看固定路由规则 + 当前模型文件映射
python scripts/explain_routing.py --model-dir ../models
```

接口：

```text
GET /routing-rules
GET /limits
POST /v1/files/upload-inline
GET /v1/files/{file_asset_id}
POST /v1/predict-assets
# 兼容接口（受限，仅 ops/admin token）
POST /v1/users
GET /v1/users
GET /v1/users/{user_id}
POST /v1/patients
GET /v1/patients
GET /v1/patients/{patient_id}
POST /v1/encounters
GET /v1/patients/{patient_id}/encounters
POST /v1/predictions
GET /v1/predictions/{prediction_run_id}
GET /v1/ops/health
GET /v1/ops/model-info
GET /v1/ops/db-status
GET /v1/ops/audit-logs
GET /v1/ops/users
POST /v1/ops/users
PATCH /v1/ops/users/{user_id}
POST /v1/ops/users/{user_id}/reset-password
POST /v1/ops/users/{user_id}/activate
POST /v1/ops/users/{user_id}/deactivate
GET /v1/ops/db/tables
GET /v1/ops/db/tables/{table_name}/schema
GET /v1/ops/db/tables/{table_name}/rows
```
