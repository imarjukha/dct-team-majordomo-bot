from datetime import date, timedelta
from telegram import Bot
from sqlalchemy import select, func
from db.database import AsyncSessionLocal
from db.models import Group, ActivityLog
from config import HR_GROUP_ID

INACTIVE_DAYS = 30


async def send_weekly_report(bot: Bot):
    """Send weekly activity report to HR group."""
    cutoff = date.today() - timedelta(days=INACTIVE_DAYS)

    async with AsyncSessionLocal() as session:
        groups = (await session.scalars(select(Group).where(Group.is_configured == True))).all()

        active_groups = []
        inactive_groups = []

        for group in groups:
            total = await session.scalar(
                select(func.sum(ActivityLog.message_count)).where(
                    ActivityLog.group_id == group.id,
                    ActivityLog.date >= cutoff,
                )
            ) or 0

            last_log = await session.scalar(
                select(ActivityLog).where(
                    ActivityLog.group_id == group.id
                ).order_by(ActivityLog.date.desc())
            )
            last_date = last_log.date if last_log else None

            if total == 0 or (last_date and last_date < cutoff):
                inactive_groups.append((group.name, last_date))
            else:
                active_groups.append((group.name, total))

    lines = ["📊 *Еженедельный отчёт по активности групп*\n"]

    if active_groups:
        lines.append("✅ *Активные группы (30 дней):*")
        for name, count in sorted(active_groups, key=lambda x: -x[1]):
            lines.append(f"  • {name}: {count} сообщений")

    if inactive_groups:
        lines.append(f"\n⚠️ *Неактивные 30+ дней — рекомендую удалить или архивировать:*")
        for name, last_date in inactive_groups:
            last_str = last_date.strftime("%d.%m.%Y") if last_date else "нет данных"
            lines.append(f"  • {name} (последняя активность: {last_str})")

    if not inactive_groups and not active_groups:
        lines.append("Данных пока нет.")

    await bot.send_message(
        chat_id=HR_GROUP_ID,
        text="\n".join(lines),
        parse_mode="Markdown"
    )
