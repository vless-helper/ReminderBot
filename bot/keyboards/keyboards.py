from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.utils.helpers import format_months, format_price
from bot.utils.pricing import subscription_price


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📦 Купить подписку"),
                KeyboardButton(text="🔄 Продлить подписку"),
                KeyboardButton(text="❓ Задать вопрос"),
            ],
            [
                KeyboardButton(text="ℹ️ Моя подписка"),
                KeyboardButton(text="📱 Мои конфиги"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def get_extend_keyboard(configs_count: int) -> InlineKeyboardMarkup:
    """Выбор срока продления. Цена берётся из pricing — ровно та же, что потом начислится."""
    builder = InlineKeyboardBuilder()
    for months, _ in config.period_tiers():
        price = subscription_price(months, configs_count).total
        builder.button(
            text=f"{format_months(months)} — {format_price(price)}",
            callback_data=f"extend:{months}",
        )
    builder.button(text="❌ Отмена", callback_data="close")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Оплата. В callback_data сразу id платежа — админ подтвердит именно его."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я оплатил(а)", callback_data=f"pay:{payment_id}")
    builder.button(text="❌ Отмена", callback_data="close")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_payment_keyboard(payment_id: int, amount: int, title: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Подтвердить {title} ({format_price(amount)})", callback_data=f"ok:{payment_id}")
    builder.button(text="❌ Отклонить", callback_data=f"no:{payment_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_question_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="close")
    builder.adjust(1)
    return builder.as_markup()


def get_configs_keyboard(configs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cfg in configs:
        icon = "🔒" if cfg.is_protected else "📱"
        builder.button(
            text=f"{icon} Конфиг #{cfg.config_number}",
            callback_data=f"cfg:{cfg.id}",
        )
    builder.button(text="➕ Докупить конфиг", callback_data="cfgnew")
    builder.button(text="❌ Закрыть", callback_data="close")
    builder.adjust(1)
    return builder.as_markup()


def get_config_actions_keyboard(cfg) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Получить ссылку", callback_data=f"link:{cfg.id}")

    if not cfg.is_protected:
        builder.button(text="🗑 Удалить конфиг", callback_data=f"del:{cfg.id}")

    builder.button(text="◀️ К списку", callback_data="cfglist")
    builder.adjust(1)
    return builder.as_markup()


def get_config_pay_keyboard(payment_id: int, price: int) -> InlineKeyboardMarkup:
    return get_payment_keyboard(payment_id)


def render_price_breakdown(counts_text: str, price, extra: str = "") -> str:
    """Единый вид цены для всех экранов оплаты."""
    lines = [f"💰 К оплате: <b>{format_price(price.total)}</b>", ""]
    lines += [f"• {line}" for line in price.lines()]
    if extra:
        lines += ["", extra]
    if counts_text:
        lines.insert(2, counts_text)
    return "\n".join(lines)


def get_retry_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Повторная обработка платежа, который зачислили, но не смогли применить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Повторить", callback_data=f"retry:{payment_id}"
                )
            ]
        ]
    )
