from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import asyncio
from sqlalchemy import select
from bot.db.models import User


from bot.db.models import ClientConfig
from bot.db import crud
from bot.keyboards.keyboards import (
    get_main_keyboard, 
    get_payment_keyboard, 
    get_question_keyboard, 
    get_admin_keyboard, 
    get_extend_payment_keyboard, 
    get_configs_keyboard, 
    get_config_actions_keyboard
)
from bot.config import config
from bot.api.client import admin_api

router = Router()

MAIN_MENU_BUTTONS = [
    "📦 Купить подписку",
    "🔄 Продлить подписку", 
    "❓ Задать вопрос",
    "ℹ️ Моя подписка"
]

# Состояния для FSM
class PaymentState(StatesGroup):
    waiting_for_payment_confirmation = State()


class QuestionState(StatesGroup):
    waiting_for_question = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""

    await state.clear()

    async with message.bot.get_db_session() as session:
        user = await crud.get_or_create_user(
            session, 
            message.from_user.id,
            message.from_user.username
        )
        
        has_subscription = await crud.check_subscription_status(session, user.id)
        
        welcome_text = (
            "👋 Добро пожаловать!\n\n"
            "Я бот для продажи Amnesia VPN.\n\n"
            f"💰 Цена подписки: {config.BASE_PRICE}₽/месяц\n\n"
            "📌 Для покупки нажмите кнопку 'Купить подписку'\n"
            "❓ Если есть вопросы - кнопка 'Задать вопрос'\n"
            "ℹ️ Для проверки статуса - 'Моя подписка'"
        )
        
        await message.answer(
            welcome_text,
            reply_markup=get_main_keyboard()
        )
        
        if has_subscription:
            await message.answer("✅ У вас есть активная подписка!")

@router.message(F.text.in_(MAIN_MENU_BUTTONS), StateFilter(QuestionState.waiting_for_question))
async def block_buttons_during_question(message: Message, state: FSMContext):
    """Блокируем нажатие на другие кнопки во время вопроса"""
    await message.answer(
        "⚠️ Вы сейчас задаете вопрос.\n"
        "Пожалуйста, напишите ваш вопрос или нажмите 'Отмена'.\n\n",
        reply_markup=get_question_keyboard()
    )

@router.message(F.text == "📦 Купить подписку", ~StateFilter(QuestionState.waiting_for_question))
async def buy_subscription(message: Message):
    """Покупка подписки"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        has_subscription = await crud.check_subscription_status(session, user.id)
        
        if has_subscription:
            subscription = await crud.get_user_subscription(session, user.id)
            days_left = (subscription.next_payment - datetime.now()).days
            await message.answer(
                f"✅ У вас уже есть активная подписка!\n"
                f"Осталось дней: {days_left}\n\n"
                f"Вы можете продлить подписку в любой момент."
            )
            return
        
        monthly_price = await crud.calculate_monthly_price(session, user.id)
        
        price_info = (
            f"💰 Стоимость подписки: {monthly_price}₽\n\n"
            f"📥 Скачать Amnesia: {config.AMNESIA_DOWNLOAD_LINK}\n\n"
            f"🔧 Инструкция по настройке туннеля:\n{config.TUNNEL_INSTRUCTION}\n\n"
            f"💳 Оплата:\n"
            f"Карта: {config.CARD_NUMBER}\n"
            f"Получатель: {config.CARD_HOLDER}\n\n"
            f"❗️ После оплаты нажмите кнопку 'Я оплатил(а)'"
        )
        
        await message.answer(
            price_info,
            reply_markup=get_payment_keyboard()
        )

@router.message(F.text == "🔄 Продлить подписку", ~StateFilter(QuestionState.waiting_for_question))
async def extend_subscription(message: Message):
    """Продление подписки"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        has_subscription = await crud.check_subscription_status(session, user.id)
        
        if not has_subscription:
            await message.answer(
                "❌ У вас нет активной подписки.\n"
                "Для покупки нажмите кнопку 'Купить подписку'"
            )
            return
        
        # Получаем количество активных конфигов
        configs_count = await crud.get_active_configs_count(session, user.id)
        
        await message.answer(
            "📅 Выберите срок продления:",
            reply_markup=get_extend_payment_keyboard(user.id, configs_count)
        )

@router.message(F.text == "❓ Задать вопрос", ~StateFilter(QuestionState.waiting_for_question))
async def ask_question(message: Message, state: FSMContext):
    """Начать процесс задавания вопроса"""
    await message.answer(
        "📝 Напишите ваш вопрос.\n"
        "Администратор ответит вам в ближайшее время.\n\n",
        reply_markup=get_question_keyboard()
    )
    await state.set_state(QuestionState.waiting_for_question)

