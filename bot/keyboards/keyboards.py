# bot/keyboards/keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.config import config 
from bot.utils.helpers import format_months, format_price


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура"""
    buttons = [
            [KeyboardButton(text="📦 Купить подписку"), KeyboardButton(text="🔄 Продлить подписку"), KeyboardButton(text="❓ Задать вопрос")],
            [KeyboardButton(text="ℹ️ Моя подписка"), KeyboardButton(text="📱 Мои конфиги")]
        ]
        
    return ReplyKeyboardMarkup(
        keyboard=buttons, 
        resize_keyboard=True,  # Автоматически подгонять размер
        one_time_keyboard=True,  # Скрывать после нажатия (раскомментировать если нужно)
        input_field_placeholder="Выберите действие",  # Подсказка в поле ввода
        # selective=True  # Показывать только определенным пользователям
    )

def get_extend_payment_keyboard(user_id: int, configs_count: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора количества месяцев продления"""
    builder = InlineKeyboardBuilder()
    
    # Базовая цена за один конфиг
    base_price = config.BASE_PRICE
    
    # Ежемесячная цена с учетом количества конфигов
    monthly_price = base_price * configs_count
    
    # 1 месяц - без скидки
    price_1_month = monthly_price
    
    # 3 месяца - 5% скидка
    price_3_months = int(monthly_price * 3 * 0.95)
    
    # 6 месяцев - 10% скидка
    price_6_months = int(monthly_price * 6 * 0.9)
    
    # 12 месяцев - 15% скидка
    price_12_months = int(monthly_price * 12 * 0.85)
    
    builder.button(text=f"1 месяц ({format_price(price_1_month)})", callback_data="extend_1_month")
    builder.button(text=f"3 месяца ({format_price(price_3_months)})", callback_data="extend_3_months")
    builder.button(text=f"6 месяцев ({format_price(price_6_months)})", callback_data="extend_6_months")
    builder.button(text=f"12 месяцев ({format_price(price_12_months)})", callback_data="extend_12_months")
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

def get_admin_keyboard(user_id: int, is_new_config: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для админа"""
    builder = InlineKeyboardBuilder()
    if is_new_config:
        builder.button(text="✅ Подтвердить создание конфига", callback_data=f"confirm_payment_{user_id}_new")
    else:
        builder.button(text="✅ Подтвердить оплату", callback_data=f"confirm_payment_{user_id}")
    builder.button(text="❌ Отклонить оплату", callback_data=f"reject_payment_{user_id}")
    return builder.as_markup()


def get_confirm_question_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения ответа на вопрос"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Отправить ответ", callback_data=f"send_answer_{question_id}")
    return builder.as_markup()

# Клава для конфигов 

def get_config_actions_keyboard(config_id: int, config_number: int, is_protected: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура действий с конфигом"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Получить ссылку", callback_data=f"show_config_{config_id}")
    
    # Кнопка удаления только если конфиг не защищен
    if not is_protected:
        builder.button(text="🗑 Удалить конфиг", callback_data=f"delete_config_{config_id}")
    
    builder.button(text="◀️ Назад к списку", callback_data="back_to_configs")
    builder.adjust(1)
    return builder.as_markup()


def get_configs_keyboard(user_id: int, configs: list) -> InlineKeyboardMarkup:
    """Клавиатура для выбора конфига"""
    builder = InlineKeyboardBuilder()
    
    if not configs:
        builder.button(text="➕ Создать конфиг", callback_data="create_new_config")
    else:
        for cfg in configs:
            # Показываем защищенный конфиг с особым значком
            icon = "🔒" if cfg.is_protected else "📱"
            builder.button(
                text=f"{icon} Конфиг #{cfg.config_number}", 
                callback_data=f"select_config_{cfg.id}"
            )
        builder.button(text="➕ Создать новый конфиг", callback_data="create_new_config")
    
    builder.button(text="❌ Закрыть", callback_data="close_configs")
    builder.adjust(1)
    return builder.as_markup()

def get_config_payment_keyboard(config_number: int, price: int) -> InlineKeyboardMarkup:
    """Клавиатура для оплаты нового конфига"""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💰 Оплатить конфиг #{config_number} ({price}₽)", callback_data=f"pay_new_config_{config_number}")
    builder.button(text="❌ Отмена", callback_data="cancel_new_config")
    builder.adjust(1)
    return builder.as_markup()