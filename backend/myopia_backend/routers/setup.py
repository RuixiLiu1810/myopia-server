from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db.models import AuditLog, User
from ..db.session import session_scope
from ..install_state import get_setup_status, write_install_marker
from ..schemas import SetupBootstrapRequest, SetupBootstrapResponse, SetupStatusResponse
from ..security.auth import hash_password


def _setup_page_html(status_payload: dict) -> str:
    status_json = json.dumps(status_payload, ensure_ascii=True)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Myopia Server Setup</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #f6f8fb; margin: 0; color: #1f2937; }}
    .wrap {{ max-width: 760px; margin: 48px auto; padding: 0 20px; }}
    .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; box-shadow: 0 10px 22px rgba(17,24,39,.06); padding: 22px; }}
    h1 {{ margin: 0 0 10px; font-size: 24px; }}
    p {{ margin: 0 0 14px; color: #4b5563; }}
    .row {{ display: grid; gap: 8px; margin: 10px 0; }}
    label {{ font-size: 13px; font-weight: 600; color: #374151; }}
    input {{ width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 8px; padding: 10px 12px; font-size: 14px; }}
    button {{ margin-top: 8px; border: 0; background: #2563eb; color: #fff; border-radius: 8px; padding: 10px 14px; font-weight: 600; cursor: pointer; }}
    button:disabled {{ opacity: .6; cursor: not-allowed; }}
    .status {{ margin: 12px 0; padding: 10px 12px; border-radius: 8px; font-size: 13px; }}
    .ok {{ background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }}
    .warn {{ background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }}
    .err {{ background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <h1>Myopia Server First-Time Setup</h1>
      <p>Create the initial administrator account. After setup, doctors and operators can sign in normally.</p>
      <div id=\"status\" class=\"status warn\">Checking setup status...</div>
      <form id=\"setup-form\">
        <div class=\"row\">
          <label for=\"username\">Admin Username</label>
          <input id=\"username\" name=\"username\" placeholder=\"admin\" value=\"admin\" required />
        </div>
        <div class=\"row\">
          <label for=\"display_name\">Display Name</label>
          <input id=\"display_name\" name=\"display_name\" placeholder=\"System Admin\" value=\"System Admin\" />
        </div>
        <div class=\"row\">
          <label for=\"password\">Password (>= 8 chars)</label>
          <input id=\"password\" name=\"password\" type=\"password\" required minlength=\"8\" />
        </div>
        <button id=\"submit\" type=\"submit\">Initialize Server</button>
      </form>
      <p class=\"mono\" id=\"meta\"></p>
    </div>
  </div>
<script>
  const initialStatus = {status_json};
  const statusEl = document.getElementById('status');
  const metaEl = document.getElementById('meta');
  const form = document.getElementById('setup-form');
  const submitBtn = document.getElementById('submit');

  function renderStatus(data) {{
    const required = !!data.setup_required;
    const dbReady = !!data.db_ready;
    const markerFile = data.marker_file || '';
    const reasons = Array.isArray(data.reasons) ? data.reasons.join(', ') : '';
    metaEl.textContent = `db_ready=${{dbReady}} | admin_user_count=${{data.admin_user_count ?? '-'}} | marker_file=${{markerFile}}`;

    if (!required) {{
      statusEl.className = 'status ok';
      statusEl.textContent = 'Setup completed. You can now use /v1/auth/login and ops/doctor clients.';
      form.style.display = 'none';
      return;
    }}

    if (!dbReady) {{
      statusEl.className = 'status err';
      statusEl.textContent = 'Database is not ready. Run migrations first, then refresh this page.';
      submitBtn.disabled = true;
      return;
    }}

    statusEl.className = 'status warn';
    statusEl.textContent = reasons ? `Setup required: ${{reasons}}` : 'Setup required.';
    submitBtn.disabled = false;
  }}

  async function refreshStatus() {{
    try {{
      const resp = await fetch('/v1/setup/status', {{ cache: 'no-store' }});
      const data = await resp.json();
      renderStatus(data);
    }} catch (err) {{
      statusEl.className = 'status err';
      statusEl.textContent = `Failed to load setup status: ${{err}}`;
    }}
  }}

  form.addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    submitBtn.disabled = true;
    statusEl.className = 'status warn';
    statusEl.textContent = 'Initializing...';

    const payload = {{
      username: document.getElementById('username').value,
      display_name: document.getElementById('display_name').value,
      password: document.getElementById('password').value,
    }};

    try {{
      const resp = await fetch('/v1/setup/bootstrap', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload),
      }});
      const data = await resp.json();
      if (!resp.ok) {{
        throw new Error(data.detail || JSON.stringify(data));
      }}
      statusEl.className = 'status ok';
      statusEl.textContent = 'Server initialized successfully. You can now log in as admin.';
      await refreshStatus();
    }} catch (err) {{
      statusEl.className = 'status err';
      statusEl.textContent = `Initialization failed: ${{err.message || err}}`;
      submitBtn.disabled = false;
    }}
  }});

  renderStatus(initialStatus);
  refreshStatus();
</script>
</body>
</html>"""


def build_setup_router(settings) -> APIRouter:
    router = APIRouter(tags=["setup"])

    @router.get("/", include_in_schema=False)
    def root_entry():
        status = get_setup_status(settings)
        if status.setup_required:
            return HTMLResponse(_setup_page_html(status.to_dict()))
        return {
            "service": "myopia-server",
            "status": "ok",
            "message": "setup completed",
            "setup": status.to_dict(),
        }

    @router.get("/setup", include_in_schema=False)
    def setup_page():
        status = get_setup_status(settings)
        return HTMLResponse(_setup_page_html(status.to_dict()))

    @router.get("/v1/setup/status", response_model=SetupStatusResponse)
    def setup_status():
        return get_setup_status(settings).to_dict()

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
                exists = session.execute(
                    select(User).where(User.username == username)
                ).scalar_one_or_none()
                if exists is not None:
                    raise HTTPException(status_code=409, detail="username already exists")

                admin_exists = session.execute(
                    select(User).where(User.role == "admin")
                ).scalar_one_or_none()
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
