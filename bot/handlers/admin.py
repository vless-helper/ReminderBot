from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

from bot.config import config
from bot.db import crud

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