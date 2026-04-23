from sqlalchemy import (
    BigInteger,
    Integer,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone, timedelta
from typing import Optional

from bot.db.base import Base

class ClientConfig(Base):
    __tablename__ = "client_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    config_number: Mapped[int] = mapped_column(Integer, default=1)  # Номер конфига (1, 2, 3...)
    config_name: Mapped[str] = mapped_column(String(128))  # Имя в админке (user_123456789_1)
    vless_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # VLESS ссылка
    is_active: Mapped[bool] = mapped_column(default=True)  # Активен ли конфиг
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now())
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Связь с пользователем
    user: Mapped["User"] = relationship("User", back_populates="configs")

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )

    # Добавляем связь с подпиской
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription", 
        back_populates="user", 
        uselist=False,  # одна подписка на пользователя
        cascade="all, delete-orphan"
    )
    
    # Добавляем связь с платежами
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", 
        back_populates="user",
        cascade="all, delete-orphan"
    )

    configs: Mapped[list["ClientConfig"]] = relationship(
        "ClientConfig", 
        back_populates="user",
        cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    next_payment: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="active")
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    last_reminder_sent: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), 
        nullable=True
    )
    
    # Обратная связь
    user: Mapped["User"] = relationship("User", back_populates="subscription")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now()
    )
    status: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Сумма платежа
    months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Количество месяцев
    
    user: Mapped["User"] = relationship("User", back_populates="payments")