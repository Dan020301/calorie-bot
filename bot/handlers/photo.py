"""Приём фото еды -> Gemini -> подтверждение/корректировка -> дневник."""

import io
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.db import database
from bot.services import off
from bot.services.vision import (
    analyze_photo,
    analyze_voice,
    extract_barcode,
    parse_text_meal,
    refine_items,
)

logger = logging.getLogger(__name__)
router = Router()

# Неподтверждённые разборы фото: user_id -> {"items": [...], "awaiting_index": int|None}
PENDING: dict[int, dict[str, Any]] = {}

NUTRIENTS = ("kcal", "protein", "fat", "carbs")

EDIT_PROMPT = (
    "✍️ <b>Пункт «{name}»</b>\n\n"
    "Удобнее всего — просто напиши своими словами, что не так 👇\n"
    "<i>Например: «порция была больше, грамм 400» или "
    "«это индейка, а не курица»</i>\n\n"
    "Если знаешь точные значения, пришли их одной строкой через <code>|</code>:\n"
    "<code>Название | вес_г | ккал | белки | жиры | углеводы</code>\n"
    "Например: <code>Курица гриль | 250 | 410 | 62 | 18 | 0</code>"
)

REMARK_PROMPT = (
    "💬 Напиши своими словами, что поправить в отчёте.\n\n"
    "<i>Например: «рису было меньше», «забыл кофе с молоком», "
    "«убери десерт — это не мой»</i>\n\n"
    "Я передам замечание ИИ и пересчитаю всё автоматически 🤖"
)


async def refine_and_reply(message: Message, state: dict) -> None:
    """Словесное замечание -> Gemini -> обновлённый отчёт с кнопками."""
    status = await message.answer("🤖 Уточняю оценку по твоему замечанию...")
    try:
        refined = await refine_items(state["items"], message.text)
    except Exception:
        logger.exception("Ошибка уточнения оценки")
        await status.edit_text(
            "⚠️ Не получилось пересчитать. Попробуй ещё раз или введи значения вручную."
        )
        return

    state["items"] = refined
    state["awaiting_index"] = None
    state["awaiting_remark"] = False
    await status.edit_text(
        "✏️ Пересчитал:\n\n" + format_report({"items": refined}),
        reply_markup=confirm_keyboard(),
    )



def calc_total(items: list[dict]) -> dict[str, float]:
    """Итог считается из пунктов — так корректно после ручных правок."""
    total = {key: 0.0 for key in NUTRIENTS}
    for item in items:
        for key in NUTRIENTS:
            total[key] += float(item.get(key) or 0)
    return {key: round(value, 1) for key, value in total.items()}


def _fmt_num(value: Any, digits: int = 1) -> str:
    return f"{round(float(value or 0), digits):g}"


def format_report(data: dict) -> str:
    items = data.get("items", [])
    if not items:
        reason = data.get("error") or "не удалось найти еду на фото."
        return f"🤷 {reason}\n\nПопробуй сфотографировать еду ближе и при хорошем свете."

    lines = ["🍽 <b>Распознано:</b>"]
    for i, item in enumerate(items, 1):
        portion = item.get("portion_g")
        portion_part = f" (~{_fmt_num(portion)} г)" if portion else ""
        lines.append(
            f"{i}. <b>{item.get('name', 'Неизвестно')}</b>{portion_part} — "
            f"{_fmt_num(item.get('kcal'), 0)} ккал\n"
            f"   Б: {_fmt_num(item.get('protein'))} · "
            f"Ж: {_fmt_num(item.get('fat'))} · "
            f"У: {_fmt_num(item.get('carbs'))}"
        )

    total = calc_total(items)
    lines.append(
        "\n<b>Итого:</b> 🔥 " + _fmt_num(total["kcal"], 0) + " ккал\n"
        f"🥩 Б: {_fmt_num(total['protein'])} · "
        f"🧈 Ж: {_fmt_num(total['fat'])} · "
        f"🍞 У: {_fmt_num(total['carbs'])}"
    )
    return "\n".join(lines)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Добавить", callback_data="meal:add_all"),
                InlineKeyboardButton(text="✍️ Исправить", callback_data="meal:edit_menu"),
            ],
            [InlineKeyboardButton(text="💬 Замечание", callback_data="meal:remark")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="meal:cancel")],
        ]
    )


