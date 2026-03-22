from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db.models import AuditLog, User
from ..db.session import session_scope
from ..install_state import get_setup_status, write_install_marker
from ..schemas import (
    SetupBootstrapRequest,
    SetupBootstrapResponse,
    SetupCommandRunRequest,
    SetupCommandRunResponse,
    SetupDiagnosticsResponse,
    SetupEnvWriteRequest,
    SetupEnvWriteResponse,
    SetupStatusResponse,
)
from ..security.auth import hash_password

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
PREFLIGHT_SCRIPT = BACKEND_DIR / "scripts" / "preflight_server_env.py"
_MODEL_PATTERNS = (
    "Xu*b_state_dict.pt",
    "Xu*b.pth",
    "Fen*b_state_dict.pt",
    "Fen*b.pth",
    "FenG*b_state_dict.pt",
    "FenG*b.pth",
)


def _read_os_pretty_name() -> str:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return "unknown"
    values: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values.get("PRETTY_NAME") or values.get("NAME") or "unknown"


def _count_model_assets(model_dir: str) -> tuple[bool, int]:
    root = Path(str(model_dir or "").strip()).expanduser()
    if not root.exists() or not root.is_dir():
        return False, 0
    files: set[str] = set()
    for pattern in _MODEL_PATTERNS:
        for p in root.glob(pattern):
            if p.is_file():
                files.add(str(p.resolve()))
    return True, len(files)


def _safe_output(value: str, *, limit: int = 24000) -> str:
    text_value = str(value or "")
    if len(text_value) <= limit:
        return text_value
    return text_value[-limit:]


