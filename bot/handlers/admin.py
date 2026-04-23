from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import html

from bot.config import config
from bot.db import crud
from bot.keyboards.keyboards import get_admin_extend_keyboard
from bot.api.client import admin_api

router = Router()

@router.message(Command("answer"))
async def answer_question(message: Message):
    """Ответ на вопрос пользователя"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для этой команды")
        return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Использование: /answer [user_id] [текст ответа]\n"
            "Пример: /answer 123456789 Спасибо за вопрос!"
        )
        return
    
    try:
        user_id = int(parts[1])
        answer_text = parts[2]
        
        await message.bot.send_message(
            user_id,
            f"📨 Ответ от администратора:\n\n{answer_text}"
        )
        
        await message.answer(f"✅ Ответ отправлен пользователю {user_id}")
        
    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя")

@router.callback_query(F.data.startswith("confirm_payment_"))
async def confirm_payment(callback: CallbackQuery):
    """Подтверждение оплаты и создание конфига"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    username = f"user_{user_id}"
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, user_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        # Находим последний необработанный платеж
        payment = await crud.get_last_pending_payment(session, user.id)
        if payment:
            await crud.update_payment_status(session, payment.id, "completed")
        
        # Продлеваем подписку
        await crud.extend_subscription(session, user.id, config.SUBSCRIPTION_DAYS)
        
        await callback.answer("✅ Оплата подтверждена!", show_alert=True)
        
        # Удаляем клавиатуру у сообщения админа
        await callback.message.edit_reply_markup(reply_markup=None)
        
        # СОЗДАЕМ КОНФИГ В АДМИНКЕ
        await callback.message.answer(f"🔄 Создаю конфиг для пользователя {user_id}...")
        
        config_number = 1
        config_name = f"user_{user_id}_{config_number}"

        vless_link = await admin_api.create_user_and_get_config(config_name)

        if vless_link:
            await crud.create_client_config(session, user.id, config_number, config_name, vless_link)
        
        if vless_link:
            # Отправляем пользователю конфиг
            await callback.bot.send_message(
                user_id,
                f"✅ Ваша оплата подтверждена!\n"
                f"Подписка активирована на {config.SUBSCRIPTION_DAYS} дней.\n\n"
                f"🔗 Ваш VLESS конфиг:\n`{vless_link}`\n\n"
                f"📱 Инструкция:\n"
                f"1. Скачайте Amnesia\n"
                f"2. Нажмите 'Импорт из буфера обмена'\n"
                f"3. Вставьте ссылку\n\n"
                f"Спасибо за покупку!",
                parse_mode="Markdown"
            )
            await callback.message.answer(f"✅ Конфиг создан и отправлен пользователю {user_id}")
        else:
            await callback.message.answer(f"⚠️ Оплата подтверждена, но не удалось создать конфиг для {user_id}. Создайте вручную командой /create_config {user_id}")
            
            await callback.bot.send_message(
                user_id,
                f"✅ Ваша оплата подтверждена!\n"
                f"Подписка активирована на {config.SUBSCRIPTION_DAYS} дней.\n\n"
                f"⚠️ Конфиг будет отправлен администратором в ближайшее время.\n\n"
                f"Спасибо за покупку!"
            )

