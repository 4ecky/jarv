from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import requests
import os
import time
from datetime import datetime, timezone, timedelta

# ================= НАСТРОЙКИ =================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

API_URL = "https://api.football-data.org/v4/matches"
HEADERS = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}

ADMIN_ID = 7024518865  # твой ID

# ================= СОСТОЯНИЯ =================

STARTED_CHATS = set()
LIVE_CHATS = set()
DM_CHATS = set()

NOTIFIED_MATCHES = set()

# ================= КЕШ =================

CACHE = {
    "live": {},                 # match_id -> {"hg": int, "ag": int}
    "scheduled": [],
    "last_live_update": 0,
    "last_scheduled_update": 0,
}

# ================= ВСПОМОГАТЕЛЬНОЕ =================

async def send(bot, chat_id, text, reply_markup=None):
    await bot.send_message(chat_id, text, reply_markup=reply_markup)

# ================= API =================

def fetch_live():
    try:
        r = requests.get(
            API_URL,
            headers=HEADERS,
            params={"status": "LIVE"},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print("LIVE API ERROR:", e)
        return []

def fetch_scheduled():
    try:
        r = requests.get(
            API_URL,
            headers=HEADERS,
            params={"status": "SCHEDULED"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print("SCHEDULED API ERROR:", e)
        return []

# ================= КЛАВИАТУРА =================

def main_menu(chat_id):
    keyboard = [
        ["📩 DM"],
        ["🔴 Сейчас"],
        ["📅 Ближайшие матчи"],
    ]

    if chat_id == ADMIN_ID:
        keyboard.append(["🧪 Test goal"])

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ================= ГОЛЫ (100% ГАРАНТИЯ) =================

async def process_goals(context, live_matches):
    current_ids = set()

    for m in live_matches:
        match_id = m["id"]
        current_ids.add(match_id)

        score = m.get("score", {}).get("fullTime", {})
        hg = score.get("home")
        ag = score.get("away")

        minute_raw = m.get("minute")
        minute = int(minute_raw) if isinstance(minute_raw, int) else None

        if hg is None or ag is None:
            continue

        last = CACHE["live"].get(match_id, {"hg": hg, "ag": ag})

        # догенерация голов
        for _ in range(hg - last["hg"]):
            await notify_goal(context, m, minute)

        for _ in range(ag - last["ag"]):
            await notify_goal(context, m, minute)

        CACHE["live"][match_id] = {"hg": hg, "ag": ag}

    # 🧹 автоочистка завершённых матчей
    finished = set(CACHE["live"].keys()) - current_ids
    for mid in finished:
        del CACHE["live"][mid]

async def notify_goal(context, match, minute):
    text = (
        "⚽ ГООООЛ!\n"
        f"{match['homeTeam']['name']} "
        f"{match['score']['fullTime']['home']} : "
        f"{match['score']['fullTime']['away']} "
        f"{match['awayTeam']['name']}\n"
        f"⏱ {minute if minute else '?'} мин"
    )

    # 🔴 LIVE — всегда
    for chat_id in LIVE_CHATS:
        await send(context.bot, chat_id, text)

    # 📩 DM — фильтр минут
    if minute and (2 <= minute <= 11 or 69 <= minute <= 72):
        for chat_id in DM_CHATS:
            await send(context.bot, chat_id, text)

# ================= НАПОМИНАНИЯ =================

async def process_upcoming(context):
    now = datetime.now(timezone.utc)
    blocks = []

    for m in CACHE["scheduled"]:
        kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        diff = (kickoff - now).total_seconds()

        if 9 * 60 <= diff <= 11 * 60 and m["id"] not in NOTIFIED_MATCHES:
            blocks.append(f"{m['homeTeam']['name']} — {m['awayTeam']['name']}")
            NOTIFIED_MATCHES.add(m["id"])

    if blocks:
        text = "⏰ Матчи начнутся через 10 минут:\n\n" + "\n".join(blocks)
        for chat_id in STARTED_CHATS:
            await send(context.bot, chat_id, text, main_menu(chat_id))

# ================= ЕДИНСТВЕННЫЙ JOB =================

async def main_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    if now - CACHE["last_live_update"] >= 30:
        live = fetch_live()
        await process_goals(context, live)
        CACHE["last_live_update"] = now

    if now - CACHE["last_scheduled_update"] >= 600:
        CACHE["scheduled"] = fetch_scheduled()
        CACHE["last_scheduled_update"] = now

    await process_upcoming(context)

# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STARTED_CHATS.add(chat_id)

    await send(
        context.bot,
        chat_id,
        "👋 Бот запущен",
        main_menu(chat_id),
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
    chat_id = update.effective_chat.id
    text = update.message.text

    if text == "📩 DM":
        DM_CHATS.add(chat_id)
        LIVE_CHATS.discard(chat_id)
        await update.message.reply_text("📩 DM включён")

    elif text == "🔴 Сейчас":
        LIVE_CHATS.add(chat_id)
        matches = fetch_live()

        if not matches:
            await update.message.reply_text("⚠️ Сейчас нет LIVE матчей")
            return

        blocks = [
            f"{m['homeTeam']['name']} — {m['awayTeam']['name']}\n"
            f"{m['score']['fullTime']['home']}:{m['score']['fullTime']['away']} "
            f"⏱ {m.get('minute','?')} мин"
            for m in matches
        ]

        await update.message.reply_text("🔴 LIVE сейчас:\n\n" + "\n\n".join(blocks))

    elif text == "📅 Ближайшие матчи":
        blocks = []
        for m in CACHE["scheduled"][:5]:
            utc = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
            msk = utc.astimezone(timezone(timedelta(hours=3)))
            blocks.append(
                f"{m['homeTeam']['name']} — {m['awayTeam']['name']}\n"
                f"🕒 {msk:%d.%m %H:%M}"
            )

        await update.message.reply_text("📅 Ближайшие матчи:\n\n" + "\n\n".join(blocks))

    elif text == "🧪 Test goal" and chat_id == ADMIN_ID:
        fake = {
            "id": 999,
            "homeTeam": {"name": "Test FC"},
            "awayTeam": {"name": "Mock United"},
            "score": {"fullTime": {"home": 1, "away": 0}},
            "minute": 90,
        }
        await process_goals(context, [fake])

# ================= ЗАПУСК =================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    app.job_queue.run_repeating(main_job, interval=30, first=5)

    print("✅ Бот запущен (WEBHOOK)")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=WEBHOOK_URL,
    )

if __name__ == "__main__":
    main()

