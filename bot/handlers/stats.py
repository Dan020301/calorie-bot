"""Статистика: /today и /week с нормой, водой, стриками и выводами."""

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import WATER_NORM_ML
from bot.db import database
from bot.handlers.extras import calc_streak
from bot.handlers.profile import macros

router = Router()

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def kcal_bar(kcal: float, norm: float) -> str:
    """Прогресс-бар из 10 сегментов относительно нормы."""
    filled = round(10 * min(kcal, norm * 1.5) / norm) if norm else 0
    return "█" * filled + "░" * max(0, 10 - filled)


def split_by_time(rows: list[tuple[int, float]]) -> dict[str, float]:
    """Распределение калорий по приёмам пищи по времени записи."""
    buckets = {"🌅 Завтрак": 0.0, "☀️ Обед": 0.0, "🌆 Ужин": 0.0, "🌙 Перекусы": 0.0}
    for hour, kcal in rows:
        if 6 <= hour < 11:
            buckets["🌅 Завтрак"] += kcal
        elif 11 <= hour < 16:
            buckets["☀️ Обед"] += kcal
        elif 16 <= hour < 22:
            buckets["🌆 Ужин"] += kcal
        else:
            buckets["🌙 Перекусы"] += kcal
    return {key: round(value) for key, value in buckets.items() if value > 0}


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    user_id = message.from_user.id
    totals = await database.get_day_totals(user_id)
    profile = await database.get_profile(user_id)

    if totals["meals"] == 0:
        await message.answer(
            "📭 Сегодня ещё ничего не записано.\n"
            "Отправь фото еды — я посчитаю КБЖУ!"
        )
        return

    lines = [f"📅 <b>Сегодня</b> ({totals['meals']} записей):"]

    if profile:
        norm = round(profile["norm_kcal"])
        kcal = totals["kcal"]
        percent = round(kcal * 100 / norm)
        left = norm - kcal
        lines.append(
            f"\n🔥 Калории: <b>{kcal}</b> / {norm} ккал\n"
            f"{kcal_bar(kcal, norm)} {percent}%"
        )
        if left > 0:
            lines.append(f"Осталось на сегодня: <b>{left}</b> ккал 🍽")
        else:
            lines.append(f"Превышение нормы на <b>{-left}</b> ккал — "
                         "ничего страшного, завтра получится лучше 🙂")
        p_rec, f_rec, c_rec = macros(norm)
    else:
        p_rec = f_rec = c_rec = None
        lines.append(f"\n🔥 Калории: <b>{totals['kcal']}</b> ккал")

    lines.append(
        f"🥩 Белки: <b>{totals['protein']}</b> г"
        + (f" / {p_rec}" if p_rec else "")
        + f"\n🧈 Жиры: <b>{totals['fat']}</b> г"
        + (f" / {f_rec}" if f_rec else "")
        + f"\n🍞 Углеводы: <b>{totals['carbs']}</b> г"
        + (f" / {c_rec}" if c_rec else "")
    )

    distribution = split_by_time(
        await database.get_day_meals_with_time(user_id))
    if len(distribution) > 1:
        lines.append("\n<b>Распределение за день:</b>")
        lines += [f"  {meal}: {kcal} ккал" for meal, kcal in distribution.items()]

    water_ml = await database.get_water(user_id)
    water_percent = min(100, round(water_ml * 100 / WATER_NORM_ML))
    lines.append(
        f"\n💧 Вода: {water_ml:g} мл ({water_percent}% от {WATER_NORM_ML})"
    )

    streak = calc_streak(await database.get_logged_dates(user_id),
                         date.today().isoformat())
    if streak > 1:
        lines.append(f"🔥 Серия: <b>{streak}</b> дн. подряд!")

    await message.answer("\n".join(lines))


def _bar(kcal: float, max_kcal: float, width: int = 12) -> str:
    filled = round(width * kcal / max_kcal) if max_kcal > 0 else 0
    return "█" * filled + "░" * (width - filled)


def week_insights(days: list[dict], norm: float | None) -> list[str]:
    """Мягкие наблюдения за неделю: перебор/недобор/белок/пропуски."""
    logged = [d for d in days if d["meals"] > 0]
    insights: list[str] = []
    if not logged:
        return insights
    avg = sum(d["kcal"] for d in logged) / len(logged)
    if norm:
        ratio = avg / norm
        if ratio >= 1.15:
            insights.append(
                f"📈 В среднем {avg:.0f} ккал/день — это на "
                f"{round((ratio - 1) * 100)}% выше нормы.")
        elif ratio <= 0.7:
            insights.append(
                f"📉 В среднем {avg:.0f} ккал/день — заметно меньше нормы. "
                "Не пропускай приёмы пищи!")
        else:
            insights.append(
                f"✅ Средняя калорийность {avg:.0f} ккал/день — близко к норме.")
        avg_protein = sum(d["protein"] for d in logged) / len(logged)
        protein_norm = norm * 0.30 / 4
        if avg_protein < protein_norm * 0.7:
            insights.append("🥩 Белка маловато — попробуй добавить творог, "
                            "яйца, курицу или рыбу.")
    skipped = len(days) - len(logged)
    if skipped >= 2 and days[-1]["meals"] == 0:
        insights.append(f"⏳ Дней без записей за неделю: {skipped}. "
                        "Фото еды занимает пару секунд 😉")
    return insights


@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    user_id = message.from_user.id
    days = await database.get_week_totals(user_id)
    profile = await database.get_profile(user_id)
    norm = round(profile["norm_kcal"]) if profile else None
    max_kcal = max((d["kcal"] for d in days), default=0)
    total = {key: sum(d[key] for d in days)
             for key in ("kcal", "protein", "fat", "carbs")}
    logged_days = sum(1 for d in days if d["meals"] > 0)

    lines = ["📊 <b>Калории за последние 7 дней:</b>\n"]
    for d in days:
        weekday = WEEKDAYS[d["day"].isoweekday() - 1]
        mark = ""
        if norm and d["kcal"]:
            mark = " ✔" if d["kcal"] <= norm * 1.1 else " ⚠"
        lines.append(
            f"{weekday} {d['day'][5:]}  {_bar(d['kcal'], max_kcal)}  "
            f"{d['kcal']:>6.1f}{mark}")

    avg_part = f" (в среднем {total['kcal'] / 7:.0f}/день)" if logged_days else ""
    lines.append(
        "\n<b>Итого за неделю:</b>\n"
        f"🔥 {total['kcal']:.1f} ккал{avg_part}\n"
        f"🥩 Б: {total['protein']:.1f} г · 🧈 Ж: {total['fat']:.1f} г · "
        f"🍞 У: {total['carbs']:.1f} г · записи в {logged_days} дн. из 7"
    )

    insights = week_insights(days, norm)
    if insights:
        lines.append("\n<b>Наблюдения:</b>")
        lines += [f"• {insight}" for insight in insights]

    await message.answer("\n".join(lines))