@router.message(F.text == "ℹ️ Моя подписка")
async def check_subscription(message: Message):
    """Проверка статуса подписки"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        has_subscription = await crud.check_subscription_status(session, user.id)
        
        if has_subscription:
            subscription = await crud.get_user_subscription(session, user.id)
            days_left = (subscription.next_payment - datetime.now()).days + 1
            await message.answer(
                f"✅ Подписка активна!\n\n"
                f"📅 Следующее списание: {subscription.next_payment.strftime('%d.%m.%Y')}\n"
                f"⏰ Осталось дней: {days_left}"
            )
        else:
            await message.answer(
                "❌ У вас нет активной подписки.\n\n"
                "Для покупки нажмите кнопку 'Купить подписку'"
            )

@router.callback_query(F.data == "one-month_payment")
async def extend_one_month_payment(callback: CallbackQuery, state: FSMContext):
    """Пользователь продлил подписку на один месяц"""
    await callback.message.edit_reply_markup(reply_markup=None)
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if user:
            payment = await crud.create_payment(session, user.id, status="pending", amount=config.BASE_PRICE, months=1)
            await state.update_data(payment_id=payment.id)
    
    await callback.message.answer(
        "✅ Спасибо! Я отправил уведомление администратору.\n"
        "Ожидайте подтверждения оплаты. Обычно это занимает до 30 минут."
    )
    
    for admin_id in config.ADMIN_IDS:
        await callback.bot.send_message(
            admin_id,
            f"💰 Новый платеж!\n\n"
            f"Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"ID: {callback.from_user.id}\n"
            f"Сумма: {config.BASE_PRICE}₽\n\n"
            f"Проверьте банк и подтвердите оплату.",
            reply_markup=get_admin_keyboard(callback.from_user.id)
        )
    
    await callback.answer()


@router.callback_query(F.data == "payment_confirmed")
async def payment_confirmed(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил оплату (может быть оплата подписки или нового конфига)"""
    await callback.message.edit_reply_markup(reply_markup=None)
    
    # Проверяем, есть ли данные о создании нового конфига
    state_data = await state.get_data()
    is_new_config = state_data.get("new_config_number") is not None
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            await callback.message.answer("❌ Ошибка!")
            return
        
        monthly_price = await crud.calculate_monthly_price(session, user.id)

        # Создаем запись о платеже
        payment = await crud.create_payment(session, user.id, status="pending")
        
        if is_new_config:
            # Это оплата нового конфига
            await state.update_data(payment_id=payment.id, is_new_config=True)
            payment_type = "нового конфига"
        else:
            # Это оплата подписки
            await state.update_data(payment_id=payment.id)
            payment_type = "подписки"
    
    await callback.message.answer(
        f"✅ Спасибо! Я отправил уведомление администратору об оплате {payment_type}.\n"
        "Ожидайте подтверждения. Обычно это занимает до 30 минут."
    )
    
    # Отправляем уведомление админу
    for admin_id in config.ADMIN_IDS:
        await callback.bot.send_message(
            admin_id,
            f"💰 НОВЫЙ ПЛАТЕЖ ({payment_type.upper()})!\n\n"
            f"Сумма: {monthly_price}₽\n\n"
            f"Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"ID: {callback.from_user.id}\n"
            f"Тип: {payment_type}\n\n"
            f"Проверьте банк и подтвердите оплату.",
            reply_markup=get_admin_keyboard(callback.from_user.id, is_new_config=is_new_config)
        )
    
    await callback.answer()


@router.callback_query(F.data == "payment_cancel")
async def payment_cancel(callback: CallbackQuery):
    """Отмена оплаты"""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Оплата отменена.")
    await callback.answer()

@router.callback_query(F.data == "question_cancel")
async def question_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Вопрос отменен")
    await callback.answer()


@router.message(QuestionState.waiting_for_question)
async def process_question(message: Message, state: FSMContext):
    """Обработка вопроса"""
    question_text = message.text
    
    for admin_id in config.ADMIN_IDS:
        await message.bot.send_message(
            admin_id,
            f"❓ Новый вопрос от пользователя!\n\n"
            f"От: @{message.from_user.username or message.from_user.id}\n"
            f"ID: {message.from_user.id}\n\n"
            f"Вопрос:\n{question_text}\n\n"
            f"Для ответа используйте:\n/answer {message.from_user.id} [текст ответа]"
        )
    
    await message.answer(
        "✅ Ваш вопрос отправлен администратору!\n"
        "Ответ придет в этот чат."
    )
    
    await state.clear()


