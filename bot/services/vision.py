"""Распознавание еды на фото через бесплатный API Google Gemini.

Модель настраивается через .env (VISION_MODEL). По умолчанию используется
алиас gemini-flash-latest — он всегда указывает на актуальную flash-модель,
что защищает от устаревания конкретных имён моделей.
"""

import asyncio
import json
import re
from typing import Any

from google import genai
from google.genai import types

from bot.config import GEMINI_API_KEY, VISION_MODEL

PROMPT = """Ты — опытный нутрициолог. Проанализируй фотографию еды.

Правила:
1. Определи каждое блюдо или продукт на фото.
2. Оцени вес порции каждого пункта в граммах по визуальным признакам.
3. Посчитай для каждого пункта калорийность (ккал), белки, жиры и углеводы (граммы).
4. Посчитай итоговую сумму по всем пунктам.
5. Если на фото нет еды, заполни поле error коротким объяснением по-русски,
   а items оставь пустым списком.

Ответь ТОЛЬКО валидным JSON без markdown-обёртки, строго по схеме:
{
  "items": [
    {"name": "название", "portion_g": 0, "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}
  ],
  "total": {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0},
  "error": null
}"""

_client: genai.Client | None = None

# Запасные модели, если основная перегружена/недоступна (503/429/404)
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]


def _get_client() -> genai.Client:
    """Создаёт клиент Gemini один раз, при первом обращении."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON из ответа модели, отбрасывая возможные ```-обёртки."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"В ответе модели нет JSON:\n{text[:200]}")
    return json.loads(text[start : end + 1])


def _generate(parts: list) -> str:
    """Запрос к Gemini с перебором моделей, если основная недоступна."""
    last_error: Exception | None = None
    for model in [VISION_MODEL] + FALLBACK_MODELS:
        try:
            response = _get_client().models.generate_content(
                model=model,
                contents=[types.Content(parts=parts)],
            )
            return response.text or ""
        except Exception as exc:  # 503/429/404 и т.п. — пробуем следующую
            last_error = exc
    raise RuntimeError(f"Ни одна модель не ответила: {last_error}") from last_error


async def analyze_photo(image_bytes: bytes, note: str | None = None) -> dict[str, Any]:
    """Фото -> отчёт КБЖУ. note — пояснение пользователя из подписи к фото."""
    prompt_text = PROMPT
    if note and note.strip():
        prompt_text += (
            f"\n\nДополнительное пожелание пользователя к этому фото:"
            f"\n«{note.strip()}»"
            "\nОбязательно учти его при оценке блюд и порций."
        )
    parts = [
        types.Part.from_text(text=prompt_text),
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
    ]
    raw = await asyncio.to_thread(_generate, parts)
    data = _extract_json(raw)
    data.setdefault("items", [])
    data.setdefault("total", {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0})
    if data.get("error") is None and not data["items"]:
        data["error"] = "Не удалось найти еду на фото."
    return data


# ---------- Уточнение оценки по словесному замечанию ----------

REFINE_PROMPT = """Ты — опытный нутрициолог. Вот текущая оценка съеденного в JSON:
{items_json}

Замечание от пользователя: «{remark}»

Внеси исправления строго в соответствии с замечанием: можешь менять названия,
вес порций и любые значения КБЖУ (например, добавить забытое блюдо или убрать лишнее).

Ответь ТОЛЬКО валидным JSON без markdown-обёртки, строго по схеме:
{{"items": [{{"name": "название", "portion_g": 0, "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}}]}}"""


async def refine_items(items: list[dict], remark: str) -> list[dict]:
    """Пересчитывает пункты отчёта с учётом словесного замечания пользователя."""
    prompt = REFINE_PROMPT.format(
        items_json=json.dumps(items, ensure_ascii=False),
        remark=remark.strip(),
    )
    raw = await asyncio.to_thread(_generate, [types.Part.from_text(text=prompt)])
    data = _extract_json(raw)
    refined = data.get("items")
    if not isinstance(refined, list) or not refined:
        raise ValueError("Модель вернула пустой список блюд")
    return refined


# ---------- Голосовые сообщения ----------

VOICE_PROMPT = """Ты — опытный нутрициолог. Пользователь наговорил голосовым сообщением,
что он съел. Послушай аудио, расшифруй его и определи все съеденные блюда/продукты.

Правила:
1. Оцени вес порции каждого пункта в граммах по описанию (или по здравому смыслу,
   если вес не назван).
2. Посчитай для каждого пункта калорийность (ккал), белки, жиры и углеводы (граммы).
3. Если из аудио не удалось понять еду, заполни поле error коротким объяснением
   по-русски, а items оставь пустым списком.

Ответь ТОЛЬКО валидным JSON без markdown-обёртки, строго по схеме:
{
  "transcript": "расшифровка аудио на русском",
  "items": [
    {"name": "название", "portion_g": 0, "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}
  ],
  "error": null
}"""


async def analyze_voice(audio_bytes: bytes) -> dict[str, Any]:
    """Голосовое сообщение -> {transcript, items} с оценкой КБЖУ."""
    parts = [
        types.Part.from_text(text=VOICE_PROMPT),
        types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
    ]
    raw = await asyncio.to_thread(_generate, parts)
    data = _extract_json(raw)
    data.setdefault("items", [])
    data.setdefault("transcript", "")
    if data.get("error") is None and not data["items"]:
        data["error"] = "Не получилось разобрать, что ты съел. Попробуй ещё раз."
    return data


# ---------- Еда, описанная текстом ----------

TEXT_MEAL_PROMPT = """Ты — опытный нутрициолог. Пользователь перечислил съеденное текстом.
Определи каждое блюдо/продукт, оцени вес порции в граммах и посчитай
калорийность (ккал), белки, жиры и углеводы (г) для каждого пункта.

Ответь ТОЛЬКО валидным JSON без markdown-обёртки, строго по схеме:
{"items": [{"name": "название", "portion_g": 0, "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}]}"""


async def parse_text_meal(text: str) -> list[dict]:
    """Текстовое описание еды -> список пунктов с КБЖУ."""
    prompt = TEXT_MEAL_PROMPT + f"\n\nОписание пользователя: «{text.strip()}»"
    raw = await asyncio.to_thread(_generate, [types.Part.from_text(text=prompt)])
    data = _extract_json(raw)
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Модель вернула пустой список блюд")
    return items


# ---------- Штрихкоды ----------

BARCODE_PROMPT = """Найди на фотографии штрихкод (EAN/UPC). Внимательно прочитай цифры под
полосами или сами полосы. Ответь ТОЛЬКО валидным JSON без markdown:
{"barcode": "цифры_штрихкода"}
Если штрихкода на фото нет, верни {"barcode": null}."""


async def extract_barcode(image_bytes: bytes) -> str | None:
    """Пытается прочитать штрихкод с фото через Gemini."""
    parts = [
        types.Part.from_text(text=BARCODE_PROMPT),
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
    ]
    raw = await asyncio.to_thread(_generate, parts)
    data = _extract_json(raw)
    barcode = data.get("barcode")
    if isinstance(barcode, str) and sum(ch.isdigit() for ch in barcode) >= 8:
        return "".join(ch for ch in barcode if ch.isdigit())
    return None

