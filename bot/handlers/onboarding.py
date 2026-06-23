from telegram import Bot, Message
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, or_
from db.database import AsyncSessionLocal
from db.models import Group, GroupMember, Employee, BotAdmin
from config import SUPERADMIN_ID


async def run_onboarding(bot: Bot, employee: Employee, hr_message: Message):
    """Onboard employee: add directly to groups if tg_user_id known, otherwise give HR a forward message."""
    async with AsyncSessionLocal() as session:
        emp = await session.get(Employee, employee.id)

        groups_q = select(Group).where(
            Group.is_configured == True,
            or_(Group.business_unit_id.is_(None), Group.business_unit_id == emp.business_unit_id),
            or_(Group.role_id.is_(None), Group.role_id == emp.role_id),
        )
        groups = (await session.scalars(groups_q)).all()

        name = emp.name or f"@{emp.tg_username}"

        if not groups:
            await hr_message.reply_text(
                f"✅ {name} добавлен в систему, но подходящих групп не найдено."
            )
            return

        if emp.tg_user_id:
            # Employee already started the bot — add directly to groups
            added_groups = []
            failed_groups = []
            for group in groups:
                try:
                    await bot.add_chat_member(
                        chat_id=group.tg_chat_id,
                        user_id=emp.tg_user_id
                    )
                    added_groups.append(group.name)
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
                    failed_groups.append(group.name)

            await session.commit()

            groups_list = ", ".join(added_groups) if added_groups else "—"
            await bot.send_message(
                chat_id=emp.tg_user_id,
                text=(
                    f"👋 Добро пожаловать в команду!\n\n"
                    f"Я добавил тебя в: {groups_list}.\n"
                    "Если что-то не открывается — напиши своему руководителю."
                )
            )
            msg = f"✅ {name} уже запустил бота — добавлен в группы: {groups_list}."
            if failed_groups:
                msg += f"\n⚠️ Не удалось добавить в: {', '.join(failed_groups)}."
            await hr_message.reply_text(msg)

        else:
            # Employee hasn't started the bot yet — give HR a short forward message
            bot_me = await bot.get_me()
            bot_username = bot_me.username
            await hr_message.reply_text(
                f"✅ {name} добавлен в систему.\n\n"
                f"📨 Перешли ему это сообщение:\n\n"
                f"——————————\n"
                f"Привет! Напиши боту @{bot_username} команду /start "
                f"— он автоматически подключит тебя к нужным рабочим группам.\n"
                f"——————————"
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
            await session.refresh(employee)
            # Auto-onboard: add to groups immediately
            from bot.handlers.onboarding import run_onboarding as _onboard
            await _onboard(update.get_bot(), employee, update.message)
            return
        else:
            await update.message.reply_text(
                "Привет! Ты пока не числишься в системе. Обратись к HR."
            )

