import os
import logging
import json
from datetime import datetime
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai

from web import start_web_in_background

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
OPENAI_KEY  = os.getenv("OPENAI_API_KEY", "")
OWNER_ID    = int(os.getenv("OWNER_ID", "0"))
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBAPP_URL  = f"https://{RAILWAY_DOMAIN}" if RAILWAY_DOMAIN else os.getenv("WEBAPP_URL", "")
DATA_FILE   = Path("data.json")
MORNING_H   = int(os.getenv("MORNING_H", "7"))
MORNING_M   = int(os.getenv("MORNING_M", "0"))
EVENING_H   = int(os.getenv("EVENING_H", "20"))
EVENING_M   = int(os.getenv("EVENING_M", "0"))
TZ          = os.getenv("TZ", "Asia/Almaty")

MONTHS_S = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
DAYS_RU  = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']


def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def today_key():
    return datetime.now().strftime("%Y-%m-%d")

def get_tasks(data):
    return data.get("days", {}).get(today_key(), {}).get("tasks", [])

def set_tasks(data, tasks):
    data.setdefault("days", {}).setdefault(today_key(), {})["tasks"] = tasks
    save_data(data)

def add_task(data, text):
    tasks = get_tasks(data)
    t = {"id": int(datetime.now().timestamp()*1000) % 9999999,
         "text": text.strip(), "done": False,
         "added": datetime.now().strftime("%H:%M")}
    tasks.append(t)
    set_tasks(data, tasks)
    return t

def toggle_task(data, tid):
    tasks = get_tasks(data)
    for t in tasks:
        if t["id"] == tid:
            t["done"] = not t["done"]
            set_tasks(data, tasks)
            return t["done"]
    return False

def save_thought(data, text):
    data.setdefault("thoughts", []).insert(0, {
        "id": int(datetime.now().timestamp()),
        "text": text.strip(),
        "date": datetime.now().strftime("%d.%m.%Y %H:%M")
    })
    save_data(data)

def save_weight(data, w):
    data.setdefault("weight", {})[today_key()] = w
    save_data(data)


def main_kb():
    rows = [
        [KeyboardButton("📋 Задачи"), KeyboardButton("✅ Отметить")],
        [KeyboardButton("➕ Добавить задачу"), KeyboardButton("💭 Мысль в дневник")],
        [KeyboardButton("⚖️ Записать вес"), KeyboardButton("📊 Статистика")],
    ]
    if WEBAPP_URL:
        rows.append([KeyboardButton("🗂 Открыть планер", web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def tasks_kb(tasks):
    btns = []
    for t in tasks:
        icon = "✅" if t["done"] else "⬜"
        label = t["text"][:40] + ("…" if len(t["text"]) > 40 else "")
        btns.append([InlineKeyboardButton(f"{icon} {label}", callback_data=f"tog_{t['id']}")])
    btns.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh")])
    return InlineKeyboardMarkup(btns)

def progress_bar(done, total):
    pct = int(done / total * 100) if total else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    return bar, pct


async def transcribe(path):
    if not OPENAI_KEY:
        return ""
    try:
        client = openai.OpenAI(api_key=OPENAI_KEY)
        with open(path, "rb") as f:
            r = client.audio.transcriptions.create(model="whisper-1", file=f, language="ru")
        return r.text.strip()
    except Exception as e:
        logger.error(f"Whisper: {e}")
        return ""


async def cmd_start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = u.effective_user.first_name or "!"
    await u.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        "Я твой личный планер и дневник мыслей.\n\n"
        f"🌅 В 07:00 — спрошу задачи на день\n"
        f"🌙 В 20:00 — напомню отметить что сделала\n"
        "🎙 Голосовые — расшифрую и запишу\n"
        "💭 Мысли — собираются в книгу\n\n"
        "Поехали!",
        reply_markup=main_kb()
    )

async def show_tasks(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    tasks = get_tasks(data)
    if not tasks:
        await u.message.reply_text(
            "На сегодня задач пока нет.\n\nНапиши или отправь голосовое 🎙",
            reply_markup=main_kb()
        )
        return
    done = sum(1 for t in tasks if t["done"])
    bar, pct = progress_bar(done, len(tasks))
    await u.message.reply_text(
        f"📋 Сегодня: {done}/{len(tasks)}\n{bar} {pct}%\n\nНажми чтобы отметить:",
        reply_markup=tasks_kb(tasks)
    )

async def ask_add(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "add_task"
    await u.message.reply_text("Напиши задачу или отправь голосовое:")

async def ask_thought(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "thought"
    await u.message.reply_text("Говори или пиши — запишу в дневник 💭\nВсе мысли собираются в книгу.")

async def ask_weight(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = "weight"
    await u.message.reply_text("Напиши вес в кг (например: 62.5):")

async def show_stats(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    tasks = get_tasks(data)
    thoughts = data.get("thoughts", [])
    weights = data.get("weight", {})
    done = sum(1 for t in tasks if t["done"])
    today_w = weights.get(today_key(), "—")
    days = len(data.get("days", {}))
    diff_str = ""
    if len(weights) >= 2:
        sv = sorted(weights.items())
        diff = round(sv[-1][1] - sv[0][1], 1)
        diff_str = f"\nДинамика веса: {'+' if diff>0 else ''}{diff} кг за {len(sv)} дней"
    await u.message.reply_text(
        f"📊 Статистика\n\n"
        f"Задачи сегодня: {done}/{len(tasks)}\n"
        f"Дней в трекере: {days}\n"
        f"Записей в дневнике: {len(thoughts)}\n"
        f"Вес сегодня: {today_w} кг{diff_str}",
        reply_markup=main_kb()
    )

async def handle_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = u.message.text
    mode = ctx.user_data.get("mode", "")

    cmds = {
        "📋 Задачи": show_tasks,
        "✅ Отметить": show_tasks,
        "➕ Добавить задачу": ask_add,
        "💭 Мысль в дневник": ask_thought,
        "⚖️ Записать вес": ask_weight,
        "📊 Статистика": show_stats,
    }
    if text in cmds:
        await cmds[text](u, ctx)
        return

    data = load_data()

    if mode == "add_task":
        task = add_task(data, text)
        ctx.user_data["mode"] = ""
        await u.message.reply_text(f"✅ Добавила: «{task['text']}»", reply_markup=main_kb())

    elif mode == "thought":
        save_thought(data, text)
        ctx.user_data["mode"] = ""
        total = len(data.get("thoughts", []))
        await u.message.reply_text(f"💭 Записала. Всего в дневнике: {total}", reply_markup=main_kb())

    elif mode == "weight":
        try:
            w = float(text.replace(",", "."))
            save_weight(data, w)
            ctx.user_data["mode"] = ""
            await u.message.reply_text(f"⚖️ Записала: {w} кг", reply_markup=main_kb())
        except ValueError:
            await u.message.reply_text("Напиши число, например: 62.5")

    elif mode == "morning_tasks":
        lines = [l.strip().lstrip("—-•·1234567890.) ").strip()
                 for l in text.replace(";", "\n").split("\n") if l.strip()]
        added = [add_task(data, l)["text"] for l in lines[:5] if len(l) > 3]
        ctx.user_data["mode"] = ""
        if added:
            await u.message.reply_text(
                "Записала на сегодня:\n\n" + "\n".join(f"• {t}" for t in added) + "\n\nУдачного дня! 💪",
                reply_markup=main_kb()
            )
        else:
            await u.message.reply_text("Напиши задачи каждую на новой строке.")

    else:
        await u.message.reply_text("Выбери действие:", reply_markup=main_kb())

async def handle_voice(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not OPENAI_KEY:
        await u.message.reply_text("Голосовой ввод: добавь OPENAI_API_KEY в Railway Variables.")
        return
    msg = await u.message.reply_text("Слушаю... 🎧")
    voice = u.message.voice
    tg_file = await ctx.bot.get_file(voice.file_id)
    path = f"/tmp/v_{voice.file_id}.ogg"
    await tg_file.download_to_drive(path)
    transcript = await transcribe(path)
    Path(path).unlink(missing_ok=True)
    if not transcript:
        await msg.edit_text("Не удалось расшифровать. Попробуй ещё раз.")
        return
    await msg.edit_text(f"Услышала: «{transcript}»")
    mode = ctx.user_data.get("mode", "")
    data = load_data()
    if mode == "thought":
        save_thought(data, transcript)
        ctx.user_data["mode"] = ""
        await u.message.reply_text(f"💭 Записала в дневник. Всего: {len(data.get('thoughts',[]))}", reply_markup=main_kb())
    elif mode == "morning_tasks":
        parts = [p.strip() for p in transcript.replace(",", "\n").split("\n") if p.strip()]
        added = [add_task(data, p)["text"] for p in parts[:5] if len(p) > 3]
        ctx.user_data["mode"] = ""
        await u.message.reply_text(
            "Записала:\n\n" + "\n".join(f"• {t}" for t in added) + "\n\nУдачного дня! 💪",
            reply_markup=main_kb()
        )
    else:
        task = add_task(data, transcript)
        await u.message.reply_text(f"✅ Добавила: «{task['text']}»", reply_markup=main_kb())

async def handle_cb(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    data = load_data()
    if q.data.startswith("tog_"):
        toggle_task(data, int(q.data.split("_")[1]))
    tasks = get_tasks(data)
    done = sum(1 for t in tasks if t["done"])
    bar, pct = progress_bar(done, len(tasks))
    await q.edit_message_text(
        f"📋 Сегодня: {done}/{len(tasks)}\n{bar} {pct}%\n\nНажми чтобы отметить:",
        reply_markup=tasks_kb(tasks)
    )

async def morning_job(app):
    if not OWNER_ID:
        return
    now = datetime.now()
    app.user_data.setdefault(OWNER_ID, {})["mode"] = "morning_tasks"
    await app.bot.send_message(
        chat_id=OWNER_ID,
        text=(f"Доброе утро! ☀️\n{DAYS_RU[now.weekday()]}, "
              f"{now.day} {MONTHS_S[now.month-1]}\n\n"
              "Какие три важные задачи на сегодня?\n\n"
              "Напиши каждую на новой строке\nили отправь голосовое 🎙")
    )

async def evening_job(app):
    if not OWNER_ID:
        return
    data = load_data()
    tasks = get_tasks(data)
    done = sum(1 for t in tasks if t["done"])
    if not tasks:
        await app.bot.send_message(chat_id=OWNER_ID, text="Добрый вечер 🌙\nКак прошёл день?")
        return
    bar, pct = progress_bar(done, len(tasks))
    await app.bot.send_message(
        chat_id=OWNER_ID,
        text=f"Добрый вечер 🌙\n\nВыполнено: {done}/{len(tasks)}\n{bar} {pct}%\n\nОтметь что сделала:",
        reply_markup=tasks_kb(tasks)
    )

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан!")
        return

    start_web_in_background()
    logger.info(f"Веб-планер запущен | порт {os.getenv('PORT', 8080)}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CommandHandler("add", ask_add))
    app.add_handler(CommandHandler("thought", ask_thought))
    app.add_handler(CommandHandler("weight", ask_weight))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_cb))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(morning_job, "cron", hour=MORNING_H, minute=MORNING_M, args=[app])
    scheduler.add_job(evening_job, "cron", hour=EVENING_H, minute=EVENING_M, args=[app])
    scheduler.start()

    logger.info(f"Бот запущен | {MORNING_H:02d}:{MORNING_M:02d} утро | {EVENING_H:02d}:{EVENING_M:02d} вечер | {TZ}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
