"""Диагностика подключения к Gemini API.

Запуск: python debug_vision.py [имя_модели]
Без аргумента используется VISION_MODEL из .env.
"""

import asyncio
import io
import json
import sys
import traceback

from bot.config import GEMINI_API_KEY, VISION_MODEL


def make_test_image() -> bytes:
    """Генерирует маленький тестовый JPEG (зелёный квадрат)."""
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (40, 160, 60)).save(buf, format="JPEG")
        return buf.getvalue()
    except ImportError:
        # минимальный валидный JPEG 1x1 без Pillow
        import base64

        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
            "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
            "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
        )


async def main() -> None:
    from google import genai
    from google.genai import types

    # модель можно передать аргументом: python debug_vision.py gemini-2.5-flash
    model = sys.argv[1] if len(sys.argv) > 1 else VISION_MODEL

    client = genai.Client(api_key=GEMINI_API_KEY)
    image = make_test_image()
    print(f"Ключ задан: {bool(GEMINI_API_KEY)} (первые 6 символов: {GEMINI_API_KEY[:6]})")
    print(f"Тестовое изображение: {len(image)} байт")
    print(f"Модель: {model}\n")

    # Шаг 1: какие модели доступны (только если модель не указана вручную)
    if len(sys.argv) <= 1:
        print("=== Доступные flash-модели ===")
        try:
            models = await asyncio.to_thread(lambda: list(client.models.list()))
            names = [m.name for m in models if "flash" in m.name.lower()]
            print("\n".join(names) or "(ничего не найдено)")
        except Exception:
            print("Не удалось получить список моделей:")
            traceback.print_exc()
        print()

    # Шаг 2: прямой запрос с картинкой
    print(f"=== Тестовый запрос ({model}) ===")
    try:
        resp = await asyncio.to_thread(
            lambda: client.models.generate_content(
                model=model,
                contents=[
                    types.Content(
                        parts=[
                            types.Part.from_text(text="Что на картинке? Одним предложением."),
                            types.Part.from_bytes(data=image, mime_type="image/jpeg"),
                        ]
                    )
                ],
            )
        )
        print("OK! Ответ:", resp.text)
    except Exception:
        print("ОШИБКА:")
        traceback.print_exc()

    # Шаг 3: полный путь analyze_photo (как в боте)
    print("\n=== Тест analyze_photo (полный путь бота) ===")
    try:
        from bot.services.vision import analyze_photo

        data = await analyze_photo(image)
        print("OK! Результат:", json.dumps(data, ensure_ascii=False)[:400])
    except Exception:
        print("ОШИБКА:")
        traceback.print_exc()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
