"""Слой доступа к данным: SQLite через aiosqlite (асинхронно)."""

from datetime import date, timedelta

import aiosqlite

from bot.config import DATABASE_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    reminder_on INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS meals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kcal       REAL NOT NULL DEFAULT 0,
    protein    REAL NOT NULL DEFAULT 0,
    fat        REAL NOT NULL DEFAULT 0,
    carbs      REAL NOT NULL DEFAULT 0,
    weight_g   REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_meals_user_day ON meals(user_id, date(created_at));

CREATE TABLE IF NOT EXISTS weights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    weight        REAL NOT NULL,
    recorded_date TEXT NOT NULL,
    UNIQUE(user_id, recorded_date)
);

CREATE TABLE IF NOT EXISTS water (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    ml      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""

# Колонки профиля, добавляемые миграцией к существующей таблице users
_PROFILE_COLUMNS = {
    "goal": "TEXT",            # lose / maintain / gain
    "gender": "TEXT",          # male / female
    "age": "INTEGER",
    "height": "REAL",
    "activity": "REAL",        # коэффициент активности
    "norm_kcal": "REAL",       # рассчитанная дневная норма
    "target_weight": "REAL",   # целевой вес
}



async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(_SCHEMA)
        # мягкая миграция: добавляем колонки профиля, если их ещё нет
        for column, ctype in _PROFILE_COLUMNS.items():
            try:
                await db.execute(
                    f"ALTER TABLE users ADD COLUMN {column} {ctype}"
                )
            except aiosqlite.OperationalError:
                pass  # колонка уже существует
        await db.commit()


# ---------- Приёмы пищи ----------


async def add_meal(
    user_id: int,
    name: str,
    kcal: float,
    protein: float,
    fat: float,
    carbs: float,
    weight_g: float | None = None,
) -> None:
    """Сохраняет один пункт из распознанного фото как отдельный приём пищи."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO meals (user_id, name, kcal, protein, fat, carbs, weight_g)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, kcal, protein, fat, carbs, weight_g),
        )
        await db.commit()


async def get_day_totals(user_id: int, day: str | None = None) -> dict:
    """Итоги КБЖУ за указанный день ('YYYY-MM-DD'), по умолчанию — сегодня."""
    day = day or date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT COUNT(*) AS n,"
            " COALESCE(SUM(kcal), 0) AS kcal,"
            " COALESCE(SUM(protein), 0) AS protein,"
            " COALESCE(SUM(fat), 0) AS fat,"
            " COALESCE(SUM(carbs), 0) AS carbs"
            " FROM meals WHERE user_id = ? AND date(created_at) = ?",
            (user_id, day),
        )
        row = await cur.fetchone()
        return {
            "day": day,
            "meals": row["n"],
            "kcal": round(row["kcal"], 1),
            "protein": round(row["protein"], 1),
            "fat": round(row["fat"], 1),
            "carbs": round(row["carbs"], 1),
        }


async def get_week_totals(user_id: int) -> list[dict]:
    """Итоги КБЖУ по каждому из последних 7 дней (включая сегодня)."""
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return [await get_day_totals(user_id, day) for day in days]


# ---------- Дневник веса ----------


async def add_weight(user_id: int, weight: float) -> bool:
    """Записывает вес на сегодня. Если запись уже есть — обновляет её.

    Возвращает True, если запись создана, False — если обновлена.
    """
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM weights WHERE user_id = ? AND recorded_date = ?",
            (user_id, today),
        )
        exists = await cur.fetchone() is not None
        if exists:
            await db.execute(
                "UPDATE weights SET weight = ? WHERE user_id = ? AND recorded_date = ?",
                (weight, user_id, today),
            )
        else:
            await db.execute(
                "INSERT INTO weights (user_id, weight, recorded_date) VALUES (?, ?, ?)",
                (user_id, weight, today),
            )
        await db.commit()
        return not exists


async def get_weights(user_id: int, limit: int = 14) -> list[tuple[str, float]]:
    """Последние записи веса: список (дата, вес), от старых к новым."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT recorded_date, weight FROM weights"
            " WHERE user_id = ? ORDER BY recorded_date DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [(r[0], r[1]) for r in reversed(rows)]


# ---------- Пользователи и напоминания ----------


async def ensure_user(user_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def set_reminders(user_id: int, on: bool) -> bool:
    """Включает/выключает напоминания. Возвращает новое состояние."""
    await ensure_user(user_id)
    state = 1 if on else 0
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET reminder_on = ? WHERE user_id = ?", (state, user_id)
        )
        await db.commit()
    return on


async def are_reminders_on(user_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT reminder_on FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0])


