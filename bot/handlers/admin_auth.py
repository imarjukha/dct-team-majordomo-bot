"""
Admin authentication module.

Hierarchy:
- Superadmin: defined by SUPERADMIN_ID env var — cannot be removed
- Admins: stored in DB — managed by superadmin via /add_admin /remove_admin
"""
from functools import wraps
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import BotAdmin
from config import SUPERADMIN_ID


async def is_admin(user_id: int) -> bool:
    if user_id == SUPERADMIN_ID:
        return True
    async with AsyncSessionLocal() as session:
        result = await session.scalar(
            select(BotAdmin).where(BotAdmin.tg_user_id == user_id)
        )
        return result is not None


def require_admin(func):
    """Decorator: blocks non-admins from accessing a handler."""
    @wraps(func)
    async def wrapper(update, context):
        user = update.effective_user
        if not await is_admin(user.id):
            await update.message.reply_text("⛔ Нет доступа.")
            return
        return await func(update, context)
    return wrapper


def require_superadmin(func):
    """Decorator: blocks non-superadmins."""
    @wraps(func)
    async def wrapper(update, context):
        if update.effective_user.id != SUPERADMIN_ID:
            await update.message.reply_text("⛔ Только суперадмин.")
            return
        return await func(update, context)
    return wrapper


async def ensure_superadmin_in_db():
    """On startup: make sure superadmin exists in bot_admins table."""
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(BotAdmin).where(BotAdmin.tg_user_id == SUPERADMIN_ID)
        )
        if not existing:
            session.add(BotAdmin(
                tg_user_id=SUPERADMIN_ID,
                tg_username="ivanmaryukha",
                is_superadmin=True,
            ))
            await session.commit()


# /add_admin /remove_admin /admins — superadmin only

async def cmd_add_admin(update, context):
    if update.effective_user.id != SUPERADMIN_ID:
        await update.message.reply_text("⛔ Только суперадмин.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /add_admin @username")
        return

    username = context.args[0].lstrip("@")

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(BotAdmin).where(BotAdmin.tg_username == username)
        )
        if existing:
            await update.message.reply_text(f"@{username} уже является админом.")
            return

        session.add(BotAdmin(tg_user_id=0, tg_username=username, is_superadmin=False))
        await session.commit()

    await update.message.reply_text(
        f"✅ @{username} добавлен как админ.\n"
        f"⚠️ User ID будет привязан когда они напишут боту /start."
    )


async def cmd_remove_admin(update, context):
    if update.effective_user.id != SUPERADMIN_ID:
        await update.message.reply_text("⛔ Только суперадмин.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /remove_admin @username")
        return

    username = context.args[0].lstrip("@")

    async with AsyncSessionLocal() as session:
        admin = await session.scalar(
            select(BotAdmin).where(
                BotAdmin.tg_username == username,
                BotAdmin.is_superadmin == False,
            )
        )
        if not admin:
            await update.message.reply_text(f"@{username} не найден или является суперадмином.")
            return

        await session.delete(admin)
        await session.commit()

    await update.message.reply_text(f"✅ @{username} удалён из админов.")


async def cmd_list_admins(update, context):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    async with AsyncSessionLocal() as session:
        admins = (await session.scalars(select(BotAdmin))).all()

    lines = ["👥 *Администраторы бота:*\n"]
    for a in admins:
        tag = " 👑" if a.is_superadmin else ""
        username = f"@{a.tg_username}" if a.tg_username else f"id:{a.tg_user_id}"
        lines.append(f"• {username}{tag}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
