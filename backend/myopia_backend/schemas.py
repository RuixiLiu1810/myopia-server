from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class VisitIn(BaseModel):
    image_path: str = Field(..., description="Absolute or project-relative image path")
    se: float = Field(..., description="Spherical equivalent for the visit")


class PredictRequest(BaseModel):
    visits: List[VisitIn] = Field(..., description="Chronological visit list")
    horizons: Optional[List[int]] = Field(default=None, description="Requested horizons")
    device: Optional[str] = Field(default=None, description='Optional inference device, e.g. "cpu"')
    model_families: Optional[List[str]] = Field(
        default=None,
        description='Optional model families, e.g. ["xu"], ["xu","fen","feng"]',
    )
    risk_threshold: Optional[float] = Field(
        default=0.5, description="Risk threshold for Fen/FenG classification [0,1]"
    )


class VisitInlineIn(BaseModel):
    image_b64: str = Field(..., description="Base64 image bytes or data URL")
    se: float = Field(..., description="Spherical equivalent for the visit")
    image_ext: Optional[str] = Field(default=".jpg", description="Optional extension")


class PredictInlineRequest(BaseModel):
    visits: List[VisitInlineIn] = Field(..., description="Chronological visit list")
    horizons: Optional[List[int]] = Field(default=None, description="Requested horizons")
    device: Optional[str] = Field(default=None, description='Optional inference device, e.g. "cpu"')
    model_families: Optional[List[str]] = Field(
        default=None,
        description='Optional model families, e.g. ["xu"], ["xu","fen","feng"]',
    )
    risk_threshold: Optional[float] = Field(
        default=0.5, description="Risk threshold for Fen/FenG classification [0,1]"
    )


class UploadInlineFileRequest(BaseModel):
    image_b64: str = Field(..., description="Base64 image bytes or data URL")
    image_ext: Optional[str] = Field(default=".jpg", description="Optional extension")
    original_filename: Optional[str] = Field(default=None, description="Original filename")
    content_type: Optional[str] = Field(default=None, description="File content type")
    metadata: Optional[dict[str, Any]] = Field(default=None, description="Custom file metadata")


class VisitAssetIn(BaseModel):
    file_asset_id: int = Field(..., description="Stored file asset id")
    se: float = Field(..., description="Spherical equivalent for the visit")


class PredictByAssetRequest(BaseModel):
    visits: List[VisitAssetIn] = Field(..., description="Chronological visit list by file_asset_id")
    horizons: Optional[List[int]] = Field(default=None, description="Requested horizons")
    device: Optional[str] = Field(default=None, description='Optional inference device, e.g. "cpu"')
    model_families: Optional[List[str]] = Field(
        default=None,
        description='Optional model families, e.g. ["xu"], ["xu","fen","feng"]',
    )
    risk_threshold: Optional[float] = Field(
        default=0.5, description="Risk threshold for Fen/FenG classification [0,1]"
    )


class UserCreateRequest(BaseModel):
    username: str = Field(..., description="Unique login-style username")
    display_name: Optional[str] = Field(default=None, description="Optional display name")
    password: Optional[str] = Field(default=None, description="Optional password for login")
    role: Optional[str] = Field(default="operator", description="User role")
    is_active: Optional[bool] = Field(default=True, description="Whether user is active")


class UserOut(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    role: str
    is_active: bool
    last_login_at: Optional[str] = None
    created_at: str
    updated_at: str


class OpsUserCreateRequest(BaseModel):
    username: str = Field(..., description="Unique login-style username")
    display_name: Optional[str] = Field(default=None, description="Optional display name")
    password: str = Field(..., description="Initial password")
    role: Optional[str] = Field(default="operator", description="User role")
    is_active: Optional[bool] = Field(default=True, description="Whether user is active")


class OpsUserUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, description="Optional display name")
    role: Optional[str] = Field(default=None, description="User role")
    is_active: Optional[bool] = Field(default=None, description="Whether user is active")


class OpsUserResetPasswordRequest(BaseModel):
    new_password: str = Field(..., description="New password")


class OpsActionRequest(BaseModel):
    precheck: bool = Field(default=False, description="Run precheck only")
    table_name: Optional[str] = Field(default=None, description="Target table for reindex")
    reason: Optional[str] = Field(default=None, description="Optional operator note")


class LoginRequest(BaseModel):
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    role: str
    username: str


class PatientCreateRequest(BaseModel):
    patient_code: str = Field(..., description="External patient code")
    full_name: Optional[str] = Field(default=None, description="Optional display name")
    sex: Optional[str] = Field(default=None, description="Optional sex marker")
    birth_date: Optional[date] = Field(default=None, description="Optional birth date")


class PatientOut(BaseModel):
    id: int
    patient_code: str
    full_name: Optional[str]
    sex: Optional[str]
    birth_date: Optional[date]
    created_at: str
    updated_at: str


class EncounterCreateRequest(BaseModel):
    patient_id: int = Field(..., description="Patient id")
    encounter_date: Optional[date] = Field(default=None, description="Visit date")
    se: Optional[float] = Field(default=None, description="Spherical equivalent")
    image_asset_id: Optional[int] = Field(default=None, description="Linked file_asset id")
    notes: Optional[dict[str, Any]] = Field(default=None, description="Additional notes")


class EncounterUpdateRequest(BaseModel):
    encounter_date: Optional[date] = Field(default=None, description="Visit date")
    se: Optional[float] = Field(default=None, description="Spherical equivalent")
    image_asset_id: Optional[int] = Field(default=None, description="Linked file_asset id, null to clear")
    notes: Optional[dict[str, Any]] = Field(default=None, description="Additional notes, null to clear")


