from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db.models import AuditLog, Encounter, FileAsset, Patient, PredictionRun, User
from ..db.session import session_scope
from ..dependencies.rbac import AuthContext, require_roles
from ..inference_service import predict_future
from ..security.auth import hash_password
from ..schemas import (
    EncounterCreateRequest,
    EncounterOut,
    EncounterUpdateRequest,
    PatientPredictionListItem,
    PatientCreateRequest,
    PatientOut,
    PredictionByEncountersRequest,
    PredictionCreateRequest,
    PredictionRunOut,
    UserCreateRequest,
    UserOut,
)
from ..services.file_asset_service import resolve_asset_local_path
from .inference import _resolve_device, _resolve_model_dir, _validate_visits_count


def _patient_out(patient: Patient) -> dict:
    return {
        "id": int(patient.id),
        "patient_code": patient.patient_code,
        "full_name": patient.full_name,
        "sex": patient.sex,
        "birth_date": patient.birth_date,
        "created_at": patient.created_at.isoformat(),
        "updated_at": patient.updated_at.isoformat(),
    }


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


def _encounter_out(encounter: Encounter) -> dict:
    return {
        "id": int(encounter.id),
        "patient_id": int(encounter.patient_id),
        "encounter_date": encounter.encounter_date,
        "se": encounter.se,
        "image_asset_id": encounter.image_asset_id,
        "notes": encounter.notes_json,
        "created_at": encounter.created_at.isoformat(),
    }


def _prediction_out(prediction: PredictionRun) -> dict:
    models = prediction.models if isinstance(prediction.models, dict) else {}
    predictions = prediction.predictions if isinstance(prediction.predictions, dict) else {}
    requested_model_families = _safe_str_list(prediction.requested_model_families)
    family_results = prediction.family_results if isinstance(prediction.family_results, dict) else {}

    if not family_results and (models or predictions):
        family_results = {
            "xu": {
                "kind": "regression",
                "models": {str(k): str(v) for k, v in models.items()},
                "predictions": {
                    str(k): float(v)
                    for k, v in predictions.items()
                    if isinstance(v, (int, float))
                },
            }
        }

    if not requested_model_families and family_results:
        requested_model_families = [str(x) for x in family_results.keys()]

    return {
        "id": int(prediction.id),
        "patient_id": int(prediction.patient_id),
        "encounter_id": prediction.encounter_id,
        "input_asset_id": prediction.input_asset_id,
        "requested_horizons": [int(x) for x in prediction.requested_horizons],
        "used_seq_len": int(prediction.used_seq_len),
        "used_horizons": [int(x) for x in prediction.used_horizons],
        "requested_model_families": requested_model_families,
        "risk_threshold": prediction.risk_threshold,
        "models": {str(k): str(v) for k, v in models.items()},
        "predictions": {
            str(k): float(v)
            for k, v in predictions.items()
            if isinstance(v, (int, float))
        },
        "family_results": family_results,
        "latency_ms": prediction.latency_ms,
        "created_at": prediction.created_at.isoformat(),
    }


def _safe_int_list(value) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _safe_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text:
            out.append(text)
    return out


def _prediction_list_item_out(
    prediction: PredictionRun,
    *,
    encounter_ids: list[int] | None = None,
    visit_asset_ids: list[int] | None = None,
) -> dict:
    row = _prediction_out(prediction)
    resolved_encounter_ids = (
        [int(x) for x in (encounter_ids or [])]
        if encounter_ids
        else ([int(prediction.encounter_id)] if prediction.encounter_id is not None else [])
    )
    resolved_visit_asset_ids = (
        [int(x) for x in (visit_asset_ids or [])]
        if visit_asset_ids
        else ([int(prediction.input_asset_id)] if prediction.input_asset_id is not None else [])
    )
    row["encounter_ids"] = resolved_encounter_ids
    row["visit_asset_ids"] = resolved_visit_asset_ids
    return row


def _write_audit_log(
    *,
    action: str,
    actor: str | None,
    target_type: str,
    target_id: str | int,
    detail_json: dict | None,
    source_ip: str | None = None,
) -> AuditLog:
    return AuditLog(
        action=action,
        actor=actor,
        target_type=target_type,
        target_id=str(target_id),
        detail_json=detail_json,
        source_ip=source_ip,
    )


