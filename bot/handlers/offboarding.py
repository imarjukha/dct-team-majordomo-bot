from datetime import datetime
from telegram import Bot
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Employee, GroupMember, Group


async def run_offboarding(bot: Bot, employee: Employee, initiated_by: str | None = None) -> int:
    """Kick employee from all groups and mark as fired.
    initiated_by: Telegram username of the person who reported the firing (for farewell message).
    Returns count of groups kicked from, or -1 if tg_user_id unknown.
    """
    kicked = 0

    async with AsyncSessionLocal() as session:
        emp = await session.get(Employee, employee.id)

        if not emp.tg_user_id:
            return -1

        memberships = (await session.scalars(
            select(GroupMember).where(
                GroupMember.employee_id == emp.id,
                GroupMember.left_at == None,
            )
        )).all()

        for membership in memberships:
            group = await session.get(Group, membership.group_id)
            if not group:
                continue
            try:
                await bot.ban_chat_member(chat_id=group.tg_chat_id, user_id=emp.tg_user_id)
                await bot.unban_chat_member(chat_id=group.tg_chat_id, user_id=emp.tg_user_id, only_if_banned=True)
                membership.left_at = datetime.utcnow()
                kicked += 1
            except Exception:
                pass

        emp.status = "fired"
        emp.fired_at = datetime.utcnow()
        await session.commit()

    # Send farewell message to the employee
    if emp.tg_user_id:
        contact_line = f"\n\nЕсли возникнут вопросы — можешь написать @{initiated_by}." if initiated_by else ""
        try:
            await bot.send_message(
                chat_id=emp.tg_user_id,
                text=(
                    f"Привет, {emp.name or emp.tg_username}!\n\n"
                    f"Хотим поблагодарить тебя за время, проведённое в команде. "
                    f"Желаем удачи в новых начинаниях и всего самого лучшего! 🙌"
                    f"{contact_line}"
                )
            )
        except Exception:
            pass  # Employee may have blocked the bot

    return kicked
