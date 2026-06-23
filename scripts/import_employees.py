import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal, init_db
from db.models import Employee, BusinessUnit, Role

employees = [
    ("Никита",    "Акимушкин",   "westcost777"),
    ("Яна",       "Товменко",    "chebunel"),
    ("Сыймык",    "Касымалиев",  "kasymaliev99"),
    ("Елизавета", "Гейко",       "ms_liza_geyko"),
    ("Руслан",    "Таиров",      "monti_tr"),
    ("Милена",    "Погосян",     "panquem"),
    ("Владимир",  "Головань",    "VladimirGolovan2002"),
]

async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        bu = await session.scalar(select(BusinessUnit).where(BusinessUnit.name.ilike("%AEROPLAN%")))
        if not bu:
            print("ERROR: BU AEROPLAN not found")
            return
        role = await session.scalar(select(Role).where(Role.name.ilike("%бариста%")))
        if not role:
            print("ERROR: Role Бариста not found")
            return

        print(f"BU: {bu.name} (id={bu.id})")
        print(f"Role: {role.name} (id={role.id})")

        added = 0
        skipped = 0
        for first, last, username in employees:
            existing = await session.scalar(
                select(Employee).where(Employee.tg_username == username)
            )
            if existing:
                print(f"  SKIP @{username} — already exists")
                skipped += 1
                continue
            emp = Employee(
                name=f"{first} {last}",
                tg_username=username,
                business_unit_id=bu.id,
                role_id=role.id,
                status="active",
            )
            session.add(emp)
            added += 1
            print(f"  + {first} {last} @{username}")

        await session.commit()
        print(f"\nДобавлено: {added}, пропущено: {skipped}")

asyncio.run(main())
