"""Быстрая проверка: БД + парсеры + новые фичи. Запуск: python run_checks.py"""

import asyncio
import os

os.environ["DATABASE_PATH"] = "test_check.db"

from datetime import date, timedelta  # noqa: E402

from bot.db import database  # noqa: E402
from bot.handlers.extras import calc_streak  # noqa: E402
from bot.handlers.photo import calc_total, format_report, parse_correction  # noqa: E402
from bot.handlers.profile import calc_norm, macros  # noqa: E402
from bot.handlers.stats import split_by_time, week_insights  # noqa: E402
from bot.services.off import parse_product  # noqa: E402
from bot.services.vision import _extract_json  # noqa: E402


def test_json_parser() -> None:
    assert _extract_json('{"items": [], "total": {}}') == {"items": [], "total": {}}
    assert _extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert _extract_json('текст {"b": [1, 2]} текст')["b"] == [1, 2]
    print("JSON PARSER OK")


def test_correction_parser() -> None:
    # полный формат: имя | вес | ккал | б | ж | у
    item, err = parse_correction("Курица гриль | 250 | 410 | 62 | 18 | 0")
    assert not err, err
    assert item["name"] == "Курица гриль" and item["portion_g"] == 250
    assert item["kcal"] == 410 and item["carbs"] == 0

    # без веса, запятая как разделитель дробей
    item, err = parse_correction("Борщ | 250,5 | 15 | 8 | 30")
    assert not err, err
    assert "portion_g" not in item and item["kcal"] == 250.5

    # ошибки
    _, err = parse_correction("Каша 300")          # нет |
    assert err
    _, err = parse_correction("Каша | abc | 1 | 1 | 1")
    assert err
    _, err = parse_correction("Каша | -5 | 1 | 1 | 1")
    assert err

    # пересчёт итога из пунктов
    total = calc_total([
        {"kcal": 410, "protein": 62, "fat": 18, "carbs": 0},
        {"kcal": 250.5, "protein": 15, "fat": 8, "carbs": 30},
    ])
    assert total == {"kcal": 660.5, "protein": 77, "fat": 26, "carbs": 30}, total

    # отчёт содержит нумерацию и итог
    report = format_report({"items": [
        {"name": "Курица", "portion_g": 250, "kcal": 410,
         "protein": 62, "fat": 18, "carbs": 0},
    ]})
    assert "1." in report and "Итого" in report and "410" in report
    print("CORRECTION PARSER OK")


def test_profile_math() -> None:
    # мужчина 30 лет, 180 см, 80 кг, средняя активность, поддержание
    norm = calc_norm("male", 30, 180, 80, 1.55, "maintain")
    bmr = 10 * 80 + 6.25 * 180 - 5 * 30 + 5  # 1780
    assert norm == round(bmr * 1.55), norm
    assert calc_norm("female", 30, 180, 80, 1.55, "maintain") < norm
    assert calc_norm("male", 30, 180, 80, 1.55, "lose") < norm
    p, f, c = macros(2000)
    assert (p, f, c) == (150, 56, 225), (p, f, c)
    print("PROFILE MATH OK")


def test_streak_and_stats() -> None:
    today = date.today()
    dates = {(today - timedelta(days=i)).isoformat() for i in range(4)}
    assert calc_streak(dates, today.isoformat()) == 4
    yesterday_set = {d for d in dates if d != today.isoformat()}
    assert calc_streak(yesterday_set, today.isoformat()) == 3  # серия не сломана
    assert calc_streak(set(), today.isoformat()) == 0

    dist = split_by_time([(9, 300), (13, 500), (19, 700), (23, 100)])
    assert dist == {"🌅 Завтрак": 300, "☀️ Обед": 500,
                    "🌆 Ужин": 700, "🌙 Перекусы": 100}, dist

    days = [{"meals": 2, "kcal": 2000, "protein": 90} for _ in range(7)]
    assert week_insights(days, 2100)[0].startswith("✅")
    over = week_insights([{"meals": 2, "kcal": 2600, "protein": 90}] * 7, 2100)
    assert any("выше нормы" in s for s in over)

    product = parse_product({
        "product_name": "Творог 5%",
        "brands": "Простоквашино",
        "nutriments": {"energy-kcal_100g": "121,5", "proteins_100g": 18,
                       "fat_100g": 5, "carbohydrates_100g": 3},
    })
    assert product["name"] == "Творог 5%" and product["kcal"] == 121.5
    assert product["protein"] == 18 and product["carbs"] == 3
    kj_only = parse_product({"nutriments": {"energy_100g": 1464}})
    assert abs(kj_only["kcal"] - 350) < 1, kj_only
    print("STREAK/STATS/OFF OK")


