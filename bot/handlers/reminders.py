"""Управление напоминаниями: /remind on|off, /status."""

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import REMINDER_TIMES
from bot.db import database

router = Router()


@router.message(Command("remind"))
async def cmd_remind(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)[1:] or [""]
    arg = args[0].strip().lower()
    if arg in ("on", "вкл", "1", "да"):
        await database.set_reminders(message.from_user.id, True)
        times = ", ".join(REMINDER_TIMES)
        await message.answer(f"🔔 Напоминания включены. Время: {times}")
    elif arg in ("off", "выкл", "0", "нет"):
        await database.set_reminders(message.from_user.id, False)
        await message.answer("🔕 Напоминания выключены.")
    else:
        state = await database.are_reminders_on(message.from_user.id)
        current = "включены 🔔" if state else "выключены 🔕"
        await message.answer(
            f"Напоминания сейчас {current}.\n"
            "Переключить: <code>/remind on</code> или <code>/remind off</code>"
        )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user_id = message.from_user.id
    day = await database.get_day_totals(user_id)
    reminders_on = await database.are_reminders_on(user_id)
    weights = await database.get_weights(user_id, limit=1)
    weight_str = f"{weights[0][1]:g} кг ({weights[0][0]})" if weights else "—"
    water_ml = await database.get_water(user_id)

    profile = await database.get_profile(user_id)
    norm_line = ""
    if profile:
        from bot.handlers.extras import calc_streak

        norm_line = f"\n🎯 Норма: {round(profile['norm_kcal'])} ккал/день"
        streak = calc_streak(await database.get_logged_dates(user_id),
                             date.today().isoformat())
        if streak > 1:
            norm_line += f"\n🔥 Серия: {streak} дн. подряд"

    await message.answer(
        "📋 <b>Твой статус:</b>\n\n"
        f"🔥 Сегодня: {day['kcal']} ккал ({day['meals']} записей){norm_line}\n"
        f"💧 Вода: {water_ml:g} мл\n"
        f"⚖️ Последний вес: {weight_str}\n"
        f"🔔 Напоминания: {'включены' if reminders_on else 'выключены'}"
    )
