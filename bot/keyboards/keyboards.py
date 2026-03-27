# bot/keyboards/keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура"""
    buttons = [
        [KeyboardButton(text="📦 Купить подписку")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="ℹ️ Моя подписка")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


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