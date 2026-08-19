"""
ANX MEDIA — Telegram Bot
Admin: /admin -> parol -> Anime qo'shish / Tahrirlash
Foydalanuvchi: /start -> anime ID yuboradi -> post + epizod tugmalari
"""

import os
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Any, Optional

import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("anx_bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
DATABASE_URL = os.environ["DATABASE_URL"]

EPISODES_PER_PAGE = 12

# Har bir admin uchun alohida holat (bir nechta admin bir vaqtda ishlashi mumkin)
admin_sessions: Dict[int, Dict[str, Any]] = {}

db_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return db_pool


# ---------------- DB yordamchilari ----------------

async def db_save_anime(anime_id, title, banner_file_id, description, episodes):
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO bot_anime (id, title, banner_file_id, description, episodes)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            banner_file_id = EXCLUDED.banner_file_id,
            description = EXCLUDED.description,
            episodes = EXCLUDED.episodes
        """,
        anime_id, title, banner_file_id, description, json.dumps(episodes),
    )


async def db_get_anime(anime_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM bot_anime WHERE id = $1", anime_id)
    if not row:
        return None
    episodes = row["episodes"]
    if isinstance(episodes, str):
        episodes = json.loads(episodes)
    return {
        "id": row["id"],
        "title": row["title"],
        "banner_file_id": row["banner_file_id"],
        "description": row["description"],
        "episodes": episodes or [],
    }


# ---------------- Admin oqimi ----------------

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_sessions[user_id] = {"stage": "await_password"}
    await update.message.reply_text("🔐 Parol kiriting:")


async def tahrirlash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if not session or not session.get("authenticated"):
        await update.message.reply_text("Avval /admin orqali kiring.")
        return
    session["stage"] = "await_edit_id"
    session["data"] = {}
    await update.message.reply_text("Tahrirlash uchun anime ID kiriting:")


async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if not session or session.get("stage") != "collecting_episodes":
        await update.message.reply_text("Saqlash uchun avval anime ma'lumotlarini kiriting.")
        return

    data = session["data"]
    if not data.get("id") or not data.get("title"):
        await update.message.reply_text("❌ Anime ID yoki post ma'lumoti yetarli emas.")
        return

    await db_save_anime(
        data["id"], data["title"], data.get("banner_file_id"),
        data.get("description", ""), data.get("episodes", []),
    )
    count = len(data.get("episodes", []))
    await update.message.reply_text(f"✅ Saqlandi! Anime ID: {data['id']}, {count} ta epizod bilan.")
    admin_sessions[user_id] = {"stage": "idle", "authenticated": True}


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if not session:
        return

    stage = session.get("stage")
    text = (update.message.text or "").strip()

    if stage == "await_password":
        if text == ADMIN_PASSWORD:
            session["authenticated"] = True
            session["stage"] = "idle"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Anime qo'shish", callback_data="admin:add")],
                [InlineKeyboardButton("✏️ Tahrirlash", callback_data="admin:edit")],
            ])
            await update.message.reply_text("✅ Xush kelibsiz, admin!", reply_markup=keyboard)
        else:
            await update.message.reply_text("❌ Noto'g'ri parol. Qayta urinib ko'ring:")
        return

    if stage == "await_id":
        if not text.isdigit():
            await update.message.reply_text("Iltimos, faqat raqam kiriting:")
            return
        session["data"]["id"] = int(text)
        session["data"]["episodes"] = []
        session["stage"] = "collecting_episodes"
        await update.message.reply_text(
            "✅ ID belgilandi. Endi video fayllarni ketma-ket forward qiling.\n"
            "Tugatgach /save deb yozing."
        )
        return

    if stage == "await_edit_id":
        if not text.isdigit():
            await update.message.reply_text("Iltimos, faqat raqam kiriting:")
            return
        anime_id = int(text)
        anime = await db_get_anime(anime_id)
        if not anime:
            await update.message.reply_text("Bunday ID topilmadi. Qayta kiriting:")
            return
        session["data"] = dict(anime)
        session["stage"] = "collecting_episodes"
        ep_count = len(anime["episodes"])
        await update.message.reply_text(
            f"'{anime['title']}' (ID {anime_id}) yuklandi, {ep_count} ta epizod bor.\n\n"
            "— Yangi post yuborsangiz, banner/tavsif yangilanadi\n"
            "— Video forward qilsangiz, yangi epizod qo'shiladi\n"
            "— Tugatgach /save deb yozing"
        )
        return


async def admin_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if not session or not session.get("authenticated"):
        return
    stage = session.get("stage")
    if stage not in ("await_post", "collecting_episodes"):
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ""
    title = caption.split("\n")[0][:100] if caption else session.get("data", {}).get("title", "Nomsiz")

    if stage == "await_post":
        session["data"] = {"banner_file_id": photo.file_id, "description": caption, "title": title}
        session["stage"] = "await_id"
        await update.message.reply_text("📌 Post qabul qilindi. Endi anime ID raqamini kiriting:")
    else:
        session["data"]["banner_file_id"] = photo.file_id
        session["data"]["description"] = caption
        session["data"]["title"] = title
        await update.message.reply_text("✅ Post yangilandi.")


async def admin_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if not session or not session.get("authenticated") or session.get("stage") != "collecting_episodes":
        return
    video = update.message.video or update.message.document
    if not video:
        return
    episodes = session["data"].setdefault("episodes", [])
    label = f"{len(episodes) + 1}-qism"
    episodes.append({"label": label, "file_id": video.file_id})
    await update.message.reply_text(f"🎬 {label} qabul qilindi. (Jami: {len(episodes)})")


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    session = admin_sessions.get(user_id)
    await query.answer()
    if not session or not session.get("authenticated"):
        return

    if query.data == "admin:add":
        session["stage"] = "await_post"
        session["data"] = {}
        await query.message.reply_text("📸 Anime posti (rasm + matn) tashlang:")
    elif query.data == "admin:edit":
        session["stage"] = "await_edit_id"
        session["data"] = {}
        await query.message.reply_text("Tahrirlash uchun anime ID kiriting:")


# ---------------- Foydalanuvchi oqimi ----------------

def build_episode_keyboard(anime_id: int, episodes: list, page: int = 0) -> InlineKeyboardMarkup:
    total = len(episodes)
    start = page * EPISODES_PER_PAGE
    end = min(start + EPISODES_PER_PAGE, total)

    buttons, row = [], []
    for i in range(start, end):
        row.append(InlineKeyboardButton(episodes[i]["label"], callback_data=f"ep:{anime_id}:{i}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page:{anime_id}:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"page:{anime_id}:{page + 1}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Assalomu alaykum! ANX MEDIA botiga xush kelibsiz.\n\n"
        "Anime ko'rish uchun uning ID raqamini yuboring (masalan: 1).\n"
        "ID raqamlarini @ANX_MEDIA_UZBEKISTAN kanalidagi postlardan topasiz."
    )


async def user_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.isdigit():
        await update.message.reply_text("Iltimos, anime ID raqamini yuboring (masalan: 1).")
        return

    anime = await db_get_anime(int(text))
    if not anime:
        await update.message.reply_text("Bunday ID topilmadi.")
        return

    keyboard = build_episode_keyboard(anime["id"], anime["episodes"], page=0)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=anime["banner_file_id"],
        caption=anime["description"],
        reply_markup=keyboard,
    )


async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    if parts[0] == "page":
        anime_id, page = int(parts[1]), int(parts[2])
        anime = await db_get_anime(anime_id)
        if not anime:
            return
        keyboard = build_episode_keyboard(anime_id, anime["episodes"], page)
        await query.message.edit_reply_markup(reply_markup=keyboard)

    elif parts[0] == "ep":
        anime_id, ep_index = int(parts[1]), int(parts[2])
        anime = await db_get_anime(anime_id)
        if not anime or ep_index >= len(anime["episodes"]):
            return
        episode = anime["episodes"][ep_index]
        await context.bot.send_video(
            chat_id=query.message.chat_id,
            video=episode["file_id"],
            caption=f"🎬 ANX MEDIA — @ANX_MEDIA_UZBEKISTAN\n{episode['label']}",
            reply_to_message_id=query.message.message_id,
        )


# ---------------- Render uchun soxta veb-server ----------------
# Render "Web Service" turida faqat biror portni tinglayotgan xizmatni bepul tarifga qo'yadi.
# Bot o'zi hech qanday tashqi so'rov qabul qilmaydi (faqat Telegram bilan gaplashadi),
# shuning uchun bu server hech narsa qilmaydi — faqat "men tiriman" deb javob beradi.

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ANX MEDIA bot ishlab turibdi")

    def log_message(self, format, *args):
        pass  # konsolni ortiqcha log bilan to'ldirmaslik uchun


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info(f"Health server {port}-portda ishga tushdi")
    server.serve_forever()


# ---------------- Router / ishga tushirish ----------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = admin_sessions.get(user_id)
    if session and session.get("stage") not in (None, "idle"):
        await admin_text_handler(update, context)
    else:
        await user_text_handler(update, context)


def main():
    # Soxta HTTP server'ni alohida oqimda (thread) ishga tushiramiz,
    # shu bilan bir vaqtda asosiy bot polling orqali ishlayveradi.
    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("tahrirlash", tahrirlash_cmd))
    app.add_handler(CommandHandler("save", save_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, admin_video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(user_callback_handler, pattern="^(page|ep):"))

    logger.info("Bot ishga tushdi (polling)")
    app.run_polling()


if __name__ == "__main__":
    main()
