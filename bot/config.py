"""Загрузка настроек из .env"""

import os

from dotenv import load_dotenv

load_dotenv()

# Токен бота от @BotFather
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# API-ключ Google Gemini (https://aistudio.google.com/apikey)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Путь к базе SQLite
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "calories.db")

# Часовой пояс (для планировщика напоминаний)
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")

# Время напоминаний "HH:MM"
REMINDER_TIMES: list[str] = [
    t.strip()
    for t in os.getenv("REMINDER_TIMES", "09:00,13:00,18:00,21:00").split(",")
    if t.strip()
]

# Модель Gemini для распознавания еды на фото.
# "gemini-3.5-flash-lite" — стабильная и быстрая, щедрый бесплатный лимит.
VISION_MODEL: str = os.getenv("VISION_MODEL", "gemini-3.5-flash-lite")

# Норма воды в день (мл)
WATER_NORM_ML: int = int(os.getenv("WATER_NORM_ML", "2000"))

# Время ежедневного отчёта "итоги дня" (HH:MM)
DAILY_SUMMARY_TIME: str = os.getenv("DAILY_SUMMARY_TIME", "21:30")

# Время еженедельной сводки (воскресенье, HH:MM)
WEEKLY_SUMMARY_TIME: str = os.getenv("WEEKLY_SUMMARY_TIME", "20:00")


