import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey, JSON, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class IikoSyncStatus(str, enum.Enum):
    pending = "pending"; synced = "synced"; error = "error"


class MailingType(str, enum.Enum):
    manual = "manual"; scheduled = "scheduled"; birthday = "birthday"; holiday = "holiday"


class MailingStatus(str, enum.Enum):
    draft = "draft"; scheduled = "scheduled"; sending = "sending"; sent = "sent"; cancelled = "cancelled"; failed = "failed"


class RunStatus(str, enum.Enum):
    sending = "sending"; sent = "sent"; failed = "failed"


class ApplicationSetting(Base, TimestampMixin):
    __tablename__ = "application_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(1000))


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100))
    birthday: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(254))
    personal_data_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    loyalty_account: Mapped["LoyaltyAccount | None"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)
    notification_settings: Mapped["NotificationSettings | None"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)
    loyalty_transactions: Mapped[list["LoyaltyTransaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    purchases: Mapped[list["Purchase"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class LoyaltyAccount(Base, TimestampMixin):
    __tablename__ = "loyalty_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    iiko_customer_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    iiko_card_id: Mapped[str | None] = mapped_column(String(128))
    card_number: Mapped[str | None] = mapped_column(String(64), unique=True)
    card_track: Mapped[str | None] = mapped_column(String(128))
    qr_payload: Mapped[str | None] = mapped_column(String(256))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    last_known_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    balance_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_transaction_revision: Mapped[int | None] = mapped_column(BigInteger)
    last_transaction_id: Mapped[str | None] = mapped_column(String(128))
    iiko_sync_status: Mapped[IikoSyncStatus] = mapped_column(Enum(IikoSyncStatus), default=IikoSyncStatus.pending, index=True)
    iiko_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="loyalty_account")


class Restaurant(Base, TimestampMixin):
    __tablename__ = "restaurants"
    id: Mapped[int] = mapped_column(primary_key=True)
    iiko_organization_id: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    code: Mapped[str | None] = mapped_column(String(100))
    inn: Mapped[str | None] = mapped_column(String(32))
    delivery_url: Mapped[str | None] = mapped_column(String(500))
    reviews_url: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(500))
    image_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    wallet_id: Mapped[str | None] = mapped_column(String(128))
    program_id: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(64))
    type_name: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    comment: Mapped[str | None] = mapped_column(Text)
    balance_before: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    order_number: Mapped[str | None] = mapped_column(String(64))
    order_sum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    pos_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(128))
    terminal_group_id: Mapped[str | None] = mapped_column(String(128))
    is_delivery: Mapped[bool | None] = mapped_column(Boolean)
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at_iiko: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    when_created_order: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="loyalty_transactions")


class Purchase(Base, TimestampMixin):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id", ondelete="SET NULL"))
    pos_order_id: Mapped[str] = mapped_column(String(128), unique=True)
    order_number: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    bonus_earned: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    bonus_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped[User] = relationship(back_populates="purchases")
    restaurant: Mapped[Restaurant | None] = relationship()


class NotificationSettings(Base):
    __tablename__ = "notification_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    promotions_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    holidays_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="notification_settings")


class Mailing(Base, TimestampMixin):
    __tablename__ = "mailings"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    image_file_id: Mapped[str | None] = mapped_column(String(500))
    type: Mapped[MailingType] = mapped_column(Enum(MailingType), default=MailingType.manual)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[MailingStatus] = mapped_column(Enum(MailingStatus), default=MailingStatus.draft, index=True)
    runs: Mapped[list["MailingRun"]] = relationship(back_populates="mailing", cascade="all, delete-orphan")


class MailingRun(Base):
    __tablename__ = "mailing_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    mailing_id: Mapped[int] = mapped_column(ForeignKey("mailings.id", ondelete="CASCADE"), index=True)
    total_count: Mapped[int] = mapped_column(default=0)
    sent_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.sending)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    mailing: Mapped[Mailing] = relationship(back_populates="runs")