@router.callback_query(F.data.startswith("reject_payment_"))
async def reject_payment(callback: CallbackQuery):
    """Отклонение оплаты"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, user_id)
        if user:
            # Находим последний платеж и обновляем статус
            payment = await crud.get_last_pending_payment(session, user.id)
            if payment:
                await crud.update_payment_status(session, payment.id, "rejected")
        
        await callback.answer("❌ Оплата отклонена", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        
        await callback.bot.send_message(
            user_id,
            "❌ Ваша оплата не подтверждена.\n"
            "Пожалуйста, свяжитесь с администратором для уточнения деталей."
        )


@router.callback_query(F.data.startswith("extend_"))
async def extend_payment_selected(callback: CallbackQuery, state: FSMContext):
    """Выбрано количество месяцев для продления"""
    months = int(callback.data.split("_")[1])
    
    # Рассчитываем сумму
    if months == 1:
        amount = config.SUBSCRIPTION_PRICE
    elif months == 3:
        amount = int(config.SUBSCRIPTION_PRICE * 2.7)  # 2700
    elif months == 6:
        amount = int(config.SUBSCRIPTION_PRICE * 5)    # 5000
    elif months == 12:
        amount = int(config.SUBSCRIPTION_PRICE * 9)    # 9000
    else:
        amount = config.SUBSCRIPTION_PRICE * months
    
    await callback.message.edit_reply_markup(reply_markup=None)
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if user:
            payment = await crud.create_payment(session, user.id, status="pending", amount=amount, months=months)
            await state.update_data(payment_id=payment.id, months=months, amount=amount)
    
    await callback.message.answer(
        f"✅ Спасибо! Вы выбрали продление на {months} месяц(ев).\n"
        f"Сумма к оплате: {amount}₽\n\n"
        f"Я отправил уведомление администратору.\n"
        f"Ожидайте подтверждения оплаты. Обычно это занимает до 30 минут."
    )
    
    for admin_id in config.ADMIN_IDS:
        await callback.bot.send_message(
            admin_id,
            f"💰 НОВЫЙ ПЛАТЕЖ (ПРОДЛЕНИЕ НА {months} МЕСЯЦЕВ)!\n\n"
            f"Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"ID: {callback.from_user.id}\n"
            f"Месяцев: {months}\n"
            f"Сумма: {amount}₽\n\n"
            f"Проверьте банк и подтвердите оплату.",
            reply_markup=get_admin_extend_keyboard(callback.from_user.id, months, amount)
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_extend_"))
async def confirm_extend_payment(callback: CallbackQuery):
    """Подтверждение оплаты продления"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав", show_alert=True)
        return
    
    parts = callback.data.split("_")
    user_id = int(parts[2])
    months = int(parts[3])
    amount = int(parts[4])
    
    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, user_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        # Находим последний платеж
        payment = await crud.get_last_pending_payment(session, user.id)
        if payment:
            await crud.update_payment_status(session, payment.id, "completed")
        
        # Продлеваем подписку на количество месяцев
        await crud.extend_subscription_months(session, user.id, months)

        username = f"user_{user_id}"
        await admin_api.create_user(username)
        vless_link = await admin_api.get_vless_link(username)
        
        await callback.answer(f"✅ Оплата на {months} месяц(ев) подтверждена!", show_alert=True)
        
        # Удаляем клавиатуру
        await callback.message.edit_reply_markup(reply_markup=None)
        
        # Уведомляем пользователя
        if vless_link:
            await cllback.bot.send_message(
                user_id,
                f"✅ Ваша оплата подтверждена!\n"
                f"Подписка активирована на {config.SUBSCRIPTION_DAYS} дней.\n\n"
                f"🔗 Ваш VLESS конфиг:\n`{vless_link}`\n\n"
                f"📱 Инструкция:\n"
                f"1. Скачайте Amnesia\n"
                f"2. Нажмите 'Импорт из буфера обмена'\n"
                f"3. Вставьте ссылку\n\n"
                f"Спасибо за покупку!",
                parse_mode="Markdown"
            )
        else:
            await callback.bot.send_message(
                user_id,
                f"✅ Ваша оплата подтверждена!\n"
                f"Подписка продлена на {months} месяц(ев).\n\n"
                "Спасибо за покупку!"
            )

#API

# bot/handlers/admin.py - добавить новые команды
from bot.api.client import admin_api

@router.message(Command("create_config"))
async def create_user_config(message: Message):
    """Создать конфиг для пользователя (только админ)"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /create_config [telegram_id]\n"
            "Пример: /create_config 123456789"
        )
        return
    
    try:
        telegram_id = int(parts[1])
        username = f"user_{telegram_id}"
        
        async with message.bot.get_db_session() as session:
            user = await crud.get_user_by_telegram_id(session, telegram_id)
            if not user:
                await message.answer(f"❌ Пользователь {telegram_id} не найден в БД бота")
                return
        
        # Получаем конфиг (создаем если нет)
        await message.answer(f"🔄 Получаю конфиг для пользователя {telegram_id}...")
        
        vless_link = await admin_api.get_or_create_user_config(username)

        safe_link = html.escape(vless_link)
        
        if vless_link:
            # Отправляем конфиг пользователю
            await message.bot.send_message(
                telegram_id,
                f"✅ Ваш конфиг готов!\n\n"
                f"🔗 VLESS ссылка:\n<code>{safe_link}</code>\n\n"
                f"📱 Для установки:\n"
                f"1. Скачайте Amnesia\n"
                f"2. Нажмите 'Импорт из буфера обмена'\n"
                f"3. Вставьте ссылку",
                parse_mode="HTML"
            )
            
            await message.answer(f"✅ Конфиг отправлен пользователю {telegram_id}")
        else:
            await message.answer(f"❌ Не удалось получить конфиг для {telegram_id}. Проверьте доступность админки.")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@router.message(Command("get_vless"))
async def get_vless_link(message: Message):
    """Получить VLESS ссылку пользователя (только админ)"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /get_vless [telegram_id]\n"
            "Пример: /get_vless 123456789"
        )
        return
    
    try:
        telegram_id = int(parts[1])
        username = f"user_{telegram_id}"
        
        vless_link = await admin_api.get_vless_link(username)
        
        if vless_link:
            await message.answer(
                f"🔗 VLESS ссылка для {telegram_id}:\n\n"
                f"`{vless_link}`",
                parse_mode="Markdown"
            )
        else:
            await message.answer(f"❌ Пользователь {telegram_id} не найден в админке или конфиг не создан")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID")


