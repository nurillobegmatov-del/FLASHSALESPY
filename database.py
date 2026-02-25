# database.py
"""
Asinxron SQLite (aiosqlite) baza qatlami.
Barcha model va query'lar shu yerda jamlangan.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)

import os
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "."), "flash_sale.db")

# ════════════════════════════════════════════════════════════
#  DDL — Jadvallar
# ════════════════════════════════════════════════════════════

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Mahsulotlar
CREATE TABLE IF NOT EXISTS products (
    id              TEXT    PRIMARY KEY,          -- #FL-XXXX
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    sale_price      REAL    NOT NULL,             -- admin kiritgan narx (chegirmali)
    original_price  REAL    NOT NULL,             -- +15% hisoblangan "eski narx"
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Media fayllar (har bir mahsulotga ko'p media)
CREATE TABLE IF NOT EXISTS product_media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT    NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    file_id     TEXT    NOT NULL,
    media_type  TEXT    NOT NULL CHECK(media_type IN ('photo','video')),
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Guruhga yuborilgan postlar (avto-o'chirish uchun)
CREATE TABLE IF NOT EXISTS flash_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          TEXT    NOT NULL REFERENCES products(id),
    chat_id             INTEGER NOT NULL,
    album_message_ids   TEXT    NOT NULL,   -- JSON array of int
    text_message_id     INTEGER NOT NULL,
    expires_at          TEXT    NOT NULL,   -- ISO datetime
    is_expired          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Xaridorlar (kim "Sotib olaman" bosdi)
CREATE TABLE IF NOT EXISTS purchase_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES flash_posts(id),
    product_id      TEXT    NOT NULL,
    buyer_id        INTEGER NOT NULL,
    buyer_username  TEXT,
    buyer_fullname  TEXT,
    requested_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Bot sozlamalari
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('flash_duration_minutes', '30'),
    ('admin_id',               '0'),
    ('group_chat_id',          '0');
"""

# ════════════════════════════════════════════════════════════
#  Connection context
# ════════════════════════════════════════════════════════════

@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()
    logger.info("✅ DB initsializatsiya qilindi: %s", DB_PATH)


# ════════════════════════════════════════════════════════════
#  Settings
# ════════════════════════════════════════════════════════════

async def get_setting(key: str) -> str | None:
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value)
        )
        await db.commit()


async def get_flash_duration() -> int:
    v = await get_setting("flash_duration_minutes")
    return int(v) if v else 30


# ════════════════════════════════════════════════════════════
#  Products
# ════════════════════════════════════════════════════════════

async def create_product(
    product_id: str,
    name: str,
    description: str,
    sale_price: float,
    original_price: float,
    media: list[dict],          # [{"file_id": str, "media_type": str, "sort_order": int}]
) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO products (id, name, description, sale_price, original_price)
               VALUES (?,?,?,?,?)""",
            (product_id, name, description, sale_price, original_price),
        )
        for item in media:
            await db.execute(
                """INSERT INTO product_media (product_id, file_id, media_type, sort_order)
                   VALUES (?,?,?,?)""",
                (product_id, item["file_id"], item["media_type"], item["sort_order"]),
            )
        await db.commit()


async def get_product(product_id: str) -> aiosqlite.Row | None:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM products WHERE id=?", (product_id,))
        return await cur.fetchone()


async def get_product_media(product_id: str) -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM product_media WHERE product_id=? ORDER BY sort_order",
            (product_id,),
        )
        return await cur.fetchall()


async def list_active_products() -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM products WHERE is_active=1 ORDER BY created_at DESC"
        )
        return await cur.fetchall()


async def deactivate_product(product_id: str) -> None:
    async with get_db() as db:
        await db.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))
        await db.commit()


# ════════════════════════════════════════════════════════════
#  Flash Posts
# ════════════════════════════════════════════════════════════

import json


async def create_flash_post(
    product_id: str,
    chat_id: int,
    album_message_ids: list[int],
    text_message_id: int,
    expires_at: datetime,
) -> int:
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO flash_posts
               (product_id, chat_id, album_message_ids, text_message_id, expires_at)
               VALUES (?,?,?,?,?)""",
            (
                product_id,
                chat_id,
                json.dumps(album_message_ids),
                text_message_id,
                expires_at.isoformat(),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_flash_posts() -> list[aiosqlite.Row]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM flash_posts WHERE is_expired=0"
        )
        return await cur.fetchall()


async def get_flash_post(post_id: int) -> aiosqlite.Row | None:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM flash_posts WHERE id=?", (post_id,))
        return await cur.fetchone()


async def mark_post_expired(post_id: int) -> None:
    async with get_db() as db:
        await db.execute("UPDATE flash_posts SET is_expired=1 WHERE id=?", (post_id,))
        await db.commit()


# ════════════════════════════════════════════════════════════
#  Purchase Requests
# ════════════════════════════════════════════════════════════

async def create_purchase_request(
    post_id: int,
    product_id: str,
    buyer_id: int,
    buyer_username: str | None,
    buyer_fullname: str,
) -> int:
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO purchase_requests
               (post_id, product_id, buyer_id, buyer_username, buyer_fullname)
               VALUES (?,?,?,?,?)""",
            (post_id, product_id, buyer_id, buyer_username, buyer_fullname),
        )
        await db.commit()
        return cur.lastrowid


async def has_already_requested(post_id: int, buyer_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM purchase_requests WHERE post_id=? AND buyer_id=?",
            (post_id, buyer_id),
        )
        return (await cur.fetchone()) is not None