"""Старт, справка и запасной обработчик текста."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.db import database
from bot.handlers.profile import cmd_norm

router = Router()

HELP_TEXT = (
    "🥗 <b>Calorie Bot — твой дневник питания</b>\n\n"
    "<b>Что я умею:</b>\n"
    "📸 Отправь <b>фото еды</b> — распознаю блюда и посчитаю КБЖУ.\n"
    "🎙 Или наговори <b>голосовым</b>, что съел.\n"
    "💬 Или напиши текстом: <code>/eat два яйца и тост</code>.\n"
    "🏷 Фото штрихкода с подписью «штрих» или <code>/barcode 46...</code> — "
    "продукт из открытой базы.\n\n"
    "<b>Команды:</b>\n"
    "/today — итоги за сегодня\n"
    "/week — статистика за неделю\n"
    "/water 250 — выпитая вода\n"
    "/weight 82.5 — записать вес на сегодня\n"
    "/weights — график и история веса\n"
    "/norm — мой профиль и норма калорий\n"
    "/top — частые продукты\n"
    "/streak — серия дней подряд\n"
    "/export — выгрузить дневник в CSV\n"
    "/remind on|off — напоминания о подсчёте\n"
    "/status — сводка по тебе\n"
    "/help — эта справка\n\n"
    "⚠️ Оценки КБЖУ приблизительные и зависят от качества фото."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await database.ensure_user(message.from_user.id, message.from_user.first_name)
    profile = await database.get_profile(message.from_user.id)
    if profile:
        await message.answer(
            f"С возвращением, <b>{message.from_user.first_name}</b>! 👋\n\n"
            + HELP_TEXT
        )
    else:
        # новый пользователь — предлагаем заполнить анкету и рассчитать норму
        await cmd_norm(message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message()
async def fallback(message: Message) -> None:
    """Ловим всё остальное (текст) и подсказываем, что делать."""
    await message.answer(
        "🤔 Я понимаю фото еды, голосовые, команды из /help.\n"
        "Опиши съеденное через /eat или отправь фото блюда — посчитаю калории!"
    )
