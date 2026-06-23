import json
import logging
import re
from datetime import datetime, timedelta
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Employee, BusinessUnit, Venue, Role, ScheduledOffboarding
from config import HR_GROUP_ID, ANTHROPIC_API_KEY
from bot.handlers.onboarding import run_onboarding
from bot.handlers.offboarding import run_offboarding

logger = logging.getLogger(__name__)
ai_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
USERNAME_RE = re.compile(r"@(\w+)")


async def _load_catalog() -> dict:
    async with AsyncSessionLocal() as session:
        bus = (await session.scalars(select(BusinessUnit))).all()
        venues = (await session.scalars(select(Venue))).all()
        roles = (await session.scalars(select(Role))).all()
        employees = (await session.scalars(select(Employee).where(Employee.status == "active"))).all()
    return {
        "bus": [{"id": b.id, "name": b.name} for b in bus],
        "venues": [{"id": v.id, "name": v.name, "bu_id": v.business_unit_id} for v in venues],
        "roles": [{"id": r.id, "name": r.name} for r in roles],
        "employees": [{"id": e.id, "name": e.name or "", "username": e.tg_username or ""} for e in employees],
    }


async def hr_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != HR_GROUP_ID:
        return
    message = update.message
    if not message or not message.text:
        return
    text = message.text
    # Must mention at least one person — either @username or look like an HR event
    # We'll let Claude decide if it's relevant

    catalog = await _load_catalog()
    parsed = await _parse_hr_message(text, catalog)
    if not parsed or not parsed.get("action"):
        return

    action = parsed["action"]
    username = parsed.get("username")

    # If no username from Claude, try to match by full_name in catalog
    if not username:
        full_name = parsed.get("full_name", "")
        if full_name:
            name_lower = full_name.lower().strip()
            for emp in catalog.get("employees", []):
                emp_name = (emp.get("name") or "").lower().strip()
                if emp_name and (name_lower in emp_name or emp_name in name_lower):
                    username = emp.get("username")
                    break
            if not username:
                parts = name_lower.split()
                for emp in catalog.get("employees", []):
                    emp_name = (emp.get("name") or "").lower()
                    if any(part in emp_name for part in parts if len(part) > 3):
                        username = emp.get("username")
                        break

    if not username:
        full_name = parsed.get("full_name", "")
        if full_name:
            await update.message.reply_text(
                f"⚠️ Не удалось найти сотрудника «{full_name}» в базе.\n"
                f"Укажи @username или добавь сотрудника."
            )
        return

    if action == "hire":
        await _handle_hire(
            update, context, username,
            parsed.get("role_id"), parsed.get("bu_id"),
            parsed.get("missing", []), catalog
        )
    elif action == "fire":
        await _handle_fire(update, context, username, parsed.get("last_day"))


