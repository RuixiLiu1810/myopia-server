from __future__ import annotations

import csv
import io
import json
import math
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, or_, select, text

from ..db.models import AuditLog, Encounter, FileAsset, Patient, PredictionRun, User
from ..db.session import session_scope
from ..dependencies.rbac import AuthContext, require_roles
from ..inference_service import routing_rules
from ..model_store import list_available_model_assets, list_available_models
from ..schemas import (
    OpsActionRequest,
    OpsUserCreateRequest,
    OpsUserResetPasswordRequest,
    OpsUserUpdateRequest,
)
from ..security.auth import hash_password


_ALLOWED_ROLES = {"doctor", "operator", "ops", "admin"}
_OPS_MANAGEABLE_ROLES = {"doctor", "operator"}
_DB_TABLE_MODEL_MAP = {
    "users": User,
    "patients": Patient,
    "encounters": Encounter,
    "prediction_runs": PredictionRun,
    "file_assets": FileAsset,
    "audit_logs": AuditLog,
}
_OPS_JOB_LOCK = threading.Lock()
_OPS_JOBS: dict[str, dict] = {}
_OPS_JOB_ORDER: list[str] = []
_OPS_JOB_MAX = 300


def _masked_db_url(raw: str) -> str:
    value = str(raw or "")
    if "://" not in value or "@" not in value:
        return value
    scheme, rest = value.split("://", 1)
    creds, host_part = rest.split("@", 1)
    if ":" in creds:
        user, _pwd = creds.split(":", 1)
        safe_creds = f"{user}:***"
    else:
        safe_creds = "***"
    return f"{scheme}://{safe_creds}@{host_part}"


def _normalize_role(value: str | None) -> str:
    role = str(value or "").strip().lower()
    if role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role: {value}")
    return role


