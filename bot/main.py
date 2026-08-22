"""Точка входа: инициализация БД, роутеров, планировщика и polling."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import GEMINI_API_KEY, TELEGRAM_BOT_TOKEN
from bot.db.database import init_db
from bot.handlers import extras, photo, profile, reminders, start, stats, water, weight
from bot.scheduler import start_scheduler

# Список команд для меню «☰» слева от строки ввода
BOT_COMMANDS = [
    BotCommand(command="start", description="🚀 Запуск и справка"),
    BotCommand(command="today", description="📊 Итоги за сегодня"),
    BotCommand(command="week", description="📈 Статистика за неделю"),
    BotCommand(command="eat", description="🍽 Описать еду текстом"),
    BotCommand(command="water", description="💧 Вода: /water 250"),
    BotCommand(command="weight", description="⚖️ Записать вес: /weight 82.5"),
    BotCommand(command="weights", description="📉 График и история веса"),
    BotCommand(command="norm", description="🎯 Профиль и норма калорий"),
    BotCommand(command="barcode", description="🏷 Продукт по штрихкоду"),
    BotCommand(command="top", description="🏅 Частые продукты"),
    BotCommand(command="streak", description="🔥 Серия дней подряд"),
    BotCommand(command="export", description="📄 Экспорт дневника в CSV"),
    BotCommand(command="remind", description="🔔 Напоминания вкл/выкл"),
    BotCommand(command="status", description="📋 Мой статус"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
        sys.exit(
            "Ошибка: заполни .env (TELEGRAM_BOT_TOKEN и GEMINI_API_KEY).\n"
            "Шаблон — в .env.example"
        )

    await init_db()

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands(BOT_COMMANDS)

    dp = Dispatcher()

    # порядок важен: start.py регистрируется последним — там запасной обработчик
    dp.include_routers(photo.router, water.router, extras.router, stats.router,
                       weight.router, reminders.router, profile.router,
                       start.router)

    start_scheduler(bot)

    logging.info("Бот запущен")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
