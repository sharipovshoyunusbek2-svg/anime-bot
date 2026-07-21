import asyncio
import sqlite3
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ===== SOZLAMALAR =====
TOKEN = "8935853087:AAFTDXc2jP542PgYgRhebXH5_00MvOIIF1Q"
ADMIN_IDS = []  # Hozircha bo'sh — bot ishlagach /myid yozing

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ===== BAZA =====
def db():
    return sqlite3.connect("anx.db")

def setup_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS animelar (
                id INTEGER PRIMARY KEY,
                nom TEXT NOT NULL,
                banner_file_id TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS qismlar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_id INTEGER,
                qism INTEGER,
                file_id TEXT,
                FOREIGN KEY(anime_id) REFERENCES animelar(id)
            )
        """)
        con.commit()

def is_admin(user_id):
    if not ADMIN_IDS:
        return True  # Admin ro'yxati bo'sh bo'lsa hammaga ruxsat (sozlash uchun)
    return user_id in ADMIN_IDS

# ===== FSM STATES =====
class AddEp(StatesGroup):
    anime_id = State()
    waiting_file = State()

class AddBanner(StatesGroup):
    anime_id = State()
    waiting_file = State()

# ===== FOYDALANUVCHI: raqam yozsa =====
@dp.message(F.text.regexp(r'^\d+$'))
async def send_anime(message: types.Message):
    anime_id = int(message.text)
    with db() as con:
        anime = con.execute(
            "SELECT id, nom, banner_file_id FROM animelar WHERE id=?", (anime_id,)
        ).fetchone()

    if not anime:
        await message.answer(f"❌ {anime_id}-raqamli anime topilmadi.")
        return

    _, nom, banner = anime

    with db() as con:
        qismlar = con.execute(
            "SELECT qism, file_id FROM qismlar WHERE anime_id=? ORDER BY qism",
            (anime_id,)
        ).fetchall()

    if not qismlar:
        await message.answer(f"🎌 <b>{nom}</b>\n\n⚠️ Hali qismlar qo'shilmagan.", parse_mode="HTML")
        return

    await message.answer(
        f"🎌 <b>{nom}</b>\n📺 {len(qismlar)} ta qism yuborilmoqda...",
        parse_mode="HTML"
    )

    # Banner
    if banner:
        await message.answer_photo(banner)
        await asyncio.sleep(0.3)

    # Qismlar
    for qism, file_id in qismlar:
        try:
            await message.answer_document(file_id)
            await asyncio.sleep(0.5)
        except Exception as e:
            await message.answer(f"⚠️ {qism}-qism yuborishda xato.")

# ===== /start =====
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🎌 <b>ANX MEDIA Bot</b>\n\n"
        "Anime ID raqamini yozing, barcha qismlar yuboriladi!\n\n"
        "Masalan: <code>1</code> yoki <code>25</code>",
        parse_mode="HTML"
    )

# ===== /myid =====
@dp.message(Command("myid"))
async def myid(message: types.Message):
    await message.answer(f"🆔 Sizning ID: <code>{message.from_user.id}</code>", parse_mode="HTML")

# ===== /animelist =====
@dp.message(Command("animelist"))
async def animelist(message: types.Message):
    with db() as con:
        animelar = con.execute("SELECT id, nom FROM animelar ORDER BY id").fetchall()
    if not animelar:
        await message.answer("📭 Hali anime qo'shilmagan.")
        return
    matn = "🎌 <b>Anime ro'yxati:</b>\n\n"
    for aid, nom in animelar:
        matn += f"<code>{aid}</code> — {nom}\n"
    await message.answer(matn, parse_mode="HTML")

# ===== ADMIN: /addanime =====
@dp.message(Command("addanime"))
async def addanime(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❗ Format: /addanime [id] [nom]\nMasalan: /addanime 25 Naruto")
        return
    anime_id, nom = int(args[1]), args[2]
    with db() as con:
        try:
            con.execute("INSERT INTO animelar (id, nom) VALUES (?, ?)", (anime_id, nom))
            con.commit()
            await message.answer(f"✅ <b>{nom}</b> ({anime_id}) qo'shildi!", parse_mode="HTML")
        except sqlite3.IntegrityError:
            await message.answer(f"⚠️ {anime_id}-raqam allaqachon mavjud.")

# ===== ADMIN: /addep =====
@dp.message(Command("addep"))
async def addep_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❗ Format: /addep [anime_id]\nMasalan: /addep 25")
        return
    anime_id = int(args[1])
    with db() as con:
        anime = con.execute("SELECT nom FROM animelar WHERE id=?", (anime_id,)).fetchone()
    if not anime:
        await message.answer(f"❌ {anime_id}-raqamli anime topilmadi. Avval /addanime qiling.")
        return
    await state.set_state(AddEp.waiting_file)
    await state.update_data(anime_id=anime_id)
    await message.answer(f"📁 <b>{anime[0]}</b> uchun faylni yuboring yoki forward qiling:", parse_mode="HTML")

@dp.message(AddEp.waiting_file, F.document | F.video)
async def addep_file(message: types.Message, state: FSMContext):
    data = await state.get_data()
    anime_id = data["anime_id"]
    file_id = message.document.file_id if message.document else message.video.file_id
    with db() as con:
        qism_count = con.execute(
            "SELECT COUNT(*) FROM qismlar WHERE anime_id=?", (anime_id,)
        ).fetchone()[0]
        yangi_qism = qism_count + 1
        con.execute(
            "INSERT INTO qismlar (anime_id, qism, file_id) VALUES (?, ?, ?)",
            (anime_id, yangi_qism, file_id)
        )
        con.commit()
    await state.clear()
    await message.answer(f"✅ {yangi_qism}-qism saqlandi! Yana qo'shish uchun /addep {anime_id}")

# ===== ADMIN: /addbanner =====
@dp.message(Command("addbanner"))
async def addbanner_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❗ Format: /addbanner [anime_id]\nMasalan: /addbanner 25")
        return
    anime_id = int(args[1])
    with db() as con:
        anime = con.execute("SELECT nom FROM animelar WHERE id=?", (anime_id,)).fetchone()
    if not anime:
        await message.answer(f"❌ {anime_id}-raqamli anime topilmadi.")
        return
    await state.set_state(AddBanner.waiting_file)
    await state.update_data(anime_id=anime_id)
    await message.answer(f"🖼️ <b>{anime[0]}</b> uchun banner rasmini yuboring:", parse_mode="HTML")

@dp.message(AddBanner.waiting_file, F.photo)
async def addbanner_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    anime_id = data["anime_id"]
    file_id = message.photo[-1].file_id
    with db() as con:
        con.execute("UPDATE animelar SET banner_file_id=? WHERE id=?", (file_id, anime_id))
        con.commit()
    await state.clear()
    await message.answer(f"✅ Banner saqlandi!")

# ===== ADMIN: /delanime =====
@dp.message(Command("delanime"))
async def delanime(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❗ Format: /delanime [id]")
        return
    anime_id = int(args[1])
    with db() as con:
        con.execute("DELETE FROM qismlar WHERE anime_id=?", (anime_id,))
        con.execute("DELETE FROM animelar WHERE id=?", (anime_id,))
        con.commit()
    await message.answer(f"🗑️ {anime_id}-anime o'chirildi.")

# ===== ISHGA TUSHIRISH =====
async def main():
    setup_db()
    print("✅ ANX MEDIA Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
