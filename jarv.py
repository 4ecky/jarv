from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import requests
import time
from datetime import datetime, timezone, timedelta
import os

# ================= НАСТРОЙКИ =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

API_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

ADMIN_ID = 7024518865

# ================= СОСТОЯНИЯ =================

STARTED_CHATS = set()
LIVE_CHATS = set()
DM_CHATS = set()

# ================= КЕШ =================

CACHE = {
    "sent_goals": set(),      # уникальные goal_id
    "scheduled": [],
    "last_events": 0,
    "last_scheduled": 0,
}

# ================= ВСПОМОГАТЕЛЬНОЕ =================

async def safe_send(bot, chat_id, text, reply_markup=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        pass  # пользователь мог заблокировать бота

# ================= API =================

def fetch_live_events():
    try:
        r = requests.get(
            f"{API_URL}/fixtures/events",
            headers=HEADERS,
            params={"live": "all"},
            timeout=5,
        )
        return r.json().get("response", [])
    except Exception as e:
        print("LIVE EVENTS ERROR:", e)
        return []

def fetch_live_fixtures():
    try:
        r = requests.get(
            f"{API_URL}/fixtures",
            headers=HEADERS,
            params={"live": "all"},
            timeout=5,
        )
        return r.json().get("response", [])
    except Exception:
        return []

def fetch_scheduled():
    try:
        r = requests.get(
            f"{API_URL}/fixtures",
            headers=HEADERS,
            params={"next": 20},
            timeout=10,
        )
        return r.json().get("response", [])
    except Exception:
        return []

# ================= КЛАВИАТУРА =================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["📩 DM"],
            ["🔴 Сейчас"],
            ["📅 Ближайшие матчи"],
        ],
        resize_keyboard=True,
    )

# ================= ГОЛЫ =================

async def process_goals(context):
    events = fetch_live_events()
    print(f"✅ LIVE EVENTS FOUND: {len(events)}")

    for e in events:
        if e.get("type") != "Goal":
            continue

        fixture = e.get("fixture", {})
        league = e.get("league", {})
        teams = e.get("teams", {})
        goals = e.get("goals", {})
        time_info = e.get("time", {})

        match_id = fixture.get("id")
        minute = time_info.get("elapsed")

        goal_id = f"{match_id}_{minute}_{e.get('player', {}).get('id')}"

        if goal_id in CACHE["sent_goals"]:
            continue

        CACHE["sent_goals"].add(goal_id)

        text = (
            "⚽ ГОООООЛ!\n"
            f"{league.get('name', 'Лига')}\n"
            f"{teams.get('home', {}).get('name')} — {teams.get('away', {}).get('name')}\n"
            f"Счёт: {goals.get('home')} : {goals.get('away')}\n"
            f"⏱ {minute} мин"
        )

        for chat_id in LIVE_CHATS:
            await safe_send(context.bot, chat_id, text)

        if minute and (2 <= minute <= 11 or 69 <= minute <= 72):
            for chat_id in DM_CHATS:
                await safe_send(context.bot, chat_id, text)

# ================= НАПОМИНАНИЯ =================

async def process_upcoming(context):
    now = datetime.now(timezone.utc)

    for m in CACHE["scheduled"]:
        fixture = m.get("fixture", {})
        teams = m.get("teams", {})
        league = m.get("league", {})

        kickoff = datetime.fromisoformat(
            fixture["date"].replace("Z", "+00:00")
        )

        diff = (kickoff - now).total_seconds()

        if 9 * 60 <= diff <= 11 * 60:
            text = (
                "⏰ Матч начнётся через 10 минут:\n"
                f"{league.get('name')}\n"
                f"{teams['home']['name']} — {teams['away']['name']}"
            )

            for chat_id in STARTED_CHATS:
                await safe_send(context.bot, chat_id, text, main_menu())

# ================= JOB =================

async def main_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    if now - CACHE["last_events"] >= 20:
        await process_goals(context)
        CACHE["last_events"] = now

    if now - CACHE["last_scheduled"] >= 600:
        CACHE["scheduled"] = fetch_scheduled()
        CACHE["last_scheduled"] = now

    await process_upcoming(context)

# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STARTED_CHATS.add(chat_id)

    await safe_send(
        context.bot,
        chat_id,
        "👋 Бот запущен",
        main_menu(),
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STARTED_CHATS.discard(chat_id)
    LIVE_CHATS.discard(chat_id)
    DM_CHATS.discard(chat_id)

    await update.message.reply_text(
        "⛔ Бот остановлен",
        reply_markup=ReplyKeyboardRemove(),
    )

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    text = update.message.text

    if text == "📩 DM":
        DM_CHATS.add(chat_id)
        LIVE_CHATS.discard(chat_id)
        await update.message.reply_text("📩 DM включён")

    elif text == "🔴 Сейчас":
        LIVE_CHATS.add(chat_id)
        fixtures = fetch_live_fixtures()

        if not fixtures:
            await update.message.reply_text("⚠️ Сейчас нет LIVE матчей")
            return

        blocks = []
        for m in fixtures:
            league = m["league"]["name"]
            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            goals = m["goals"]
            minute = m["fixture"]["status"].get("elapsed", "?")

            blocks.append(
                f"{league}\n{home} — {away}\n"
                f"{goals['home']}:{goals['away']} ⏱ {minute} мин"
            )

        msg = "🔴 LIVE сейчас:\n\n" + "\n\n".join(blocks)

        if len(msg) > 4000:
            msg = msg[:4000] + "\n\n⚠️ Слишком много матчей"

        await update.message.reply_text(msg)

    elif text == "📅 Ближайшие матчи":
        if not CACHE["scheduled"]:
            await update.message.reply_text("⚠️ Нет данных о ближайших матчах")
            return

        blocks = []
        for m in CACHE["scheduled"][:5]:
            utc = datetime.fromisoformat(
                m["fixture"]["date"].replace("Z", "+00:00")
            )
            msk = utc.astimezone(timezone(timedelta(hours=3)))

            blocks.append(
                f'{m["league"]["name"]}\n'
                f'{m["teams"]["home"]["name"]} — {m["teams"]["away"]["name"]}\n'
                f"🕒 {msk:%d.%m %H:%M}"
            )

        await update.message.reply_text(
            "📅 Ближайшие матчи:\n\n" + "\n\n".join(blocks)
        )

# ================= ЗАПУСК (WEBHOOK) =================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    app.job_queue.run_repeating(main_job, interval=20, first=5)

    print("✅ Бот запущен (WEBHOOK)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
    )

if __name__ == "__main__":
    main()
