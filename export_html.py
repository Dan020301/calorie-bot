"""Генератор HTML-отчёта по пользователям бота из базы SQLite.

Запуск:
    python export_html.py            # создаст users_report.html
    python export_html.py --open     # создать и сразу открыть в браузере
"""

import html
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from bot.config import DATABASE_PATH

OUTPUT = Path("users_report.html")
GOALS = {"lose": "Похудеть", "maintain": "Поддерживать", "gain": "Набрать"}
ACTIVITY = {1.2: "Минимальная", 1.375: "Лёгкая", 1.55: "Средняя", 1.725: "Высокая"}


def load_data() -> list[dict]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    # мягкая миграция: если бот ещё не создавал колонку first_name
    try:
        conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    except sqlite3.OperationalError:
        pass
    today = date.today().isoformat()

    users = []
    for user in conn.execute("SELECT * FROM users ORDER BY first_name, user_id"):
        uid = user["user_id"]
        weights = [
            (r["recorded_date"], r["weight"])
            for r in conn.execute(
                "SELECT recorded_date, weight FROM weights WHERE user_id = ?"
                " ORDER BY recorded_date DESC LIMIT 60", (uid,))
        ]
        weights.reverse()
        day = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(kcal),0) kcal,"
            " COALESCE(SUM(protein),0) p, COALESCE(SUM(fat),0) f,"
            " COALESCE(SUM(carbs),0) c FROM meals"
            " WHERE user_id = ? AND date(created_at) = ?", (uid, today)
        ).fetchone()
        week_kcal = [r[0] for r in conn.execute(
            "SELECT COALESCE(SUM(kcal),0) FROM meals"
            " WHERE user_id = ? AND date(created_at) >= ?"
            " GROUP BY date(created_at) ORDER BY date(created_at)",
            (uid, (date.today() - timedelta(days=6)).isoformat())
        )]
        meals = [
            dict(r) for r in conn.execute(
                "SELECT name, kcal, created_at FROM meals"
                " WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (uid,))
        ]
        water = conn.execute(
            "SELECT COALESCE(SUM(ml),0) FROM water WHERE user_id = ? AND day = ?",
            (uid, today)).fetchone()[0]
        logged_dates = {r[0] for r in conn.execute(
            "SELECT DISTINCT date(created_at) FROM meals"
            " WHERE user_id = ? AND date(created_at) >= ?",
            (uid, (date.today() - timedelta(days=120)).isoformat()))}
        streak, d = 0, date.today()
        if today not in logged_dates:
            d -= timedelta(days=1)
        while d.isoformat() in logged_dates:
            streak += 1
            d -= timedelta(days=1)

        users.append({
            "id": uid,
            "name": user["first_name"] or f"Пользователь #{uid}",
            "reminders": bool(user["reminder_on"]),
            "goal": GOALS.get(user["goal"], "—"),
            "gender": {"male": "муж.", "female": "жен."}.get(user["gender"], "—"),
            "age": user["age"],
            "height": user["height"],
            "activity": ACTIVITY.get(user["activity"], "—"),
            "norm": round(user["norm_kcal"]) if user["norm_kcal"] else None,
            "target_weight": user["target_weight"],
            "weights": [{"d": w[0], "v": w[1]} for w in weights],
            "today": {"kcal": round(day["kcal"]), "p": round(day["p"]),
                      "f": round(day["f"]), "c": round(day["c"]), "n": day["n"]},
            "water_today": round(water),
            "week_kcal": [round(x) for x in week_kcal],
            "streak": streak,
            "meals_recent": meals,
        })
    conn.close()
    return users


def esc(value) -> str:
    return html.escape(str(value if value is not None else "—"))


def build_cards(users: list[dict]) -> str:
    cards = []
    for u in users:
        last_weight = u["weights"][-1]["v"] if u["weights"] else None
        weight_line = f"{last_weight:g} кг" if last_weight else "нет данных"
        norm_line = f"/ {u['norm']} ккал" if u["norm"] else ""
        delta = ""
        if len(u["weights"]) > 1:
            diff = u["weights"][-1]["v"] - u["weights"][0]["v"]
            if abs(diff) >= 0.05:
                sign = "+" if diff > 0 else ""
                arrow = "📈" if diff > 0 else "📉"
                delta = f'<span class="delta">{arrow} {sign}{diff:.1f} кг</span>'
        cards.append(
            f'<div class="card" onclick="showUser({u["id"]})">'
            f'<div class="card-top"><span class="avatar">'
            f'{esc(u["name"][:1].upper())}</span><div>'
            f'<div class="name">{esc(u["name"])}</div>'
            f'<div class="sub">🔥 сегодня: {u["today"]["kcal"]} {esc(norm_line)}</div>'
            f'</div></div>'
            f'<div class="weight-row"><span class="weight">⚖️ {weight_line}</span>'
            f'{delta}</div></div>'
        )
    return "\n".join(cards)


CSS = """
  :root { --bg:#f4f6fa; --card:#fff; --text:#24292f; --muted:#6b7280;
          --accent:#2e86de; --border:#e5e9f0; }
  * { box-sizing:border-box; margin:0; }
  body { font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg);
         color:var(--text); padding:32px; }
  h1 { margin-bottom:6px; }
  .hint { color:var(--muted); margin-bottom:24px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
          gap:16px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px;
          padding:18px; cursor:pointer; transition:transform .12s, box-shadow .12s; }
  .card:hover { transform:translateY(-2px); box-shadow:0 6px 18px rgba(30,40,70,.10); }
  .card-top { display:flex; gap:12px; align-items:center; }
  .avatar { width:44px; height:44px; border-radius:50%; background:var(--accent);
            color:#fff; display:flex; align-items:center; justify-content:center;
            font-size:20px; font-weight:600; flex-shrink:0; }
  .name { font-size:17px; font-weight:600; }
  .sub { color:var(--muted); font-size:13px; margin-top:2px; }
  .weight-row { display:flex; justify-content:space-between; align-items:baseline;
                margin-top:14px; padding-top:12px; border-top:1px solid var(--border); }
  .weight { font-size:19px; font-weight:600; }
  .delta { color:var(--muted); font-size:13px; }
  #detail { position:fixed; inset:0; background:var(--bg); overflow-y:auto;
            display:none; padding:32px; }
  #detail.open { display:block; }
  .back { background:none; border:none; color:var(--accent); font-size:16px;
          cursor:pointer; margin-bottom:18px; padding:0; }
  .head { display:flex; align-items:center; gap:16px; margin-bottom:20px; }
  .head .avatar { width:64px; height:64px; font-size:28px; }
  .head .name { font-size:26px; }
  .cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
          gap:16px; max-width:1000px; }
  .box { background:var(--card); border:1px solid var(--border); border-radius:14px;
         padding:18px; }
  .box h3 { font-size:13px; text-transform:uppercase; letter-spacing:.04em;
            color:var(--muted); margin-bottom:12px; }
  .row { display:flex; justify-content:space-between; padding:5px 0;
         border-bottom:1px dashed var(--border); font-size:15px; }
  .row:last-child { border-bottom:none; }
  .row span:first-child { color:var(--muted); }
  .big { font-size:28px; font-weight:700; color:var(--accent); }
  .meals { padding:0; list-style:none; }
  .meals li { padding:6px 0; border-bottom:1px dashed var(--border); font-size:14px; }
"""


def build_html(users: list[dict]) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Calorie Bot — пользователи</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        "<h1>🥗 Calorie Bot — пользователи</h1>\n"
        f"<p class=\"hint\">Всего пользователей: <b>{len(users)}</b>. "
        "Нажми на карточку, чтобы увидеть подробности.</p>\n"
        f"<div class=\"grid\">{build_cards(users) or '<p>В базе пока нет пользователей.</p>'}"
        "</div>\n<div id=\"detail\">\n"
        "<button class=\"back\" onclick=\"closeDetail()\">← Ко всем пользователям</button>\n"
        "<div class=\"head\"><span class=\"avatar\" id=\"d-avatar\"></span>"
        "<div><div class=\"name\" id=\"d-name\"></div>"
        "<div class=\"sub\" id=\"d-id\"></div></div></div>\n"
        "<div class=\"cols\">\n"
        "<div class=\"box\"><h3>Сегодня</h3><div class=\"big\" id=\"d-kcal\"></div>"
        "<div class=\"row\"><span>Белки / Жиры / Углеводы</span><b id=\"d-macros\"></b></div>"
        "<div class=\"row\"><span>Записей за день</span><b id=\"d-meals\"></b></div>"
        "<div class=\"row\"><span>💧 Вода</span><b id=\"d-water\"></b></div></div>\n"
        "<div class=\"box\"><h3>Профиль</h3><div id=\"d-profile\"></div></div>\n"
        "<div class=\"box\"><h3>⚖️ Вес и цель</h3>"
        "<svg id=\"d-chart\" width=\"100%\" height=\"120\"></svg>"
        "<div id=\"d-weight\"></div></div>\n"
        "<div class=\"box\"><h3>📈 Неделя (ккал/день)</h3><div id=\"d-week\"></div>"
        "<div class=\"row\"><span>🔥 Серия дней подряд</span>"
        "<b id=\"d-streak\"></b></div></div>\n"
        "<div class=\"box\" style=\"max-height:340px;overflow-y:auto\">"
        "<h3>🍽 Последние записи</h3><ul class=\"meals\" id=\"d-food\"></ul></div>\n"
        "</div>\n</div>\n<script>\n"
        + build_js(users)
        + "\n</script>\n</body>\n</html>\n"
    )