@router.message(F.text == "ℹ️ Моя подписка")
async def check_subscription(message: Message):
    """Проверка статуса подписки"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        has_subscription = await crud.check_subscription_status(session, user.id)

        monthly_price = await crud.calculate_monthly_price(session, user.id)
        
        if has_subscription:
            subscription = await crud.get_user_subscription(session, user.id)
            days_left = (subscription.next_payment - datetime.now()).days + 1
            await message.answer(
                f"✅ Подписка активна!\n\n"
                f"📅 Подписка будет заморожена: {subscription.next_payment.strftime('%d.%m.%Y')}\n"
                f"Сумма списания: {monthly_price}₽\n"
                f"⏰ Осталось дней: {days_left - 1}"
            )
        else:
            await message.answer(
                "❌ У вас нет активной подписки.\n\n"
                "Для покупки нажмите кнопку 'Купить подписку'"
            )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("✅ Действие отменено.")


# Обработка создания доп. конфигов

@router.message(F.text == "📱 Мои конфиги")
async def my_configs(message: Message):
    """Показать список конфигов пользователя"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        configs = await crud.get_user_configs(session, user.id)
        
        # Добавьте отладочный вывод
        print(f"Найдено конфигов: {len(configs)}")
        for cfg in configs:
            print(f"Конфиг #{cfg.config_number}: id={cfg.id}, is_active={cfg.is_active}")
        
        monthly_price = await crud.calculate_monthly_price(session, user.id)
        
        if not configs:
            await message.answer(
                "📭 У вас пока нет конфигов.\n\n"
                "Создайте новый конфиг через меню 'Мои конфиги'"
            )
            return
        
        text = f"📱 Ваши конфиги ({len(configs)} шт.):\n\n"
        text += f"💰 Ежемесячный платеж: {monthly_price}₽\n\n"

        for cfg in configs:
            protected_mark = " 🔒" if cfg.is_protected else ""
            text += f"▫️ Конфиг #{cfg.config_number}{protected_mark} - создан {cfg.created_at.strftime('%d.%m.%Y')}\n"
        
        # Проверьте, что клавиатура создается
        keyboard = get_configs_keyboard(user.id, configs)
        
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "create_new_config")
async def create_new_config(callback: CallbackQuery, state: FSMContext):
    """Создать новый конфиг (требует оплаты)"""
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            await callback.answer("❌ Ошибка!", show_alert=True)
            return
        
        # Проверяем активную подписку
        has_subscription = await crud.check_subscription_status(session, user.id)
        if not has_subscription:
            await callback.message.answer(
                "❌ У вас нет активной подписки.\n"
                "Сначала оформите подписку через кнопку 'Купить подписку'"
            )
            await callback.answer()
            return
        
        # Получаем следующий номер конфига
        next_number = await crud.get_next_config_number(session, user.id)
        # Получаем остаток дней до основного платежа
        days_left = await crud.get_remaining_days_until_next_payment(session, user.id)
        # Базовая цена за конфиг
        base_config_price = config.BASE_PRICE

        # Рассчитываем пропорциональную цену
        if days_left > 0:
            standard_month = 30
            ratio = days_left / standard_month
            prorated_price = int(base_config_price * ratio)
            new_config_price = max(1, prorated_price)
        
            price_explanation = (
                f"📅 До следующего платежа осталось {days_left} дней.\n"
                f"💰 Плата за новый конфиг составит {new_config_price}₽ "
                f"(пропорционально остатку месяца).\n"
                f"При следующем продлении будет взиматься полная стоимость."
            )
        else:
            new_config_price = base_config_price
            price_explanation = "💰 Оплата за полный месяц."
        
        # Сохраняем в состояние
        await state.update_data(
            new_config_number=next_number,
            new_config_price=new_config_price,
            new_config_prorated=days_left > 0
        )
        
        await callback.message.answer(
            f"📱 Создание нового конфига #{next_number}\n\n"
            f"{price_explanation}\n\n"
            f"Сумма к оплате: {new_config_price}₽\n\n"
            f"💳 Реквизиты для оплаты:\n"
            f"Карта: {config.CARD_NUMBER}\n"
            f"Получатель: {config.CARD_HOLDER}\n\n"
            f"❗️ После оплаты нажмите кнопку 'Я оплатил(а)'",
            reply_markup=get_payment_keyboard()
        )
    
    await callback.answer()

