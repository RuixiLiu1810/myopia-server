"""core tables

Revision ID: 0001_phase_a_core_tables (legacy id for compatibility)
Revises:
Create Date: 2026-03-19 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_phase_a_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_code", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
        sa.UniqueConstraint("patient_code", name=op.f("uq_patients_patient_code")),
    )
    op.create_index(op.f("ix_patients_patient_code"), "patients", ["patient_code"], unique=False)

    op.create_table(
        "file_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_assets")),
        sa.UniqueConstraint("object_key", name=op.f("uq_file_assets_object_key")),
    )
    op.create_index(op.f("ix_file_assets_object_key"), "file_assets", ["object_key"], unique=False)
    op.create_index(op.f("ix_file_assets_sha256"), "file_assets", ["sha256"], unique=False)

    op.create_table(
        "encounters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("encounter_date", sa.Date(), nullable=True),
        sa.Column("se", sa.Float(), nullable=True),
        sa.Column("image_asset_id", sa.Integer(), nullable=True),
        sa.Column("notes_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_asset_id"],
            ["file_assets.id"],
            name=op.f("fk_encounters_image_asset_id_file_assets"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_encounters_patient_id_patients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_encounters")),
    )
    op.create_index(op.f("ix_encounters_encounter_date"), "encounters", ["encounter_date"], unique=False)
    op.create_index(op.f("ix_encounters_image_asset_id"), "encounters", ["image_asset_id"], unique=False)
    op.create_index(op.f("ix_encounters_patient_id"), "encounters", ["patient_id"], unique=False)

    op.create_table(
        "prediction_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("encounter_id", sa.Integer(), nullable=True),
        sa.Column("input_asset_id", sa.Integer(), nullable=True),
        sa.Column("requested_horizons", sa.JSON(), nullable=False),
        sa.Column("used_seq_len", sa.Integer(), nullable=False),
        sa.Column("used_horizons", sa.JSON(), nullable=False),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("predictions", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["encounter_id"],
            ["encounters.id"],
            name=op.f("fk_prediction_runs_encounter_id_encounters"),
        ),
        sa.ForeignKeyConstraint(
            ["input_asset_id"],
            ["file_assets.id"],
            name=op.f("fk_prediction_runs_input_asset_id_file_assets"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_prediction_runs_patient_id_patients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prediction_runs")),
    )
    op.create_index(op.f("ix_prediction_runs_encounter_id"), "prediction_runs", ["encounter_id"], unique=False)
    op.create_index(op.f("ix_prediction_runs_input_asset_id"), "prediction_runs", ["input_asset_id"], unique=False)
    op.create_index(op.f("ix_prediction_runs_patient_id"), "prediction_runs", ["patient_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_actor"), "audit_logs", ["actor"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_request_id"), "audit_logs", ["request_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_target_id"), "audit_logs", ["target_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_target_type"), "audit_logs", ["target_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_target_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_target_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_request_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_prediction_runs_patient_id"), table_name="prediction_runs")
    op.drop_index(op.f("ix_prediction_runs_input_asset_id"), table_name="prediction_runs")
    op.drop_index(op.f("ix_prediction_runs_encounter_id"), table_name="prediction_runs")
    op.drop_table("prediction_runs")

    op.drop_index(op.f("ix_encounters_patient_id"), table_name="encounters")
    op.drop_index(op.f("ix_encounters_image_asset_id"), table_name="encounters")
    op.drop_index(op.f("ix_encounters_encounter_date"), table_name="encounters")
    op.drop_table("encounters")

    op.drop_index(op.f("ix_file_assets_sha256"), table_name="file_assets")
    op.drop_index(op.f("ix_file_assets_object_key"), table_name="file_assets")
    op.drop_table("file_assets")

    op.drop_index(op.f("ix_patients_patient_code"), table_name="patients")
    op.drop_table("patients")
