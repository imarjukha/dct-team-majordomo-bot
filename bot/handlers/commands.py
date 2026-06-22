from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func, delete
from db.database import AsyncSessionLocal
from db.models import Group, Employee, BusinessUnit, Venue, Role, AdminState
from bot.handlers.admin_auth import require_admin, is_admin

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏢 Бизнес-юниты", "🏠 Заведения"],
        ["💼 Роли", "👥 Сотрудники"],
        ["📋 Группы", "⚙️ Главное меню"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

REPLY_KB_ROUTES = {
    "🏢 Бизнес-юниты": "admin:bus",
    "🏠 Заведения": "admin:venues",
    "💼 Роли": "admin:roles",
    "👥 Сотрудники": "admin:employees",
    "📋 Группы": "admin:groups",
}



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
    await _set_state(update.effective_user.id, None)
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
    await update.message.reply_text("Быстрый доступ:", reply_markup=ADMIN_KEYBOARD)


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
            if venues:
                # Load BU names for display
                bus = {b.id: b.name for b in (await session.scalars(select(BusinessUnit))).all()}
                lines = []
                keyboard_rows = []
                for v in venues:
                    bu_name = bus.get(v.business_unit_id, "?")
                    lines.append(f"• {v.name} [{bu_name}]")
                    keyboard_rows.append([InlineKeyboardButton(
                        f"✏️ {v.name} → BU",
                        callback_data=f"admin:venue_set_bu:{v.id}"
                    )])
                text = "🏠 *Заведения:*\n" + "\n".join(lines)
                keyboard_rows.append([InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")])
            else:
                text = "Нет заведений."
                keyboard_rows = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_rows))

        elif data == "admin:roles":
            await _set_state(user_id, None)
            roles = (await session.scalars(select(Role))).all()
            text = ("💼 *Роли:*\n" + "\n".join(f"• {r.name}" for r in roles)) if roles else "Нет ролей."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_role")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin:add_bu":
            await _set_state(user_id, "bu")
            await query.edit_message_text(
                "Напиши название (бизнес-юнит).\n"
                "После каждого я подтвержу запись и предложу добавить ещё."
            )

        elif data == "admin:add_venue":
            await _set_state(user_id, "venue_name")
            await query.edit_message_text(
                "Напиши название (заведение).\n"
                "После каждого я подтвержу запись и предложу добавить ещё."
            )

        elif data == "admin:add_role":
            await _set_state(user_id, "role")
            await query.edit_message_text(
                "Напиши название (роль).\n"
                "После каждого я подтвержу запись и предложу добавить ещё."
            )

        elif data.startswith("admin:venue_bu:"):
            # Format: admin:venue_bu:{venue_id}:{bu_id}
            parts = data.split(":")
            venue_id = int(parts[2])
            bu_id = int(parts[3])
            venue = await session.get(Venue, venue_id)
            bu = await session.get(BusinessUnit, bu_id)
            if venue and bu:
                venue.business_unit_id = bu_id
                await session.commit()
                await _set_state(user_id, None)
                await query.edit_message_text(
                    f"✅ Заведение «{venue.name}» привязано к «{bu.name}»."
                )
            else:
                await query.edit_message_text("⚠️ Не найдено.")

        elif data.startswith("admin:venue_set_bu:"):
            # Show BU picker for existing venue
            venue_id = int(data.split(":")[2])
            venue = await session.get(Venue, venue_id)
            if not venue:
                await query.edit_message_text("⚠️ Заведение не найдено.")
                return
            bus = (await session.scalars(select(BusinessUnit))).all()
            if not bus:
                await query.edit_message_text("⚠️ Сначала создай бизнес-юниты.")
                return
            keyboard = [[InlineKeyboardButton(b.name, callback_data=f"admin:venue_bu:{venue_id}:{b.id}")] for b in bus]
            await query.edit_message_text(
                f"Выбери бизнес-юнит для заведения «{venue.name}»:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )


async def _ask_venue_bu(update, user_id: int, venue_name: str):
    """After venue name entered — show BU picker."""
    async with AsyncSessionLocal() as session:
        bus = (await session.scalars(select(BusinessUnit))).all()
    if not bus:
        # No BUs yet — save with id=1 as fallback and warn
        async with AsyncSessionLocal() as session:
            session.add(Venue(name=venue_name, business_unit_id=1))
            await session.commit()
        await _set_state(user_id, None)
        await update.message.reply_text(
            f"✅ Заведение «{venue_name}» сохранено.\n"
            "⚠️ Бизнес-юнитов нет — привяжи позже через список заведений."
        )
        return
    # Save venue without BU first (use id=0 as temp), then ask BU
    # Better: save with first BU, let user change via button
    await _set_state(user_id, f"venue_bu:{venue_name}")
    keyboard = [[InlineKeyboardButton(b.name, callback_data=f"admin:new_venue_bu:{b.id}")] for b in bus]
    await update.message.reply_text(
        f"Заведение «{venue_name}» — выбери бизнес-юнит:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )



async def _handle_nav_message(update: Update, user_id: int, route: str):
    """Handle Reply Keyboard navigation buttons."""
    async with AsyncSessionLocal() as session:
        if route == "admin:bus":
            bus = (await session.scalars(select(BusinessUnit))).all()
            text = ("🏢 *Бизнес-юниты:*\n" + "\n".join(f"• {b.name}" for b in bus)) if bus else "Нет бизнес-юнитов."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_bu")]]
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif route == "admin:venues":
            venues = (await session.scalars(select(Venue))).all()
            if venues:
                bus_map = {b.id: b.name for b in (await session.scalars(select(BusinessUnit))).all()}
                lines = [f"• {v.name} [{bus_map.get(v.business_unit_id, '?')}]" for v in venues]
                keyboard_rows = [
                    [InlineKeyboardButton(f"✏️ {v.name} → BU", callback_data=f"admin:venue_set_bu:{v.id}")]
                    for v in venues
                ]
                keyboard_rows.append([InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")])
                text = "🏠 *Заведения:*\n" + "\n".join(lines)
            else:
                text = "Нет заведений."
                keyboard_rows = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_venue")]]
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_rows))

        elif route == "admin:roles":
            roles = (await session.scalars(select(Role))).all()
            text = ("💼 *Роли:*\n" + "\n".join(f"• {r.name}" for r in roles)) if roles else "Нет ролей."
            keyboard = [[InlineKeyboardButton("+ Добавить", callback_data="admin:add_role")]]
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif route == "admin:employees":
            total = await session.scalar(select(func.count(Employee.id)))
            active = await session.scalar(
                select(func.count(Employee.id)).where(Employee.status == "active")
            )
            await update.message.reply_text(
                f"👥 *Сотрудники:*\nВсего: {total}\nАктивных: {active}",
                parse_mode="Markdown"
            )

        elif route == "admin:groups":
            groups = (await session.scalars(select(Group))).all()
            text = ("📋 *Группы:*\n" + "\n".join(
                f"• {g.name} {'✅' if g.is_configured else '⚙️ не настроена'}" for g in groups
            )) if groups else "Групп пока нет. Добавь бота в группу и выполни /setup."
            await update.message.reply_text(text, parse_mode="Markdown")


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if not await is_admin(update.effective_user.id):
        return

    user_id = update.effective_user.id
    text = update.message.text

    # Reply Keyboard navigation
    if text == "⚙️ Главное меню":
        await _set_state(user_id, None)
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
        return

    if text in REPLY_KB_ROUTES:
        await _set_state(user_id, None)
        await _handle_nav_message(update, user_id, REPLY_KB_ROUTES[text])
        return

    adding = await _get_state(user_id)
    if not adding:
        return

    name = update.message.text.strip()

    # Step 2 of venue creation: state = "venue_bu:{name}" — handled via callback, not text
    if adding.startswith("venue_bu:"):
        return

    async with AsyncSessionLocal() as session:
        if adding == "bu":
            existing = await session.scalar(select(BusinessUnit).where(BusinessUnit.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            session.add(BusinessUnit(name=name))
            await session.commit()
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("+ Добавить ещё", callback_data="admin:add_bu")],
                [InlineKeyboardButton("◀️ К списку", callback_data="admin:bus")],
            ])
            await update.message.reply_text(f"✅ Бизнес-юнит «{name}» сохранён.", reply_markup=keyboard)

        elif adding == "venue_name":
            existing = await session.scalar(select(Venue).where(Venue.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            # Don't save yet — ask BU first
            await _ask_venue_bu(update, user_id, name)

        elif adding == "role":
            existing = await session.scalar(select(Role).where(Role.name.ilike(name)))
            if existing:
                await update.message.reply_text(f"⚠️ «{name}» уже есть. Напиши другое название или /admin.")
                return
            session.add(Role(name=name))
            await session.commit()
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("+ Добавить ещё", callback_data="admin:add_role")],
                [InlineKeyboardButton("◀️ К списку", callback_data="admin:roles")],
            ])
            await update.message.reply_text(f"✅ Роль «{name}» сохранена.", reply_markup=keyboard)

        else:
            return


async def handle_new_venue_bu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when user picks BU for a new venue."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа.")
        return

    user_id = query.from_user.id
    state = await _get_state(user_id)

    if not state or not state.startswith("venue_bu:"):
        await query.edit_message_text("⚠️ Сессия истекла. Начни заново через /admin.")
        return

    venue_name = state[len("venue_bu:"):]
    bu_id = int(query.data.split(":")[2])  # admin:new_venue_bu:{bu_id}

    async with AsyncSessionLocal() as session:
        bu = await session.get(BusinessUnit, bu_id)
        existing = await session.scalar(select(Venue).where(Venue.name.ilike(venue_name)))
        if existing:
            await _set_state(user_id, None)
            await query.edit_message_text(f"⚠️ «{venue_name}» уже есть.")
            return
        session.add(Venue(name=venue_name, business_unit_id=bu_id))
        await session.commit()

    await _set_state(user_id, None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("+ Добавить ещё", callback_data="admin:add_venue")],
        [InlineKeyboardButton("◀️ К списку", callback_data="admin:venues")],
    ])
    await query.edit_message_text(
        f"✅ Заведение «{venue_name}» сохранено в «{bu.name}».",
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
