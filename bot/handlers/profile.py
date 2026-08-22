"""Профиль пользователя: анкета при старте, расчёт нормы калорий, /norm."""

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.db import database

logger = logging.getLogger(__name__)
router = Router()

# Анкета в памяти: user_id -> {"step": ..., "data": {...}}
ONBOARDING: dict[int, dict[str, Any]] = {}

GOAL_LABELS = {"lose": "🔥 Похудеть", "maintain": "⚖️ Поддерживать", "gain": "💪 Набрать"}
GENDER_LABELS = {"male": "👨 Мужской", "female": "👩 Женский"}
ACTIVITY_LABELS = {
    1.2: "🪑 Минимум (сидячая работа)",
    1.375: "🚶 Лёгкая (прогулки 1-3 р/нед)",
    1.55: "🏃 Средняя (тренировки 3-5 р/нед)",
    1.725: "🏋️ Высокая (спорт почти ежедневно)",
}

STEPS_ORDER = ["goal", "gender", "age", "height", "weight",
               "activity", "target", "done"]


def calc_norm(gender: str, age: int, height: float,
              weight: float, activity: float, goal: str) -> float:
    """Миффлин–Сан Жеор + коэффициент активности + поправка на цель."""
    bmr = 10 * weight + 6.25 * height - 5 * age
    bmr += 5 if gender == "male" else -161
    kcal = bmr * activity
    if goal == "lose":
        kcal *= 0.85
    elif goal == "gain":
        kcal *= 1.1
    return round(kcal)


def macros(norm_kcal: float) -> tuple[float, float, float]:
    """Рекомендуемые белки/жиры/углеводы для заданной нормы."""
    protein = round(norm_kcal * 0.30 / 4)
    fat = round(norm_kcal * 0.25 / 9)
    carbs = round(norm_kcal * 0.45 / 4)
    return protein, fat, carbs


def _goal_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"profile:goal:{key}")]
            for key, label in GOAL_LABELS.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gender_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=GENDER_LABELS["male"], callback_data="profile:gender:male")],
        [InlineKeyboardButton(text=GENDER_LABELS["female"], callback_data="profile:gender:female")],
    ])


def _activity_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"profile:activity:{factor}")]
        for factor, label in ACTIVITY_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def start_onboarding(send, user_id: int, first_name: str) -> None:
    """Запускает анкету. send — coroutine, отправляющая сообщение."""
    ONBOARDING[user_id] = {"step": "goal", "data": {}}
    await send(
        f"Рад знакомству, <b>{first_name}</b>! 👋\n\n"
        "Давай настроим твою дневную норму калорий — это займёт минуту.\n\n"
        "<b>Какая у тебя цель?</b>",
        reply_markup=_goal_kb(),
    )


def is_answering(message: Message) -> bool:
    """Фильтр: пользователь сейчас отвечает на вопрос анкеты текстом."""
    state = ONBOARDING.get(message.from_user.id or 0)
    return bool(state) and message.text is not None


@router.message(Command("norm"))
async def cmd_norm(message: Message) -> None:
    profile = await database.get_profile(message.from_user.id)
    if not profile:
        ONBOARDING[message.from_user.id] = {"step": "goal", "data": {}}
        await message.answer(
            f"Рад знакомству, <b>{message.from_user.first_name or 'друг'}</b>! 👋\n\n"
            "Давай настроим дневную норму калорий — это займёт минуту.\n\n"
            "<b>Какая у тебя цель?</b>",
            reply_markup=_goal_kb(),
        )
        return
    p, f, c = macros(profile["norm_kcal"])
    goal = GOAL_LABELS.get(profile.get("goal"), "—")
    target = ""
    if profile.get("target_weight"):
        target = f"\n🎯 Целевой вес: <b>{profile['target_weight']:g} кг</b>"
    await message.answer(
        "📋 <b>Твой профиль:</b>\n\n"
        f"🎯 Цель: {goal}\n"
        f"👤 {GENDER_LABELS.get(profile.get('gender'), '—')}, "
        f"{profile.get('age') or '—'} лет, {profile.get('height') or '—'} см\n"
        f"⚡ Активность: {ACTIVITY_LABELS.get(profile.get('activity'), '—')}{target}\n\n"
        f"🔥 <b>Норма: {round(profile['norm_kcal'])} ккал/день</b>\n"
        f"   🥩 Белки ~{p} г · 🧈 Жиры ~{f} г · 🍞 Углеводы ~{c} г",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересчитать", callback_data="profile:restart")],
        ]),
    )


