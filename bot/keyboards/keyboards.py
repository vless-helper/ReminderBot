# bot/keyboards/keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура"""
    buttons = [
        [KeyboardButton(text="📦 Купить подписку")],
        [KeyboardButton(text="🔄 Продлить подписку")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="ℹ️ Моя подписка")],
        [KeyboardButton(text="📱 Мои конфиги")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_extend_payment_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора количества месяцев продления"""
    builder = InlineKeyboardBuilder()
    builder.button(text="1 месяц (1000₽)", callback_data="extend_1_month")
    builder.button(text="3 месяца (2700₽)", callback_data="extend_3_months")
    builder.button(text="6 месяцев (5000₽)", callback_data="extend_6_months")
    builder.button(text="12 месяцев (9000₽)", callback_data="extend_12_months")
    builder.button(text="❌ Отмена", callback_data="payment_cancel")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_extend_keyboard(user_id: int, months: int, amount: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа с указанием месяцев"""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Подтвердить оплату ({months} мес, {amount}₽)", 
        callback_data=f"confirm_extend_{user_id}_{months}_{amount}"
    )
    builder.button(
        text="❌ Отклонить оплату", 
        callback_data=f"reject_payment_{user_id}"
    )
    return builder.as_markup()

def get_payment_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для оплаты"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я оплатил(а)", callback_data="payment_confirmed")
    builder.button(text="❌ Отмена", callback_data="payment_cancel")
    builder.adjust(1)
    return builder.as_markup()

def get_question_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="question_cancel")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа"""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить оплату",
        callback_data=f"confirm_payment_{user_id}"
    )
    builder.button(
        text="❌ Отклонить оплату", 
        callback_data=f"reject_payment_{user_id}"
    )
    return builder.as_markup()


def get_confirm_question_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения ответа на вопрос"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Отправить ответ", callback_data=f"send_answer_{question_id}")
    return builder.as_markup()

# Клава для конфигов 

def get_configs_keyboard(user_id: int, configs: list) -> InlineKeyboardMarkup:
    """Клавиатура для выбора конфига"""
    builder = InlineKeyboardBuilder()
    
    for config in configs:
        builder.button(
            text=f"📱 Конфиг #{config.config_number}", 
            callback_data=f"get_config_{config.id}"
        )
    
    builder.button(text="➕ Создать новый конфиг", callback_data="create_new_config")
    builder.button(text="❌ Закрыть", callback_data="close_configs")
    builder.adjust(1)
    return builder.as_markup()


def get_config_actions_keyboard(config_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий с конфигом"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Получить ссылку", callback_data=f"show_config_{config_id}")
    builder.button(text="🗑 Удалить конфиг", callback_data=f"delete_config_{config_id}")
    builder.button(text="◀️ Назад", callback_data="back_to_configs")
    builder.adjust(1)
    return builder.as_markup()