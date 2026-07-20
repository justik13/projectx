from datetime import datetime, timezone, date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    backref,
)

from utils.datetime_helpers import now_utc
from utils.encryption import EncryptedString


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    tos_accepted: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    subscription_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    device_limit: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    current_tariff_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tariffs.id", ondelete="SET NULL"),
        nullable=True,
    )

    referred_by: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )

    referral_days: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    last_payment_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_banned: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_bot_blocked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
    )

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    notification_retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    last_notification_attempt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )

    notified_3d: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    notified_1d: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    notified_2h: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Новые флаги для grace-периода после истечения подписки.
    #
    # notified_expired:
    #   отправлено уведомление сразу после истечения подписки.
    #
    # notified_grace_12h:
    #   отправлено уведомление за 12 часов до удаления устройств.
    notified_expired: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    notified_grace_12h: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    device_creations_today: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    last_creation_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    profiles = relationship(
        "VPNProfile",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payments = relationship(
        "Payment",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    current_tariff = relationship(
        "Tariff",
        foreign_keys=[current_tariff_id],
    )

    referrals = relationship(
        "User",
        backref=backref("referrer", remote_side=[telegram_id]),
        foreign_keys=[referred_by],
        primaryjoin="User.telegram_id == User.referred_by",
    )


class VPNProfile(Base):
    __tablename__ = "vpn_profiles"

    __table_args__ = (
        Index(
            "uq_vpn_profiles_peer_id",
            "peer_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    device_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    peer_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Пользовательская конфигурация не является критичным серверным
    # секретом в том же смысле, что и API-ключ сервера.
    #
    # При ошибке расшифровки допустим безопасный fallback с предложением
    # пересоздать устройство.
    raw_config: Mapped[str] = mapped_column(
        EncryptedString(critical=False),
        nullable=False,
    )

    traffic_down: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
    )

    traffic_up: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
    )

    last_connected: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_ip: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    sync_fail_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )

    user = relationship(
        "User",
        back_populates="profiles",
    )

    server = relationship("Server")


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    country_flag: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    api_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # API-ключ сервера — критичный секрет.
    #
    # При ошибке расшифровки нужно логировать critical и не продолжать
    # тихо работать с пустым ключом.
    api_key: Mapped[str] = mapped_column(
        EncryptedString(critical=True),
        nullable=False,
    )

    protocol: Mapped[str] = mapped_column(
        String(50),
        default="amneziawg2",
    )

    max_clients: Mapped[int] = mapped_column(
        Integer,
        default=50,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    duration_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    device_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
    )

    # Цены тарифов остаются целыми числами, потому что продукт сейчас
    # оперирует целыми рублями и целыми звёздами.
    price_rub: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    price_stars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )


class Payment(Base):
    __tablename__ = "payments"

    __table_args__ = (
        Index(
            "ix_payment_external_completed",
            "external_id",
            unique=True,
            postgresql_where="status = 'completed'",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tariff_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tariffs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Деньги храним как Numeric(12,2).
    #
    # Даже если сейчас используются целые рубли, это защищает от:
    # - ошибок float;
    # - будущих копеек;
    # - некорректных сравнений суммы.
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Увеличено до 30 символов, чтобы безопасно хранить:
    # pending / completed / cancelled / failed / refunded /
    # requires_manual_review
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        index=True,
    )

    # Причина перевода платежа в ручную проверку.
    #
    # Например:
    # - banned_or_deleted
    # - inactive_tariff
    # - amount_mismatch
    # - amount_missing
    # - payload_mismatch
    # - paid_after_cancel
    # - stars_not_confirmed
    # - device_limit_exceeded
    manual_review_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    payment_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    qr_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    payment_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    user = relationship(
        "User",
        back_populates="payments",
    )

    tariff = relationship("Tariff")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    admin_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    target_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    target_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )


class BroadcastProgress(Base):
    __tablename__ = "broadcast_progress"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    admin_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )

    total_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    success_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    fail_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    last_processed_id: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
    )

    target_audience: Mapped[str] = mapped_column(
        String(20),
        default="all",
    )

    broadcast_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    media_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    content_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    label: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="in_progress",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class PendingAPIDeletion(Base):
    __tablename__ = "pending_api_deletions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    server_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    api_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # API-ключ в очереди удалений — критичный секрет.
    api_key: Mapped[str] = mapped_column(
        EncryptedString(critical=True),
        nullable=False,
    )

    peer_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    client_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Почему запись попала в очередь:
    #
    # - device_delete_api_failed
    # - ban_delete_api_failed
    # - grace_delete_api_failed
    # - chargeback_delete_api_failed
    # - server_delete_api_failed
    reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )


class MaintenanceMode(Base):
    """
    Таблица режима технических работ.

    Используется как singleton:
    - id = 1;
    - is_enabled = True/False.

    При включённом режиме:
    - запрещены новые подключения устройств;
    - запрещены новые платежи;
    - существующие подключения продолжают работать;
    - админка доступна;
    - админы могут обходить режим.
    """

    __tablename__ = "maintenance_mode"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    updated_by: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class HubMessage(Base):
    """
    Таблица для хранения ID сообщений-хабов по чатам.

    Нужно, чтобы после рестарта бота:
    - знать, какие старые хабы нужно удалить;
    - не дублировать сообщения;
    - сохранять чат чистым.

    Составной первичный ключ:
    - chat_id;
    - message_id.
    """

    __tablename__ = "hub_messages"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    message_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )