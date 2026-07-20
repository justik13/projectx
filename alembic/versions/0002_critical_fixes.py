"""Critical fixes migration.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20 00:10:00.000000
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN amount TYPE numeric(12, 2)
        USING amount::numeric(12, 2)
        """
    )
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN status TYPE varchar(30)
        """
    )
    op.execute(
        """
        ALTER TABLE payments
        ADD COLUMN IF NOT EXISTS manual_review_reason varchar(255)
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS notified_expired boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS notified_grace_12h boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        ALTER TABLE pending_api_deletions
        ADD COLUMN IF NOT EXISTS reason varchar(50)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_mode (
            id integer PRIMARY KEY,
            is_enabled boolean NOT NULL DEFAULT false,
            message text,
            updated_by bigint,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hub_messages (
            chat_id bigint NOT NULL,
            message_id bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, message_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_hub_messages_chat_id
        ON hub_messages (chat_id)
        """
    )
    op.execute(
        """
        INSERT INTO maintenance_mode (id, is_enabled, message)
        VALUES (
            1,
            false,
            '⚠️ Ведутся технические работы. Некоторые действия временно недоступны. Попробуйте позже.'
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_users_expired_grace_notify
        ON users (subscription_end, telegram_id)
        WHERE is_deleted = false
          AND is_bot_blocked = false
          AND subscription_end IS NOT NULL
          AND (notified_expired = false OR notified_grace_12h = false)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_expired_grace_notify")
    op.execute("DROP TABLE IF EXISTS hub_messages")
    op.execute("DROP TABLE IF EXISTS maintenance_mode")
    op.execute("ALTER TABLE pending_api_deletions DROP COLUMN IF EXISTS reason")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notified_grace_12h")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notified_expired")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS manual_review_reason")
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN status TYPE varchar(20)
        """
    )
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN amount TYPE integer
        USING round(amount)::integer
        """
    )