def build_clinical_router(
    settings,
    *,
    prefix: str = "/v1",
    required_roles: tuple[str, ...] | None = None,
) -> APIRouter:
    dependencies = [Depends(require_roles(*required_roles))] if required_roles else None
    router = APIRouter(prefix=prefix, tags=["clinical"], dependencies=dependencies)

    @router.post("/users", response_model=UserOut)
    def create_user(
        req: UserCreateRequest,
        ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        username = req.username.strip().lower()
        if not username:
            raise HTTPException(status_code=400, detail="username cannot be empty")
        password_hash = None
        if req.password is not None:
            if not req.password.strip():
                raise HTTPException(status_code=400, detail="password cannot be empty")
            try:
                password_hash = hash_password(req.password)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            with session_scope() as session:
                user = User(
                    username=username,
                    display_name=(req.display_name or "").strip() or None,
                    role=(req.role or "operator").strip() or "operator",
                    is_active=bool(req.is_active),
                    password_hash=password_hash,
                )
                session.add(user)
                session.flush()
                session.add(
                    _write_audit_log(
                        action="user.create",
                        actor=ctx.username,
                        target_type="user",
                        target_id=user.id,
                        detail_json={"username": username, "role": user.role},
                    )
                )
                return _user_out(user)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="username already exists") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"create user failed: {exc}") from exc

    @router.get("/users", response_model=list[UserOut])
    def list_users(
        limit: int = 50,
        offset: int = 0,
        _ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        try:
            safe_limit = max(1, min(int(limit), 200))
            safe_offset = max(0, int(offset))
            with session_scope() as session:
                rows = (
                    session.execute(select(User).order_by(User.id).limit(safe_limit).offset(safe_offset))
                    .scalars()
                    .all()
                )
                return [_user_out(u) for u in rows]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list users failed: {exc}") from exc

    @router.get("/users/{user_id}", response_model=UserOut)
    def get_user(
        user_id: int,
        _ctx: AuthContext = Depends(require_roles("ops", "admin")),
    ):
        try:
            with session_scope() as session:
                user = session.get(User, int(user_id))
                if user is None:
                    raise HTTPException(status_code=404, detail=f"user not found: {user_id}")
                return _user_out(user)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"query user failed: {exc}") from exc

    @router.post("/patients", response_model=PatientOut)
    def create_patient(req: PatientCreateRequest):
        patient_code = req.patient_code.strip()
        if not patient_code:
            raise HTTPException(status_code=400, detail="patient_code cannot be empty")

        try:
            with session_scope() as session:
                patient = Patient(
                    patient_code=patient_code,
                    full_name=(req.full_name or "").strip() or None,
                    sex=(req.sex or "").strip() or None,
                    birth_date=req.birth_date,
                )
                session.add(patient)
                session.flush()
                session.add(
                    _write_audit_log(
                        action="patient.create",
                        actor=None,
                        target_type="patient",
                        target_id=patient.id,
                        detail_json={"patient_code": patient.patient_code},
                    )
                )
                return _patient_out(patient)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="patient_code already exists") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"create patient failed: {exc}") from exc

    @router.get("/patients", response_model=list[PatientOut])
    def list_patients(limit: int = 50, offset: int = 0):
        try:
            safe_limit = max(1, min(int(limit), 200))
            safe_offset = max(0, int(offset))
            with session_scope() as session:
                rows = (
                    session.execute(
                        select(Patient).order_by(Patient.id).limit(safe_limit).offset(safe_offset)
                    )
                    .scalars()
                    .all()
                )
                return [_patient_out(p) for p in rows]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list patients failed: {exc}") from exc

    @router.get("/patients/{patient_id}", response_model=PatientOut)
    def get_patient(patient_id: int):
        try:
            with session_scope() as session:
                patient = session.get(Patient, int(patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {patient_id}")
                return _patient_out(patient)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"query patient failed: {exc}") from exc

    @router.post("/encounters", response_model=EncounterOut)
    def create_encounter(req: EncounterCreateRequest):
        try:
            with session_scope() as session:
                patient = session.get(Patient, int(req.patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {req.patient_id}")

                if req.image_asset_id is not None:
                    asset = session.get(FileAsset, int(req.image_asset_id))
                    if asset is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"file_asset_id not found: {req.image_asset_id}",
                        )

                encounter = Encounter(
                    patient_id=int(req.patient_id),
                    encounter_date=req.encounter_date,
                    se=req.se,
                    image_asset_id=req.image_asset_id,
                    notes_json=req.notes,
                )
                session.add(encounter)
                session.flush()
                session.add(
                    _write_audit_log(
                        action="encounter.create",
                        actor=None,
                        target_type="encounter",
                        target_id=encounter.id,
                        detail_json={"patient_id": int(req.patient_id)},
                    )
                )
                return _encounter_out(encounter)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"create encounter failed: {exc}") from exc

    @router.patch("/encounters/{encounter_id}", response_model=EncounterOut)
    def update_encounter(encounter_id: int, req: EncounterUpdateRequest):
        try:
            changed_fields = set(
                getattr(req, "model_fields_set", getattr(req, "__fields_set__", set())) or set()
            )
            if not changed_fields:
                raise HTTPException(status_code=400, detail="no fields to update")
            with session_scope() as session:
                encounter = session.get(Encounter, int(encounter_id))
                if encounter is None:
                    raise HTTPException(status_code=404, detail=f"encounter not found: {encounter_id}")

                if "image_asset_id" in changed_fields and req.image_asset_id is not None:
                    asset = session.get(FileAsset, int(req.image_asset_id))
                    if asset is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"file_asset_id not found: {req.image_asset_id}",
                        )

                if "encounter_date" in changed_fields:
                    encounter.encounter_date = req.encounter_date
                if "se" in changed_fields:
                    encounter.se = req.se
                if "image_asset_id" in changed_fields:
                    encounter.image_asset_id = (
                        int(req.image_asset_id) if req.image_asset_id is not None else None
                    )
                if "notes" in changed_fields:
                    encounter.notes_json = req.notes

                session.add(encounter)
                session.flush()
                session.add(
                    _write_audit_log(
                        action="encounter.update",
                        actor=None,
                        target_type="encounter",
                        target_id=encounter.id,
                        detail_json={
                            "changed_fields": sorted(changed_fields),
                            "patient_id": int(encounter.patient_id),
                        },
                    )
                )
                return _encounter_out(encounter)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"update encounter failed: {exc}") from exc

    @router.get("/patients/{patient_id}/encounters", response_model=list[EncounterOut])
    def list_patient_encounters(patient_id: int, limit: int = 50, offset: int = 0):
        try:
            safe_limit = max(1, min(int(limit), 200))
            safe_offset = max(0, int(offset))
            with session_scope() as session:
                patient = session.get(Patient, int(patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {patient_id}")

                rows = (
                    session.execute(
                        select(Encounter)
                        .where(Encounter.patient_id == int(patient_id))
                        .order_by(Encounter.id.desc())
                        .limit(safe_limit)
                        .offset(safe_offset)
                    )
                    .scalars()
                    .all()
                )
                return [_encounter_out(e) for e in rows]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list encounters failed: {exc}") from exc

    @router.post("/predictions")
    def create_prediction(req: PredictionCreateRequest, model_dir: str | None = None):
        try:
            _validate_visits_count(len(req.visits), settings.max_visits)
            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            started = time.perf_counter()

            prepared_visits: list[dict] = []
            visit_asset_ids: list[int] = []
            input_asset_id: int | None = None

            with session_scope() as session:
                patient = session.get(Patient, int(req.patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {req.patient_id}")

                encounter_id: int | None = None
                if req.encounter_id is not None:
                    encounter = session.get(Encounter, int(req.encounter_id))
                    if encounter is None:
                        raise HTTPException(
                            status_code=404, detail=f"encounter not found: {req.encounter_id}"
                        )
                    if int(encounter.patient_id) != int(req.patient_id):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"encounter {req.encounter_id} does not belong "
                                f"to patient {req.patient_id}"
                            ),
                        )
                    encounter_id = int(encounter.id)

                for visit in req.visits:
                    file_asset_id = int(visit.file_asset_id)
                    asset = session.get(FileAsset, file_asset_id)
                    if asset is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"file_asset_id not found: {file_asset_id}",
                        )
                    path = resolve_asset_local_path(storage_dir=settings.local_storage_dir, asset=asset)
                    if not path.exists():
                        raise HTTPException(
                            status_code=400,
                            detail=f"asset file not found on storage: {path}",
                        )
                    prepared_visits.append({"image_path": str(path), "se": float(visit.se)})
                    visit_asset_ids.append(file_asset_id)
                    input_asset_id = file_asset_id

                result = predict_future(
                    visits=prepared_visits,
                    model_dir=used_model_dir,
                    horizons=req.horizons,
                    device=_resolve_device(settings.default_device, req.device),
                    model_families=req.model_families,
                    risk_threshold=float(req.risk_threshold if req.risk_threshold is not None else 0.5),
                )
                latency_ms = round((time.perf_counter() - started) * 1000, 2)

                prediction = PredictionRun(
                    patient_id=int(req.patient_id),
                    encounter_id=encounter_id,
                    input_asset_id=input_asset_id,
                    requested_horizons=(
                        [int(x) for x in req.horizons]
                        if req.horizons is not None
                        else [int(x) for x in result["used_horizons"]]
                    ),
                    used_seq_len=int(result["used_seq_len"]),
                    used_horizons=[int(x) for x in result["used_horizons"]],
                    requested_model_families=[
                        str(x) for x in (result.get("requested_model_families") or [])
                    ],
                    risk_threshold=float(
                        req.risk_threshold if req.risk_threshold is not None else 0.5
                    ),
                    models={str(k): str(v) for k, v in result["models"].items()},
                    predictions={str(k): float(v) for k, v in result["predictions"].items()},
                    family_results=result.get("family_results") or {},
                    latency_ms=latency_ms,
                )
                session.add(prediction)
                session.flush()

                session.add(
                    _write_audit_log(
                        action="prediction.create",
                        actor=(req.actor or "").strip() or None,
                        target_type="prediction_run",
                        target_id=prediction.id,
                        detail_json={
                            "patient_id": int(req.patient_id),
                            "encounter_id": encounter_id,
                            "visit_asset_ids": visit_asset_ids,
                            "requested_model_families": [
                                str(x) for x in (result.get("requested_model_families") or [])
                            ],
                            "risk_threshold": float(
                                req.risk_threshold if req.risk_threshold is not None else 0.5
                            ),
                        },
                    )
                )

                out = _prediction_out(prediction)
                out["file_asset_ids"] = visit_asset_ids
                return out
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"create prediction failed: {exc}") from exc

    @router.post("/predictions/by-encounters")
    def create_prediction_by_encounters(
        req: PredictionByEncountersRequest,
        model_dir: str | None = None,
    ):
        try:
            raw_ids = [int(x) for x in (req.encounter_ids or [])]
            if not raw_ids:
                raise HTTPException(status_code=400, detail="encounter_ids cannot be empty")
            # Keep stable order while removing duplicates.
            dedup_ids = list(dict.fromkeys(raw_ids))
            _validate_visits_count(len(dedup_ids), settings.max_visits)
            used_model_dir = _resolve_model_dir(settings.model_dir, model_dir)
            started = time.perf_counter()

            prepared_visits: list[dict] = []
            visit_asset_ids: list[int] = []
            selected_encounter_ids: list[int] = []
            encounter_ref_id: int | None = None
            input_asset_id: int | None = None

            with session_scope() as session:
                patient = session.get(Patient, int(req.patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {req.patient_id}")

                rows = (
                    session.execute(
                        select(Encounter).where(Encounter.id.in_(dedup_ids))
                    )
                    .scalars()
                    .all()
                )
                by_id = {int(e.id): e for e in rows}
                missing_ids = [eid for eid in dedup_ids if eid not in by_id]
                if missing_ids:
                    raise HTTPException(status_code=404, detail=f"encounter not found: {missing_ids[0]}")

                encounters = [by_id[eid] for eid in dedup_ids]
                encounters = sorted(
                    encounters,
                    key=lambda e: (e.encounter_date is None, e.encounter_date, int(e.id)),
                )

                for enc in encounters:
                    if int(enc.patient_id) != int(req.patient_id):
                        raise HTTPException(
                            status_code=400,
                            detail=f"encounter {enc.id} does not belong to patient {req.patient_id}",
                        )
                    if enc.se is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"encounter {enc.id} missing se",
                        )
                    if enc.image_asset_id is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"encounter {enc.id} missing image_asset_id",
                        )

                    asset = session.get(FileAsset, int(enc.image_asset_id))
                    if asset is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"file_asset_id not found: {enc.image_asset_id}",
                        )
                    path = resolve_asset_local_path(storage_dir=settings.local_storage_dir, asset=asset)
                    if not path.exists():
                        raise HTTPException(
                            status_code=400,
                            detail=f"asset file not found on storage: {path}",
                        )
                    prepared_visits.append({"image_path": str(path), "se": float(enc.se)})
                    selected_encounter_ids.append(int(enc.id))
                    visit_asset_ids.append(int(asset.id))
                    encounter_ref_id = int(enc.id)
                    input_asset_id = int(asset.id)

                result = predict_future(
                    visits=prepared_visits,
                    model_dir=used_model_dir,
                    horizons=req.horizons,
                    device=_resolve_device(settings.default_device, req.device),
                    model_families=req.model_families,
                    risk_threshold=float(req.risk_threshold if req.risk_threshold is not None else 0.5),
                )
                latency_ms = round((time.perf_counter() - started) * 1000, 2)

                prediction = PredictionRun(
                    patient_id=int(req.patient_id),
                    encounter_id=encounter_ref_id,
                    input_asset_id=input_asset_id,
                    requested_horizons=(
                        [int(x) for x in req.horizons]
                        if req.horizons is not None
                        else [int(x) for x in result["used_horizons"]]
                    ),
                    used_seq_len=int(result["used_seq_len"]),
                    used_horizons=[int(x) for x in result["used_horizons"]],
                    requested_model_families=[
                        str(x) for x in (result.get("requested_model_families") or [])
                    ],
                    risk_threshold=float(
                        req.risk_threshold if req.risk_threshold is not None else 0.5
                    ),
                    models={str(k): str(v) for k, v in result["models"].items()},
                    predictions={str(k): float(v) for k, v in result["predictions"].items()},
                    family_results=result.get("family_results") or {},
                    latency_ms=latency_ms,
                )
                session.add(prediction)
                session.flush()

                session.add(
                    _write_audit_log(
                        action="prediction.create",
                        actor=(req.actor or "").strip() or None,
                        target_type="prediction_run",
                        target_id=prediction.id,
                        detail_json={
                            "patient_id": int(req.patient_id),
                            "encounter_ids": selected_encounter_ids,
                            "visit_asset_ids": visit_asset_ids,
                            "requested_model_families": [
                                str(x) for x in (result.get("requested_model_families") or [])
                            ],
                            "risk_threshold": float(
                                req.risk_threshold if req.risk_threshold is not None else 0.5
                            ),
                        },
                    )
                )

                out = _prediction_list_item_out(
                    prediction,
                    encounter_ids=selected_encounter_ids,
                    visit_asset_ids=visit_asset_ids,
                )
                return out
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"create prediction by encounters failed: {exc}",
            ) from exc

    @router.get("/predictions/{prediction_run_id}", response_model=PredictionRunOut)
    def get_prediction(prediction_run_id: int):
        try:
            with session_scope() as session:
                prediction = session.get(PredictionRun, int(prediction_run_id))
                if prediction is None:
                    raise HTTPException(
                        status_code=404, detail=f"prediction_run not found: {prediction_run_id}"
                    )
                return _prediction_out(prediction)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"query prediction failed: {exc}") from exc

    @router.get("/patients/{patient_id}/predictions", response_model=list[PatientPredictionListItem])
    def list_patient_predictions(patient_id: int, limit: int = 50, offset: int = 0):
        try:
            safe_limit = max(1, min(int(limit), 200))
            safe_offset = max(0, int(offset))
            with session_scope() as session:
                patient = session.get(Patient, int(patient_id))
                if patient is None:
                    raise HTTPException(status_code=404, detail=f"patient not found: {patient_id}")

                rows = (
                    session.execute(
                        select(PredictionRun)
                        .where(PredictionRun.patient_id == int(patient_id))
                        .order_by(PredictionRun.id.desc())
                        .limit(safe_limit)
                        .offset(safe_offset)
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    return []

                row_ids = [str(int(r.id)) for r in rows]
                audit_rows = (
                    session.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.action == "prediction.create",
                            AuditLog.target_type == "prediction_run",
                            AuditLog.target_id.in_(row_ids),
                        )
                        .order_by(AuditLog.id.desc())
                    )
                    .scalars()
                    .all()
                )
                audit_detail_by_prediction_id: dict[str, dict] = {}
                for log in audit_rows:
                    key = str(log.target_id or "")
                    if key in audit_detail_by_prediction_id:
                        continue
                    if isinstance(log.detail_json, dict):
                        audit_detail_by_prediction_id[key] = log.detail_json

                out: list[dict] = []
                for row in rows:
                    detail = audit_detail_by_prediction_id.get(str(row.id), {})
                    out.append(
                        _prediction_list_item_out(
                            row,
                            encounter_ids=_safe_int_list(detail.get("encounter_ids")),
                            visit_asset_ids=_safe_int_list(detail.get("visit_asset_ids")),
                        )
                    )
                return out
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"list patient predictions failed: {exc}") from exc

    return router
