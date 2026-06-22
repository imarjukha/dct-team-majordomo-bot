from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func, delete
from db.database import AsyncSessionLocal
from db.models import Group, Employee, BusinessUnit, Venue, Role, AdminState
from bot.handlers.admin_auth import require_admin, is_admin


async def _get_state(user_id: int) -> str | None:
    async with AsyncSessionLocal() as session:
        row = await session.get(AdminState, user_id)
        return row.action if row else None


async def _set_state(user_id: int, action: str | None):
    async with AsyncSessionLocal() as session:
        row = await session.get(AdminState, user_id)
        if action is None:
            if row:
                await session.delete(row)
        else:
            if row:
                row.action = action
            else:
                session.add(AdminState(tg_user_id=user_id, action=action))
        await session.commit()


@require_admin
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await _set_state(update.effective_user.id, None)  # сбросить состояние
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

    if not await is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа.")
        return

    data = query.data
    user_id = query.from_user.id

    async with AsyncSessionLocal() as session:

        if data == "admin:groups":
            groups = (await session.scalars(select(Group))).all()
            text = ("📋 *Группы:*\n" + "\n".join(
                f"• {g.name} {'✅' if g.is_configured else '⚙️ не настроена'}" for g in groups
            )) if groups else "Групп пока нет. Добавь бота в группу и выполни /setup."
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
            await _set_state(user_id, None)
            bus = (await session.scalars(select(BusinessUnit))).all()
            text = ("🏢 *Бизнес-юниты:*\n" + "\n".join(f"• {b.name}" for b in bus)) if bus else "Нет бизнес-юнитов."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_bu")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin:venues":
            await _set_state(user_id, None)
            venues = (await session.scalars(select(Venue))).all()
            text = ("🏠 *Заведения:*\n" + "\n".join(f"• {v.name}" for v in venues)) if venues else "Нет заведений."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin:roles":
            await _set_state(user_id, None)
            roles = (await session.scalars(select(Role))).all()
            text = ("💼 *Роли:*\n" + "\n".join(f"• {r.name}" for r in roles)) if roles else "Нет ролей."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_role")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data in ("admin:add_bu", "admin:add_venue", "admin:add_role"):
            entity_map = {"admin:add_bu": ("bu", "бизнес-юнит"), "admin:add_venue": ("venue", "заведение"), "admin:add_role": ("role", "роль")}
            key, label = entity_map[data]
            await _set_state(user_id, key)
            await query.edit_message_text(
                f"Напиши название ({label}).\n"
                f"После каждого я подтвержу запись и предложу добавить ещё."
            )


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if not await is_admin(update.effective_user.id):
        return

    user_id = update.effective_user.id
    adding = await _get_state(user_id)
    if not adding:
        return

    name = update.message.text.strip()

    async with AsyncSessionLocal() as session:
        if adding == "bu":
            existing = await session.scalar(select(BusinessUnit).where(BusinessUnit.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            session.add(BusinessUnit(name=name))
            label, add_cb, list_cb = "Бизнес-юнит", "admin:add_bu", "admin:bus"

        elif adding == "venue":
            existing = await session.scalar(select(Venue).where(Venue.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            session.add(Venue(name=name, business_unit_id=1))
            label, add_cb, list_cb = "Заведение", "admin:add_venue", "admin:venues"

        elif adding == "role":
            existing = await session.scalar(select(Role).where(Role.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            session.add(Role(name=name))
            label, add_cb, list_cb = "Роль", "admin:add_role", "admin:roles"

        else:
            return

        await session.commit()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("+ Добавить ещё", callback_data=add_cb)],
        [InlineKeyboardButton("◀️ К списку", callback_data=list_cb)],
    ])
    await update.message.reply_text(
        f"✅ {label} «{name}» сохранён.",
        reply_markup=keyboard
    )


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


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current DB state for debugging."""
    if not await is_admin(update.effective_user.id):
        return
    user_id = update.effective_user.id
    state = await _get_state(user_id)
    async with AsyncSessionLocal() as session:
        bus = (await session.scalars(select(BusinessUnit))).all()
        venues = (await session.scalars(select(Venue))).all()
        roles = (await session.scalars(select(Role))).all()
    bu_names = ", ".join(b.name for b in bus) or "—"
    venue_names = ", ".join(v.name for v in venues) or "—"
    role_names = ", ".join(r.name for r in roles) or "—"
    text = (
        f"Debug info\n"
        f"user_id: {user_id}\n"
        f"state: {state}\n"
        f"BUs: {bu_names}\n"
        f"Venues: {venue_names}\n"
        f"Roles: {role_names}"
    )
    await update.message.reply_text(text)
