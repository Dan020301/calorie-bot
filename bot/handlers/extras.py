"""Дополнительные команды: /top, /export, /streak."""

import csv
import io
from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from bot.db import database
from bot.handlers.profile import macros

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    rows = await database.get_top_products(message.from_user.id)
    if not rows:
        await message.answer(
            "📭 В дневнике пока пусто.\nОтправь фото еды — и здесь появится статистика!"
        )
        return
    lines = ["🏅 <b>Твои частые продукты:</b>\n"]
    for i, (name, count, avg_kcal) in enumerate(rows, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(f"{medal} {name} — <b>{count}</b> раз (в ср. {avg_kcal:.0f} ккал)")
    await message.answer("\n".join(lines))


def calc_streak(dates: set[str], today_iso: str) -> int:
    """Серия подряд идущих дней с записями (заканчивается сегодня или вчера)."""
    from datetime import timedelta

    day = date.fromisoformat(today_iso)
    if today_iso not in dates:
        day -= timedelta(days=1)  # сегодня ещё не отмечен — не ломаем серию
    streak = 0
    while day.isoformat() in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


@router.message(Command("streak"))
async def cmd_streak(message: Message) -> None:
    dates = await database.get_logged_dates(message.from_user.id)
    streak = calc_streak(dates, date.today().isoformat())
    if streak == 0:
        await message.answer(
            "🔥 Серия пока пустая.\nЗаписывай еду каждый день — и она вырастет!"
        )
        return
    fire = "🔥" * min(streak, 10)
    await message.answer(
        f"{fire}\n<b>Серия: {streak} {'день' if streak % 10 == 1 else 'дней'} подряд!</b>\n"
        "Не прерывай — каждый день с записями делает привычку сильнее 💪"
    )


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    rows = await database.get_all_meals_for_export(message.from_user.id)
    if not rows:
        await message.answer("📭 Дневник пуст — экспортировать пока нечего.")
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["дата", "продукт", "ккал", "белки", "жиры",
                     "углеводы", "вес_г"])
    for name, kcal, protein, fat, carbs, weight_g, created_at in rows:
        writer.writerow([created_at, name, kcal, protein, fat,
                         carbs, weight_g or ""])
    data = buffer.getvalue().encode("utf-8-sig")  # BOM для Excel
    filename = f"food_diary_{message.from_user.id}.csv"
    await message.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=f"📄 Экспорт дневника: {len(rows)} записей.",
    )