@router.callback_query(F.data.startswith("select_config_"))
async def select_config(callback: CallbackQuery):
    """Выбор конфига для действий"""
    config_id = int(callback.data.split("_")[2])
    
    async with callback.bot.get_db_session() as session:
        # Находим пользователя
        
        stmt_user = select(User).where(User.telegram_id == callback.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Ищем конфиг
        stmt = select(ClientConfig).where(
            ClientConfig.id == config_id,
            ClientConfig.user_id == user.id,
            ClientConfig.is_active == True
        )
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("❌ Конфиг не найден", show_alert=True)
            return
        
        status_text = "Активен"
        if config.paid_until:
            if config.paid_until < datetime.now():
                status_text = "⚠️ Требуется продление"
            else:
                days_left = (config.paid_until - datetime.now()).days
                status_text = f"Активен до {config.paid_until.strftime('%d.%m.%Y')} (осталось {days_left} дн.)"
        
        await callback.message.edit_text(
            f"📱 Конфиг #{config.config_number}\n\n"
            f"📅 Создан: {config.created_at.strftime('%d.%m.%Y')}\n"
            f"🔒 Статус: {status_text}\n"
            f"{'🔒 Защищенный (основной)' if config.is_protected else ''}\n\n"
            f"Выберите действие:",
            reply_markup=get_config_actions_keyboard(config.id, config.config_number, config.is_protected)
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("delete_config_"))
async def delete_config(callback: CallbackQuery):
    """Удалить конфиг"""
    config_id = int(callback.data.split("_")[2])
    
    async with callback.bot.get_db_session() as session:
        # Сначала находим пользователя по telegram_id
        from bot.db.models import User
        from sqlalchemy import select
        
        stmt_user = select(User).where(User.telegram_id == callback.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Находим конфиг по внутреннему user_id
        stmt = select(ClientConfig).where(
            ClientConfig.id == config_id,
            ClientConfig.user_id == user.id,  # <-- используем внутренний ID!
            ClientConfig.is_active == True
        )
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("❌ Конфиг не найден", show_alert=True)
            return
        
        config_name = config.config_name
        config_number = config.config_number
        
        # Деактивируем в БД бота
        success = await crud.deactivate_config(session, config_id, user.id)
        
        if success:
            # Удаляем из админки
            await crud.delete_config_from_admin(session, config_name)
            
            await callback.message.edit_text(
                f"✅ Конфиг #{config_number} успешно удален!\n\n"
                f"Вы можете создать новый конфиг через меню 'Мои конфиги'."
            )
            
            # Обновляем список конфигов
            await asyncio.sleep(2)
            
            configs = await crud.get_user_configs(session, user.id)
            monthly_price = await crud.calculate_monthly_price(session, user.id)
            
            if configs:
                text = f"📱 Ваши конфиги ({len(configs)} шт.):\n\n"
                text += f"💰 Ежемесячный платеж: {monthly_price}₽\n\n"
                
                for cfg in configs:
                    text += f"▫️ Конфиг #{cfg.config_number} - создан {cfg.created_at.strftime('%d.%m.%Y')}\n"
                
                await callback.message.answer(
                    text,
                    reply_markup=get_configs_keyboard(user.id, configs)
                )
            else:
                await callback.message.answer(
                    "📭 У вас больше нет активных конфигов.\n\n"
                    "Создайте новый конфиг через меню 'Мои конфиги'"
                )
        else:
            await callback.answer("❌ Не удалось удалить конфиг", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "back_to_configs")
async def back_to_configs(callback: CallbackQuery):
    """Вернуться к списку конфигов"""
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if user:
            configs = await crud.get_user_configs(session, user.id)
            monthly_price = await crud.calculate_monthly_price(session, user.id)
            
            text = f"📱 Ваши конфиги ({len(configs)} шт.):\n\n"
            text += f"💰 Ежемесячный платеж: {monthly_price}₽\n\n"
            
            for cfg in configs:
                text += f"▫️ Конфиг #{cfg.config_number} - создан {cfg.created_at.strftime('%d.%m.%Y')}\n"
            
            await callback.message.edit_text(
                text,
                reply_markup=get_configs_keyboard(user.id, configs)
            )
    
    await callback.answer()


@router.callback_query(F.data == "close_configs")
async def close_configs(callback: CallbackQuery):
    """Закрыть меню конфигов"""
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith("show_config_"))
async def show_config(callback: CallbackQuery):
    """Показать VLESS ссылку конфига"""
    config_id = int(callback.data.split("_")[2])
    
    async with callback.bot.get_db_session() as session:
        # Сначала находим пользователя
        from bot.db.models import User
        from sqlalchemy import select
        
        stmt_user = select(User).where(User.telegram_id == callback.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        stmt = select(ClientConfig).where(
            ClientConfig.id == config_id,
            ClientConfig.user_id == user.id,
            ClientConfig.is_active == True
        )
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if config and config.vless_link:
            await callback.message.answer(
                f"🔗 Конфиг #{config.config_number}:\n\n"
                f"`{config.vless_link}`\n\n"
                f"📱 Инструкция по установке в Amnesia",
                parse_mode="Markdown"
            )
        else:
            await callback.answer("❌ Конфиг не найден", show_alert=True)
    
    await callback.answer()