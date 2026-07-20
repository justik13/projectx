"""Initial baseline migration.

Эта миграция создаёт все таблицы по текущей модели через
Base.metadata.create_all.

Для новой базы:
- создаёт полную схему.

Для существующей базы:
- create_all использует checkfirst, поэтому существующие таблицы
  не пересоздаются;
- реальные изменения существующих таблиц добавляются следующей
  миграцией 0002_critical_fixes.

Revision ID: 0001
Revises:
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op

from database.models import Base


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)