def edit_menu_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{i}. {item.get('name', '?')}",
                callback_data=f"meal:edit:{i - 1}",
            )
        ]
        for i, item in enumerate(items, 1)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="meal:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_correction(text: str) -> tuple[dict[str, Any], str]:
    """Разбирает строку правки. Возвращает (пункт, ошибка; ошибка '' при успехе)."""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) == 6:
        name, weight_s, kcal_s, p_s, f_s, c_s = parts
    elif len(parts) == 5:
        name, kcal_s, p_s, f_s, c_s = parts
        weight_s = ""
    else:
        return {}, "Нужно 5 или 6 значений через «|»."

    values: list[Any] = []
    for raw in (weight_s, kcal_s, p_s, f_s, c_s):
        if not raw and raw is weight_s:
            values.append(None)  # вес не указан
            continue
        try:
            num = float(raw.replace(",", "."))
        except ValueError:
            return {}, f"«{raw}» — не число."
        if num < 0:
            return {}, "Значения не могут быть отрицательными."
        values.append(num)

    weight, kcal, protein, fat, carbs = values
    item: dict[str, Any] = {
        "name": name or "Без названия",
        "kcal": kcal,
        "protein": round(protein, 1),
        "fat": round(fat, 1),
        "carbs": round(carbs, 1),
    }
    if weight is not None:
        item["portion_g"] = weight
    return item, ""


# ---------- Приём фото ----------


async def _download_image(message: Message) -> bytes:
    buffer = io.BytesIO()
    await message.bot.download(message.photo[-1], destination=buffer)
    return buffer.getvalue()


BARCODE_KEYWORDS = ("штрих", "штрихкод", "код", "barcode")


async def offer_confirmation(message: Message, status: Message, data: dict,
                             note: str | None = None) -> None:
    """Показывает отчёт с кнопками подтверждения и сохраняет разбор."""
    user_id = message.from_user.id
    PENDING[user_id] = {
        "items": data["items"],
        "awaiting_index": None,
        "awaiting_remark": False,
    }
    caption_line = f"\n💬 Учёл твоё пояснение: <i>{note}</i>\n" if note else ""
    await status.edit_text(
        format_report(data) + caption_line + "\n<i>Добавляем в дневник?</i>",
        reply_markup=confirm_keyboard(),
    )


def _barcode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Добавить 100 г", callback_data="meal:add_barcode")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="meal:cancel")],
    ])


async def _show_product(message: Message, status: Message,
                        product: dict, barcode: str) -> None:
    brand = f" ({product['brand']})" if product["brand"] else ""
    PENDING[message.from_user.id] = {
        "barcode_product": product,
        "awaiting_index": None,
        "awaiting_remark": False,
    }
    await status.edit_text(
        f"🏷 <b>{product['name']}</b>{brand}\n"
        f"Штрихкод: <code>{barcode}</code>\n"
        f"На 100 г: 🔥 {product['kcal']} ккал · "
        f"Б {product['protein']} · Ж {product['fat']} · У {product['carbs']}\n\n"
        "<i>Добавить порцию 100 г в дневник?</i>",
        reply_markup=_barcode_keyboard(),
    )


async def _lookup_and_show(message: Message, status: Message, barcode: str) -> None:
    product = await off.get_product(barcode)
    if not product:
        await status.edit_text(
            f"🔎 Штрихкод: <code>{barcode}</code>\n"
            "😔 В открытой базе продуктов его не нашлось. "
            "Сфотографируй этикетку с названием — оценю КБЖУ визуально."
        )
        return
    await _show_product(message, status, product, barcode)


