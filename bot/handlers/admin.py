from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.config import config
from bot.db import crud
from bot.keyboards.keyboards import get_admin_extend_keyboard

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


@router.callback_query(F.data.startswith("confirm_payment"))
async def confirm_payment(callback: CallbackQuery):
    """Подтверждение оплаты"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав для этой команды", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])

    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, user_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        payment = await crud.get_last_pending_payment(session, user.id)
        if payment:
            await crud.update_payment_status(session, payment.id, "completed")

        await crud.extend_subscription(session, user.id, config.SUBSCRIPTION_DAYS)
        
        await callback.answer("✅ Оплата подтверждена!", show_alert=True)

        await callback.message.edit_reply_markup(reply_markup=None)

        await callback.bot.send_message(
            user_id,
            f"✅ Ваша оплата подтверждена!\n"
            f"Подписка активирована на 30 дней."
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
        
        await callback.answer(f"✅ Оплата на {months} месяц(ев) подтверждена!", show_alert=True)
        
        # Удаляем клавиатуру
        await callback.message.edit_reply_markup(reply_markup=None)
        
        # Уведомляем пользователя
        await callback.bot.send_message(
            user_id,
            f"✅ Ваша оплата подтверждена!\n"
            f"Подписка продлена на {months} месяц(ев).\n\n"
            "Спасибо за покупку!"
        )


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