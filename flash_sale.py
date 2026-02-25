# handlers/flash_sale.py
"""
Flash Sale — FSM handler.

FSM oqimi:
  CollectMedia → WaitName → WaitDescription → WaitPrice → Confirm / Cancel
"""

from __future__ import annotations

import logging
import random

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from config import (
    build_admin_notify,
    build_group_caption,
    calc_original_price,
    config,
    fmt_price,
    generate_product_id,
)
from database import (
    create_flash_post,
    create_product,
    create_purchase_request,
    deactivate_product,
    get_db,
    get_flash_duration,
    get_flash_post,
    get_product,
    get_product_media,
    has_already_requested,
    list_active_products,
    set_setting,
)

logger = logging.getLogger(__name__)

router = Router(name="flash_sale")


# ── Faqat adminga ruxsat ─────────────────────────────────

def is_admin(message: Message) -> bool:
    return message.from_user.id == config.admin_id


# ════════════════════════════════════════════════════════════
#  FSM STATES
# ════════════════════════════════════════════════════════════

class AddProduct(StatesGroup):
    collect_media    = State()
    wait_name        = State()
    wait_description = State()
    wait_price       = State()
    confirm          = State()


class ChangeInterval(StatesGroup):
    waiting = State()


# ════════════════════════════════════════════════════════════
#  KLAVIATURALAR
# ════════════════════════════════════════════════════════════

def kb_ready() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tayyor", callback_data="media_ready"),
    ]])


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",   callback_data="product_confirm"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="product_cancel"),
    ]])


def kb_buy(post_id: int, product_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Sotib olaman 🛒",
            callback_data=f"buy:{post_id}:{product_id}",
        )
    ]])


def kb_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Yangi mahsulot",    callback_data="panel_add"),
            InlineKeyboardButton(text="📋 Mahsulotlar",       callback_data="panel_list"),
        ],
        [
            InlineKeyboardButton(text="⏱ Muddatni sozlash",  callback_data="panel_interval"),
            InlineKeyboardButton(text="📤 Hozir yuborish",    callback_data="panel_send_now"),
        ],
    ])


# ════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ════════════════════════════════════════════════════════════

@router.message(Command("flash"), is_admin)
async def cmd_flash_panel(message: Message) -> None:
    duration = await get_flash_duration()
    products = await list_active_products()
    await message.answer(
        f"⚡️ <b>Flash Sale Admin Paneli</b>\n\n"
        f"⏱ Post muddati: <b>{duration} daqiqa</b>\n"
        f"📦 Aktiv mahsulotlar: <b>{len(products)} ta</b>",
        parse_mode="HTML",
        reply_markup=kb_admin_panel(),
    )


@router.callback_query(F.data == "panel_add")
async def cb_panel_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.answer(
        "📸 <b>Mahsulot rasmlari yoki videolarini yuboring.</b>\n\n"
        "Bir nechta media yuborishingiz mumkin.\n"
        "Hammasini yuborganingizdan so'ng <b>✅ Tayyor</b> tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=kb_ready(),
    )
    await state.update_data(media_items=[])
    await state.set_state(AddProduct.collect_media)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  FSM — MEDIA TO'PLASH
#
#  Aiogram v3 da MediaGroup bir nechta alohida Message event sifatida keladi.
#  Biz media_group_id orqali takroriyliklarni nazorat qilamiz,
#  file_id larni FSM xotirasida to'playmiz.
# ════════════════════════════════════════════════════════════

@router.message(
    StateFilter(AddProduct.collect_media),
    F.content_type.in_({"photo", "video"}),
)
async def fsm_collect_media(message: Message, state: FSMContext) -> None:
    data  = await state.get_data()
    items: list[dict] = data.get("media_items", [])

    # file_id olish
    if message.photo:
        file_id    = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id    = message.video.file_id
        media_type = "video"
    else:
        return

    # Bir media_group_id ichidagi barcha fayllar alohida event sifatida keladi.
    # file_id takroriyligini tekshiramiz:
    existing_ids = {i["file_id"] for i in items}
    if file_id in existing_ids:
        return

    items.append({
        "file_id":    file_id,
        "media_type": media_type,
        "sort_order": len(items),
    })
    await state.update_data(media_items=items)

    # Har 1 va keyingi har 5 ta mediada xabar yuboring
    count = len(items)
    if count == 1:
        await message.answer(
            "✅ <b>1 ta media qabul qilindi.</b>\n"
            "Davom ettiravering yoki '✅ Tayyor' tugmasini bosing.",
            parse_mode="HTML",
            reply_markup=kb_ready(),
        )
    elif count % 5 == 0:
        await message.answer(
            f"📦 Jami <b>{count} ta media</b> qabul qilindi.",
            parse_mode="HTML",
            reply_markup=kb_ready(),
        )


