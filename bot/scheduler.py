"""Планировщик: умные напоминания, отчёт за день, еженедельная сводка."""

import logging
from datetime import date, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import DAILY_SUMMARY_TIME, REMINDER_TIMES, TIMEZONE, WEEKLY_SUMMARY_TIME
from bot.db import database

logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "⏰ Напоминаю зафиксировать приём пищи!\n"
    "Отправь фото еды — я посчитаю калории и БЖУ 📸"
)


def _progress_emoji(kcal: float, norm: float) -> str:
    ratio = kcal / norm if norm else 0
    if ratio >= 1.15:
        return "📈"
    if ratio >= 0.85:
        return "✅"
    return "🕐"


async def send_reminders(bot: Bot) -> None:
    """Умные напоминания: текст зависит от прогресса за день."""
    user_ids = await database.get_users_with_reminders()
    logger.info("Рассылка напоминаний: %d пользователей", len(user_ids))
    for user_id in user_ids:
        try:
            text = REMINDER_TEXT
            profile = await database.get_profile(user_id)
            if profile:
                totals = await database.get_day_totals(user_id)
                norm = round(profile["norm_kcal"])
                left = norm - totals["kcal"]
                emoji = _progress_emoji(totals["kcal"], norm)
                if left > 0:
                    text = (
                        f"{emoji} Сегодня: {totals['kcal']:.0f} / {norm} ккал, "
                        f"осталось <b>{left:.0f}</b>.\n"
                        "Если что-то ел(а) — отправь фото, я посчитаю 📸"
                    )
                elif left > -200:
                    text = (
                        f"{emoji} Норма почти выбрана: {totals['kcal']:.0f} / {norm} ккал.\n"
                        "Если ещё что-то съел(а) — зафиксируй фото 📸"
                    )
                else:
                    text = (
                        f"{emoji} Норма превышена на {abs(left):.0f} ккал — "
                        "но это не повод бросить подсчёт 😉\n"
                        "Зафиксируй, что ел(а), чтобы день был учтён честно."
                    )
            await bot.send_message(user_id, text)
        except Exception:
            logger.warning("Не удалось доставить напоминание user=%s", user_id)


async def send_daily_summary(bot: Bot) -> None:
    """Вечерний отчёт: итоги дня, вода, серия."""
    user_ids = await database.get_users_with_reminders()
    logger.info("Ежедневный отчёт: %d пользователей", len(user_ids))
    for user_id in user_ids:
        try:
            totals = await database.get_day_totals(user_id)
            water = await database.get_water(user_id)
            dates = await database.get_logged_dates(user_id)
            streak = sum(1 for i in range(len(dates) + 1)
                         if (date.today() - timedelta(days=i)).isoformat() in dates)
            lines = ["🌆 <b>Итоги дня:</b>"]
            if totals["meals"]:
                lines.append(
                    f"🔥 {totals['kcal']} ккал · "
                    f"🥩 {totals['protein']} г · 🧈 {totals['fat']} г · "
                    f"🍞 {totals['carbs']} г"
                )
            else:
                lines.append("Записей за сегодня нет. Если ел(а) — можно "
                             "отправить фото прямо сейчас 📸")
            lines.append(f"💧 Вода: {water:g} мл")
            if streak > 1:
                lines.append(f"🔥 Серия: {streak} дн. подряд!")
            await bot.send_message(user_id, "\n".join(lines))
        except Exception:
            logger.warning("Не удалось доставить отчёт user=%s", user_id)


async def send_weekly_summary(bot: Bot) -> None:
    """Воскресная сводка: средние, вес, мягкие выводы."""
    from bot.handlers.stats import week_insights

    user_ids = await database.get_users_with_reminders()
    logger.info("Еженедельная сводка: %d пользователей", len(user_ids))
    for user_id in user_ids:
        try:
            days = await database.get_week_totals(user_id)
            profile = await database.get_profile(user_id)
            norm = round(profile["norm_kcal"]) if profile else None
            logged = [d for d in days if d["meals"] > 0]
            if not logged:
                continue
            avg = sum(d["kcal"] for d in days) / len(days)
            lines = [f"📅 <b>Твоя неделя</b> (записей в {len(logged)} дн. из 7):",
                     f"🔥 В среднем {avg:.0f} ккал/день"]
            if norm:
                lines.append(f"🎯 Норма: {norm} ккал/день")
            weights = await database.get_weights(user_id, limit=2)
            if len(weights) == 2 and abs(weights[1][1] - weights[0][1]) >= 0.05:
                delta = weights[1][1] - weights[0][1]
                sign = "+" if delta > 0 else ""
                lines.append(f"⚖️ Вес за неделю: {sign}{delta:.1f} кг")
            insights = week_insights(days, norm)
            if insights:
                lines.append("")
                lines += [f"• {insight}" for insight in insights]
            lines.append("\nХорошей следующей недели! 💪")
            await bot.send_message(user_id, "\n".join(lines))
        except Exception:
            logger.warning("Не удалось доставить сводку user=%s", user_id)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    for time_str in REMINDER_TIMES:
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            send_reminders,
            trigger="cron",
            hour=hour,
            minute=minute,
            args=[bot],
            id=f"reminder_{time_str}",
        )
        logger.info("Напоминание запланировано на %s", time_str)

    hour, minute = map(int, DAILY_SUMMARY_TIME.split(":"))
    scheduler.add_job(
        send_daily_summary, trigger="cron",
        hour=hour, minute=minute, args=[bot], id="daily_summary",
    )
    logger.info("Ежедневный отчёт запланирован на %s", DAILY_SUMMARY_TIME)

    hour, minute = map(int, WEEKLY_SUMMARY_TIME.split(":"))
    scheduler.add_job(
        send_weekly_summary, trigger="cron",
        day_of_week="sun", hour=hour, minute=minute, args=[bot],
        id="weekly_summary",
    )
    logger.info("Еженедельная сводка запланирована на вс %s", WEEKLY_SUMMARY_TIME)

    scheduler.start()
    return scheduler
