from telegram import Bot, Message
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, or_
from db.database import AsyncSessionLocal
from db.models import Group, GroupMember, Employee, BotAdmin, PendingUser
from config import SUPERADMIN_ID


async def run_onboarding(bot: Bot, employee: Employee, hr_message: Message):
    """Onboard employee: send invite links directly if tg_user_id known, otherwise give HR a forward message."""
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
                f"✅ {name} добавлен(а) в систему, но подходящих групп не найдено."
            )
            return

        # If tg_user_id not set, check PendingUser table
        if not emp.tg_user_id and emp.tg_username:
            pending = await session.scalar(
                select(PendingUser).where(PendingUser.tg_username == emp.tg_username)
            )
            if pending:
                emp.tg_user_id = pending.tg_user_id
                if not emp.name:
                    emp.name = pending.full_name
                    name = emp.name or f"@{emp.tg_username}"
                await session.commit()

        # Generate invite links
        links = []
        for group in groups:
            try:
                link = await bot.create_chat_invite_link(
                    chat_id=group.tg_chat_id,
                    member_limit=1,
                    name=f"Онбординг {emp.tg_username or emp.name}"
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
                f"⚠️ Не удалось создать ссылки для {name}. "
                "Проверь права бота в группах (Пригласительные ссылки)."
            )
            return

        links_text = "\n".join(f"• {gname}: {url}" for gname, url in links)

        if emp.tg_user_id:
            try:
                await bot.send_message(
                    chat_id=emp.tg_user_id,
                    text=(
                        f"👋 Добро пожаловать в команду!\n\n"
                        f"Вот ссылки на твои рабочие группы:\n\n{links_text}\n\n"
                        "Ссылки одноразовые — используй каждую один раз."
                    )
                )
                await hr_message.reply_text(
                    f"✅ {name} уже запускал(а) бота — ссылки отправлены ему/ей в личку."
                )
            except Exception:
                await hr_message.reply_text(
                    f"✅ {name} добавлен(а) в систему.\n\n"
                    f"📎 Ссылки ({len(links)} групп):\n\n{links_text}"
                )
        else:
            bot_me = await bot.get_me()
            bot_username = bot_me.username
            await hr_message.reply_text(
                f"✅ {name} добавлен(а) в систему.\n\n"
                f"📨 Перешли ему/ей это сообщение:\n\n"
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

        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if employee:
            employee.tg_user_id = user.id
            employee.name = user.full_name
            await session.commit()
            await session.refresh(employee)
            await run_onboarding(update.get_bot(), employee, update.message)
            return
        else:
            pending = await session.get(PendingUser, user.id)
            if not pending:
                session.add(PendingUser(
                    tg_user_id=user.id,
                    tg_username=username,
                    full_name=user.full_name,
                ))
                await session.commit()
            await update.message.reply_text(
                "Привет! Ты пока не числишься в системе. Обратись к своему руководителю — "
                "как только тебя оформят, я автоматически добавлю тебя в нужные группы."
            )