@router.message(Command("delete_config"))
async def delete_user_config(message: Message):
    """Удалить конфиг пользователя (только админ)"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /delete_config [telegram_id]\n"
            "Пример: /delete_config 123456789"
        )
        return
    
    try:
        telegram_id = int(parts[1])
        username = f"user_{telegram_id}"
        
        success = await admin_api.delete_user(username)
        
        if success:
            await message.answer(f"✅ Конфиг для пользователя {telegram_id} удален из админки")
            
            # Уведомляем пользователя
            await message.bot.send_message(
                telegram_id,
                f"⚠️ Ваш конфиг был удален администратором.\n"
                f"Для получения нового конфига обратитесь к администратору."
            )
        else:
            await message.answer(f"❌ Не удалось удалить конфиг для {telegram_id}")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID")


@router.message(Command("admin_status"))
async def admin_status(message: Message):
    """Проверить статус админки"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    is_healthy = await admin_api.health_check()
    
    if is_healthy:
        await message.answer("✅ Админка доступна")
    else:
        await message.answer("❌ Админка недоступна. Проверьте сервер.")


@router.message(Command("sync_user"))
async def sync_user_to_admin(message: Message):
    """Синхронизировать пользователя из БД бота в админку"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /sync_user [telegram_id]\n"
            "Пример: /sync_user 123456789"
        )
        return
    
    try:
        telegram_id = int(parts[1])
        username = f"user_{telegram_id}"
        
        async with message.bot.get_db_session() as session:
            user = await crud.get_user_by_telegram_id(session, telegram_id)
            if not user:
                await message.answer(f"❌ Пользователь {telegram_id} не найден в БД бота")
                return
            
            has_subscription = await crud.check_subscription_status(session, user.id)
        
        # Создаем пользователя в админке
        success = await admin_api.create_user(username)
        
        if success:
            await message.answer(
                f"✅ Пользователь {telegram_id} синхронизирован с админкой\n"
                f"Подписка активна: {'да' if has_subscription else 'нет'}"
            )
        else:
            await message.answer(f"❌ Не удалось синхронизировать пользователя {telegram_id}")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID")

#Тесты

@router.message(Command("check_reminders"))
async def check_reminders_now(message: Message):
    """Принудительная проверка напоминаний (только админ)"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    await message.answer("🔄 Проверяю напоминания...")
    
    # Импортируем функцию проверки
    from bot.reminder import check_and_send_reminders
    
    try:
        await check_and_send_reminders(message.bot)
        await message.answer("✅ Проверка напоминаний выполнена!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@router.message(Command("reset_reminder"))
async def reset_reminder_flag(message: Message):
    """Сбросить флаг напоминания для тестирования"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /reset_reminder [telegram_id]\n"
            "Пример: /reset_reminder 123456789"
        )
        return
    
    try:
        telegram_id = int(parts[1])
        
        async with message.bot.get_db_session() as session:
            user = await crud.get_user_by_telegram_id(session, telegram_id)
            if not user:
                await message.answer(f"❌ Пользователь {telegram_id} не найден")
                return
            
            subscription = await crud.get_user_subscription(session, user.id)
            if not subscription:
                await message.answer("❌ У пользователя нет подписки")
                return
            
            await crud.reset_reminder_flag(session, subscription.id)
            
            await message.answer(
                f"✅ Флаг напоминания сброшен для пользователя {telegram_id}\n"
                f"Следующее напоминание будет отправлено {subscription.next_payment.strftime('%d.%m.%Y')}"
            )
            
    except ValueError:
        await message.answer("❌ Неверный формат ID")