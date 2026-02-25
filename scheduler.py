# scheduler.py
"""
APScheduler — Flash Sale post muddatini boshqarish.

Har bir Flash post guruhga yuborilgandan so'ng,
belgilangan vaqt o'tgach:
  1. 'Sotib olaman 🛒' tugmasi o'chiriladi
  2. Xabar matni '❌ BU AKSIYA YAKUNLANDI' ga o'zgartiriladi
  3. Baza yangilanadi (is_expired = 1)

Best-practice:
  - Har bir post uchun alohida job yaratiladi (job id = f"expire_{post_id}")
  - Bot qayta ishga tushsa, bazadagi muddati o'tmagan postlar
    uchun joblar qayta tiklanadi (restore_pending_jobs).
  - AsyncIOScheduler ishlatiladi — aiogram event loop bilan muvofiqlashadi.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from config import build_expired_caption, config

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  Singleton scheduler
# ════════════════════════════════════════════════════════════

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone="Asia/Tashkent",
            job_defaults={
                "misfire_grace_time": 120,
                "coalesce": True,
            },
        )
    return _scheduler


# ════════════════════════════════════════════════════════════
#  POST EXPIRE LOGIC
# ════════════════════════════════════════════════════════════

async def expire_flash_post(bot: Bot, post_id: int) -> None:
    """
    Scheduler chaqiradigan asosiy funksiya.
    Post muddati tugaganda xabarni yangilaydi va bazani o'zgartiradi.
    """
    from database import get_flash_post, get_product, mark_post_expired

    logger.info("⏰ expire_flash_post chaqirildi: post_id=%d", post_id)

    post = await get_flash_post(post_id)
    if post is None:
        logger.warning("Post #%d topilmadi", post_id)
        return

    if post["is_expired"]:
        logger.info("Post #%d allaqachon yakunlangan, o'tkazib yuborildi", post_id)
        return

    product = await get_product(post["product_id"])
    if product is None:
        logger.warning("Mahsulot %s topilmadi", post["product_id"])
        return

    chat_id         = post["chat_id"]
    text_message_id = post["text_message_id"]

    # ── 1. Xabar matnini yangilash (tugmani olib tashlash) ──
    expired_text = build_expired_caption(
        product_id     = product["id"],
        name           = product["name"],
        original_price = product["original_price"],
    )

    try:
        await bot.edit_message_text(
            chat_id      = chat_id,
            message_id   = text_message_id,
            text         = expired_text,
            parse_mode   = "HTML",
            reply_markup = None,    # ← tugmani olib tashlash
        )
        logger.info("✅ Post #%d xabari yangilandi (aksiya yakunlandi)", post_id)

    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Xabar o'zgarmagan, e'tiborsiz qoldirish")
        elif "message to edit not found" in str(e):
            logger.warning("Post #%d xabari topilmadi (o'chirilgan bo'lishi mumkin)", post_id)
        else:
            logger.error("Post #%d yangilashda xato: %s", post_id, e)

    # ── 2. Bazani yangilash ──────────────────────────────
    await mark_post_expired(post_id)
    logger.info("✅ Post #%d bazada yakunlangan deb belgilandi", post_id)

    # ── 3. Adminga xabar ────────────────────────────────
    try:
        await bot.send_message(
            chat_id    = config.admin_id,
            text       = (
                f"⏱ <b>Flash Sale yakunlandi!</b>\n\n"
                f"📦 Mahsulot: <b>{product['name']}</b>\n"
                f"🏷 ID: <code>{product['id']}</code>\n"
                f"💰 Asl narx: <b>{product['original_price']:,.0f} so'm</b>"
            ),
            parse_mode = "HTML",
        )
    except Exception as e:
        logger.warning("Admin xabari yuborilmadi: %s", e)


# ════════════════════════════════════════════════════════════
#  JOB QO'SHISH / O'CHIRISH
# ════════════════════════════════════════════════════════════

def _job_id(post_id: int) -> str:
    return f"expire_post_{post_id}"


async def schedule_post_expiry(
    bot: Bot,
    post_id: int,
    delay_seconds: int,
) -> None:
    """
    Flash post yuborilgandan so'ng chaqiriladi.
    `delay_seconds` vaqt o'tgach expire_flash_post ishga tushadi.
    """
    scheduler = get_scheduler()
    run_at    = datetime.now() + timedelta(seconds=delay_seconds)
    job_id    = _job_id(post_id)

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    scheduler.add_job(
        func             = expire_flash_post,
        trigger          = DateTrigger(run_date=run_at),
        id               = job_id,
        name             = f"Flash Post #{post_id} Expire",
        kwargs           = {"bot": bot, "post_id": post_id},
        replace_existing = True,
    )
    logger.info(
        "✅ Expire job qo'shildi: post_id=%d run_at=%s (delay=%ds)",
        post_id, run_at.strftime("%H:%M:%S"), delay_seconds,
    )


def cancel_post_expiry(post_id: int) -> None:
    """Agar admin post'ni qo'lda o'chirsa — jobni ham bekor qiladi."""
    scheduler = get_scheduler()
    job_id    = _job_id(post_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Job bekor qilindi: post_id=%d", post_id)


# ════════════════════════════════════════════════════════════
#  BOT QAYTA ISHGA TUSHGANDA TIKLANISH
# ════════════════════════════════════════════════════════════

async def restore_pending_jobs(bot: Bot) -> None:
    """
    Bot restart bo'lganda bazadagi muddati o'tmagan postlar uchun
    joblarni qayta yaratadi.

    Muddati o'tib ketgan postlar esa darhol expire qilinadi.
    """
    from database import get_active_flash_posts

    posts       = await get_active_flash_posts()
    now         = datetime.now()
    restored    = 0
    expired_now = 0

    for post in posts:
        expires_at = datetime.fromisoformat(post["expires_at"])
        remaining  = (expires_at - now).total_seconds()

        if remaining <= 0:
            logger.info(
                "Post #%d muddati o'tib ketgan (%s), darhol yakunlanmoqda...",
                post["id"], post["expires_at"],
            )
            await expire_flash_post(bot, post["id"])
            expired_now += 1
        else:
            await schedule_post_expiry(
                bot           = bot,
                post_id       = post["id"],
                delay_seconds = int(remaining),
            )
            restored += 1

    logger.info(
        "🔄 Joblar tiklandi: %d ta restored, %d ta darhol yakunlandi",
        restored, expired_now,
    )


# ════════════════════════════════════════════════════════════
#  STARTUP / SHUTDOWN HOOKLAR
# ════════════════════════════════════════════════════════════

async def on_startup(bot: Bot) -> None:
    """main.py: dp.startup.register(lambda: on_startup(bot))"""
    from database import init_db

    await init_db()

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("✅ APScheduler ishga tushdi (timezone: Asia/Tashkent)")

    await restore_pending_jobs(bot)


async def on_shutdown() -> None:
    """main.py: dp.shutdown.register(on_shutdown)"""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 APScheduler to'xtatildi")
