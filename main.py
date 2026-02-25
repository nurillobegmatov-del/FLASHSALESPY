# main.py
"""
Asosiy bot fayli — barcha qismlarni birlashtiradi.

O'rnatish:
    pip install aiogram apscheduler aiosqlite python-dotenv

.env fayli (faqat 2 ta qiymat!):
    BOT_TOKEN=...
    ADMIN_ID=...

Guruhni sozlash tartibi:
    1. Botni savdo guruhingizga admin qilib qo'shing
    2. Bot o'zi adminga xabar yuboradi va "Shu guruhni tanlash" tugmasi chiqadi
    3. Admin tugmani bosadi — tamom, shaxsiy chatdan!
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config, load_group_chat_id, save_group_chat_id
from flash_sale import router as flash_router
from scheduler import on_shutdown, on_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  Setup router
# ════════════════════════════════════════════════════════════

setup_router = Router(name="setup")


def is_admin(obj: Message | CallbackQuery) -> bool:
    return obj.from_user.id == config.admin_id


# ── Bot guruhga qo'shilganda → adminga inline tugma ─────

@setup_router.my_chat_member()
async def on_bot_added_to_group(event: ChatMemberUpdated, bot: Bot) -> None:
    """
    Bot guruhga qo'shilganda (yoki admin bo'lganda) ishga tushadi.
    Admin shaxsiy chatiga "Shu guruhni tanlash" tugmasi bilan xabar yuboradi.
    Hamma narsa botda — guruhda hech narsa yozilmaydi.
    """
    new_status = event.new_chat_member.status
    chat       = event.chat

    # Faqat group/supergroup
    if chat.type not in ("group", "supergroup"):
        return

    # Qo'shildi yoki admin bo'ldi
    if new_status not in ("administrator", "member"):
        return

    logger.info("Bot guruhga qo'shildi: '%s' (%d)", chat.title, chat.id)

    # Adminga inline tugmali xabar
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"✅ '{chat.title}' ni tanlash",
            callback_data=f"select_group:{chat.id}:{chat.title[:40]}",
        )
    ]])

    try:
        await bot.send_message(
            chat_id    = config.admin_id,
            text       = (
                "🔔 <b>Bot yangi guruhga qo'shildi!</b>\n\n"
                f"📣 <b>Guruh:</b> {chat.title}\n"
                f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n\n"
                "Bu guruhni Flash Sale uchun asosiy guruh qilib belgilaysizmi?"
            ),
            parse_mode = "HTML",
            reply_markup = kb,
        )
    except Exception as e:
        logger.warning("Admin xabari yuborilmadi: %s", e)


# ── Admin "Tanlash" tugmasini bosadi ────────────────────

@setup_router.callback_query(F.data.startswith("select_group:"), is_admin)
async def cb_select_group(call: CallbackQuery) -> None:
    """
    Admin 'Shu guruhni tanlash' tugmasini shaxsiy chatda bosadi.
    Guruh ID bazaga saqlanadi va config yangilanadi.
    """
    parts     = call.data.split(":", 2)
    chat_id   = int(parts[1])
    chat_name = parts[2]

    await save_group_chat_id(chat_id)

    await call.message.edit_text(
        f"✅ <b>Guruh muvaffaqiyatli sozlandi!</b>\n\n"
        f"📣 <b>Guruh:</b> {chat_name}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat_id}</code>\n\n"
        "Endi Flash Sale postlari shu guruhga yuboriladi.\n"
        "👇 Admin panelni ochish uchun /flash yuboring.",
        parse_mode="HTML",
    )
    await call.answer("✅ Saqlandi!")
    logger.info("Admin guruh tanladi: '%s' (%d)", chat_name, chat_id)


# ── /status — hozirgi holat ──────────────────────────────

@setup_router.message(Command("status"), is_admin)
async def cmd_status(message: Message, bot: Bot) -> None:
    if config.group_chat_id == 0:
        group_line = (
            "❌ <b>Guruh sozlanmagan!</b>\n"
            "   Botni guruhga admin qilib qo'shing — tugma avtomatik keladi."
        )
    else:
        try:
            chat = await bot.get_chat(config.group_chat_id)
            group_line = f"✅ <b>{chat.title}</b> (<code>{config.group_chat_id}</code>)"
        except Exception:
            group_line = f"✅ Chat ID: <code>{config.group_chat_id}</code>"

    await message.answer(
        f"⚙️ <b>Bot holati:</b>\n\n"
        f"👤 Admin ID: <code>{config.admin_id}</code>\n"
        f"📣 Guruh: {group_line}",
        parse_mode="HTML",
    )


# ── /change_group — guruhni qayta tanlash ───────────────

@setup_router.message(Command("change_group"), is_admin)
async def cmd_change_group(message: Message) -> None:
    await message.answer(
        "🔄 <b>Guruhni o'zgartirish uchun:</b>\n\n"
        "1️⃣ Botni yangi guruhga admin qilib qo'shing\n"
        "2️⃣ Bot avtomatik ravishda sizga yangi guruh tugmasini yuboradi\n"
        "3️⃣ Tugmani bosing — tayyor!",
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

async def main() -> None:
    bot = Bot(
        token   = config.bot_token,
        default = DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Routerlar
    dp.include_router(setup_router)
    dp.include_router(flash_router)

    async def startup() -> None:
        await on_startup(bot)
        await load_group_chat_id()
        if config.group_chat_id:
            try:
                chat = await bot.get_chat(config.group_chat_id)
                logger.info("✅ Guruh yuklandi: '%s' (%d)", chat.title, config.group_chat_id)
            except Exception:
                logger.info("✅ Guruh yuklandi: chat_id=%d", config.group_chat_id)
        else:
            logger.warning("⚠️  Guruh sozlanmagan! Botni guruhga admin qilib qo'shing.")
            # Adminga eslatma
            try:
                await bot.send_message(
                    config.admin_id,
                    "⚠️ <b>Guruh hali sozlanmagan!</b>\n\n"
                    "Botni savdo guruhingizga <b>admin</b> qilib qo'shing.\n"
                    "Bot avtomatik ravishda sizga guruh tanlash tugmasini yuboradi.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    dp.startup.register(startup)
    dp.shutdown.register(on_shutdown)

    logger.info("🤖 Bot ishga tushmoqda...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())