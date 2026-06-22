from datetime import date
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Group, ActivityLog


async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Increment message counter for the group. Runs on every message."""
    chat = update.effective_chat
    if chat.type == "private":
        return

    today = date.today()

    async with AsyncSessionLocal() as session:
        group = await session.scalar(
            select(Group).where(Group.tg_chat_id == chat.id)
        )
        if not group:
            return

        log = await session.scalar(
            select(ActivityLog).where(
                ActivityLog.group_id == group.id,
                ActivityLog.date == today,
            )
        )
        if log:
            log.message_count += 1
        else:
            session.add(ActivityLog(group_id=group.id, date=today, message_count=1))

        await session.commit()
