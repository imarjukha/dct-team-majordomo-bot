import json
import re
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Employee, BusinessUnit, Venue, Role, AdminState
from config import HR_GROUP_ID, ANTHROPIC_API_KEY
from bot.handlers.onboarding import run_onboarding
from bot.handlers.offboarding import run_offboarding

ai_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

USERNAME_RE = re.compile(r"@(\w+)")


async def _load_catalog() -> dict:
    """Load all BUs, venues, roles from DB for prompt context."""
    async with AsyncSessionLocal() as session:
        bus = (await session.scalars(select(BusinessUnit))).all()
        venues = (await session.scalars(select(Venue))).all()
        roles = (await session.scalars(select(Role))).all()
    return {
        "bus": [{"id": b.id, "name": b.name} for b in bus],
        "venues": [{"id": v.id, "name": v.name, "bu_id": v.business_unit_id} for v in venues],
        "roles": [{"id": r.id, "name": r.name} for r in roles],
    }


async def hr_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Listen to HR group messages and detect hire/fire events."""
    if update.effective_chat.id != HR_GROUP_ID:
        return

    message = update.message
    if not message or not message.text:
        return

    text = message.text
    usernames = USERNAME_RE.findall(text)
    if not usernames:
        return

    catalog = await _load_catalog()
    parsed = await _parse_hr_message(text, catalog)
    if not parsed:
        return

    action = parsed.get("action")
    username = parsed.get("username")
    role_id = parsed.get("role_id")
    bu_id = parsed.get("bu_id")
    venue_id = parsed.get("venue_id")
    missing = parsed.get("missing", [])

    if not action or not username:
        return

    if action == "hire":
        await _handle_hire(update, context, username, role_id, bu_id, venue_id, missing, catalog)
    elif action == "fire":
        await _handle_fire(update, context, username)


async def _parse_hr_message(text: str, catalog: dict) -> dict | None:
    """Use Claude to extract structured data, matching against actual DB catalog."""

    bu_list = ", ".join(f'"{b["name"]}"' for b in catalog["bus"]) or "нет данных"
    venue_list = ", ".join(f'"{v["name"]}"' for v in catalog["venues"]) or "нет данных"
    role_list = ", ".join(f'"{r["name"]}"' for r in catalog["roles"]) or "нет данных"

    prompt = f"""Ты парсер HR-сообщений. Извлеки данные о найме или увольнении сотрудника.

Доступные значения в системе:
- Бизнес-юниты: {bu_list}
- Заведения: {venue_list}  
- Роли: {role_list}

Верни ТОЛЬКО JSON, без пояснений:
{{
  "action": "hire" | "fire" | null,
  "username": "username_без_@" | null,
  "role_name": "точное название из списка выше или null если не упомянута/не ясна",
  "bu_name": "точное название из списка выше или null если не упомянут/не ясен",
  "venue_name": "точное название из списка выше или null если не упомянуто/не ясно",
  "missing": ["role" | "bu" | "venue"]  // список того что не удалось определить
}}

Правила:
- Матч нечёткий: "аэроплан" → "AEROPLAN", "варит кофе" → роль "Бариста" если есть такая
- Если заведение однозначно указывает на BU — выведи оба
- missing = список полей которые реально нужны но не определены из текста

