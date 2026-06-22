import json
import re
import anthropic
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Employee, BusinessUnit, Venue, Role
from config import HR_GROUP_ID, ANTHROPIC_API_KEY
from bot.handlers.onboarding import run_onboarding
from bot.handlers.offboarding import run_offboarding

ai_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Must contain @username — everything else is AI's job
USERNAME_RE = re.compile(r"@(\w+)")


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
        return  # No @username — not an event

    parsed = await _parse_hr_message(text)
    if not parsed:
        return

    action = parsed.get("action")          # "hire" | "fire" | None
    username = parsed.get("username")
    role_name = parsed.get("role")
    bu_name = parsed.get("business_unit")
    venue_name = parsed.get("venue")

    if action == "hire":
        await _handle_hire(update, context, username, role_name, bu_name, venue_name)
    elif action == "fire":
        await _handle_fire(update, context, username)


async def _parse_hr_message(text: str) -> dict | None:
    """Use Claude to extract structured data from HR message."""
    prompt = f"""Ты парсер HR-сообщений для Telegram-бота управления группами.

Проанализируй сообщение и верни JSON. Только JSON, без пояснений.

Поля:
- action: "hire" (принят/нанят/выходит/добавляем) | "fire" (уволен/увольняется/покидает) | null
- username: telegram username без @ (если есть)
- role: должность/роль на русском (если упомянута) | null
- business_unit: бизнес-юнит (если упомянут) | null  
- venue: конкретное заведение (если упомянуто) | null

Сообщение: {text}"""

    try:
        response = await ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        return json.loads(raw)
    except Exception:
        return None


async def _handle_hire(update, context, username, role_name, bu_name, venue_name):
    async with AsyncSessionLocal() as session:
        # Find or create employee
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )

        # Resolve role/bu/venue from DB
        role = await _resolve(session, Role, role_name)
        bu = await _resolve(session, BusinessUnit, bu_name)
        venue = await _resolve(session, Venue, venue_name)

        missing = []
        if not role: missing.append("роль")
        if not bu: missing.append("бизнес-юнит")
        if not venue: missing.append("заведение")

        if missing:
            # Ask HR to clarify
            await update.message.reply_text(
                f"✅ Найм @{username} зафиксирован.\n"
                f"⚠️ Не удалось определить: {', '.join(missing)}.\n"
                f"Уточни командой:\n"
                f"`/set_employee @{username} role:... bu:... venue:...`",
                parse_mode="Markdown"
            )
            # Save with nulls for now
            if not employee:
                employee = Employee(
                    tg_username=username,
                    business_unit_id=bu.id if bu else None,
                    venue_id=venue.id if venue else None,
                    role_id=role.id if role else None,
                )
                session.add(employee)
                await session.commit()
            return

        if not employee:
            employee = Employee(
                tg_username=username,
                business_unit_id=bu.id,
                venue_id=venue.id,
                role_id=role.id,
            )
            session.add(employee)
        else:
            employee.business_unit_id = bu.id
            employee.venue_id = venue.id
            employee.role_id = role.id
            employee.status = "active"
            employee.fired_at = None

        await session.commit()
        await session.refresh(employee)

    await run_onboarding(context.bot, employee, update.message)


async def _handle_fire(update, context, username):
    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee).where(Employee.tg_username == username)
        )
        if not employee:
            await update.message.reply_text(f"⚠️ Сотрудник @{username} не найден в базе.")
            return

    kicked_from = await run_offboarding(context.bot, employee)
    await update.message.reply_text(
        f"🚫 @{username} удалён из {kicked_from} групп."
    )


async def _resolve(session, model, name: str | None):
    if not name:
        return None
    return await session.scalar(
        select(model).where(model.name.ilike(f"%{name}%"))
    )
