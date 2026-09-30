from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.config import utcnow
from bot.db.base import Base
from bot.db.types import UTCDateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )

    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    configs: Mapped[list["ClientConfig"]] = relationship(
        "ClientConfig",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # Когда подписка заморозится (UTC aware)
    next_payment: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    period_days: Mapped[int] = mapped_column(Integer, default=30)

    active_configs_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship("User", back_populates="subscription")


class ReminderLog(Base):
    """Отметка «напоминание за N дней отправлено».

    Раньше был один флаг last_reminder_sent на все виды напоминаний, поэтому
    3-дневное гасило 1-дневное, а при смене REMINDER_DAYS_BEFORE одно молча
    ломалось. Теперь на каждое окно своя строка с уникальным ключом.
    days_before = -1 означает уведомление об истечении подписки.
    """

    __tablename__ = "reminder_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    days_before: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("subscription_id", "days_before", name="uq_reminder_sub_days"),
    )


class Payment(Base):
    """Платёж хранит всё, что нужно для подтверждения.

    Раньше эти данные лежали в FSM плательщика, а админ читал из своего FSM —
    из-за этого у другого админа/другого аккаунта всё ломалось.
    Теперь админ подтверждает конкретный payment_id из callback_data.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # subscription | extend | new_config
    type: Mapped[str] = mapped_column(String(32), default="subscription", index=True)

    # pending | processing | completed | rejected | failed
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    # Эффект в БД уже применён — повторная обработка не должна продлевать снова
    effect_applied: Mapped[bool] = mapped_column(Boolean, default=False)

    # Последняя ошибка применения, показывается админу
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    amount: Mapped[int] = mapped_column(Integer, default=0)
    months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    config_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now(), index=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    resolved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="payments")

    @property
    def is_open(self) -> bool:
        """Можно ещё подтверждать (или повторять после сбоя)."""
        return self.status in {"pending", "failed"}


class ClientConfig(Base):
    __tablename__ = "client_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )

    config_number: Mapped[int] = mapped_column(Integer, default=1)
    config_name: Mapped[str] = mapped_column(String(128), unique=True)
    vless_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )
    paid_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)

    user: Mapped["User"] = relationship("User", back_populates="configs")

    __table_args__ = (
        # Один конфиг с номером на пользователя
        UniqueConstraint("user_id", "config_number", name="uq_config_user_number"),
    )