class EncounterOut(BaseModel):
    id: int
    patient_id: int
    encounter_date: Optional[date]
    se: Optional[float]
    image_asset_id: Optional[int]
    notes: Optional[dict[str, Any]]
    created_at: str


class PredictionCreateRequest(BaseModel):
    patient_id: int = Field(..., description="Patient id")
    encounter_id: Optional[int] = Field(default=None, description="Optional encounter id")
    visits: List[VisitAssetIn] = Field(..., description="Chronological visit list by file_asset_id")
    horizons: Optional[List[int]] = Field(default=None, description="Requested horizons")
    device: Optional[str] = Field(default=None, description='Optional inference device, e.g. "cpu"')
    model_families: Optional[List[str]] = Field(
        default=None,
        description='Optional model families, e.g. ["xu"], ["xu","fen","feng"]',
    )
    risk_threshold: Optional[float] = Field(
        default=0.5, description="Risk threshold for Fen/FenG classification [0,1]"
    )
    actor: Optional[str] = Field(default=None, description="Optional actor for audit log")


class PredictionByEncountersRequest(BaseModel):
    patient_id: int = Field(..., description="Patient id")
    encounter_ids: List[int] = Field(..., description="Chronological or selectable encounter ids")
    horizons: Optional[List[int]] = Field(default=None, description="Requested horizons")
    device: Optional[str] = Field(default=None, description='Optional inference device, e.g. "cpu"')
    model_families: Optional[List[str]] = Field(
        default=None,
        description='Optional model families, e.g. ["xu"], ["xu","fen","feng"]',
    )
    risk_threshold: Optional[float] = Field(
        default=0.5, description="Risk threshold for Fen/FenG classification [0,1]"
    )
    actor: Optional[str] = Field(default=None, description="Optional actor for audit log")


class PredictionRunOut(BaseModel):
    id: int
    patient_id: int
    encounter_id: Optional[int]
    input_asset_id: Optional[int]
    requested_horizons: List[int]
    used_seq_len: int
    used_horizons: List[int]
    requested_model_families: List[str] = Field(default_factory=list)
    risk_threshold: Optional[float] = None
    models: dict[str, str]
    predictions: dict[str, float]
    family_results: dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[float]
    created_at: str


class PatientPredictionListItem(BaseModel):
    id: int
    patient_id: int
    encounter_id: Optional[int]
    encounter_ids: List[int]
    input_asset_id: Optional[int]
    visit_asset_ids: List[int]
    requested_horizons: List[int]
    used_seq_len: int
    used_horizons: List[int]
    requested_model_families: List[str] = Field(default_factory=list)
    risk_threshold: Optional[float] = None
    models: dict[str, str]
    predictions: dict[str, float]
    family_results: dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[float]
    created_at: str


class SetupStatusResponse(BaseModel):
    setup_required: bool
    db_ready: bool
    admin_user_count: int
    marker_exists: bool
    marker_file: str
    reasons: List[str] = Field(default_factory=list)


class SetupBootstrapRequest(BaseModel):
    username: str = Field(..., description="Initial admin username")
    password: str = Field(..., description="Initial admin password")
    display_name: Optional[str] = Field(default="System Admin", description="Admin display name")


class SetupBootstrapResponse(BaseModel):
    ok: bool
    username: str
    marker_written: bool
    marker_file: str
    setup_required: bool


class SetupEnvWriteRequest(BaseModel):
    database_url: str = Field(..., description="Database URL")
    model_dir: str = Field(..., description="Absolute path to model directory")
    default_device: Optional[str] = Field(default="cpu", description='Default device, e.g. "cpu"')
    storage_backend: str = Field(default="local", description="Storage backend")
    local_storage_dir: str = Field(..., description="Storage directory path")
    allowed_origins: str = Field(..., description="Comma-separated CORS origins")
    auth_secret: Optional[str] = Field(default=None, description="Auth secret; generated if empty")
    auth_token_ttl_minutes: int = Field(default=480, description="Access token TTL in minutes")
    max_visits: int = Field(default=5, description="Max visits per prediction request")
    max_inline_image_bytes: int = Field(default=8 * 1024 * 1024, description="Max bytes per inline image")
    max_inline_total_bytes: int = Field(default=32 * 1024 * 1024, description="Max total inline payload")
    setup_enabled: bool = Field(default=True, description="Enable setup wizard")
    setup_enforce_lock: bool = Field(default=True, description="Lock non-setup routes during setup")
    enable_legacy_public_clinical_routes: bool = Field(
        default=False,
        description="Enable legacy unauth clinical routes",
    )


class SetupEnvWriteResponse(BaseModel):
    ok: bool
    env_file: str
    keys_written: int
    auth_secret_generated: bool


class SetupCommandRunRequest(BaseModel):
    database_url: Optional[str] = Field(default=None, description="Optional DB URL override")


class SetupCommandRunResponse(BaseModel):
    ok: bool
    action: str
    command: List[str] = Field(default_factory=list)
    return_code: int
    stdout: str = ""
    stderr: str = ""


class SetupDiagnosticsResponse(BaseModel):
    setup: SetupStatusResponse
    env_file: str
    python_version: str
    os_pretty_name: str
    model_dir: str
    model_dir_exists: bool
    model_asset_count: int
    db_ok: bool
    db_message: str
    env_file_exists: bool