async def handle_answer(message: Message) -> None:
    """Текстовый ответ на вопрос анкеты."""
    user_id = message.from_user.id
    state = ONBOARDING[user_id]
    step, data = state["step"], state["data"]

    if message.text.startswith("/"):
        ONBOARDING.pop(user_id, None)
        await message.answer("⏸ Настройку профиля можно продолжить позже через /norm.")
        return

    if step in ("age", "height", "weight", "target"):
        raw = message.text.replace(",", ".").strip()
        if step == "target" and raw.lower() in ("skip", "пропустить"):
            data["target_weight"] = None
        else:
            try:
                value = float(raw)
            except ValueError:
                hint = {
                    "age": "возраст, например <code>28</code>",
                    "height": "рост в см, например <code>176</code>",
                    "weight": "вес в кг, например <code>74.5</code>",
                    "target": "целевой вес в кг, например <code>70</code> "
                              "(или напиши «пропустить»)",
                }[step]
                await message.answer(f"🤔 Не понял число. Пришли {hint}")
                return
            low, high = {"age": (10, 100), "height": (100, 250),
                         "weight": (30, 300), "target": (30, 300)}[step]
            if not low <= value <= high:
                await message.answer(f"🤔 Значение должно быть от {low} до {high}.")
                return
            data[{"age": "age", "height": "height",
                  "weight": "weight", "target": "target_weight"}[step]] = value

    await _advance(state, message)


async def _advance(state: dict, message: Message) -> None:
    """Переходит к следующему шагу анкеты или завершает её."""
    nxt = STEPS_ORDER[STEPS_ORDER.index(state["step"]) + 1]
    state["step"] = nxt
    prompts = {
        "gender": ("👤 Укажи свой пол:", _gender_kb()),
        "age": ("🎂 Сколько тебе лет? Просто цифрой.", None),
        "height": ("📏 Твой рост в сантиметрах?", None),
        "weight": ("⚖️ Текущий вес в килограммах?", None),
        "activity": ("⚡ Какая у тебя физическая активность?", _activity_kb()),
        "target": ("🎯 Есть целевой вес? Пришли его в кг, "
                   "или напиши «пропустить».", None),
    }
    if nxt == "done":
        await finish_onboarding(state, message)
        return
    text, kb = prompts[nxt]
    await message.answer(text, reply_markup=kb)


async def finish_onboarding(state: dict, message: Message) -> None:
    data = state["data"]
    user_id = message.from_user.id
    norm = calc_norm(
        gender=data["gender"], age=int(data["age"]), height=data["height"],
        weight=data["weight"], activity=data["activity"], goal=data["goal"],
    )
    fields = {
        "goal": data["goal"], "gender": data["gender"],
        "age": int(data["age"]), "height": data["height"],
        "activity": data["activity"], "norm_kcal": norm,
    }
    if data.get("target_weight"):
        fields["target_weight"] = data["target_weight"]
    await database.save_profile(user_id, **fields)
    try:
        await database.add_weight(user_id, data["weight"])
    except Exception:
        logger.warning("Не удалось записать стартовый вес user=%s", user_id)
    ONBOARDING.pop(user_id, None)

    p, f, c = macros(norm)
    target_line = f"\n🎯 Целевой вес: {data['target_weight']:g} кг" \
        if data.get("target_weight") else ""
    await message.answer(
        "✅ Готово! Профиль сохранён.\n\n"
        f"🎯 Цель: {GOAL_LABELS[data['goal']]}{target_line}\n\n"
        f"🔥 <b>Твоя дневная норма: {norm} ккал</b>\n"
        f"   🥩 Белки ~{p} г · 🧈 Жиры ~{f} г · 🍞 Углеводы ~{c} г\n\n"
        "Теперь /today показывает прогресс к норме.\n"
        "Отправляй фото еды — я всё посчитаю! 📸"
    )
    logger.info("user=%s: профиль сохранён, норма %d ккал", user_id, norm)


router.message.register(handle_answer, is_answering)


@router.callback_query(F.data.startswith("profile:"))
async def cb_profile(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    parts = call.data.split(":")
    action = parts[1]

    if action == "restart":
        ONBOARDING[user_id] = {"step": "goal", "data": {}}
        await call.message.answer("Давай заново 👇\n\n<b>Какая у тебя цель?</b>",
                                  reply_markup=_goal_kb())
        await call.answer()
        return

    state = ONBOARDING.get(user_id)
    if not state:
        await call.answer("Анкета не активна. Запусти /norm.", show_alert=True)
        return

    if action == "goal":
        state["data"]["goal"] = parts[2]
        next_step = STEPS_ORDER[STEPS_ORDER.index("goal") + 1]  # gender
        state["step"] = next_step
        await call.message.answer("Отлично! 👤 Укажи свой пол:",
                                  reply_markup=_gender_kb())
    elif action == "gender":
        state["data"]["gender"] = parts[2]
        await _advance(state, call.message)
    elif action == "activity":
        state["data"]["activity"] = float(parts[2])
        await _advance(state, call.message)
    await call.answer()


