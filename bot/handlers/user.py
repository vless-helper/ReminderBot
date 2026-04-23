from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot.db.models import ClientConfig
from bot.db import crud
from bot.keyboards.keyboards import get_main_keyboard, get_payment_keyboard, get_question_keyboard, get_admin_keyboard, get_extend_payment_keyboard, get_configs_keyboard, get_config_actions_keyboard
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
            f"💰 Цена подписки: {config.SUBSCRIPTION_PRICE}₽/месяц\n\n"
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
        
        price_info = (
            f"💰 Стоимость подписки: {config.SUBSCRIPTION_PRICE}₽\n\n"
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
        
        await message.answer(
            "📅 Выберите срок продления:",
            reply_markup=get_extend_payment_keyboard()
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
            payment = await crud.create_payment(session, user.id, status="pending", amount=config.SUBSCRIPTION_PRICE, months=1)
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
            f"Сумма: {config.SUBSCRIPTION_PRICE}₽\n\n"
            f"Проверьте банк и подтвердите оплату.",
            reply_markup=get_admin_keyboard(callback.from_user.id)
        )
    
    await callback.answer()


@router.callback_query(F.data == "payment_confirmed")
async def payment_confirmed(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил оплату"""
    await callback.message.edit_reply_markup(reply_markup=None)
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if user:
            payment = await crud.create_payment(session, user.id, status="pending")
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
            f"Сумма: {config.SUBSCRIPTION_PRICE}₽\n\n"
            f"Проверьте банк и подтвердите оплату.",
            reply_markup=get_admin_keyboard(callback.from_user.id)
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


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("✅ Действие отменено.")


# Обработка создания доп. конфигов

# bot/handlers/user.py
@router.message(F.text == "📱 Мои конфиги")
async def my_configs(message: Message):
    """Показать список конфигов пользователя"""
    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, message.from_user.id)
        if not user:
            await message.answer("❌ Ошибка! Попробуйте /start")
            return
        
        configs = await crud.get_user_configs(session, user.id)
        
        if not configs:
            await message.answer(
                "📭 У вас пока нет конфигов.\n\n"
                "Создайте первый конфиг: /new_config"
            )
            return
        
        text = "📱 Ваши конфиги:\n\n"
        for cfg in configs:
            text += f"▫️ Конфиг #{cfg.config_number} - {cfg.created_at.strftime('%d.%m.%Y')}\n"
        
        await message.answer(
            text,
            reply_markup=get_configs_keyboard(user.id, configs)
        )


@router.callback_query(F.data == "create_new_config")
async def create_new_config(callback: CallbackQuery):
    """Создать новый конфиг"""
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            await callback.answer("❌ Ошибка!", show_alert=True)
            return
        
        # Получаем следующий номер
        next_number = await crud.get_next_config_number(session, user.id)
        config_name = f"user_{callback.from_user.id}_{next_number}"
        
        await callback.answer("🔄 Создаю новый конфиг...")
        
        # Создаем конфиг в админке
        vless_link = await admin_api.create_user_and_get_config(config_name)
        
        if vless_link:
            # Сохраняем в БД
            config = await crud.create_client_config(
                session, 
                user.id, 
                next_number, 
                config_name, 
                vless_link
            )
            
            await callback.message.answer(
                f"✅ Новый конфиг #{next_number} создан!\n\n"
                f"🔗 VLESS ссылка:\n`{vless_link}`\n\n"
                f"Сохраните ссылку в надежном месте.",
                parse_mode="Markdown"
            )
        else:
            await callback.message.answer("❌ Не удалось создать конфиг. Попробуйте позже.")
    
    await callback.answer()


@router.callback_query(F.data.startswith("show_config_"))
async def show_config(callback: CallbackQuery):
    """Показать VLESS ссылку конфига"""
    config_id = int(callback.data.split("_")[2])
    
    async with callback.bot.get_db_session() as session:
        stmt = select(ClientConfig).where(ClientConfig.id == config_id)
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