def _run_command(
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout_seconds)),
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "return_code": int(proc.returncode),
            "stdout": _safe_output(proc.stdout),
            "stderr": _safe_output(proc.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "return_code": 124,
            "stdout": _safe_output(exc.stdout or ""),
            "stderr": _safe_output((exc.stderr or "") + "\n[timeout] command exceeded allowed time"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "return_code": 1,
            "stdout": "",
            "stderr": f"runner error: {exc}",
        }


def _setup_env_defaults(settings) -> dict[str, object]:
    origins = settings.allowed_origins or ["*"]
    return {
        "database_url": settings.database_url,
        "model_dir": settings.model_dir,
        "default_device": settings.default_device or "cpu",
        "storage_backend": settings.storage_backend,
        "local_storage_dir": settings.local_storage_dir,
        "allowed_origins": ",".join(origins),
        "auth_secret": "",
        "auth_token_ttl_minutes": settings.auth_token_ttl_minutes,
        "max_visits": settings.max_visits,
        "max_inline_image_bytes": settings.max_inline_image_bytes,
        "max_inline_total_bytes": settings.max_inline_total_bytes,
        "setup_enabled": settings.setup_enabled,
        "setup_enforce_lock": settings.setup_enforce_lock,
        "enable_legacy_public_clinical_routes": settings.enable_legacy_public_clinical_routes,
    }


def _validate_env_payload(req: SetupEnvWriteRequest) -> tuple[str, bool]:
    if not str(req.database_url or "").strip():
        raise HTTPException(status_code=400, detail="database_url cannot be empty")
    if not str(req.model_dir or "").strip():
        raise HTTPException(status_code=400, detail="model_dir cannot be empty")
    if not str(req.local_storage_dir or "").strip():
        raise HTTPException(status_code=400, detail="local_storage_dir cannot be empty")
    if not str(req.allowed_origins or "").strip():
        raise HTTPException(status_code=400, detail="allowed_origins cannot be empty")

    raw = str(req.auth_secret or "").strip()
    if raw:
        if len(raw) < 24:
            raise HTTPException(status_code=400, detail="auth_secret too short (<24)")
        return raw, False
    return secrets.token_urlsafe(48), True


def _render_server_env(req: SetupEnvWriteRequest, *, auth_secret: str) -> str:
    lines = [
        f"MYOPIA_DATABASE_URL={req.database_url.strip()}",
        f"MYOPIA_MODEL_DIR={req.model_dir.strip()}",
        f"MYOPIA_DEFAULT_DEVICE={str(req.default_device or 'cpu').strip()}",
        f"MYOPIA_STORAGE_BACKEND={str(req.storage_backend or 'local').strip()}",
        f"MYOPIA_LOCAL_STORAGE_DIR={req.local_storage_dir.strip()}",
        f"MYOPIA_ALLOWED_ORIGINS={req.allowed_origins.strip()}",
        f"MYOPIA_AUTH_SECRET={auth_secret}",
        f"MYOPIA_AUTH_TOKEN_TTL_MINUTES={int(req.auth_token_ttl_minutes)}",
        f"MYOPIA_MAX_VISITS={int(req.max_visits)}",
        f"MYOPIA_MAX_INLINE_IMAGE_BYTES={int(req.max_inline_image_bytes)}",
        f"MYOPIA_MAX_INLINE_TOTAL_BYTES={int(req.max_inline_total_bytes)}",
        "MYOPIA_SKIP_STARTUP_CHECK=0",
        f"MYOPIA_ENABLE_LEGACY_PUBLIC_CLINICAL_ROUTES={1 if req.enable_legacy_public_clinical_routes else 0}",
        f"MYOPIA_SETUP_ENABLED={1 if req.setup_enabled else 0}",
        f"MYOPIA_SETUP_ENFORCE_LOCK={1 if req.setup_enforce_lock else 0}",
    ]
    return "\n".join(lines) + "\n"


def _collect_diagnostics(settings) -> dict:
    setup = get_setup_status(settings).to_dict()
    model_dir_exists, model_asset_count = _count_model_assets(settings.model_dir)

    db_ok = bool(setup.get("db_ready"))
    if db_ok:
        db_message = "ok"
    else:
        reasons = setup.get("reasons") or []
        db_message = ", ".join([str(x) for x in reasons if str(x).strip()]) or "database_unavailable_or_migrations_pending"

    return {
        "setup": setup,
        "env_file": settings.setup_env_file,
        "python_version": sys.version.split(" ")[0],
        "os_pretty_name": _read_os_pretty_name(),
        "model_dir": settings.model_dir,
        "model_dir_exists": model_dir_exists,
        "model_asset_count": model_asset_count,
        "db_ok": db_ok,
        "db_message": db_message,
        "env_file_exists": Path(settings.setup_env_file).expanduser().exists(),
    }


def _setup_page_html(status_payload: dict, env_defaults: dict[str, object], diagnostics: dict) -> str:
    status_json = json.dumps(status_payload, ensure_ascii=True)
    env_json = json.dumps(env_defaults, ensure_ascii=True)
    diag_json = json.dumps(diagnostics, ensure_ascii=True)
    template = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Myopia Server Installer</title>
  <style>
    :root {
      --bg:#f4f7fb;
      --card:#ffffff;
      --line:#dbe3ef;
      --text:#142033;
      --muted:#4a5a71;
      --primary:#0066cc;
      --primary-soft:#e6f1ff;
      --ok:#0f7b3f;
      --ok-bg:#eafaf0;
      --warn:#935f00;
      --warn-bg:#fff9e6;
      --err:#ad2534;
      --err-bg:#fff0f2;
      --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top right, #eaf2ff, var(--bg) 55%);
      color: var(--text);
      font-family: "Segoe UI", "PingFang SC", "Microsoft Yahei", sans-serif;
    }
    .wrap { max-width: 1080px; margin: 28px auto; padding: 0 16px 28px; }
    .hero {
      background: linear-gradient(120deg, #ffffff 10%, #edf4ff 100%);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px 18px 14px;
      box-shadow: 0 14px 30px rgba(18, 40, 79, .08);
      margin-bottom: 16px;
    }
    .hero h1 { margin: 0 0 6px; font-size: 25px; }
    .hero p { margin: 0; color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: 1.3fr .9fr;
      gap: 14px;
      align-items: start;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 8px 24px rgba(13, 30, 60, .06);
      padding: 14px;
    }
    .card h2 { margin: 0 0 8px; font-size: 18px; }
    .card p.tip { margin: 0 0 10px; color: var(--muted); font-size: 13px; }
    .status {
      margin: 10px 0;
      border-radius: 10px;
      border: 1px solid transparent;
      padding: 9px 11px;
      font-size: 13px;
      line-height: 1.35;
      white-space: pre-wrap;
    }
    .ok { color: var(--ok); background: var(--ok-bg); border-color: #bde8ca; }
    .warn { color: var(--warn); background: var(--warn-bg); border-color: #f6de9f; }
    .err { color: var(--err); background: var(--err-bg); border-color: #f6bcc3; }
    .rows { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px 12px; }
    .row { display: grid; gap: 5px; margin-bottom: 8px; }
    label { font-size: 12px; font-weight: 700; color: #304566; }
    input, select {
      width: 100%;
      border: 1px solid #ccd7e8;
      border-radius: 9px;
      padding: 9px 10px;
      font-size: 13px;
      background: #fff;
      color: #1a2740;
    }
    .line { border-top: 1px dashed var(--line); margin: 10px 0; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; }
    button {
      border: 0;
      border-radius: 9px;
      cursor: pointer;
      padding: 9px 12px;
      font-size: 13px;
      font-weight: 700;
      background: var(--primary);
      color: #fff;
    }
    button.secondary { background: #2f425f; }
    button.ghost { background: var(--primary-soft); color: #164f8a; }
    button:disabled { opacity: .6; cursor: not-allowed; }
    .kv {
      margin: 0;
      display: grid;
      gap: 8px;
    }
    .kv li {
      list-style: none;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 12px;
    }
    .k { color: #4a5a71; }
    .v { font-family: var(--mono); font-size: 12px; word-break: break-all; }
    pre {
      margin: 0;
      min-height: 220px;
      max-height: 460px;
      overflow: auto;
      border-radius: 12px;
      border: 1px solid #c7d6ea;
      background: #f8fbff;
      color: #1f3554;
      padding: 10px;
      font-size: 12px;
      line-height: 1.42;
      font-family: var(--mono);
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .rows { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"hero\">
      <h1>Myopia Server Install Wizard</h1>
      <p>图形化执行安装阶段：写入配置、预检、迁移数据库、创建管理员。系统级 bootstrap（apt/git/venv）仍需先通过 SSH 执行一次。</p>
      <div id=\"setup-status\" class=\"status warn\">正在读取安装状态...</div>
    </div>

    <div class=\"grid\">
      <div class=\"card\">
        <h2>Step 1 - 服务配置</h2>
        <p class=\"tip\">写入 server.env 后，可直接在页面触发预检和迁移，不需要再手打命令。</p>
        <div class=\"rows\">
          <div class=\"row\">
            <label for=\"database_url\">MYOPIA_DATABASE_URL</label>
            <input id=\"database_url\" />
          </div>
          <div class=\"row\">
            <label for=\"model_dir\">MYOPIA_MODEL_DIR</label>
            <input id=\"model_dir\" />
          </div>
          <div class=\"row\">
            <label for=\"local_storage_dir\">MYOPIA_LOCAL_STORAGE_DIR</label>
            <input id=\"local_storage_dir\" />
          </div>
          <div class=\"row\">
            <label for=\"allowed_origins\">MYOPIA_ALLOWED_ORIGINS</label>
            <input id=\"allowed_origins\" />
          </div>
          <div class=\"row\">
            <label for=\"default_device\">MYOPIA_DEFAULT_DEVICE</label>
            <input id=\"default_device\" />
          </div>
          <div class=\"row\">
            <label for=\"auth_secret\">MYOPIA_AUTH_SECRET（留空自动生成）</label>
            <input id=\"auth_secret\" type=\"password\" placeholder=\"留空则自动生成安全密钥\" />
          </div>
        </div>
        <div class=\"actions\" style=\"margin-top:8px\">
          <button id=\"btn-write-env\">写入 Env</button>
          <button id=\"btn-preflight\" class=\"secondary\">执行预检</button>
          <button id=\"btn-migrate\">执行迁移</button>
          <button id=\"btn-refresh\" class=\"ghost\">刷新状态</button>
        </div>

        <div class=\"line\"></div>

        <h2>Step 2 - 初始化管理员</h2>
        <p class=\"tip\">仅首次安装可用。初始化完成后，系统会自动退出安装态。</p>
        <form id=\"setup-form\">
          <div class=\"rows\">
            <div class=\"row\">
              <label for=\"username\">Admin Username</label>
              <input id=\"username\" value=\"admin\" required />
            </div>
            <div class=\"row\">
              <label for=\"display_name\">Display Name</label>
              <input id=\"display_name\" value=\"System Admin\" />
            </div>
            <div class=\"row\" style=\"grid-column: 1 / -1;\">
              <label for=\"password\">Password (>= 8 chars)</label>
              <input id=\"password\" type=\"password\" minlength=\"8\" required />
            </div>
          </div>
          <div class=\"actions\">
            <button id=\"btn-bootstrap\" type=\"submit\">创建管理员并完成安装</button>
          </div>
        </form>
      </div>

      <div class=\"card\">
        <h2>安装诊断</h2>
        <ul class=\"kv\" id=\"diag-kv\"></ul>
        <div class=\"line\"></div>
        <h2>执行日志</h2>
        <pre id=\"command-log\">等待操作...</pre>
      </div>
    </div>
  </div>

  <script>
    const initialStatus = __STATUS_JSON__;
    const envDefaults = __ENV_JSON__;
    const initialDiag = __DIAG_JSON__;

    const statusEl = document.getElementById('setup-status');
    const logEl = document.getElementById('command-log');
    const diagKv = document.getElementById('diag-kv');

    const form = document.getElementById('setup-form');
    const btnBootstrap = document.getElementById('btn-bootstrap');
    const btnWriteEnv = document.getElementById('btn-write-env');
    const btnPreflight = document.getElementById('btn-preflight');
    const btnMigrate = document.getElementById('btn-migrate');
    const btnRefresh = document.getElementById('btn-refresh');

    function setBusy(busy) {
      [btnBootstrap, btnWriteEnv, btnPreflight, btnMigrate, btnRefresh].forEach((btn) => {
        if (!btn) return;
        btn.disabled = !!busy;
      });
    }

    function applyEnvDefaults(values) {
      const fields = [
        'database_url',
        'model_dir',
        'local_storage_dir',
        'allowed_origins',
        'default_device',
      ];
      fields.forEach((key) => {
        const el = document.getElementById(key);
        if (el && values && values[key] !== undefined && values[key] !== null) {
          el.value = String(values[key]);
        }
      });
    }

    function renderStatus(data) {
      const required = !!data.setup_required;
      const dbReady = !!data.db_ready;
      const reasons = Array.isArray(data.reasons) ? data.reasons.join(', ') : '';

      if (!required) {
        statusEl.className = 'status ok';
        statusEl.textContent = '安装已完成。你现在可以通过 doctor / ops 客户端登录。';
        form.style.display = 'none';
        return;
      }

      if (!dbReady) {
        statusEl.className = 'status err';
        statusEl.textContent = '数据库当前不可用。请先执行迁移（Step 1 -> 执行迁移）。';
        return;
      }

      statusEl.className = 'status warn';
      statusEl.textContent = reasons ? `安装未完成：${reasons}` : '安装未完成。';
      form.style.display = '';
    }

    function renderDiag(diag) {
      const setup = diag.setup || {};
      const rows = [
        ['OS', diag.os_pretty_name || 'unknown'],
        ['Python', diag.python_version || '-'],
        ['Env File', `${diag.env_file || '-'}${diag.env_file_exists ? ' (exists)' : ' (missing)'}`],
        ['DB', `${diag.db_ok ? 'ok' : 'error'} | ${diag.db_message || '-'}`],
        ['Model Dir', `${diag.model_dir || '-'}${diag.model_dir_exists ? '' : ' (missing)'}`],
        ['Model Assets', String(diag.model_asset_count ?? 0)],
        ['Admin Users', String(setup.admin_user_count ?? '-')],
        ['Install Marker', `${setup.marker_file || '-'}${setup.marker_exists ? ' (exists)' : ' (missing)'}`],
      ];
      diagKv.innerHTML = rows.map(([k, v]) => `<li><div class=\"k\">${k}</div><div class=\"v\">${v}</div></li>`).join('');
    }

    function appendLog(title, body) {
      const now = new Date().toLocaleString();
      const chunk = `\n[${now}] ${title}\n${body || ''}\n`;
      logEl.textContent = (logEl.textContent + chunk).trimStart();
      logEl.scrollTop = logEl.scrollHeight;
    }

    async function fetchJson(url, options) {
      const resp = await fetch(url, options || {});
      let data = {};
      try {
        data = await resp.json();
      } catch (_) {
        data = {};
      }
      if (!resp.ok) {
        throw new Error(data.detail || JSON.stringify(data) || `HTTP ${resp.status}`);
      }
      return data;
    }

    async function refreshAll() {
      const data = await fetchJson('/v1/setup/diagnostics', { cache: 'no-store' });
      renderStatus(data.setup || {});
      renderDiag(data);
      appendLog('刷新诊断', JSON.stringify(data, null, 2));
      return data;
    }

    btnRefresh.addEventListener('click', async () => {
      try {
        setBusy(true);
        await refreshAll();
      } catch (err) {
        appendLog('刷新失败', String(err));
      } finally {
        setBusy(false);
      }
    });

    btnWriteEnv.addEventListener('click', async () => {
      try {
        setBusy(true);
        const payload = {
          database_url: document.getElementById('database_url').value.trim(),
          model_dir: document.getElementById('model_dir').value.trim(),
          local_storage_dir: document.getElementById('local_storage_dir').value.trim(),
          allowed_origins: document.getElementById('allowed_origins').value.trim(),
          default_device: document.getElementById('default_device').value.trim() || 'cpu',
          storage_backend: 'local',
          auth_secret: document.getElementById('auth_secret').value.trim() || null,
          auth_token_ttl_minutes: 480,
          max_visits: 5,
          max_inline_image_bytes: 8388608,
          max_inline_total_bytes: 33554432,
          setup_enabled: true,
          setup_enforce_lock: true,
          enable_legacy_public_clinical_routes: false,
        };
        const out = await fetchJson('/v1/setup/env/write', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        appendLog('写入 Env 成功', JSON.stringify(out, null, 2));
        document.getElementById('auth_secret').value = '';
        await refreshAll();
      } catch (err) {
        appendLog('写入 Env 失败', String(err));
      } finally {
        setBusy(false);
      }
    });

    btnPreflight.addEventListener('click', async () => {
      try {
        setBusy(true);
        const out = await fetchJson('/v1/setup/run/preflight', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        appendLog('预检执行结果', `cmd=${(out.command || []).join(' ')}\nexit=${out.return_code}\n\nstdout:\n${out.stdout || ''}\n\nstderr:\n${out.stderr || ''}`);
        await refreshAll();
      } catch (err) {
        appendLog('预检失败', String(err));
      } finally {
        setBusy(false);
      }
    });

    btnMigrate.addEventListener('click', async () => {
      try {
        setBusy(true);
        const out = await fetchJson('/v1/setup/run/migrate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ database_url: document.getElementById('database_url').value.trim() }),
        });
        appendLog('迁移执行结果', `cmd=${(out.command || []).join(' ')}\nexit=${out.return_code}\n\nstdout:\n${out.stdout || ''}\n\nstderr:\n${out.stderr || ''}`);
        await refreshAll();
      } catch (err) {
        appendLog('迁移失败', String(err));
      } finally {
        setBusy(false);
      }
    });

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        setBusy(true);
        const payload = {
          username: document.getElementById('username').value,
          display_name: document.getElementById('display_name').value,
          password: document.getElementById('password').value,
        };
        const out = await fetchJson('/v1/setup/bootstrap', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        appendLog('初始化管理员成功', JSON.stringify(out, null, 2));
        await refreshAll();
      } catch (err) {
        appendLog('初始化管理员失败', String(err));
      } finally {
        setBusy(false);
      }
    });

    applyEnvDefaults(envDefaults || {});
    renderStatus(initialStatus || {});
    renderDiag(initialDiag || {});
    appendLog('安装向导加载', '页面已就绪。建议顺序：写入 Env -> 预检 -> 迁移 -> 创建管理员。');
  </script>
</body>
</html>
"""
    return (
        template.replace("__STATUS_JSON__", status_json)
        .replace("__ENV_JSON__", env_json)
        .replace("__DIAG_JSON__", diag_json)
    )


def build_setup_router(settings) -> APIRouter:
    router = APIRouter(tags=["setup"])

    @router.get("/", include_in_schema=False)
    def root_entry():
        status = get_setup_status(settings)
        if status.setup_required:
            diagnostics = _collect_diagnostics(settings)
            return HTMLResponse(_setup_page_html(status.to_dict(), _setup_env_defaults(settings), diagnostics))
        return {
            "service": "myopia-server",
            "status": "ok",
            "message": "setup completed",
            "setup": status.to_dict(),
        }

    @router.get("/setup", include_in_schema=False)
    def setup_page():
        status = get_setup_status(settings)
        diagnostics = _collect_diagnostics(settings)
        return HTMLResponse(_setup_page_html(status.to_dict(), _setup_env_defaults(settings), diagnostics))

    @router.get("/v1/setup/status", response_model=SetupStatusResponse)
    def setup_status():
        return get_setup_status(settings).to_dict()

    @router.get("/v1/setup/diagnostics", response_model=SetupDiagnosticsResponse)
    def setup_diagnostics():
        return _collect_diagnostics(settings)

    @router.post("/v1/setup/env/write", response_model=SetupEnvWriteResponse)
    def setup_write_env(req: SetupEnvWriteRequest):
        status = get_setup_status(settings)
        if not status.setup_required:
            raise HTTPException(status_code=409, detail="server already initialized")

        auth_secret, generated = _validate_env_payload(req)
        env_text = _render_server_env(req, auth_secret=auth_secret)

        target = Path(settings.setup_env_file).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(env_text, encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to write env file: {exc}") from exc

        return {
            "ok": True,
            "env_file": str(target),
            "keys_written": len([x for x in env_text.splitlines() if x.strip()]),
            "auth_secret_generated": generated,
        }

    @router.post("/v1/setup/run/preflight", response_model=SetupCommandRunResponse)
    def setup_run_preflight(_: SetupCommandRunRequest):
        status = get_setup_status(settings)
        if not status.setup_required:
            raise HTTPException(status_code=409, detail="server already initialized")

        command = [sys.executable, str(PREFLIGHT_SCRIPT), "--env-file", settings.setup_env_file]
        result = _run_command(
            command,
            timeout_seconds=settings.setup_command_timeout_seconds,
            cwd=BACKEND_DIR,
        )
        return {
            "action": "preflight",
            "command": command,
            **result,
        }

    @router.post("/v1/setup/run/migrate", response_model=SetupCommandRunResponse)
    def setup_run_migrate(req: SetupCommandRunRequest):
        status = get_setup_status(settings)
        if not status.setup_required:
            raise HTTPException(status_code=409, detail="server already initialized")

        db_url = str(req.database_url or "").strip() or settings.database_url
        command = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"]
        result = _run_command(
            command,
            timeout_seconds=settings.setup_command_timeout_seconds,
            cwd=BACKEND_DIR,
            extra_env={"MYOPIA_DATABASE_URL": db_url},
        )
        return {
            "action": "migrate",
            "command": command,
            **result,
        }

    @router.post("/v1/setup/bootstrap", response_model=SetupBootstrapResponse)
    def setup_bootstrap(req: SetupBootstrapRequest):
        status = get_setup_status(settings)
        if not status.db_ready:
            raise HTTPException(
                status_code=503,
                detail="database is not ready; run migrations first (alembic upgrade head)",
            )
        if not status.setup_required:
            raise HTTPException(status_code=409, detail="server already initialized")

        username = str(req.username or "").strip().lower()
        display_name = (req.display_name or "").strip() or "System Admin"
        password = str(req.password or "")

        if not username:
            raise HTTPException(status_code=400, detail="username cannot be empty")
        if not password.strip():
            raise HTTPException(status_code=400, detail="password cannot be empty")

        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            with session_scope() as session:
                exists = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
                if exists is not None:
                    raise HTTPException(status_code=409, detail="username already exists")

                admin_exists = session.execute(select(User).where(User.role == "admin")).scalar_one_or_none()
                if admin_exists is not None:
                    raise HTTPException(status_code=409, detail="admin user already exists")

                user = User(
                    username=username,
                    display_name=display_name,
                    role="admin",
                    is_active=True,
                    password_hash=password_hash,
                )
                session.add(user)
                session.flush()
                session.add(
                    AuditLog(
                        action="setup.bootstrap_admin",
                        actor=username,
                        target_type="user",
                        target_id=str(user.id),
                        detail_json={"role": "admin"},
                    )
                )
        except HTTPException:
            raise
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="username already exists") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"setup bootstrap failed: {exc}") from exc

        marker_written = False
        marker_file = status.marker_file
        try:
            marker_file = str(write_install_marker(settings, admin_username=username))
            marker_written = True
        except Exception:
            marker_written = False

        return {
            "ok": True,
            "username": username,
            "marker_written": marker_written,
            "marker_file": marker_file,
            "setup_required": False,
        }

    return router
