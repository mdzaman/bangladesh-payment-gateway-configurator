"""two-factor sign-in

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

Adds the TOTP columns on users, single-use recovery codes, and the short-lived
challenge that stands between a correct password and a session.

Safe while the previous release is still serving: the new columns are nullable
and nothing existing changes. Downgrading switches two-factor sign-in off for
everyone, because it drops the secrets.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_login_challenges_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_challenges")),
    )
    op.create_index(op.f("ix_login_challenges_user_id"), "login_challenges", ["user_id"], unique=False)
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_recovery_codes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
    )
    op.create_index(op.f("ix_recovery_codes_user_id"), "recovery_codes", ["user_id"], unique=False)
    op.add_column("users", sa.Column("totp_secret_enc", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_started_at")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret_enc")
    op.drop_index(op.f("ix_recovery_codes_user_id"), table_name="recovery_codes")
    op.drop_table("recovery_codes")
    op.drop_index(op.f("ix_login_challenges_user_id"), table_name="login_challenges")
    op.drop_table("login_challenges")
