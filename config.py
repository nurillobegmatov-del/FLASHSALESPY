# config.py
from __future__ import annotations

import os
import random
import string
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token:     str
    admin_id:      int
    group_chat_id: int = 0

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("8743769233:AAEKh_aqVMDXKSFgwLpjOuWpdpCO-js7dyk", "")
        admin = os.environ.get("8215056224", "0")
        if not token:
            raise ValueError("BOT_TOKEN muhit o'zgaruvchisi topilmadi!")
        return cls(
            bot_token = token,
            admin_id  = int(admin),
        )


config: Config = Config.from_env()

MARKUP_PERCENT: float = 15.0


async def load_group_chat_id() -> None:
    """Startup da bazadan saqlangan GROUP_CHAT_ID ni yuklab oladi."""
    from database import get_setting
    value = await get_setting("group_chat_id")
    if value and value != "0":
        config.group_chat_id = int(value)


async def save_group_chat_id(chat_id: int) -> None:
    """Guruh ID sini bazaga va xotiraga saqlaydi."""
    from database import set_setting
    config.group_chat_id = chat_id
    await set_setting("group_chat_id", str(chat_id))


def generate_product_id() -> str:
    digits = "".join(random.choices(string.digits, k=4))
    return f"#FL-{digits}"


def calc_original_price(sale_price: float) -> float:
    return round(sale_price * (1 + MARKUP_PERCENT / 100))


def fmt_price(amount: float) -> str:
    return f"{int(amount):,}".replace(",", " ")


def build_group_caption(
    product_id:      str,
    name:            str,
    description:     str,
    sale_price:      float,
    original_price:  float,
    expires_minutes: int,
) -> str:
    return (
        f"⚡️ <b>FLASH SALE</b>  •  <code>{product_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🛍 <b>{name}</b>\n\n"
        f"📝 {description}\n\n"
        "💸 <b>Narxlar:</b>\n"
        f"   🔴 <s>{fmt_price(original_price)} so'm</s>  ← eski narx\n"
        f"   🟢 <b>{fmt_price(sale_price)} so'm</b>  ✅ ← sizga\n\n"
        f"⏳ <i>Aksiya faqat {expires_minutes} daqiqa davom etadi!</i>\n"
        "🔥 <i>Ulguring — miqdor cheklangan!</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Xarid qilish uchun quyidagi tugmani bosing:"
    )


def build_expired_caption(
    product_id:     str,
    name:           str,
    original_price: float,
) -> str:
    return (
        f"❌ <b>BU AKSIYA YAKUNLANDI</b>  •  <code>{product_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🛍 <b>{name}</b>\n\n"
        f"💰 Narx o'z holiga qaytdi: <b>{fmt_price(original_price)} so'm</b>\n\n"
        "📢 <i>Yangi aksiyalar uchun guruhimizni kuzatib boring!</i>\n"
        "🏪 <i>Tez orada yangi Flash Sale bo'ladi!</i>"
    )


def build_admin_notify(
    buyer_fullname: str,
    buyer_username: str | None,
    buyer_id:       int,
    product_id:     str,
    product_name:   str,
    sale_price:     float,
) -> str:
    username_line = f"@{buyer_username}" if buyer_username else "<i>username yo'q</i>"
    return (
        "🛒 <b>YANGI XARID SO'ROVI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Xaridor:</b> {buyer_fullname}\n"
        f"📱 <b>Username:</b> {username_line}\n"
        f"🆔 <b>Telegram ID:</b> <code>{buyer_id}</code>\n\n"
        f"📦 <b>Mahsulot:</b> {product_name}\n"
        f"🏷 <b>ID:</b> <code>{product_id}</code>\n"
        f"💰 <b>Narx:</b> {fmt_price(sale_price)} so'm\n\n"
        f"💬 <b>Xaridorga to'g'ridan to'g'ri yozish:</b>\n"
        f"<a href='tg://user?id={buyer_id}'>👉 Aloqa qilish</a>"
    )