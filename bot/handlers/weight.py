"""Дневник веса: /weight, /weights — с целью, графиком и прогнозом."""

import io
import re
from datetime import date, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db import database

router = Router()


async def _forecast_line(user_id: int, weight: float) -> str:
    """Прогноз достижения целевого веса по тренду последних 14 записей."""
    profile = await database.get_profile(user_id)
    if not profile or not profile.get("target_weight"):
        return ""
    target = profile["target_weight"]
    diff = weight - target
    if abs(diff) < 0.3:
        return "\n🎯 Целевой вес практически достигнут! Поздравляю! 🎉"

    history = await database.get_weights(user_id, limit=14)
    if len(history) < 3:
        return f"\n🎯 Целевой вес: {target:g} кг (до цели {diff:+.1f} кг). " \
               "Взвешивайся регулярно — появится прогноз!"

    # линейный тренд по последним записям: кг/день
    d0 = date.fromisoformat(history[0][0])
    xs = [(date.fromisoformat(day) - d0).days for day, _ in history]
    ys = [w for _, w in history]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0

    if abs(slope) < 0.02:  # меньше ~0.6 кг/месяц — прогноз ненадёжен
        return f"\n🎯 До целевого веса {target:g} кг осталось {diff:+.1f} кг. " \
               "Вес сейчас стоит на месте."
    days_needed = diff / slope
    if days_needed <= 0 or days_needed > 365:
        return f"\n🎯 До целевого веса {target:g} кг осталось {diff:+.1f} кг."
    eta = date.today() + timedelta(days=round(days_needed))
    return (f"\n🎯 При текущем темпе ({slope * 7:+.1f} кг/нед) достигнешь "
            f"{target:g} кг примерно <b>{eta.strftime('%d.%m.%Y')}</b>")


@router.message(Command("weight"))
async def cmd_weight(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").replace(",", ".").strip()
    match = re.fullmatch(r"\d{2,3}(?:\.\d{1,2})?", raw)
    if not match:
        await message.answer(
            "⚖️ Укажи вес в килограммах, например:\n<code>/weight 82.5</code>\n\n"
            "Целевой вес настраивается в анкете: /norm → «🔄 Пересчитать»"
        )
        return

    weight = float(raw)
    if not 30 <= weight <= 300:
        await message.answer("🤔 Кажется, вес указан неверно. Проверь значение.")
        return

    created = await database.add_weight(message.from_user.id, weight)
    verb = "Записал" if created else "Обновил сегодняшнюю запись"
    history = await database.get_weights(message.from_user.id, limit=2)
    prev = history[0][1] if len(history) == 2 else None
    delta = ""
    if prev is not None and abs(weight - prev) >= 0.05:
        sign = "+" if weight > prev else ""
        emoji = "📈" if weight > prev else "📉"
        delta = f"\n{emoji} Изменение с прошлой записи: {sign}{weight - prev:.1f} кг"

    forecast = await _forecast_line(message.from_user.id, weight)
    await message.answer(f"✅ {verb}: <b>{weight:g} кг</b>.{delta}{forecast}")



@router.message(Command("weights"))
async def cmd_weights(message: Message) -> None:
    rows = await database.get_weights(message.from_user.id, limit=14)
    if not rows:
        await message.answer(
            "📭 Дневник веса пуст.\nЗапиши первый вес: <code>/weight 80</code>"
        )
        return

    # пробуем нарисовать график; если matplotlib нет — текстовый вариант
    target = await _get_target(message.from_user.id)
    try:
        photo = _build_chart(rows, target)
    except Exception:
        photo = None

    first, last = rows[0][1], rows[-1][1]
    caption_lines = [f"⚖️ <b>Дневник веса</b> ({rows[0][0]} — {rows[-1][0]}):"]
    for day, weight in rows:
        caption_lines.append(f"• {day}: <b>{weight:g}</b> кг")
    if len(rows) > 1 and abs(last - first) >= 0.05:
        sign = "+" if last > first else ""
        caption_lines.append(f"\n📊 За период: {sign}{last - first:.1f} кг")
    forecast = await _forecast_line(message.from_user.id, last)
    caption_lines.append(forecast)

    if photo is not None:
        await message.answer_photo(photo, caption="\n".join(caption_lines))
    else:
        await message.answer("\n".join(caption_lines))


def _build_chart(rows: list[tuple[str, float]], target: float | None):
    """График веса с линией цели -> BufferedInputFile."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from aiogram.types import BufferedInputFile

    plt.rcParams["font.family"] = "DejaVu Sans"
    days = [date.fromisoformat(day) for day, _ in rows]
    values = [w for _, w in rows]

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=110)
    ax.plot(days, values, marker="o", color="#2e86de", linewidth=2,
            markersize=5, label="Вес")

    if target:
        ax.axhline(target, color="#e67e22", linestyle="--", linewidth=1.5,
                   label=f"Цель: {target:g} кг")

    ax.set_title("Динамика веса", fontsize=13)
    ax.set_ylabel("кг")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate()
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    return BufferedInputFile(buffer.getvalue(), filename="weight.png")


async def _get_target(user_id: int) -> float | None:
    profile = await database.get_profile(user_id)
    return profile.get("target_weight") if profile else None

