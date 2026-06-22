from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Group, BusinessUnit, Venue, Role


async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when bot is added to a group."""
    chat = update.effective_chat
    new_members = update.message.new_chat_members or []

    bot_was_added = any(m.id == context.bot.id for m in new_members)
    if not bot_was_added:
        return

    async with AsyncSessionLocal() as session:
        # Register group if not already registered
        existing = await session.scalar(
            select(Group).where(Group.tg_chat_id == chat.id)
        )
        if not existing:
            group = Group(tg_chat_id=chat.id, name=chat.title or "Unknown")
            session.add(group)
            await session.commit()

    await update.message.reply_text(
        f"👋 Группа *{chat.title}* добавлена в реестр.\n\n"
        "Для настройки напиши /setup — потребуются права администратора с разрешением блокировать пользователей.",
        parse_mode="Markdown"
    )


async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start group setup flow. Only admins with can_restrict_members can proceed."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("Эта команда работает только в группах.")
        return

    # Check permission
    member: ChatMember = await context.bot.get_chat_member(chat.id, user.id)
    if not getattr(member, "can_restrict_members", False):
        await update.message.reply_text("⛔ Нет прав для настройки группы.")
        return

    async with AsyncSessionLocal() as session:
        group = await session.scalar(
            select(Group).where(Group.tg_chat_id == chat.id)
        )
        if not group:
            await update.message.reply_text("Группа не найдена в реестре. Удали бота и добавь снова.")
            return

        business_units = (await session.scalars(select(BusinessUnit))).all()

    if not business_units:
        await update.message.reply_text(
            "Сначала добавь бизнес-юниты через /admin в личке бота."
        )
        return

    buttons = [
        [InlineKeyboardButton(bu.name, callback_data=f"setup_bu:{chat.id}:{bu.id}")]
        for bu in business_units
    ]
    buttons.append([InlineKeyboardButton("🌐 Все бизнес-юниты", callback_data=f"setup_bu:{chat.id}:0")])

    await update.message.reply_text(
        "⚙️ Настройка группы\n\nШаг 1/3: Для какого бизнес-юнита эта группа?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle setup wizard steps via inline buttons."""
    query = update.callback_query
    await query.answer()
    data = query.data

    async with AsyncSessionLocal() as session:

        # Step 1 → Step 2: business unit chosen, now pick venue
        if data.startswith("setup_bu:"):
            _, chat_id, bu_id = data.split(":")
            chat_id, bu_id = int(chat_id), int(bu_id)

            context.user_data["setup"] = {"chat_id": chat_id, "bu_id": bu_id or None}

            if bu_id == 0:
                # All BUs → skip venue, go to role
                venues = []
            else:
                venues = (
                    await session.scalars(
                        select(Venue).where(Venue.business_unit_id == bu_id)
                    )
                ).all()

            if not venues:
                # No venues or all BUs — skip to role step
                return await _ask_role(query, context, session, chat_id)

            buttons = [
                [InlineKeyboardButton(v.name, callback_data=f"setup_venue:{chat_id}:{v.id}")]
                for v in venues
            ]
            buttons.append([InlineKeyboardButton("🌐 Все заведения", callback_data=f"setup_venue:{chat_id}:0")])

            await query.edit_message_text(
                "Шаг 2/3: Для какого заведения?",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        # Step 2 → Step 3: venue chosen, now pick role
        elif data.startswith("setup_venue:"):
            _, chat_id, venue_id = data.split(":")
            chat_id, venue_id = int(chat_id), int(venue_id)

            context.user_data.setdefault("setup", {})
            context.user_data["setup"]["venue_id"] = venue_id or None
            context.user_data["setup"]["chat_id"] = chat_id

            await _ask_role(query, context, session, chat_id)

        # Step 3: role chosen → save
        elif data.startswith("setup_role:"):
            _, chat_id, role_id = data.split(":")
            chat_id, role_id = int(chat_id), int(role_id)

            setup = context.user_data.get("setup", {})
            bu_id = setup.get("bu_id")
            venue_id = setup.get("venue_id")

            group = await session.scalar(
                select(Group).where(Group.tg_chat_id == chat_id)
            )
            if group:
                group.business_unit_id = bu_id
                group.venue_id = venue_id
                group.role_id = role_id or None
                group.is_configured = True
                await session.commit()

            await query.edit_message_text("✅ Группа настроена и добавлена в реестр.")


async def _ask_role(query, context, session, chat_id: int):
    roles = (await session.scalars(select(Role))).all()

    buttons = [
        [InlineKeyboardButton(r.name, callback_data=f"setup_role:{chat_id}:{r.id}")]
        for r in roles
    ]
    buttons.append([InlineKeyboardButton("👥 Все роли", callback_data=f"setup_role:{chat_id}:0")])

    await query.edit_message_text(
        "Шаг 3/3: Для каких ролей эта группа?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