async def _parse_hr_message(text: str, catalog: dict) -> dict | None:
    today = datetime.now().strftime("%Y-%m-%d")
    bu_list = ", ".join(f'"{b["name"]}"' for b in catalog["bus"]) or "нет данных"
    venue_list = ", ".join(f'"{v["name"]}"' for v in catalog["venues"]) or "нет данных"
    role_list = ", ".join(f'"{r["name"]}"' for r in catalog["roles"]) or "нет данных"

    prompt = f"""Ты парсер HR-сообщений. Сегодня {today}.

Доступные значения в системе:
- Бизнес-юниты: {bu_list}
- Роли: {role_list}

Верни ТОЛЬКО JSON без пояснений:
{{
  "action": "hire" | "fire" | null,
  "username": "telegram_username без @ или null",
  "full_name": "Имя Фамилия если нет username или null",
  "role_name": "точное название из списка или null",
  "bu_name": "точное название из списка или null",
  "last_day": "YYYY-MM-DD" | null,
  "missing": ["role", "bu", "venue"]
}}

Правила:
- Нечёткий матч: "аэроплан" → "AEROPLAN", "варит кофе" → "Бариста"
- Если заведение однозначно указывает на BU — выведи оба
- last_day: только для увольнения — дата последнего рабочего дня если упомянута
  ("последний день 28 июня" → "2026-06-28", "работает до конца недели" → ближайшая пятница,
   "до конца месяца" → последний день текущего месяца, не упомянута → null)
- missing: поля которые нужны для найма но не определены

Правила:
- Если есть @username — используй его в поле username
- Если только имя/фамилия — запиши в full_name, username оставь null
- "Увольнение ДД.ММ Имя Фамилия" — action="fire", дата в last_day, имя в full_name

Сообщение: {text}"""

    try:
        response = await ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        if not raw or raw == "null":
            return None
        data = json.loads(raw)

        # Resolve names → IDs with fuzzy match
        def find_id(name, items):
            if not name:
                return None
            for item in items:
                if item["name"].lower() == name.lower():
                    return item["id"]
            for item in items:
                if name.lower() in item["name"].lower() or item["name"].lower() in name.lower():
                    return item["id"]
            return None

        role_id = find_id(data.get("role_name"), catalog["roles"])
        bu_id = find_id(data.get("bu_name"), catalog["bus"])
        # Parse last_day
        last_day = None
        raw_date = data.get("last_day")
        if raw_date:
            try:
                last_day = datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                pass

        missing = []
        if not role_id: missing.append("role")
        if not bu_id: missing.append("bu")

        username = data.get("username")
        full_name = data.get("full_name", "")

        # Try to match full_name to existing employee username
        if not username and full_name:
            name_lower = full_name.lower().strip()
            for emp in catalog.get("employees", []):
                emp_name = (emp.get("name") or "").lower().strip()
                if emp_name and (name_lower in emp_name or emp_name in name_lower):
                    username = emp.get("username")
                    break
            if not username:
                parts = name_lower.split()
                for emp in catalog.get("employees", []):
                    emp_name = (emp.get("name") or "").lower()
                    if any(part in emp_name for part in parts if len(part) > 3):
                        username = emp.get("username")
                        break

        return {
            "action": data.get("action"),
            "username": username,
            "full_name": full_name,
            "role_id": role_id,
            "bu_id": bu_id,
            "last_day": last_day,
            "missing": missing,
        }
    except Exception as e:
        logger.error(f"HR_PARSE_ERROR: {e}", exc_info=True)
        return None


async def _handle_hire(update, context, username, role_id, bu_id, missing, catalog):
    async with AsyncSessionLocal() as session:
        employee = await session.scalar(select(Employee).where(Employee.tg_username == username))
        if not employee:
            employee = Employee(tg_username=username, role_id=role_id, business_unit_id=bu_id)
            session.add(employee)
        else:
            if role_id: employee.role_id = role_id
            if bu_id: employee.business_unit_id = bu_id
            employee.status = "active"
            employee.fired_at = None
        await session.commit()
        await session.refresh(employee)

    if missing:
        await _ask_clarification(update.message, username, missing, catalog, first=True)
    else:
        await run_onboarding(context.bot, employee, update.message)