@router.callback_query(F.data == "media_ready", StateFilter(AddProduct.collect_media))
async def cb_media_ready(call: CallbackQuery, state: FSMContext) -> None:
    data  = await state.get_data()
    items = data.get("media_items", [])

    if not items:
        await call.answer("❌ Hech qanday media yuklanmadi!", show_alert=True)
        return

    await call.message.answer(
        f"✅ <b>{len(items)} ta media saqlandi.</b>\n\n"
        "✏️ Endi mahsulot nomini kiriting:",
        parse_mode="HTML",
    )
    await state.set_state(AddProduct.wait_name)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  FSM — MATN MA'LUMOTLARI
# ════════════════════════════════════════════════════════════

@router.message(StateFilter(AddProduct.wait_name), F.text)
async def fsm_got_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("❌ Nom juda qisqa. Iltimos, qaytadan kiriting:")
        return
    await state.update_data(name=name)
    await message.answer(
        "📝 <b>Mahsulot ta'rifini kiriting</b> (xususiyatlar, o'lcham, rang...):",
        parse_mode="HTML",
    )
    await state.set_state(AddProduct.wait_description)


@router.message(StateFilter(AddProduct.wait_description), F.text)
async def fsm_got_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip())
    await message.answer(
        "💰 <b>Chegirmadagi narxni kiriting</b> (so'mda, faqat raqam):\n\n"
        "<i>Misol: 85000</i>\n"
        "<i>Bot +15% qo'shib 'eski narx'ni avtomatik hisoblaydi.</i>",
        parse_mode="HTML",
    )
    await state.set_state(AddProduct.wait_price)


@router.message(StateFilter(AddProduct.wait_price), F.text)
async def fsm_got_price(message: Message, state: FSMContext) -> None:
    raw = message.text.strip().replace(" ", "").replace(",", "")
    if not raw.replace(".", "", 1).isdigit():
        await message.answer(
            "❌ Faqat raqam kiriting. Misol: <code>85000</code>",
            parse_mode="HTML",
        )
        return

    sale_price     = float(raw)
    original_price = calc_original_price(sale_price)
    await state.update_data(sale_price=sale_price, original_price=original_price)
    data = await state.get_data()

    preview = (
        "📋 <b>Mahsulot ma'lumotlari — tekshirib ko'ring:</b>\n\n"
        f"📦 <b>Nomi:</b> {data['name']}\n"
        f"📝 <b>Ta'rif:</b> {data['description']}\n\n"
        "💸 <b>Narxlar:</b>\n"
        f"   🔴 Eski narx: <s>{fmt_price(original_price)} so'm</s>\n"
        f"   🟢 Chegirmali: <b>{fmt_price(sale_price)} so'm</b>\n\n"
        f"📸 <b>Media:</b> {len(data['media_items'])} ta\n"
        "🏷 <b>ID:</b> <i>saqlashda avtomatik beriladi</i>\n\n"
        "Tasdiqlaysizmi?"
    )
    await message.answer(preview, parse_mode="HTML", reply_markup=kb_confirm())
    await state.set_state(AddProduct.confirm)


# ════════════════════════════════════════════════════════════
#  FSM — TASDIQLASH
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "product_confirm", StateFilter(AddProduct.confirm))
async def cb_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    product_id = generate_product_id()
    media_clean = [
        {"file_id": m["file_id"], "media_type": m["media_type"], "sort_order": m["sort_order"]}
        for m in data["media_items"]
    ]

    await create_product(
        product_id     = product_id,
        name           = data["name"],
        description    = data["description"],
        sale_price     = data["sale_price"],
        original_price = data["original_price"],
        media          = media_clean,
    )

    await call.message.answer(
        f"✅ <b>Mahsulot muvaffaqiyatli saqlandi!</b>\n"
        f"🏷 ID: <code>{product_id}</code>\n\n"
        "Guruhga yuborish uchun '📤 Hozir yuborish' tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=kb_admin_panel(),
    )
    await call.answer("Saqlandi ✅")