def _user_out(user: User) -> dict:
    return {
        "id": int(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


def _audit(
    *,
    action: str,
    actor: str,
    target_type: str,
    target_id: str | int,
    detail_json: dict | None,
) -> AuditLog:
    return AuditLog(
        action=action,
        actor=actor,
        target_type=target_type,
        target_id=str(target_id),
        detail_json=detail_json,
    )


def _can_manage_target(ctx: AuthContext, target_role: str) -> bool:
    role = str(ctx.role).strip().lower()
    if role == "admin":
        return True
    if role == "ops":
        return target_role in _OPS_MANAGEABLE_ROLES
    return False


def _can_assign_role(ctx: AuthContext, new_role: str) -> bool:
    role = str(ctx.role).strip().lower()
    if role == "admin":
        return True
    if role == "ops":
        return new_role in _OPS_MANAGEABLE_ROLES
    return False


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_jsonable(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _model_to_row(model_obj) -> dict:
    columns = model_obj.__table__.columns
    return {str(col.name): _jsonable(getattr(model_obj, col.name)) for col in columns}


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(_jsonable(value), ensure_ascii=False)
    return str(value)


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime_filter(value: str | None, *, param_name: str, is_end: bool = False) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    dt_obj: datetime | None = None
    try:
        dt_obj = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            date_obj = date.fromisoformat(raw)
            dt_obj = datetime.combine(
                date_obj,
                dtime.max if is_end else dtime.min,
                tzinfo=timezone.utc,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid datetime: {param_name}") from exc

    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    else:
        dt_obj = dt_obj.astimezone(timezone.utc)
    return dt_obj


def _build_audit_logs_query(
    *,
    q: str | None,
    actor: str | None,
    action: str | None,
    target_type: str | None,
    date_from: str | None,
    date_to: str | None,
):
    query = select(AuditLog)
    keyword = str(q or "").strip().lower()
    actor_kw = str(actor or "").strip().lower()
    action_kw = str(action or "").strip().lower()
    target_kw = str(target_type or "").strip().lower()

    if keyword:
        qv = f"%{keyword}%"
        query = query.where(
            or_(
                func.lower(func.coalesce(AuditLog.action, "")).like(qv),
                func.lower(func.coalesce(AuditLog.actor, "")).like(qv),
                func.lower(func.coalesce(AuditLog.target_type, "")).like(qv),
                func.lower(func.coalesce(AuditLog.target_id, "")).like(qv),
            )
        )
    if actor_kw:
        query = query.where(func.lower(func.coalesce(AuditLog.actor, "")).like(f"%{actor_kw}%"))
    if action_kw:
        query = query.where(func.lower(func.coalesce(AuditLog.action, "")).like(f"%{action_kw}%"))
    if target_kw:
        query = query.where(func.lower(func.coalesce(AuditLog.target_type, "")).like(f"%{target_kw}%"))

    dt_from = _parse_datetime_filter(date_from, param_name="date_from", is_end=False)
    dt_to = _parse_datetime_filter(date_to, param_name="date_to", is_end=True)
    if dt_from is not None:
        query = query.where(AuditLog.created_at >= dt_from)
    if dt_to is not None:
        query = query.where(AuditLog.created_at <= dt_to)

    return query.order_by(AuditLog.id.desc())


def _collect_ops_metrics(session, *, window_hours: int) -> dict:
    safe_hours = max(1, min(int(window_hours), 24 * 7))
    now_utc = datetime.now(tz=timezone.utc)
    window_start = now_utc - timedelta(hours=safe_hours)

    prediction_rows = (
        session.execute(select(PredictionRun).order_by(PredictionRun.id.desc()).limit(5000))
        .scalars()
        .all()
    )
    prediction_in_window = []
    for row in prediction_rows:
        created = _to_utc(row.created_at)
        if created is None or created < window_start:
            continue
        prediction_in_window.append(row)

    total_runs = len(prediction_in_window)
    latencies = sorted(
        [
            float(x.latency_ms)
            for x in prediction_in_window
            if x.latency_ms is not None and isinstance(x.latency_ms, (int, float))
        ]
    )
    avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
    p95_latency_ms = None
    if latencies:
        idx = max(0, min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1))
        p95_latency_ms = round(float(latencies[idx]), 2)

    audit_rows = (
        session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(5000))
        .scalars()
        .all()
    )
    audit_events_in_window = 0
    failed_runs = 0
    for row in audit_rows:
        created = _to_utc(row.created_at)
        if created is None or created < window_start:
            continue
        audit_events_in_window += 1
        action = str(row.action or "").strip().lower()
        if "prediction" in action and "failed" in action:
            failed_runs += 1

    success_runs = total_runs
    total_attempts = success_runs + failed_runs
    success_rate_pct = round(success_runs * 100.0 / total_attempts, 2) if total_attempts > 0 else None

    users_total = int(session.execute(select(func.count()).select_from(User)).scalar_one())
    users_active = int(
        session.execute(select(func.count()).select_from(User).where(User.is_active.is_(True))).scalar_one()
    )

    return {
        "generated_at": now_utc.isoformat(),
        "window_hours": safe_hours,
        "prediction": {
            "total_runs": total_runs,
            "success_runs": success_runs,
            "failed_runs": failed_runs,
            "success_rate_pct": success_rate_pct,
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": p95_latency_ms,
        },
        "users": {
            "total": users_total,
            "active": users_active,
        },
        "audit": {
            "events_in_window": audit_events_in_window,
        },
    }


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _job_public_view(job: dict) -> dict:
    return {
        "job_id": str(job.get("job_id") or ""),
        "job_type": str(job.get("job_type") or ""),
        "mode": str(job.get("mode") or "execute"),
        "actor": str(job.get("actor") or ""),
        "status": str(job.get("status") or "queued"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "note": str(job.get("note") or ""),
        "payload": _jsonable(job.get("payload") or {}),
        "logs": _jsonable(job.get("logs") or []),
    }


def _create_ops_job(*, job_type: str, mode: str, actor: str, payload: dict | None) -> dict:
    now = _now_utc_iso()
    job_id = f"job_{uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "job_type": str(job_type),
        "mode": str(mode),
        "actor": str(actor),
        "status": "queued",
        "started_at": now,
        "finished_at": None,
        "note": "任务已创建，等待执行",
        "payload": _jsonable(payload or {}),
        "logs": [{"at": now, "message": "任务已创建"}],
    }
    with _OPS_JOB_LOCK:
        _OPS_JOBS[job_id] = job
        _OPS_JOB_ORDER.insert(0, job_id)
        while len(_OPS_JOB_ORDER) > _OPS_JOB_MAX:
            dropped = _OPS_JOB_ORDER.pop()
            _OPS_JOBS.pop(dropped, None)
    return _job_public_view(job)


def _get_ops_job(job_id: str) -> dict | None:
    with _OPS_JOB_LOCK:
        job = _OPS_JOBS.get(str(job_id))
        if job is None:
            return None
        return _job_public_view(job)


def _list_ops_jobs(limit: int) -> list[dict]:
    safe_limit = max(1, min(int(limit), 200))
    with _OPS_JOB_LOCK:
        ids = _OPS_JOB_ORDER[:safe_limit]
        return [_job_public_view(_OPS_JOBS[jid]) for jid in ids if jid in _OPS_JOBS]


def _update_ops_job(job_id: str, **fields) -> dict | None:
    with _OPS_JOB_LOCK:
        job = _OPS_JOBS.get(str(job_id))
        if job is None:
            return None
        job.update(fields)
        return _job_public_view(job)


def _append_ops_job_log(job_id: str, message: str) -> None:
    with _OPS_JOB_LOCK:
        job = _OPS_JOBS.get(str(job_id))
        if job is None:
            return
        logs = job.setdefault("logs", [])
        logs.append({"at": _now_utc_iso(), "message": str(message)})
        if len(logs) > 80:
            del logs[:-80]


def _write_ops_job_audit(
    *,
    actor: str,
    job_id: str,
    job_type: str,
    mode: str,
    status: str,
    note: str,
    payload: dict | None,
) -> None:
    try:
        with session_scope() as session:
            session.add(
                _audit(
                    action=f"ops.action.{job_type}.{mode}",
                    actor=actor,
                    target_type="ops_job",
                    target_id=job_id,
                    detail_json={
                        "job_type": job_type,
                        "mode": mode,
                        "status": status,
                        "note": note,
                        "payload": _jsonable(payload or {}),
                    },
                )
            )
    except Exception:
        # Avoid crashing background job thread due to audit write failure.
        return


def _execute_backup_action(*, precheck: bool, settings) -> str:
    storage_dir = Path(str(settings.local_storage_dir or "")).expanduser()
    with session_scope() as session:
        session.execute(text("SELECT 1"))
    if not storage_dir.exists():
        raise RuntimeError(f"本地存储目录不存在: {storage_dir}")
    if not storage_dir.is_dir():
        raise RuntimeError(f"本地存储目录无效: {storage_dir}")
    if precheck:
        return "备份预检查通过：数据库可达，存储目录存在。"
    return "备份任务执行完成（最小实现：已完成连通性与目录检查）。"


def _execute_migration_check_action(*, precheck: bool) -> str:
    with session_scope() as session:
        session.execute(text("SELECT 1"))
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if not version:
        raise RuntimeError("未检测到 alembic_version，迁移状态未知。")
    if precheck:
        return f"迁移预检查通过：alembic_version={version}"
    return f"迁移检查执行完成：alembic_version={version}"


def _execute_reindex_action(*, precheck: bool, table_name: str) -> str:
    safe_table = str(table_name or "").strip().lower()
    if safe_table not in _DB_TABLE_MODEL_MAP:
        raise RuntimeError(f"不允许的表名: {table_name}")

    model = _DB_TABLE_MODEL_MAP[safe_table]
    with session_scope() as session:
        row_count = int(session.execute(select(func.count()).select_from(model)).scalar_one())
        if not precheck:
            # Minimal safe implementation across PostgreSQL/SQLite.
            session.execute(text(f'ANALYZE "{safe_table}"'))
    if precheck:
        return f"索引维护预检查通过：{safe_table} 当前行数 {row_count}"
    return f"索引维护执行完成：已分析 {safe_table}（行数 {row_count}）"


def _run_ops_job_worker(job_id: str, settings) -> None:
    with _OPS_JOB_LOCK:
        job = _OPS_JOBS.get(str(job_id))
        if job is None:
            return
        job_type = str(job.get("job_type") or "")
        mode = str(job.get("mode") or "execute")
        actor = str(job.get("actor") or "system")
        payload = dict(job.get("payload") or {})
        job["status"] = "running"
        job["note"] = "任务执行中"
    _append_ops_job_log(job_id, f"开始执行：{job_type} ({mode})")

    try:
        time.sleep(0.15)
        is_precheck = mode == "precheck"
        if job_type == "backup":
            note = _execute_backup_action(precheck=is_precheck, settings=settings)
        elif job_type == "migration-check":
            note = _execute_migration_check_action(precheck=is_precheck)
        elif job_type == "reindex":
            table_name = str(payload.get("table_name") or "prediction_runs").strip().lower()
            note = _execute_reindex_action(precheck=is_precheck, table_name=table_name)
        else:
            raise RuntimeError(f"未知动作类型: {job_type}")

        _update_ops_job(
            job_id,
            status="succeeded",
            finished_at=_now_utc_iso(),
            note=note,
        )
        _append_ops_job_log(job_id, note)
        _write_ops_job_audit(
            actor=actor,
            job_id=job_id,
            job_type=job_type,
            mode=mode,
            status="succeeded",
            note=note,
            payload=payload,
        )
    except Exception as exc:
        err_text = str(exc)
        _update_ops_job(
            job_id,
            status="failed",
            finished_at=_now_utc_iso(),
            note=err_text,
        )
        _append_ops_job_log(job_id, f"执行失败：{err_text}")
        _write_ops_job_audit(
            actor=actor,
            job_id=job_id,
            job_type=job_type,
            mode=mode,
            status="failed",
            note=err_text,
            payload=payload,
        )


def _enqueue_ops_job(*, job_type: str, req: OpsActionRequest | None, ctx: AuthContext, settings) -> dict:
    payload_req = req or OpsActionRequest()
    mode = "precheck" if bool(payload_req.precheck) else "execute"
    payload: dict[str, str] = {}
    if payload_req.reason:
        payload["reason"] = str(payload_req.reason).strip()
    if job_type == "reindex":
        table_name = str(payload_req.table_name or "prediction_runs").strip().lower()
        if table_name not in _DB_TABLE_MODEL_MAP:
            raise HTTPException(status_code=400, detail=f"table not allowed: {table_name}")
        payload["table_name"] = table_name

    created = _create_ops_job(
        job_type=job_type,
        mode=mode,
        actor=ctx.username,
        payload=payload,
    )
    _write_ops_job_audit(
        actor=ctx.username,
        job_id=str(created["job_id"]),
        job_type=job_type,
        mode=mode,
        status="queued",
        note="任务已提交",
        payload=payload,
    )

    threading.Thread(
        target=_run_ops_job_worker,
        args=(str(created["job_id"]), settings),
        daemon=True,
    ).start()

    action_name = {
        "backup": "备份任务",
        "migration-check": "迁移检查",
        "reindex": "索引维护",
    }.get(job_type, "动作任务")
    return {
        "ok": True,
        "job_id": created["job_id"],
        "job": created,
        "message": f"{action_name}已提交（{mode}）",
    }


def build_ops_router(settings) -> APIRouter:
    router = APIRouter(
        prefix="/v1/ops",
        tags=["ops"],
        dependencies=[Depends(require_roles("ops", "admin"))],
    )

    @router.get("/health")
    def ops_health(ctx: AuthContext = Depends(require_roles("ops", "admin"))):
        try:
            models = list_available_model_assets(settings.model_dir)
            with session_scope() as session:
                session.execute(text("SELECT 1"))
            return {
                "status": "ok",
                "actor": ctx.username,
                "role": ctx.role,
                "model_dir": settings.model_dir,
                "model_count": len(models),
                "storage_backend": settings.storage_backend,
                "local_storage_dir": settings.local_storage_dir,
                "database": {
                    "connected": True,
                    "url": _masked_db_url(settings.database_url),
                },
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"ops health check failed: {exc}") from exc

    @router.get("/model-info")
    def ops_model_info():
        try:
            models = list_available_models(settings.model_dir)
            assets = list_available_model_assets(settings.model_dir)
            grouped: dict[str, list[dict]] = {}
            for (seq_len, horizon), path in sorted(models.items()):
                grouped.setdefault(str(seq_len), []).append({"horizon": int(horizon), "file": path.name})
            family_groups: dict[str, dict[str, list[dict]]] = {}
            for (family, seq_len, horizon), path in sorted(assets.items()):
                family_groups.setdefault(family, {}).setdefault(str(seq_len), []).append(
                    {"horizon": int(horizon), "file": path.name}
                )
            return {
                "model_dir": settings.model_dir,
                "groups": grouped,
                "family_groups": family_groups,
                "routing_rules": routing_rules(max_seq_len=5),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to read model info: {exc}") from exc

    @router.get("/db-status")
    def db_status():
        try:
            with session_scope() as session:
                session.execute(text("SELECT 1"))
            return {
                "ok": True,
                "database_url": _masked_db_url(settings.database_url),
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"database check failed: {exc}") from exc

    @router.get("/metrics/summary")
    def metrics_summary(window_hours: int = 24):
        try:
            with session_scope() as session:
                return _collect_ops_metrics(session, window_hours=window_hours)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"collect metrics failed: {exc}") from exc

    @router.get("/alerts")
    def list_alerts(window_hours: int = 24):
        safe_hours = max(1, min(int(window_hours), 24 * 7))
        now_utc = datetime.now(tz=timezone.utc).isoformat()
        alerts: list[dict] = []

        try:
            with session_scope() as session:
                metrics = _collect_ops_metrics(session, window_hours=safe_hours)

                db_ok = True
                try:
                    session.execute(text("SELECT 1"))
                except Exception:
                    db_ok = False
                if not db_ok:
                    alerts.append(
                        {
                            "code": "DB_UNREACHABLE",
                            "level": "high",
                            "title": "数据库不可达",
                            "detail": "数据库连接检查失败，请先排查 server 与 DB 连接。",
                            "suggestion": "检查数据库实例状态、连接串、网络访问策略。",
                            "created_at": now_utc,
                        }
                    )

                model_count = len(list_available_model_assets(settings.model_dir))
                if model_count <= 0:
                    alerts.append(
                        {
                            "code": "MODEL_NOT_READY",
                            "level": "high",
                            "title": "模型未就绪",
                            "detail": "当前未发现可用模型文件，预测请求将不可用。",
                            "suggestion": "检查模型目录挂载与模型发布流程。",
                            "created_at": now_utc,
                        }
                    )

                pred = metrics.get("prediction") or {}
                total_runs = int(pred.get("total_runs") or 0)
                failed_runs = int(pred.get("failed_runs") or 0)
                if total_runs <= 0:
                    alerts.append(
                        {
                            "code": "PREDICTION_IDLE",
                            "level": "medium",
                            "title": "预测流量为 0",
                            "detail": f"最近 {safe_hours} 小时内没有预测执行记录。",
                            "suggestion": "确认医生端连接状态与预测流程是否正常。",
                            "created_at": now_utc,
                        }
                    )
                if failed_runs > 0:
                    alerts.append(
                        {
                            "code": "PREDICTION_FAILURE",
                            "level": "medium",
                            "title": "存在预测失败记录",
                            "detail": f"最近 {safe_hours} 小时内检测到 {failed_runs} 条失败相关事件。",
                            "suggestion": "进入审计中心筛选 prediction 相关 action 排查根因。",
                            "created_at": now_utc,
                        }
                    )

                users_total = int(session.execute(select(func.count()).select_from(User)).scalar_one())
                users_inactive = int(
                    session.execute(
                        select(func.count()).select_from(User).where(User.is_active.is_(False))
                    ).scalar_one()
                )
                if users_total > 0 and users_inactive > 0:
                    ratio = round(users_inactive * 100.0 / users_total, 1)
                    alerts.append(
                        {
                            "code": "USER_INACTIVE_RATIO",
                            "level": "low",
                            "title": "存在停用账号",
                            "detail": f"当前停用账号 {users_inactive}/{users_total}（{ratio}%）。",
                            "suggestion": "定期清理无效账号并核对权限分配。",
                            "created_at": now_utc,
                        }
                    )

                if not alerts:
                    alerts.append(
                        {
                            "code": "ALL_CLEAR",
                            "level": "info",
                            "title": "当前无高优先级告警",
                            "detail": "关键服务与数据状态正常。",
                            "suggestion": "保持定期巡检并关注审计异常趋势。",
                            "created_at": now_utc,
                        }
                    )

                return {
                    "generated_at": now_utc,
                    "window_hours": safe_hours,
                    "alerts": alerts,
                }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"collect alerts failed: {exc}") from exc

    @router.get("/jobs")
    def list_jobs(limit: int = 30):
        return {"jobs": _list_ops_jobs(limit=limit)}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = _get_ops_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job

    @router.post("/actions/backup")
    def create_backup_action_job(
        req: OpsActionRequest | None = None,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        return _enqueue_ops_job(job_type="backup", req=req, ctx=ctx, settings=settings)

    @router.post("/actions/migration-check")
    def create_migration_check_action_job(
        req: OpsActionRequest | None = None,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        return _enqueue_ops_job(job_type="migration-check", req=req, ctx=ctx, settings=settings)

    @router.post("/actions/reindex")
    def create_reindex_action_job(
        req: OpsActionRequest | None = None,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        return _enqueue_ops_job(job_type="reindex", req=req, ctx=ctx, settings=settings)

    @router.get("/audit-logs")
    def list_audit_logs(
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        try:
            with session_scope() as session:
                query = _build_audit_logs_query(
                    q=q,
                    actor=actor,
                    action=action,
                    target_type=target_type,
                    date_from=date_from,
                    date_to=date_to,
                )
                rows = (
                    session.execute(query.limit(safe_limit).offset(safe_offset))
                    .scalars()
                    .all()
                )
                return [
                    {
                        "id": int(r.id),
                        "action": r.action,
                        "actor": r.actor,
                        "target_type": r.target_type,
                        "target_id": r.target_id,
                        "detail_json": r.detail_json,
                        "request_id": r.request_id,
                        "source_ip": r.source_ip,
                        "notes": r.notes,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list audit logs failed: {exc}") from exc

    @router.get("/audit-logs/export")
    def export_audit_logs_csv(
        limit: int = 1000,
        offset: int = 0,
        q: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        safe_limit = max(1, min(int(limit), 5000))
        safe_offset = max(0, int(offset))
        try:
            with session_scope() as session:
                query = _build_audit_logs_query(
                    q=q,
                    actor=actor,
                    action=action,
                    target_type=target_type,
                    date_from=date_from,
                    date_to=date_to,
                )
                rows = (
                    session.execute(query.limit(safe_limit).offset(safe_offset))
                    .scalars()
                    .all()
                )

                columns = [
                    "id",
                    "created_at",
                    "action",
                    "actor",
                    "target_type",
                    "target_id",
                    "request_id",
                    "source_ip",
                    "notes",
                    "detail_json",
                ]
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "id": _csv_cell(row.id),
                            "created_at": _csv_cell(row.created_at),
                            "action": _csv_cell(row.action),
                            "actor": _csv_cell(row.actor),
                            "target_type": _csv_cell(row.target_type),
                            "target_id": _csv_cell(row.target_id),
                            "request_id": _csv_cell(row.request_id),
                            "source_ip": _csv_cell(row.source_ip),
                            "notes": _csv_cell(row.notes),
                            "detail_json": _csv_cell(row.detail_json),
                        }
                    )

                stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                filename = f"audit_logs_{stamp}.csv"
                headers = {
                    "Content-Disposition": f'attachment; filename="{filename}"',
                }
                content = csv_buffer.getvalue().encode("utf-8-sig")
                return Response(content=content, media_type="text/csv; charset=utf-8", headers=headers)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"export audit logs failed: {exc}") from exc

    @router.get("/users")
    def list_users(
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ):
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        try:
            with session_scope() as session:
                query = select(User).order_by(User.id.asc())
                if q:
                    qv = f"%{q.strip().lower()}%"
                    query = query.where(
                        func.lower(User.username).like(qv) | func.lower(func.coalesce(User.display_name, "")).like(qv)
                    )
                if role:
                    query = query.where(User.role == _normalize_role(role))
                if is_active is not None:
                    query = query.where(User.is_active == bool(is_active))
                rows = session.execute(query.limit(safe_limit).offset(safe_offset)).scalars().all()
                return [_user_out(row) for row in rows]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list ops users failed: {exc}") from exc

    @router.post("/users")
    def create_user(req: OpsUserCreateRequest, ctx: AuthContext = Depends(require_roles("ops", "admin"))):
        username = str(req.username or "").strip().lower()
        if not username:
            raise HTTPException(status_code=400, detail="username cannot be empty")
        role = _normalize_role(req.role or "operator")
        if not _can_assign_role(ctx, role):
            raise HTTPException(status_code=403, detail=f"cannot assign role: {role}")
        if not str(req.password or "").strip():
            raise HTTPException(status_code=400, detail="password cannot be empty")
        try:
            password_hash = hash_password(req.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            with session_scope() as session:
                exists = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
                if exists is not None:
                    raise HTTPException(status_code=409, detail="username already exists")
                user = User(
                    username=username,
                    display_name=(req.display_name or "").strip() or None,
                    role=role,
                    is_active=bool(req.is_active),
                    password_hash=password_hash,
                )
                session.add(user)
                session.flush()
                session.add(
                    _audit(
                        action="ops.user.create",
                        actor=ctx.username,
                        target_type="user",
                        target_id=user.id,
                        detail_json={"after": _user_out(user)},
                    )
                )
                return _user_out(user)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"create ops user failed: {exc}") from exc

    @router.patch("/users/{user_id}")
    def update_user(
        user_id: int,
        req: OpsUserUpdateRequest,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        try:
            with session_scope() as session:
                user = session.get(User, int(user_id))
                if user is None:
                    raise HTTPException(status_code=404, detail=f"user not found: {user_id}")
                if not _can_manage_target(ctx, str(user.role).strip().lower()):
                    raise HTTPException(status_code=403, detail="cannot manage target user role")

                before = _user_out(user)
                changed = False

                if req.display_name is not None:
                    user.display_name = (req.display_name or "").strip() or None
                    changed = True
                if req.role is not None:
                    new_role = _normalize_role(req.role)
                    if not _can_assign_role(ctx, new_role):
                        raise HTTPException(status_code=403, detail=f"cannot assign role: {new_role}")
                    user.role = new_role
                    changed = True
                if req.is_active is not None:
                    if int(user.id) == int(ctx.user_id) and not bool(req.is_active):
                        raise HTTPException(status_code=400, detail="cannot deactivate yourself")
                    user.is_active = bool(req.is_active)
                    changed = True

                if not changed:
                    raise HTTPException(status_code=400, detail="no fields to update")

                session.flush()
                after = _user_out(user)
                session.add(
                    _audit(
                        action="ops.user.update",
                        actor=ctx.username,
                        target_type="user",
                        target_id=user.id,
                        detail_json={"before": before, "after": after},
                    )
                )
                return after
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"update ops user failed: {exc}") from exc

    @router.post("/users/{user_id}/reset-password")
    def reset_user_password(
        user_id: int,
        req: OpsUserResetPasswordRequest,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        if not str(req.new_password or "").strip():
            raise HTTPException(status_code=400, detail="new_password cannot be empty")
        try:
            password_hash = hash_password(req.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            with session_scope() as session:
                user = session.get(User, int(user_id))
                if user is None:
                    raise HTTPException(status_code=404, detail=f"user not found: {user_id}")
                if not _can_manage_target(ctx, str(user.role).strip().lower()):
                    raise HTTPException(status_code=403, detail="cannot manage target user role")
                user.password_hash = password_hash
                session.flush()
                session.add(
                    _audit(
                        action="ops.user.reset_password",
                        actor=ctx.username,
                        target_type="user",
                        target_id=user.id,
                        detail_json={"username": user.username},
                    )
                )
                return {"ok": True}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"reset password failed: {exc}") from exc

    @router.post("/users/{user_id}/activate")
    def activate_user(user_id: int, ctx: AuthContext = Depends(require_roles("ops", "admin"))):
        return update_user(
            user_id=user_id,
            req=OpsUserUpdateRequest(is_active=True),
            ctx=ctx,
        )

    @router.post("/users/{user_id}/deactivate")
    def deactivate_user(user_id: int, ctx: AuthContext = Depends(require_roles("ops", "admin"))):
        return update_user(
            user_id=user_id,
            req=OpsUserUpdateRequest(is_active=False),
            ctx=ctx,
        )

    @router.get("/db/tables")
    def list_db_tables():
        try:
            with session_scope() as session:
                items = []
                for name, model in sorted(_DB_TABLE_MODEL_MAP.items()):
                    count = session.execute(select(func.count()).select_from(model)).scalar_one()
                    items.append({"name": name, "row_count": int(count)})
                return {"tables": items}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list db tables failed: {exc}") from exc

    @router.get("/db/tables/{table_name}/schema")
    def get_table_schema(table_name: str):
        model = _DB_TABLE_MODEL_MAP.get(str(table_name).strip().lower())
        if model is None:
            raise HTTPException(status_code=404, detail=f"table not allowed: {table_name}")
        cols = []
        for col in model.__table__.columns:
            cols.append(
                {
                    "name": str(col.name),
                    "type": str(col.type),
                    "nullable": bool(col.nullable),
                    "primary_key": bool(col.primary_key),
                }
            )
        return {"table": table_name, "columns": cols}

    @router.get("/db/tables/{table_name}/rows")
    def get_table_rows(table_name: str, limit: int = 50, offset: int = 0):
        model = _DB_TABLE_MODEL_MAP.get(str(table_name).strip().lower())
        if model is None:
            raise HTTPException(status_code=404, detail=f"table not allowed: {table_name}")
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        try:
            with session_scope() as session:
                query = select(model)
                if hasattr(model, "id"):
                    query = query.order_by(model.id.desc())
                rows = session.execute(query.limit(safe_limit).offset(safe_offset)).scalars().all()
                return {
                    "table": table_name,
                    "limit": safe_limit,
                    "offset": safe_offset,
                    "rows": [_model_to_row(row) for row in rows],
                }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"read db table rows failed: {exc}") from exc

    @router.get("/db/tables/{table_name}/rows/export")
    def export_table_rows_csv(table_name: str, limit: int = 1000, offset: int = 0):
        model = _DB_TABLE_MODEL_MAP.get(str(table_name).strip().lower())
        if model is None:
            raise HTTPException(status_code=404, detail=f"table not allowed: {table_name}")
        safe_limit = max(1, min(int(limit), 5000))
        safe_offset = max(0, int(offset))

        try:
            with session_scope() as session:
                query = select(model)
                if hasattr(model, "id"):
                    query = query.order_by(model.id.desc())
                rows = session.execute(query.limit(safe_limit).offset(safe_offset)).scalars().all()

                columns = [str(col.name) for col in model.__table__.columns]
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow({col: _csv_cell(getattr(row, col)) for col in columns})

                stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                filename = f"{str(table_name).strip().lower()}_rows_{stamp}.csv"
                headers = {
                    "Content-Disposition": f'attachment; filename="{filename}"',
                }
                # UTF-8 BOM for better spreadsheet compatibility.
                content = csv_buffer.getvalue().encode("utf-8-sig")
                return Response(content=content, media_type="text/csv; charset=utf-8", headers=headers)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"export db table rows failed: {exc}") from exc

    return router
