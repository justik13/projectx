"""Critical fixes migration.

Применяет критичные изменения схемы:

1. payments.amount -> Numeric(12,2)
2. payments.status -> String(30)
3. payments.manual_review_reason
4. users.notified_expired
5. users.notified_grace_12h
6. pending_api_deletions.reason
7. Таблица maintenance_mode
8. Таблица hub_messages
9. Сервисная строка maintenance_mode id=1

Миграция написана идемпотентно для PostgreSQL:
- ADD COLUMN IF NOT EXISTS
- CREATE TABLE IF NOT EXISTS
- ALTER COLUMN TYPE ... USING

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================
    # 1. payments.amount -> Numeric(12,2)
    # =========================================================
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN amount TYPE numeric(12, 2)
        USING amount::numeric(12, 2)
        """
    )

    # =========================================================
    # 2. payments.status -> varchar(30)
    # =========================================================
    op.execute(
        """
        ALTER TABLE payments
        ALTER COLUMN status TYPE varchar(30)
        """
    )

    # =========================================================
    # 3. payments.manual_review_reason
    # =========================================================
    op.execute(
        """
        ALTER TABLE payments
        ADD COLUMN IF NOT EXISTS manual_review_reason varchar(255)
        """
    )

    # =========================================================
    # 4. users.notified_expired
    # =========================================================
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS notified_expired boolean NOT NULL DEFAULT false
        """
    )

    # =========================================================
    # 5. users.notified_grace_12h
    # =========================================================
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS notified_grace_12h boolean NOT NULL DEFAULT false
        """
    )

    # =========================================================
    # 6. pending_api_deletions.reason
    # =========================================================
    op.execute(
        """
        ALTER TABLE pending_api_deletions
        ADD COLUMN IF NOT EXISTS reason varchar(50)
        """
    )

    # =========================================================
    # 7. maintenance_mode
    # =========================================================
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

    # =========================================================
    # 8. hub_messages
    # =========================================================
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

    # =========================================================
    # 9. Singleton maintenance_mode row
    # =========================================================
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

    # =========================================================
    # Дополнительные индексы для grace-периода и уведомлений
    # =========================================================
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
    # ВНИМАНИЕ:
    # Полный downgrade этой миграции опасен для production,
    # потому что меняет тип денежной колонки и удаляет таблицы.
    #
    # Ниже приведён технически возможный откат, но использовать его
    # нужно только в тестовой среде или после ручного бэкапа.

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