async def get_users_with_reminders() -> list[int]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM users WHERE reminder_on = 1"
        )
        return [r[0] for r in await cur.fetchall()]


# ---------- Профиль и норма калорий ----------


async def get_profile(user_id: int) -> dict | None:
    """Профиль пользователя или None, если анкета ещё не заполнена."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
    if row is None or row["norm_kcal"] is None:
        return None
    return dict(row)


async def save_profile(user_id: int, **fields) -> None:
    """Сохраняет любые поля профиля (goal, norm_kcal, target_weight...)."""
    await ensure_user(user_id)
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE users SET {columns} WHERE user_id = ?", values
        )
        await db.commit()


# ---------- Вода ----------


async def add_water(user_id: int, ml: float) -> float:
    """Добавляет выпитую воду за сегодня. Возвращает итог за день."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO water (user_id, day, ml) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, day) DO UPDATE SET ml = ml + excluded.ml",
            (user_id, today, ml),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT COALESCE(SUM(ml), 0) FROM water WHERE user_id = ? AND day = ?",
            (user_id, today),
        )
        row = await cur.fetchone()
        return float(row[0])


async def get_water(user_id: int, day: str | None = None) -> float:
    day = day or date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(ml), 0) FROM water WHERE user_id = ? AND day = ?",
            (user_id, day),
        )
        row = await cur.fetchone()
        return float(row[0])


# ---------- Повтор блюд / топ продуктов / экспорт ----------


async def get_recent_distinct_meals(
    user_id: int, limit: int = 5
) -> list[dict]:
    """Последние уникальные по названию записи дневника — для кнопки «Съесть снова»."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, name, kcal, protein, fat, carbs, weight_g"
            " FROM meals WHERE user_id = ?"
            " GROUP BY name HAVING MAX(id)"
            " ORDER BY MAX(id) DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "kcal": r["kcal"],
            "protein": r["protein"],
            "fat": r["fat"],
            "carbs": r["carbs"],
            "weight_g": r["weight_g"],
        }
        for r in rows
    ]


async def get_meal_by_id(meal_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_top_products(user_id: int, limit: int = 10) -> list[tuple]:
    """Частые продукты: [(название, раз, средние ккал), ...]."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT name, COUNT(*) AS n, AVG(kcal) FROM meals"
            " WHERE user_id = ? GROUP BY name ORDER BY n DESC LIMIT ?",
            (user_id, limit),
        )
        return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def get_day_meals_with_time(
    user_id: int, day: str | None = None
) -> list[tuple[str, float]]:
    """Записи за день с часом приёма: [(час_0_23, ккал), ...]."""
    day = day or date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT CAST(strftime('%H', created_at) AS INTEGER), kcal FROM meals"
            " WHERE user_id = ? AND date(created_at) = ?",
            (user_id, day),
        )
        return [(r[0], r[1]) for r in await cur.fetchall()]


async def get_logged_dates(user_id: int, days_back: int = 120) -> set[str]:
    """Дни, когда были записи еды (для стриков)."""
    since = (date.today() - timedelta(days=days_back)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT date(created_at) FROM meals"
            " WHERE user_id = ? AND date(created_at) >= ?",
            (user_id, since),
        )
        return {r[0] for r in await cur.fetchall()}


async def get_all_meals_for_export(user_id: int) -> list[tuple]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT name, kcal, protein, fat, carbs, weight_g, created_at"
            " FROM meals WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
                for r in await cur.fetchall()]
