"""Учёт воды: /water и кнопки быстрого добавления."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import WATER_NORM_ML
from bot.db import database

router = Router()


def _water_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ 250 мл", callback_data="water:add:250"),
        InlineKeyboardButton(text="➕ 500 мл", callback_data="water:add:500"),
        InlineKeyboardButton(text="🥤 Стакан", callback_data="water:add:200"),
    ]])


async def send_water_status(message: Message) -> None:
    total = await database.get_water(message.from_user.id)
    percent = min(100, round(total * 100 / WATER_NORM_ML))
    filled = round(percent / 10)
    bar = "💧" * filled + "▫️" * (10 - filled)
    left = max(0, WATER_NORM_ML - int(total))
    done = " — норма выполнена! 🎉" if left == 0 and total > 0 else ""
    await message.answer(
        f"💧 <b>Вода за сегодня:</b> {total:g} / {WATER_NORM_ML} мл\n"
        f"{bar} {percent}%{done}\n"
        + (f"Осталось: {left} мл" if left else ""),
        reply_markup=_water_kb(),
    )


@router.message(Command("water"))
async def cmd_water(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").replace(",", ".").strip()
    if raw:
        try:
            ml = float(raw)
        except ValueError:
            await message.answer(
                "💧 Укажи объём в миллилитрах: <code>/water 300</code>\n"
                "Или просто /water — покажу прогресс за день."
            )
            return
        if not 0 < ml <= 5000:
            await message.answer("🤔 Объём должен быть от 1 до 5000 мл.")
            return
        total = await database.add_water(message.from_user.id, ml)
        await message.answer(f"✅ Записал {ml:g} мл. За день: <b>{total:g} мл</b>")
    await send_water_status(message)


@router.callback_query(F.data.startswith("water:add:"))
async def cb_water_add(call: CallbackQuery) -> None:
    ml = float(call.data.split(":")[2])
    total = await database.add_water(call.from_user.id, ml)
    left = max(0, WATER_NORM_ML - int(total))
    emoji = "🎉 Норма выполнена!" if left == 0 else f"Осталось: {left} мл"
    await call.message.edit_text(
        f"💧 +{ml:g} мл. За день: <b>{total:g} / {WATER_NORM_ML} мл</b>\n{emoji}",
        reply_markup=_water_kb(),
    )
    await call.answer()
