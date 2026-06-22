from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from db.database import AsyncSessionLocal
from db.models import Group, Employee, BusinessUnit, Venue, Role, ActivityLog
from bot.handlers.admin_auth import require_admin, require_superadmin


@require_admin
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    keyboard = [
        [InlineKeyboardButton("📋 Группы", callback_data="admin:groups"),
         InlineKeyboardButton("👥 Сотрудники", callback_data="admin:employees")],
        [InlineKeyboardButton("🏢 Бизнес-юниты", callback_data="admin:bus"),
         InlineKeyboardButton("🏠 Заведения", callback_data="admin:venues")],
        [InlineKeyboardButton("💼 Роли", callback_data="admin:roles")],
    ]
    await update.message.reply_text(
        "⚙️ *Панель управления*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    from bot.handlers.admin_auth import is_admin
    if not await is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа.")
        return

    data = query.data

    async with AsyncSessionLocal() as session:

        if data == "admin:groups":
            groups = (await session.scalars(select(Group))).all()
            if not groups:
                text = "Групп пока нет. Добавь бота в группу и выполни /setup."
            else:
                lines = []
                for g in groups:
                    status = "✅" if g.is_configured else "⚙️ не настроена"
                    lines.append(f"• {g.name} {status}")
                text = "📋 *Группы:*\n" + "\n".join(lines)
            await query.edit_message_text(text, parse_mode="Markdown")

        elif data == "admin:employees":
            total = await session.scalar(select(func.count(Employee.id)))
            active = await session.scalar(
                select(func.count(Employee.id)).where(Employee.status == "active")
            )
            await query.edit_message_text(
                f"👥 *Сотрудники:*\nВсего: {total}\nАктивных: {active}",
                parse_mode="Markdown"
            )

        elif data == "admin:bus":
            bus = (await session.scalars(select(BusinessUnit))).all()
            text = "🏢 *Бизнес-юниты:*\n" + "\n".join(f"• {b.name}" for b in bus) if bus else "Нет бизнес-юнитов."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_bu")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin:venues":
            venues = (await session.scalars(select(Venue))).all()
            text = "🏠 *Заведения:*\n" + "\n".join(f"• {v.name}" for v in venues) if venues else "Нет заведений."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin:roles":
            roles = (await session.scalars(select(Role))).all()
            text = "💼 *Роли:*\n" + "\n".join(f"• {r.name}" for r in roles) if roles else "Нет ролей."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_role")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data in ("admin:add_bu", "admin:add_venue", "admin:add_role"):
            entity = {"admin:add_bu": "бизнес-юнит", "admin:add_venue": "заведение", "admin:add_role": "роль"}[data]
            context.user_data["adding"] = data.replace("admin:add_", "")
            await query.edit_message_text(f"Напиши название ({entity}):")


@require_admin
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    adding = context.user_data.get("adding")
    if not adding:
        return

    name = update.message.text.strip()
    context.user_data.pop("adding")

    async with AsyncSessionLocal() as session:
        if adding == "bu":
            session.add(BusinessUnit(name=name))
            label = "Бизнес-юнит"
        elif adding == "venue":
            session.add(Venue(name=name, business_unit_id=1))
            label = "Заведение"
        elif adding == "role":
            session.add(Role(name=name))
            label = "Роль"
        else:
            return
        await session.commit()

    await update.message.reply_text(f"✅ {label} «{name}» добавлен.")


@require_admin
async def set_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import re
    args = " ".join(context.args)
    username_match = re.search(r"@(\w+)", args)
    if not username_match:
        await update.message.reply_text("Укажи @username")
        return

    username = username_match.group(1)
    params = dict(re.findall(r"(\w+):([^\s]+)", args))

    async with AsyncSessionLocal() as session:
        emp = await session.scalar(select(Employee).where(Employee.tg_username == username))
        if not emp:
            await update.message.reply_text(f"@{username} не найден.")
            return

        if role_name := params.get("role"):
            role = await session.scalar(select(Role).where(Role.name.ilike(f"%{role_name}%")))
            if role: emp.role_id = role.id

        if bu_name := params.get("bu"):
            bu = await session.scalar(select(BusinessUnit).where(BusinessUnit.name.ilike(f"%{bu_name}%")))
            if bu: emp.business_unit_id = bu.id

        if venue_name := params.get("venue"):
            venue = await session.scalar(select(Venue).where(Venue.name.ilike(f"%{venue_name}%")))
            if venue: emp.venue_id = venue.id

        await session.commit()

    await update.message.reply_text(f"✅ @{username} обновлён.")
