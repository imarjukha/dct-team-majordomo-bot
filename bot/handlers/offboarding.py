from datetime import datetime
from telegram import Bot
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Employee, GroupMember, Group


async def run_offboarding(bot: Bot, employee: Employee) -> int:
    """Kick employee from all groups and mark as fired. Returns count of groups kicked from."""
    kicked = 0

    async with AsyncSessionLocal() as session:
        emp = await session.get(Employee, employee.id)

        if not emp.tg_user_id:
            # Can't kick without tg_user_id — notify HR
            return -1

        # Get all active memberships
        memberships = (
            await session.scalars(
                select(GroupMember).where(
                    GroupMember.employee_id == emp.id,
                    GroupMember.left_at == None,
                )
            )
        ).all()

        for membership in memberships:
            group = await session.get(Group, membership.group_id)
            if not group:
                continue
            try:
                await bot.ban_chat_member(
                    chat_id=group.tg_chat_id,
                    user_id=emp.tg_user_id,
                )
                # Immediately unban so they can rejoin in future if rehired
                await bot.unban_chat_member(
                    chat_id=group.tg_chat_id,
                    user_id=emp.tg_user_id,
                    only_if_banned=True,
                )
                membership.left_at = datetime.utcnow()
                kicked += 1
            except Exception:
                pass  # Bot not admin or already left

        emp.status = "fired"
        emp.fired_at = datetime.utcnow()
        await session.commit()

    return kicked