@router.callback_query(F.data == "product_cancel", StateFilter(AddProduct.confirm))
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("❌ Qo'shish bekor qilindi.", reply_markup=kb_admin_panel())
    await call.answer()


# ════════════════════════════════════════════════════════════
#  GURUHGA YUBORISH
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "panel_send_now")
async def cb_send_now(call: CallbackQuery, bot: Bot) -> None:
    products = await list_active_products()
    if not products:
        await call.answer("❌ Aktiv mahsulot yo'q!", show_alert=True)
        return
    product = dict(random.choice(products))
    await _send_flash_post(bot, product)
    await call.answer("📤 Yuborildi!")


async def _send_flash_post(bot: Bot, product: dict) -> None:
    """
    1) MediaGroup → guruh
    2) HTML matn + 'Sotib olaman' tugmasi (albomga reply)
    3) Flash post bazaga yoziladi
    4) Scheduler job — N daqiqadan so'ng tugma o'chiriladi
    """
    from datetime import datetime, timedelta
    from scheduler import schedule_post_expiry

    product_id = product["id"]
    duration   = await get_flash_duration()

    # ── 1. Media ────────────────────────────────────────
    media_rows = await get_product_media(product_id)
    if not media_rows:
        logger.warning("Mahsulot %s uchun media yo'q", product_id)
        await bot.send_message(
            config.admin_id,
            f"⚠️ <code>{product_id}</code> mahsulotiga media biriktirilmagan!",
            parse_mode="HTML",
        )
        return

    input_media: list = []
    for row in media_rows:
        if row["media_type"] == "photo":
            input_media.append(InputMediaPhoto(media=row["file_id"]))
        else:
            input_media.append(InputMediaVideo(media=row["file_id"]))

    # ── 2. Albomni yuborish ──────────────────────────────
    sent_album: list[Message] = await bot.send_media_group(
        chat_id=config.group_chat_id,
        media=input_media,
    )
    album_ids = [m.message_id for m in sent_album]

    # ── 3. Bazaga yozish (text_message_id = 0, keyin yangilaymiz) ──
    expires_at = datetime.now() + timedelta(minutes=duration)
    post_id = await create_flash_post(
        product_id        = product_id,
        chat_id           = config.group_chat_id,
        album_message_ids = album_ids,
        text_message_id   = 0,
        expires_at        = expires_at,
    )

    # ── 4. Matn xabarini yuborish ────────────────────────
    caption = build_group_caption(
        product_id      = product["id"],
        name            = product["name"],
        description     = product["description"],
        sale_price      = product["sale_price"],
        original_price  = product["original_price"],
        expires_minutes = duration,
    )
    text_msg: Message = await bot.send_message(
        chat_id             = config.group_chat_id,
        text                = caption,
        parse_mode          = "HTML",
        reply_to_message_id = sent_album[0].message_id,
        reply_markup        = kb_buy(post_id, product_id),
    )

    # text_message_id ni yangilaymiz
    async with get_db() as db:
        await db.execute(
            "UPDATE flash_posts SET text_message_id=? WHERE id=?",
            (text_msg.message_id, post_id),
        )
        await db.commit()

    # ── 5. Scheduler ─────────────────────────────────────
    await schedule_post_expiry(
        bot            = bot,
        post_id        = post_id,
        delay_seconds  = duration * 60,
    )

    logger.info(
        "✅ Flash post yuborildi | product=%s post_id=%d expires_at=%s",
        product_id, post_id, expires_at.isoformat(),
    )