async def _handle_fire(update, context, username, last_day: datetime | None):
    async with AsyncSessionLocal() as session:
        employee = await session.scalar(select(Employee).where(Employee.tg_username == username))
        if not employee:
            await update.message.reply_text(f"⚠️ Сотрудник @{username} не найден в базе.")
            return

        if last_day:
            # Schedule for end of last day
            fire_at = last_day.replace(hour=23, minute=59, second=0)

            # Cancel any previous scheduled offboarding for this employee
            old = await session.scalar(
                select(ScheduledOffboarding).where(
                    ScheduledOffboarding.employee_id == employee.id,
                    ScheduledOffboarding.cancelled == False,
                )
            )
            if old:
                old.cancelled = True

            initiator = update.effective_user.username
            scheduled = ScheduledOffboarding(
                employee_id=employee.id,
                fire_at=fire_at,
                hr_chat_id=update.effective_chat.id,
                hr_message_id=update.message.message_id,
                initiated_by=initiator,
            )
            session.add(scheduled)
            await session.commit()
            scheduled_id = scheduled.id

        else:
            scheduled_id = None
            await session.commit()

    if last_day:
        fire_at = last_day.replace(hour=23, minute=59)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отменить увольнение", callback_data=f"cancel_offboard:{scheduled_id}")
        ]])
        await update.message.reply_text(
            f"🗓 @{username} будет отключён от всех групп {last_day.strftime('%d.%m.%Y')} в 23:59.\n"
            f"Если планы изменятся — нажми кнопку ниже.",
            reply_markup=keyboard
        )
    else:
        kicked = await run_offboarding(context.bot, employee)
        if kicked == -1:
            await update.message.reply_text(
                f"⚠️ @{username} не писал боту /start — не знаем его Telegram ID, удалить из групп не получится.\n"
                "Попроси его написать боту /start или удали вручную."
            )
        else:
            await update.message.reply_text(f"🚫 @{username} удалён из {kicked} групп.")


async def handle_hr_clarify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step-by-step clarification for missing hire fields."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    field, username, value_id = parts[1], parts[2], int(parts[3])

    catalog = await _load_catalog()

    async with AsyncSessionLocal() as session:
        employee = await session.scalar(select(Employee).where(Employee.tg_username == username))
        if not employee:
            await query.edit_message_text(f"⚠️ @{username} не найден.")
            return
        if field == "role":
            employee.role_id = value_id
        elif field == "bu":
            employee.business_unit_id = value_id
        elif field == "venue":
            employee.venue_id = value_id
        await session.commit()
        await session.refresh(employee)

        missing = []
        if not employee.role_id: missing.append("role")
        if not employee.business_unit_id: missing.append("bu")
        emp_copy = employee

    if missing:
        await _ask_clarification(query, username, missing, catalog, first=False)
    else:
        await query.edit_message_text(f"✅ Все данные заполнены. Запускаю онбординг @{username}...")
        await run_onboarding(context.application.bot, emp_copy, query.message)


async def handle_cancel_offboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a scheduled offboarding."""
    query = update.callback_query
    await query.answer()

    scheduled_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        scheduled = await session.get(ScheduledOffboarding, scheduled_id)
        if not scheduled or scheduled.cancelled:
            await query.edit_message_text("⚠️ Запись не найдена или уже отменена.")
            return
        employee = await session.get(Employee, scheduled.employee_id)
        username = employee.tg_username if employee else "?"
        scheduled.cancelled = True
        await session.commit()

    await query.edit_message_text(
        f"✅ Отложенное увольнение @{username} отменено. Сотрудник остаётся в группах."
    )


async def _ask_clarification(target, username, missing, catalog, first: bool):
    label_map = {"role": "💼 Роль", "bu": "🏢 Бизнес-юнит", "venue": "🏠 Заведение"}
    first_missing = missing[0]

    if first_missing == "role":
        buttons = [[InlineKeyboardButton(r["name"], callback_data=f"hr_clarify:role:{username}:{r['id']}")] for r in catalog["roles"]]
        question = f"{'Уточни' if first else 'Теперь уточни'} роль для @{username}:"
    else:  # bu
        buttons = [[InlineKeyboardButton(b["name"], callback_data=f"hr_clarify:bu:{username}:{b['id']}")] for b in catalog["bus"]]
        question = f"{'Уточни' if first else 'Теперь уточни'} бизнес-юнит для @{username}:"

    still = [label_map[m] for m in missing[1:]]
    note = f"\n_После этого ещё уточним: {', '.join(still)}_" if still else ""
    prefix = "✅ Зафиксирован. " if first else ""

    text = f"{prefix}{question}{note}"
    kb = InlineKeyboardMarkup(buttons)

    if hasattr(target, "reply_text"):
        await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

