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
STATUS_RU = {
    "1H": "1-й тайм",
    "2H": "2-й тайм",
    "HT": "Перерыв",
    "FT": "Матч окончен",
    "ET": "Доп. время",
    "P": "Пенальти",
    "SUSP": "Приостановлен",
    "INT": "Перерыв",
    "LIVE": "Идёт матч",
}

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

NOTIFIED_MATCHES = set()

# ================= КЕШ И ПЕРЕВОД НА РУССКИЙ =================

CACHE = {
    "live_goals": {},       # match_id -> set(event_id)
    "scheduled": [],
    "last_live": 0,
    "last_scheduled": 0,
}

STATUS_RU = {
    "NS": "Матч скоро начнётся",
    "1H": "1 тайм",
    "2H": "2 тайм",
    "HT": "Перерыв",
    "FT": "Матч завершён",
    "ET": "Доп. время",
    "P": "Пенальти",
    "LIVE": "Идёт матч",
}

ROUND_RU = {
    "Regular Season": "Регулярный сезон",
    "Playoffs": "Плей-офф",
    "Group Stage": "Групповой этап",
}
# ================= ВСПОМОГАТЕЛЬНОЕ =================

async def send(bot, chat_id, text, reply_markup=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        pass  # пользователь мог заблокировать бота

# ================= API =================

def fetch_live():
    try:
        r = requests.get(
            f"{API_URL}/fixtures",
            headers=HEADERS,
            params={"status": "1H,HT,2H,ET,P"},
            timeout=5,
        )
        data = r.json().get("response", [])

        # 🔒 дополнительная защита
        return [m for m in data if m.get("fixture", {}).get("status", {}).get("elapsed")]

    except Exception as e:
        print("LIVE API ERROR:", e)
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
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ================= ГОЛЫ (100% ГАРАНТИЯ) =================

async def process_goals(context, live_matches):
    active_ids = set()

    for m in live_matches:
        fixture = m["fixture"]
        teams = m["teams"]
        goals = m["goals"]
        events = m["events"]

        match_id = fixture["id"]
        active_ids.add(match_id)

        CACHE["live_goals"].setdefault(match_id, set())

        for e in events:
            if e["type"] != "Goal":
                continue

            event_id = f'{match_id}_{e["time"]["elapsed"]}_{e["player"]["id"]}'

            if event_id in CACHE["live_goals"][match_id]:
                continue

            CACHE["live_goals"][match_id].add(event_id)

            minute = e["time"]["elapsed"]
            league = m["league"]
            league_name = f'{league["country"]} — {league["name"]}' if league.get("country") else league["name"]
            text = (
                "⚽ ГОООООЛ!\n"
                f"🏆 {league_name}\n"
                f'{teams["home"]["name"]} — {teams["away"]["name"]}\n'
                f'Счёт: {goals["home"]} : {goals["away"]}\n'
                f"⏱ {minute} мин"
            )

            for chat_id in LIVE_CHATS:
                await send(context.bot, chat_id, text)

            if 2 <= minute <= 11 or 69 <= minute <= 72:
                for chat_id in DM_CHATS:
                    await send(context.bot, chat_id, text)

    # 🧹 очистка завершённых матчей
    finished = set(CACHE["live_goals"]) - active_ids
    for mid in finished:
        del CACHE["live_goals"][mid]

# ================= НАПОМИНАНИЯ =================

async def process_upcoming(context):
    now = datetime.now(timezone.utc)

    for m in CACHE["scheduled"]:
        fixture = m["fixture"]
        teams = m["teams"]

        kickoff = datetime.fromisoformat(
            fixture["date"].replace("Z", "+00:00")
        )

        diff = (kickoff - now).total_seconds()

        if 9 * 60 <= diff <= 11 * 60 and fixture["id"] not in NOTIFIED_MATCHES:
            NOTIFIED_MATCHES.add(fixture["id"])

            text = (
                "⏰ Матч начнётся через 10 минут:\n"
                f'{teams["home"]["name"]} — {teams["away"]["name"]}'
            )

            for chat_id in STARTED_CHATS:
                await send(context.bot, chat_id, text, main_menu(chat_id))

# ================= JOB =================

async def main_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    if now - CACHE["last_live"] >= 20:
        live = fetch_live()
        await process_goals(context, live)
        CACHE["last_live"] = now

    if now - CACHE["last_scheduled"] >= 600:
        CACHE["scheduled"] = fetch_scheduled()
        CACHE["last_scheduled"] = now

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
    # ✅ ОБЯЗАТЕЛЬНАЯ защита
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

        matches = fetch_live()

        if not matches:
            await update.message.reply_text("⚠️ Сейчас нет LIVE матчей")
            return

        blocks = []
        for m in matches:
            league = m["league"]
            teams = m["teams"]
            goals = m["goals"]
            fixture = m["fixture"]
            status = fixture["status"]
            league_name = (
                f'{league["country"]} — {league["name"]}'
                if league.get("country")
                else league["name"]
            )

            elapsed = status.get("elapsed")
            status_ru = STATUS_RU.get(status.get("short"), "Идёт матч")
            time_text = f"{elapsed} мин" if elapsed else status_ru

            blocks.append(
                f"🏆 {league_name}\n"
                f'{teams["home"]["name"]} — {teams["away"]["name"]}\n'
                f'⚽ {goals["home"]}:{goals["away"]}   ⏱ {time_text}'
            )

        text_msg = "🔴 LIVE сейчас:\n\n" + "\n\n".join(blocks)

        if len(text_msg) > 4000:
            text_msg = text_msg[:4000] + "\n\n⚠️ Слишком много матчей"

        await update.message.reply_text(text_msg)

    elif text == "📅 Ближайшие матчи":
        if not CACHE["scheduled"]:
         await update.message.reply_text("⚠️ Нет данных о ближайших матчах")
         return

    blocks = []

    for m in CACHE["scheduled"][:5]:
        fixture = m["fixture"]
        teams = m["teams"]
        league = m["league"]

        utc = datetime.fromisoformat(
            fixture["date"].replace("Z", "+00:00")
        )
        msk = utc.astimezone(timezone(timedelta(hours=3)))

        league_name = (
            f'{league["country"]} — {league["name"]}'
            if league.get("country")
            else league["name"]
        )

        blocks.append(
            f"🏆 {league_name}\n"
            f'{teams["home"]["name"]} — {teams["away"]["name"]}\n'
            f"🕒 {msk:%d.%m %H:%M}"
        )

    await update.message.reply_text(
        "📅 Ближайшие матчи:\n\n" + "\n\n".join(blocks)
    )



async def error_handler(update, context):
    print("❌ BOT ERROR:", context.error)
# ================= ЗАПУСК (WEBHOOK) =================

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    app.add_error_handler(error_handler)
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