# ════════════════════════════════════════════════════════════
#  "SOTIB OLAMAN" CALLBACK
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, bot: Bot) -> None:
    parts      = call.data.split(":", 2)
    post_id    = int(parts[1])
    product_id = parts[2]

    post = await get_flash_post(post_id)
    if post is None or post["is_expired"]:
        await call.answer("⛔️ Bu aksiya allaqachon yakunlangan!", show_alert=True)
        return

    buyer = call.from_user
    if await has_already_requested(post_id, buyer.id):
        await call.answer("ℹ️ Siz allaqachon so'rov yuborgansiz!", show_alert=True)
        return

    product = await get_product(product_id)
    if not product:
        await call.answer("❌ Mahsulot topilmadi.", show_alert=True)
        return

    await create_purchase_request(
        post_id        = post_id,
        product_id     = product_id,
        buyer_id       = buyer.id,
        buyer_username = buyer.username,
        buyer_fullname = buyer.full_name,
    )

    # ── Guruhga e'lon ────────────────────────────────────
    uname = f"@{buyer.username}" if buyer.username else f"<b>{buyer.full_name}</b>"
    await bot.send_message(
        chat_id             = config.group_chat_id,
        text                = (
            f"🎉 {uname} ushbu mahsulotni "
            f"(<code>{product_id}</code>) xarid qilmoqchi!\n"
            "👥 <i>Adminlar tez orada aloqaga chiqadi.</i>"
        ),
        parse_mode          = "HTML",
        reply_to_message_id = post["text_message_id"],
    )

    # ── Adminga xabar ────────────────────────────────────
    await bot.send_message(
        chat_id    = config.admin_id,
        text       = build_admin_notify(
            buyer_fullname = buyer.full_name,
            buyer_username = buyer.username,
            buyer_id       = buyer.id,
            product_id     = product_id,
            product_name   = product["name"],
            sale_price     = product["sale_price"],
        ),
        parse_mode = "HTML",
    )

    await call.answer(
        "✅ So'rovingiz qabul qilindi! Admin tez orada siz bilan bog'lanadi.",
        show_alert=True,
    )


# ════════════════════════════════════════════════════════════
#  MAHSULOTLAR RO'YXATI
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "panel_list")
async def cb_panel_list(call: CallbackQuery) -> None:
    products = await list_active_products()
    if not products:
        await call.message.answer("📭 Aktiv mahsulot yo'q.")
        await call.answer()
        return

    lines   = ["📋 <b>Aktiv mahsulotlar:</b>\n"]
    buttons = []
    for p in products:
        lines.append(
            f"• <code>{p['id']}</code> — <b>{p['name']}</b> "
            f"({fmt_price(p['sale_price'])} so'm)"
        )
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {p['id']} o'chirish",
            callback_data=f"del_product:{p['id']}",
        )])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="panel_back")])
    await call.message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await call.answer()


@router.callback_query(F.data.startswith("del_product:"))
async def cb_del_product(call: CallbackQuery) -> None:
    product_id = call.data.split(":", 1)[1]
    await deactivate_product(product_id)
    await call.message.answer(
        f"🗑 <code>{product_id}</code> o'chirildi.", parse_mode="HTML"
    )
    await call.answer("O'chirildi ✅")


# ════════════════════════════════════════════════════════════
#  INTERVAL SOZLASH
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "panel_interval")
async def cb_panel_interval(call: CallbackQuery, state: FSMContext) -> None:
    current = await get_flash_duration()
    await call.message.answer(
        f"⏱ Hozirgi post muddati: <b>{current} daqiqa</b>\n\n"
        "Yangi vaqtni <b>daqiqada</b> kiriting (1–1440):",
        parse_mode="HTML",
    )
    await state.set_state(ChangeInterval.waiting)
    await call.answer()


@router.message(StateFilter(ChangeInterval.waiting), F.text, is_admin)
async def fsm_set_interval(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 1440):
        await message.answer("❌ 1 dan 1440 gacha butun son kiriting.")
        return
    await set_setting("flash_duration_minutes", raw)
    await state.clear()
    await message.answer(
        f"✅ Post muddati <b>{raw} daqiqa</b> qilib o'rnatildi.",
        parse_mode="HTML",
        reply_markup=kb_admin_panel(),
    )


@router.callback_query(F.data == "panel_back")
async def cb_panel_back(call: CallbackQuery) -> None:
    await cmd_flash_panel(call.message)
    await call.answer()
