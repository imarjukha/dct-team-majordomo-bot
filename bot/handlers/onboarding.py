from telegram import Bot, Message
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, or_
from db.database import AsyncSessionLocal
from db.models import Group, GroupMember, Employee, BotAdmin
from config import SUPERADMIN_ID


async def run_onboarding(bot: Bot, employee: Employee, hr_message: Message):
    """Find all matching groups and send invite links to the employee or HR."""
    async with AsyncSessionLocal() as session:
        emp = await session.get(Employee, employee.id)

        groups_q = select(Group).where(
            Group.is_configured == True,
            or_(Group.business_unit_id == None, Group.business_unit_id == emp.business_unit_id),
            or_(Group.role_id == None, Group.role_id == emp.role_id),
        )
        groups = (await session.scalars(groups_q)).all()

        if not groups:
            await hr_message.reply_text(
                f"✅ @{emp.tg_username} добавлен, но подходящих групп не найдено."
            )
            return

        links = []
        for group in groups:
            try:
                link = await bot.create_chat_invite_link(
                    chat_id=group.tg_chat_id,
                    member_limit=1,
                    name=f"Онбординг @{emp.tg_username}"
                )
                links.append((group.name, link.invite_link))

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
                pass

        await session.commit()

    if not links:
        await hr_message.reply_text(
            f"⚠️ Не удалось создать ссылки для @{emp.tg_username}. "
            "Проверь, что бот — администратор во всех группах."
        )
        return

    links_text = "\n".join(f"• {name}: {url}" for name, url in links)

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
            pass

    await hr_message.reply_text(
        f"✅ @{emp.tg_username} добавлен.\n\n"
        f"📎 Ссылки для передачи сотруднику ({len(links)} групп):\n\n{links_text}\n\n"
        "⚠️ Попроси сотрудника написать боту /start — тогда в следующий раз ссылки придут ему напрямую."
    )


ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏢 Бизнес-юниты", "🏠 Заведения"],
        ["💼 Роли", "👥 Сотрудники"],
        ["📋 Группы", "⚙️ Главное меню"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When someone writes /start — identify them and save tg_user_id."""
    user = update.effective_user
    username = user.username

    # Superadmin shortcut
    if user.id == SUPERADMIN_ID:
        async with AsyncSessionLocal() as session:
            admin = await session.scalar(
                select(BotAdmin).where(BotAdmin.tg_user_id == SUPERADMIN_ID)
            )
            if admin:
                admin.tg_username = username or admin.tg_username
                await session.commit()
        await update.message.reply_text(
            "👑 Привет, суперадмин! Используй /admin для управления.",
            reply_markup=ADMIN_KEYBOARD
        )
        return

    if not username:
        await update.message.reply_text(
            "Привет! Установи username в настройках Telegram, чтобы система тебя идентифицировала."
        )
        return

    async with AsyncSessionLocal() as session:
        # Check if bot admin
        admin = await session.scalar(
            select(BotAdmin).where(BotAdmin.tg_username == username)
        )
        if admin:
            admin.tg_user_id = user.id
            await session.commit()
            await update.message.reply_text(
                f"✅ Привет, {user.first_name}! Ты зарегистрирован как администратор бота.\n"
                "Используй /admin для управления.",
                reply_markup=ADMIN_KEYBOARD
            )
            return

        # Check if employee
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if employee:
            employee.tg_user_id = user.id
            employee.name = user.full_name
            await session.commit()
            await update.message.reply_text(
                "✅ Готово! Теперь уведомления и ссылки будут приходить тебе напрямую."
            )
        else:
            await update.message.reply_text(
                "Привет! Ты пока не числишься в системе. Обратись к HR."
            )

