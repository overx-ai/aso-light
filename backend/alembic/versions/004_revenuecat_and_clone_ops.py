"""Add revenuecat_credentials, clone_operations, and apps.revenuecat_credential_id

Revision ID: 004_revenuecat_and_clone_ops
Revises: 003_metadata_tables
Create Date: 2026-05-06

Backs the version-bump clone flow + RevenueCat integration:

* ``revenuecat_credentials`` — per-user RC project secret + project_id +
  rc_app_id (all encrypted at rest for the secret).
* ``clone_operations`` — orchestration record for sub/IAP clone runs,
  with per-step status JSON for retry/resume.
* ``apps.revenuecat_credential_id`` — optional FK linking an app to its
  RC credential.

Idempotent (matches 001-003): tolerates partial pre-existing state since
``Base.metadata.create_all`` runs at startup in the dev SQLite path.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "004_revenuecat_and_clone_ops"
down_revision: Union[str, None] = "003_metadata_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_table("revenuecat_credentials"):
        op.create_table(
            "revenuecat_credentials",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=False),
            sa.Column("rc_app_id", sa.String(length=255), nullable=True),
            sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_revenuecat_credentials_user_id",
            "revenuecat_credentials",
            ["user_id"],
        )

    if not _has_column("apps", "revenuecat_credential_id"):
        with op.batch_alter_table("apps") as batch:
            batch.add_column(
                sa.Column(
                    "revenuecat_credential_id",
                    sa.Integer(),
                    sa.ForeignKey("revenuecat_credentials.id"),
                    nullable=True,
                )
            )
            batch.create_index(
                "ix_apps_revenuecat_credential_id",
                ["revenuecat_credential_id"],
            )

    if not _has_table("clone_operations"):
        op.create_table(
            "clone_operations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "app_id",
                sa.Integer(),
                sa.ForeignKey("apps.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_kind", sa.String(length=20), nullable=False),
            sa.Column("source_local_id", sa.Integer(), nullable=False),
            sa.Column("source_asc_id", sa.String(length=255), nullable=False),
            sa.Column("source_product_id", sa.String(length=255), nullable=False),
            sa.Column("target_product_id", sa.String(length=255), nullable=False),
            sa.Column("target_asc_id", sa.String(length=255), nullable=True),
            sa.Column("scope_json", sa.JSON(), nullable=False),
            sa.Column("asc_steps_json", sa.JSON(), nullable=False),
            sa.Column("revenuecat_steps_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error_log_json", sa.JSON(), nullable=False),
            sa.Column(
                "completed_at", sa.DateTime(timezone=True), nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "source_kind IN ('subscription', 'iap')",
                name="ck_clone_operations_source_kind",
            ),
        )
        op.create_index(
            "ix_clone_operations_app_id", "clone_operations", ["app_id"],
        )
        op.create_index(
            "ix_clone_operations_user_id", "clone_operations", ["user_id"],
        )


def downgrade() -> None:
    if _has_table("clone_operations"):
        op.drop_index(
            "ix_clone_operations_user_id", table_name="clone_operations",
        )
        op.drop_index(
            "ix_clone_operations_app_id", table_name="clone_operations",
        )
        op.drop_table("clone_operations")

    if _has_column("apps", "revenuecat_credential_id"):
        with op.batch_alter_table("apps") as batch:
            batch.drop_index("ix_apps_revenuecat_credential_id")
            batch.drop_column("revenuecat_credential_id")

    if _has_table("revenuecat_credentials"):
        op.drop_index(
            "ix_revenuecat_credentials_user_id",
            table_name="revenuecat_credentials",
        )
        op.drop_table("revenuecat_credentials")
