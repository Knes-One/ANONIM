import asyncio
import logging
import sqlite3
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

BOT_TOKEN = "8418914245:AAFW75sr7QIiclam65DeIJYkF09c0-_EN9A"
ADMIN_ID = 5206303057

MESSAGES_PER_SESSION = 5
ANTISPAM_WINDOW = 15
ANTISPAM_MAX_IN_WINDOW = 3
ANTISPAM_BLOCK_SECONDS = 600


EMOJI_START = "5886436057091673541"    # ✉️ /start
EMOJI_BUTTON = "5875465628285931233"   # ☑️ Отправить
EMOJI_SENT = "5825794181183836432"     # ☑️ 
EMOJI_TOOMANY = "5872829476143894491"  # ⏳ 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
DB_PATH = "bot.db"


def tg_emoji(emoji_id: str, fallback: str) -> str:
    """HTML-тег премиум-эмодзи. Если клиент не поддерживает - покажет fallback."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def db_init() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                admin_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                sent_count INTEGER NOT NULL DEFAULT 0,
                window_start REAL NOT NULL DEFAULT 0,
                window_count INTEGER NOT NULL DEFAULT 0,
                blocked_until REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending (
                button_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        conn.commit()


def db_save_link(admin_message_id: int, user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO links (admin_message_id, user_id) VALUES (?, ?)",
            (admin_message_id, user_id),
        )
        conn.commit()


def db_get_user(admin_message_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT user_id FROM links WHERE admin_message_id = ?",
            (admin_message_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def db_get_session(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT sent_count, window_start, window_count, blocked_until "
            "FROM sessions WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO sessions (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return 0, 0.0, 0, 0.0
        return row


def db_update_session(
    user_id: int,
    sent_count: int,
    window_start: float,
    window_count: int,
    blocked_until: float,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO sessions (user_id, sent_count, window_start, window_count, blocked_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                sent_count = excluded.sent_count,
                window_start = excluded.window_start,
                window_count = excluded.window_count,
                blocked_until = excluded.blocked_until
            """,
            (user_id, sent_count, window_start, window_count, blocked_until),
        )
        conn.commit()


def db_save_pending(button_message_id: int, user_id: int, text: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending (button_message_id, user_id, text) VALUES (?, ?, ?)",
            (button_message_id, user_id, text),
        )
        conn.commit()


def db_take_pending(button_message_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT user_id, text FROM pending WHERE button_message_id = ?",
            (button_message_id,),
        )
        row = cur.fetchone()
        if row:
            conn.execute(
                "DELETE FROM pending WHERE button_message_id = ?",
                (button_message_id,),
            )
            conn.commit()
        return row


def send_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data="send_msg",
                    icon_custom_emoji_id=EMOJI_BUTTON,
                )
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"{tg_emoji(EMOJI_START, '✉️')} Напиши сообщение - передам его анонимно"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Напиши текст - я передам его анонимно\n"
        "Ответ придёт сюда же"
    )


@router.message(F.text & ~F.text.startswith("/"))
async def on_user_text(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    now = time.time()

    sent_count, window_start, window_count, blocked_until = db_get_session(user_id)

    if blocked_until > now:
        remain = int(blocked_until - now)
        minutes = remain // 60
        seconds = remain % 60
        await message.answer(
            f"{tg_emoji(EMOJI_TOOMANY, '⏳')} Слишком много сообщений\n"
            f"Попробуй позже - {minutes}м {seconds}с"
        )
        return

    if sent_count >= MESSAGES_PER_SESSION:
        await message.answer(
            f"{tg_emoji(EMOJI_SENT, '☑️')} Лимит на этот цикл исчерпан\n"
            f"Напиши /start чтобы начать заново"
        )
        return

    if now - window_start > ANTISPAM_WINDOW:
        window_start = now
        window_count = 0

    window_count += 1

    if window_count > ANTISPAM_MAX_IN_WINDOW:
        blocked_until = now + ANTISPAM_BLOCK_SECONDS
        db_update_session(user_id, sent_count, window_start, window_count, blocked_until)
        await message.answer(
            f"{tg_emoji(EMOJI_TOOMANY, '⏳')} Слишком много сообщений\nПопробуй позже - 10м"
        )
        return

    db_update_session(user_id, sent_count, window_start, window_count, blocked_until)

    text = message.text.strip()
    sent = await message.answer(
        "Нажми кнопку ниже чтобы отправить анонимно",
        reply_markup=send_keyboard(),
    )
    db_save_pending(sent.message_id, user_id, text)


@router.callback_query(F.data == "send_msg")
async def on_send_callback(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    button_message_id = callback.message.message_id

    pending = db_take_pending(button_message_id)
    if pending is None:
        await callback.answer("Уже отправлено или устарело", show_alert=True)
        return

    owner_id, text = pending
    if owner_id != user_id:
        await callback.answer("Это не ваша кнопка", show_alert=True)
        return

    text = text.strip()
    if not text:
        await callback.answer("Пустое сообщение", show_alert=True)
        return

    now = time.time()
    sent_count, window_start, window_count, blocked_until = db_get_session(user_id)

    if blocked_until > now:
        await callback.answer("Слишком много сообщений, попробуй позже", show_alert=True)
        return

    if sent_count >= MESSAGES_PER_SESSION:
        await callback.answer("Лимит исчерпан, напиши /start", show_alert=True)
        return

    try:
        sent = await bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logger.exception("Не удалось отправить админу: %s", e)
        await callback.answer("Ошибка отправки", show_alert=True)
        return

    db_save_link(sent.message_id, user_id)

    sent_count += 1
    db_update_session(user_id, sent_count, window_start, window_count, blocked_until)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Подтверждение пользователю с премиум-галочкой
    await callback.message.answer(
        f"{tg_emoji(EMOJI_SENT, '☑️')} Сообщение отправлено"
    )

    await callback.answer()


@router.message(F.reply_to_message)
async def on_admin_reply(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    if message.from_user.id != ADMIN_ID:
        return

    replied = message.reply_to_message
    if replied is None:
        return

    target_user_id = db_get_user(replied.message_id)
    if target_user_id is None:
        await message.answer("Связь потеряна - ответить нельзя")
        return

    reply_text = message.text or message.caption
    if not reply_text:
        await message.answer("Пустой ответ")
        return

    try:
        await bot.send_message(chat_id=target_user_id, text=reply_text)
    except Exception as e:
        logger.exception("Не удалось отправить ответ пользователю: %s", e)
        await message.answer("Не удалось отправить ответ")
        return

    await message.answer(
        f"{tg_emoji(EMOJI_SENT, '☑️')} Ответ отправлен"
    )


async def main() -> None:
    db_init()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")