Сообщение: {text}"""

    try:
        response = await ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)

        # Resolve names to IDs
        bu_map = {b["name"]: b["id"] for b in catalog["bus"]}
        venue_map = {v["name"]: v["id"] for v in catalog["venues"]}
        role_map = {r["name"]: r["id"] for r in catalog["roles"]}

        # Fuzzy match helper
        def find_id(name, mapping):
            if not name:
                return None
            # exact
            if name in mapping:
                return mapping[name]
            # case-insensitive
            for k, v in mapping.items():
                if k.lower() == name.lower():
                    return v
            # partial
            for k, v in mapping.items():
                if name.lower() in k.lower() or k.lower() in name.lower():
                    return v
            return None

        role_id = find_id(data.get("role_name"), role_map)
        bu_id = find_id(data.get("bu_name"), bu_map)
        venue_id = find_id(data.get("venue_name"), venue_map)

        # If venue found but BU not — infer BU from venue
        if venue_id and not bu_id:
            for v in catalog["venues"]:
                if v["id"] == venue_id:
                    bu_id = v["bu_id"]
                    break

        # Recalculate missing
        missing = []
        if not role_id:
            missing.append("role")
        if not bu_id:
            missing.append("bu")
        if not venue_id:
            missing.append("venue")

        return {
            "action": data.get("action"),
            "username": data.get("username"),
            "role_id": role_id,
            "bu_id": bu_id,
            "venue_id": venue_id,
            "missing": missing,
        }
    except Exception:
        return None


async def _handle_hire(update, context, username, role_id, bu_id, venue_id, missing, catalog):
    # Save employee to DB first
    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if not employee:
            employee = Employee(
                tg_username=username,
                role_id=role_id,
                business_unit_id=bu_id,
                venue_id=venue_id,
            )
            session.add(employee)
        else:
            if role_id: employee.role_id = role_id
            if bu_id: employee.business_unit_id = bu_id
            if venue_id: employee.venue_id = venue_id
            employee.status = "active"
            employee.fired_at = None
        await session.commit()
        await session.refresh(employee)

    if missing:
        # Build clarification keyboard
        label_map = {"role": "💼 Роль", "bu": "🏢 Бизнес-юнит", "venue": "🏠 Заведение"}
        first_missing = missing[0]

        if first_missing == "role":
            buttons = [
                [InlineKeyboardButton(r["name"], callback_data=f"hr_clarify:role:{username}:{r['id']}")]
                for r in catalog["roles"]
            ]
            question = f"Уточни роль для @{username}:"
        elif first_missing == "bu":
            buttons = [
                [InlineKeyboardButton(b["name"], callback_data=f"hr_clarify:bu:{username}:{b['id']}")]
                for b in catalog["bus"]
            ]
            question = f"Уточни бизнес-юнит для @{username}:"
        else:  # venue
            buttons = [
                [InlineKeyboardButton(v["name"], callback_data=f"hr_clarify:venue:{username}:{v['id']}")]
                for v in catalog["venues"]
            ]
            question = f"Уточни заведение для @{username}:"

        still_missing = [label_map[m] for m in missing[1:]]
        note = f"\n_После этого ещё уточним: {', '.join(still_missing)}_" if still_missing else ""

        await update.message.reply_text(
            f"✅ @{username} зафиксирован.\n{question}{note}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        # All data complete — run onboarding immediately
        await run_onboarding(context.bot, employee, update.message)


async def handle_hr_clarify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle clarification button presses in HR group."""
    query = update.callback_query
    await query.answer()

    # Format: hr_clarify:{field}:{username}:{id}
    parts = query.data.split(":")
    field = parts[1]
    username = parts[2]
    value_id = int(parts[3])

    catalog = await _load_catalog()

    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if not employee:
            await query.edit_message_text(f"⚠️ @{username} не найден в базе.")
            return

        if field == "role":
            employee.role_id = value_id
        elif field == "bu":
            employee.business_unit_id = value_id
        elif field == "venue":
            employee.venue_id = value_id

        await session.commit()
        await session.refresh(employee)

        # Check what's still missing
        missing = []
        if not employee.role_id:
            missing.append("role")
        if not employee.business_unit_id:
            missing.append("bu")
        if not employee.venue_id:
            missing.append("venue")

    if missing:
        # Ask next missing field
        first_missing = missing[0]
        if first_missing == "role":
            buttons = [
                [InlineKeyboardButton(r["name"], callback_data=f"hr_clarify:role:{username}:{r['id']}")]
                for r in catalog["roles"]
            ]
            question = f"Теперь уточни роль для @{username}:"
        elif first_missing == "bu":
            buttons = [
                [InlineKeyboardButton(b["name"], callback_data=f"hr_clarify:bu:{username}:{b['id']}")]
                for b in catalog["bus"]
            ]
            question = f"Теперь уточни бизнес-юнит для @{username}:"
        else:
            buttons = [
                [InlineKeyboardButton(v["name"], callback_data=f"hr_clarify:venue:{username}:{v['id']}")]
                for v in catalog["venues"]
            ]
            question = f"Теперь уточни заведение для @{username}:"

        label_map = {"role": "💼 Роль", "bu": "🏢 Бизнес-юнит", "venue": "🏠 Заведение"}
        still_missing = [label_map[m] for m in missing[1:]]
        note = f"\n_После этого ещё уточним: {', '.join(still_missing)}_" if still_missing else ""

        await query.edit_message_text(
            f"{question}{note}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        # All data complete — run onboarding
        await query.edit_message_text(f"✅ Все данные заполнены. Запускаю онбординг @{username}...")
        async with AsyncSessionLocal() as session:
            employee = await session.scalar(
                select(Employee).where(Employee.tg_username == username)
            )
        await run_onboarding(context._application.bot, employee, query.message)


async def _handle_fire(update, context, username):
    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if not employee:
            await update.message.reply_text(f"⚠️ Сотрудник @{username} не найден в базе.")
            return

    kicked_from = await run_offboarding(context.bot, employee)
    await update.message.reply_text(f"🚫 @{username} удалён из {kicked_from} групп.")