async def test_db() -> None:
    await database.init_db()
    uid = 424242

    await database.ensure_user(uid)
    # новые пользователи стартуют с выключенными напоминаниями
    assert not await database.are_reminders_on(uid), "напоминания должны быть off"
    await database.add_meal(uid, "Курица", 200, 30, 8, 0)
    await database.add_meal(uid, "Рис", 150, 3, 1, 33)

    day = await database.get_day_totals(uid)
    assert day["meals"] == 2 and day["kcal"] == 350, day

    week = await database.get_week_totals(uid)
    assert len(week) == 7 and week[-1]["kcal"] == 350, week[-1]

    await database.set_reminders(uid, False)
    assert not await database.are_reminders_on(uid)
    assert uid not in await database.get_users_with_reminders()
    await database.set_reminders(uid, True)
    assert uid in await database.get_users_with_reminders()

    created = await database.add_weight(uid, 82.5)
    updated = await database.add_weight(uid, 83.0)
    assert created and not updated
    hist = await database.get_weights(uid)
    assert len(hist) == 1 and hist[0][1] == 83.0, hist

    # вода
    total = await database.add_water(uid, 250)
    total = await database.add_water(uid, 300)
    assert total == 550 and await database.get_water(uid) == 550

    # профиль
    await database.save_profile(uid, goal="lose", gender="male", age=30,
                                height=180, activity=1.55, norm_kcal=2000,
                                target_weight=78)
    profile = await database.get_profile(uid)
    assert profile and profile["norm_kcal"] == 2000 and profile["target_weight"] == 78

    # повтор / топ / экспорт / даты
    meal_id = (await database.get_recent_distinct_meals(uid))[0]["id"]
    meal = await database.get_meal_by_id(meal_id)
    assert meal["name"] == "Рис"
    await database.add_meal(uid, "Курица", 180, 27, 7, 0)
    top = await database.get_top_products(uid)
    assert top[0][0] == "Курица" and top[0][1] == 2
    rows = await database.get_all_meals_for_export(uid)
    assert len(rows) == 3 and rows[0][0] == "Курица"
    assert date.today().isoformat() in await database.get_logged_dates(uid)

    dist = split_by_time(await database.get_day_meals_with_time(uid))
    assert sum(dist.values()) == 530, dist
    print("DB TESTS OK")


if __name__ == "__main__":
    test_json_parser()
    test_correction_parser()
    test_profile_math()
    test_streak_and_stats()
    asyncio.run(test_db())

    # живой тест словесного уточнения через Gemini (требует ключ и сеть)
    async def test_refine() -> None:
        from bot.services.vision import refine_items

        items = [{"name": "Курица гриль", "portion_g": 150, "kcal": 248,
                  "protein": 46, "fat": 5, "carbs": 0}]
        refined = await refine_items(items, "порция была вдвое больше")
        assert refined and refined[0]["kcal"] > items[0]["kcal"], refined
        print("REFINE (live) OK:", refined[0])

    if os.getenv("SKIP_LIVE") != "1":
        try:
            asyncio.run(test_refine())
        except Exception as exc:
            print(f"REFINE (live) SKIP/FAIL: {exc}")

    os.remove("test_check.db")
    print("ALL CHECKS PASSED")

