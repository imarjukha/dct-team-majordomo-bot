from telegram import Bot, Message
from sqlalchemy import select, or_
from db.database import AsyncSessionLocal
from db.models import Group, GroupMember, Employee


async def run_onboarding(bot: Bot, employee: Employee, hr_message: Message):
    """Find all matching groups and send invite links to the employee or HR."""
    async with AsyncSessionLocal() as session:
        # Reload employee with relationships
        emp = await session.get(Employee, employee.id)

        # Match groups: null = ANY
        groups_q = select(Group).where(
            Group.is_configured == True,
            or_(Group.business_unit_id == None, Group.business_unit_id == emp.business_unit_id),
            or_(Group.venue_id == None, Group.venue_id == emp.venue_id),
            or_(Group.role_id == None, Group.role_id == emp.role_id),
        )
        groups = (await session.scalars(groups_q)).all()

        if not groups:
            await hr_message.reply_text(
                f"✅ @{emp.tg_username} добавлен, но подходящих групп не найдено."
            )
            return

        # Generate invite links
        links = []
        for group in groups:
            try:
                link = await bot.create_chat_invite_link(
                    chat_id=group.tg_chat_id,
                    member_limit=1,
                    name=f"Онбординг @{emp.tg_username}"
                )
                links.append((group.name, link.invite_link))

                # Record membership (pending — not yet joined)
                existing = await session.scalar(
                    select(GroupMember).where(
                        GroupMember.group_id == group.id,
                        GroupMember.employee_id == emp.id,
                        GroupMember.left_at == None,
                    )
                )
                if not existing:
                    session.add(GroupMember(group_id=group.id, employee_id=emp.id))
            except Exception:
                pass  # Bot might not be admin in this group

        await session.commit()

    if not links:
        await hr_message.reply_text(
            f"⚠️ Не удалось создать ссылки для @{emp.tg_username}. "
            "Проверь, что бот — администратор во всех группах."
        )
        return

    links_text = "\n".join(f"• {name}: {url}" for name, url in links)

    # If we know the employee's tg_user_id — send in DM
    if emp.tg_user_id:
        try:
            await bot.send_message(
                chat_id=emp.tg_user_id,
                text=(
                    f"👋 Добро пожаловать!\n\n"
                    f"Вот ссылки на твои рабочие группы:\n\n{links_text}\n\n"
                    "Ссылки одноразовые — используй каждую один раз."
                )
            )
            await hr_message.reply_text(
                f"✅ @{emp.tg_username} добавлен. Ссылки отправлены в личку ({len(links)} групп)."
            )
            return
        except Exception:
            pass  # DM failed — fall through to HR message

    # Fallback: send links to HR group
    await hr_message.reply_text(
        f"✅ @{emp.tg_username} добавлен.\n\n"
        f"📎 Ссылки для передачи сотруднику ({len(links)} групп):\n\n{links_text}\n\n"
        "⚠️ Попроси сотрудника написать боту /start — тогда в следующий раз ссылки придут ему напрямую."
    )


async def handle_start(update, context):
    """When employee writes /start to bot — save their tg_user_id."""
    user = update.effective_user
    username = user.username

    if not username:
        await update.message.reply_text(
            "Привет! Установи username в настройках Telegram, чтобы система тебя идентифицировала."
        )
        return

    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if employee:
            employee.tg_user_id = user.id
            employee.name = user.full_name
            await session.commit()
            await update.message.reply_text(
                f"✅ Готово! Теперь уведомления и ссылки будут приходить тебе напрямую."
            )
        else:
            await update.message.reply_text(
                "Привет! Ты пока не числишься в системе. Обратись к HR."
            )