def build_js(users: list[dict]) -> str:
    return """const DATA = __DATA_JSON__;

function showUser(id) {
  const u = DATA[id];
  document.getElementById('d-avatar').textContent = u.name.charAt(0).toUpperCase();
  document.getElementById('d-name').textContent = u.name;
  document.getElementById('d-id').textContent = 'ID: ' + u.id +
    (u.reminders ? ' · 🔔 напоминания вкл' : ' · 🔕 напоминания выкл');
  document.getElementById('d-kcal').textContent =
    u.today.kcal + ' ккал' + (u.norm ? ' / ' + u.norm : '');
  document.getElementById('d-macros').textContent =
    u.today.p + ' / ' + u.today.f + ' / ' + u.today.c + ' г';
  document.getElementById('d-meals').textContent = u.today.n;
  document.getElementById('d-water').textContent = u.water_today + ' мл';
  const rows = [['🎯 Цель', u.goal], ['👤 Пол', u.gender],
    ['🎂 Возраст', u.age], ['📏 Рост', u.height ? u.height + ' см' : null],
    ['⚡ Активность', u.activity],
    ['🔥 Норма', u.norm ? u.norm + ' ккал/день' : null],
    ['🎯 Целевой вес', u.target_weight ? u.target_weight + ' кг' : null]];
  document.getElementById('d-profile').innerHTML = rows.map(r => r[1] == null ? '' :
    '<div class="row"><span>' + r[0] + '</span><b>' + r[1] + '</b></div>').join('');
  drawChart(u.weights, u.target_weight);
  let wl = '';
  if (u.weights.length) {
    const w = u.weights[u.weights.length - 1];
    wl += '<div class="big">' + w.v + ' кг</div>';
    wl += '<div class="row"><span>Последнее взвешивание</span><b>' + w.d + '</b></div>';
  } else {
    wl = '<p style="color:#6b7280">Нет записей веса</p>';
  }
  document.getElementById('d-weight').innerHTML = wl;
  document.getElementById('d-week').innerHTML = u.week_kcal.length
    ? u.week_kcal.map((k, i) =>
        '<div class="row"><span>День ' + (i + 1) + '</span><b>' + k + ' ккал</b></div>'
      ).join('')
    : '<p style="color:#6b7280">Нет записей за неделю</p>';
  document.getElementById('d-streak').textContent = u.streak + ' дн.';
  document.getElementById('d-food').innerHTML = u.meals_recent.map(m =>
    '<li><b>' + m.name + '</b> — ' + Math.round(m.kcal) +
    ' ккал <span style="color:#6b7280">(' + m.created_at + ')</span></li>').join('')
    || '<li style="color:#6b7280">Пока нет записей</li>';
  document.getElementById('detail').classList.add('open');
  window.scrollTo(0, 0);
}

function closeDetail() {
  document.getElementById('detail').classList.remove('open');
}

function drawChart(weights, target) {
  const svg = document.getElementById('d-chart');
  if (weights.length < 2) {
    svg.innerHTML = '<text x="10" y="60" fill="#6b7280">' +
      'Нужно минимум 2 записи веса для графика</text>';
    return;
  }
  const W = 600, H = 120, P = 8;
  const values = weights.map(w => w.v).concat(target ? [target] : []);
  const min = Math.min.apply(null, values) - 0.5;
  const max = Math.max.apply(null, values) + 0.5;
  const x = i => P + i * (W - 2 * P) / (weights.length - 1);
  const y = v => H - P - (v - min) * (H - 2 * P) / (max - min);
  const path = weights.map((w, i) =>
    (i ? 'L' : 'M') + x(i) + ',' + y(w.v)).join(' ');
  const dots = weights.map((w, i) =>
    '<circle cx="' + x(i) + '" cy="' + y(w.v) + '" r="3" fill="#2e86de"/>').join('');
  let inner = '<line x1="' + P + '" y1="' + (H - P) + '" x2="' + (W - P) +
    '" y2="' + (H - P) + '" stroke="#e5e9f0"/>';
  if (target) {
    inner += '<line x1="' + P + '" y1="' + y(target) + '" x2="' + (W - P) +
      '" y2="' + y(target) + '" stroke="#e67e22" stroke-dasharray="6 4"/>';
    inner += '<text x="' + (W - P) + '" y="' + (y(target) - 5) +
      '" fill="#e67e22" font-size="11" text-anchor="end">цель ' + target + ' кг</text>';
  }
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.innerHTML = inner +
    '<path d="' + path + '" fill="none" stroke="#2e86de" stroke-width="2"/>' + dots;
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDetail();
});""".replace("__DATA_JSON__", json.dumps({str(u["id"]): u for u in users},
                                          ensure_ascii=False))


def main() -> None:
    users = load_data()
    OUTPUT.write_text(build_html(users), encoding="utf-8")
    print(f"OK: {OUTPUT.resolve()} ({len(users)} польз.)")
    if "--open" in sys.argv:
        import os
        os.startfile(OUTPUT.resolve())


if __name__ == "__main__":
    main()