@router.message(Command("barcode"))
async def cmd_barcode(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    if not code.isdigit() or len(code) < 8:
        await message.answer(
            "🏷 Пришли цифры штрихкода: <code>/barcode 4600682050079</code>\n"
            "Или сфотографируй штрихкод с подписью «штрих»."
        )
        return
    status = await message.answer("🔎 Ищу продукт в базе...")
    await _lookup_and_show(message, status, code)


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    user_id = message.from_user.id
    note = (message.caption or "").strip()
    status = await message.answer("🔍 Анализирую фото...")

    try:
        image_bytes = await _download_image(message)
    except Exception:
        logger.exception("Ошибка скачивания фото")
        await status.edit_text("⚠️ Не удалось получить фото. Попробуй ещё раз.")
        return

    # фото штрихкода: в подписи есть ключевое слово
    if any(word in note.lower() for word in BARCODE_KEYWORDS):
        await _handle_barcode_photo(message, status, image_bytes)
        return

    try:
        data = await analyze_photo(image_bytes, note=note or None)
    except Exception:
        logger.exception("Ошибка распознавания фото")
        await status.edit_text(
            "⚠️ Не удалось распознать фото. Попробуй ещё раз чуть позже."
        )
        return

    if data.get("error") or not data.get("items"):
        await status.edit_text(format_report(data))
        return

    logger.info(
        "user=%s: %s, подпись=%r (ожидает подтверждения)",
        user_id,
        [i.get("name") for i in data["items"]],
        note or None,
    )
    await offer_confirmation(message, status, data, note=note or None)


async def _handle_barcode_photo(message: Message, status: Message,
                                image_bytes: bytes) -> None:
    """Фото штрихкода -> Gemini читает цифры -> Open Food Facts."""
    try:
        barcode = await extract_barcode(image_bytes)
    except Exception:
        logger.exception("Ошибка чтения штрихкода")
        await status.edit_text("⚠️ Не получилось прочитать штрихкод. Попробуй ещё раз.")
        return
    if not barcode:
        await status.edit_text(
            "🤷 Штрихкод на фото не нашёл.\n"
            "Сфотографируй его крупно и при хорошем свете, "
            "или пришли цифры текстом: <code>/barcode 4600682050079</code>"
        )
        return
    await _lookup_and_show(message, status, barcode)


# ---------- Голос и текст ----------


@router.message(F.voice)
async def handle_voice(message: Message) -> None:
    status = await message.answer("🎙 Слушаю голосовое...")
    try:
        buffer = io.BytesIO()
        await message.bot.download(message.voice, destination=buffer)
        data = await analyze_voice(buffer.getvalue())
    except Exception:
        logger.exception("Ошибка распознавания голосового")
        await status.edit_text(
            "⚠️ Не получилось разобрать голосовое.\n"
            "Попробуй ещё раз или напиши текстом: <code>/eat два яйца и тост</code>"
        )
        return

    transcript = (data.get("transcript") or "").strip()
    note = f"«{transcript}»" if transcript else None
    if data.get("error") or not data.get("items"):
        reason = data.get("error") or "не удалось определить еду."
        await status.edit_text(f"🤷 {reason}")
        return
    await offer_confirmation(message, status, data, note=note)


@router.message(Command("eat"))
async def cmd_eat(message: Message, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "🍽 Опиши, что съел, прямо после команды:\n"
            "<code>/eat два яйца и тост с сыром</code>"
        )
        return
    status = await message.answer("🤖 Оцениваю КБЖУ...")
    try:
        items = await parse_text_meal(text)
    except Exception:
        logger.exception("Ошибка разбора описания еды")
        await status.edit_text(
            "⚠️ Не получилось оценить. Попробуй перечислить блюда конкретнее."
        )
        return
    await offer_confirmation(message, status, {"items": items}, note=f"«{text}»")


def _is_editing_input(message: Message) -> bool:
    state = PENDING.get(message.from_user.id or 0)
    if not state or not message.text:
        return False
    return (
        state.get("awaiting_index") is not None
        or bool(state.get("awaiting_remark"))
    )


@router.message(_is_editing_input)
async def handle_edit_input(message: Message) -> None:
    user_id = message.from_user.id
    state = PENDING[user_id]
    index = state.get("awaiting_index")

    if message.text.startswith("/"):
        # команда во время правки — снимаем режим ввода значения
        state["awaiting_index"] = None
        state["awaiting_remark"] = False
        await message.answer("✍️ Правка отменена. Отчёт выше ждёт твоего решения.")
        return

    # Замечание ко всему отчёту (кнопка «💬 Замечание»)
    if state.get("awaiting_remark"):
        await refine_and_reply(message, state)
        return

    # Правка конкретного пункта
    if "|" in message.text:
        item, error = parse_correction(message.text)
        if error:
            await message.answer(f"⚠️ {error}\n\n{EDIT_PROMPT}")
            return
        state["items"][index] = item
        state["awaiting_index"] = None
        await message.answer(
            "✏️ Обновлено:\n\n" + format_report({"items": state["items"]}),
            reply_markup=confirm_keyboard(),
        )
        return

    # Словесное замечание по пункту — пусть ИИ пересчитает
    await refine_and_reply(message, state)


# ---------- Кнопки подтверждения ----------


def _get_state(call: CallbackQuery) -> dict[str, Any] | None:
    return PENDING.get(call.from_user.id)


async def _no_state(call: CallbackQuery) -> dict[str, Any] | None:
    state = _get_state(call)
    if not state:
        await call.answer("Разбор не найден. Отправь фото заново.", show_alert=True)
    return state


async def cb_add_all(call: CallbackQuery) -> None:
    state = _get_state(call)
    if not state or not state["items"]:
        await call.answer("Нет данных для сохранения.", show_alert=True)
        return

    user_id = call.from_user.id
    await database.ensure_user(user_id)
    for item in state["items"]:
        await database.add_meal(
            user_id=user_id,
            name=item.get("name", "Неизвестно"),
            kcal=float(item.get("kcal") or 0),
            protein=float(item.get("protein") or 0),
            fat=float(item.get("fat") or 0),
            carbs=float(item.get("carbs") or 0),
            weight_g=float(item["portion_g"]) if item.get("portion_g") else None,
        )
    total = calc_total(state["items"])
    PENDING.pop(user_id, None)
    logger.info(
        "user=%s добавил %d пунктов (%.0f ккал)",
        user_id, len(state["items"]), total["kcal"],
    )
    await call.message.edit_reply_markup(reply_markup=None)

    # кнопки «Съесть снова» из недавних записей
    recent = await database.get_recent_distinct_meals(user_id, limit=4)
    kb = repeat_keyboard(recent)
    text = (
        f"✅ Добавил {len(state['items'])} записей в дневник.\n"
        f"Итог: 🔥 {_fmt_num(total['kcal'], 0)} ккал · "
        f"Б {_fmt_num(total['protein'])} · Ж {_fmt_num(total['fat'])} · "
        f"У {_fmt_num(total['carbs'])}\n\n/today — итоги за день."
    )
    if kb:
        text += "\n\n<i>Было то же самое? Добавь одним нажатием:</i>"
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


def repeat_keyboard(recent: list[dict]) -> InlineKeyboardMarkup | None:
    """Кнопки быстрого повтора недавних блюд."""
    if not recent:
        return None
    rows = [[
        InlineKeyboardButton(
            text=f"🔁 {meal['name']} ({_fmt_num(meal['kcal'], 0)} ккал)",
            callback_data=f"meal:repeat:{meal['id']}",
        )
    ] for meal in recent[:3]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _add_meal_and_confirm(call: CallbackQuery, name: str, kcal: float,
                                protein: float, fat: float, carbs: float,
                                weight_g: float | None) -> None:
    user_id = call.from_user.id
    await database.add_meal(user_id, name, kcal, protein, fat, carbs, weight_g)
    weight_part = f" (~{_fmt_num(weight_g, 0)} г)" if weight_g else ""
    await call.message.answer(
        f"✅ <b>{name}</b>{weight_part} — {_fmt_num(kcal, 0)} ккал добавлено в дневник.\n"
        "/today — итоги за день."
    )


async def cb_repeat(call: CallbackQuery) -> None:
    """Кнопка «Съесть снова»: повторяет прошлую запись одним нажатием."""
    meal_id = int(call.data.split(":")[2])
    meal = await database.get_meal_by_id(meal_id)
    if not meal:
        await call.answer("Запись не найдена.", show_alert=True)
        return
    await _add_meal_and_confirm(
        call, meal["name"], float(meal["kcal"] or 0), float(meal["protein"] or 0),
        float(meal["fat"] or 0), float(meal["carbs"] or 0), meal["weight_g"],
    )
    await call.answer()


async def cb_add_barcode(call: CallbackQuery) -> None:
    state = _get_state(call)
    product = (state or {}).get("barcode_product")
    if not product:
        await call.answer("Продукт не найден.", show_alert=True)
        return
    await _add_meal_and_confirm(
        call, product["name"], product["kcal"], product["protein"],
        product["fat"], product["carbs"], 100,
    )
    PENDING.pop(call.from_user.id, None)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()


async def cb_edit_menu(call: CallbackQuery) -> None:
    state = await _no_state(call)
    if not state:
        return
    state["awaiting_index"] = None
    state["awaiting_remark"] = False
    await call.message.edit_reply_markup(reply_markup=edit_menu_keyboard(state["items"]))
    await call.answer()


async def cb_remark(call: CallbackQuery) -> None:
    state = await _no_state(call)
    if not state:
        return
    state["awaiting_index"] = None
    state["awaiting_remark"] = True
    await call.message.answer(REMARK_PROMPT)
    await call.answer()


async def cb_edit_item(call: CallbackQuery) -> None:
    state = await _no_state(call)
    if not state:
        return
    index = int(call.data.split(":")[2])
    if not 0 <= index < len(state["items"]):
        await call.answer("Пункт не найден.", show_alert=True)
        return
    state["awaiting_index"] = index
    await call.message.answer(
        EDIT_PROMPT.format(name=state["items"][index].get("name", "?"))
    )
    await call.answer()


async def cb_back(call: CallbackQuery) -> None:
    state = await _no_state(call)
    if not state:
        return
    state["awaiting_index"] = None
    await call.message.edit_text(
        format_report({"items": state["items"]}) + "\n\n<i>Добавляем в дневник?</i>",
        reply_markup=confirm_keyboard(),
    )
    await call.answer()


async def cb_cancel(call: CallbackQuery) -> None:
    PENDING.pop(call.from_user.id, None)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("❌ Отменено. Ничего не записано.")
    await call.answer()


router.callback_query.register(cb_add_all, F.data == "meal:add_all")
router.callback_query.register(cb_add_barcode, F.data == "meal:add_barcode")
router.callback_query.register(cb_repeat, F.data.startswith("meal:repeat:"))
router.callback_query.register(cb_edit_menu, F.data == "meal:edit_menu")
router.callback_query.register(cb_remark, F.data == "meal:remark")
router.callback_query.register(cb_edit_item, F.data.startswith("meal:edit:"))
router.callback_query.register(cb_back, F.data == "meal:back")
router.callback_query.register(cb_cancel, F.data == "meal:cancel")
