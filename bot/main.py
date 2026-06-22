import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from config import BOT_TOKEN
from db.database import init_db
from bot.handlers.group_setup import on_bot_added, setup_command, setup_callback
from bot.handlers.hr_group import hr_group_message
from bot.handlers.onboarding import handle_start
from bot.handlers.activity import count_message
from bot.handlers.commands import (
    admin_menu, admin_callback, handle_text_input, set_employee
)
from bot.handlers.admin_auth import (
    ensure_superadmin_in_db, cmd_add_admin, cmd_remove_admin, cmd_list_admins
)
from scheduler.weekly_report import send_weekly_report

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application):
    await init_db()
    await ensure_superadmin_in_db()
    logger.info("Database initialized, superadmin ensured")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_weekly_report,
        trigger="cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        args=[application.bot],
    )
    scheduler.start()
    logger.info("Scheduler started")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # /start — employee or admin registers
    app.add_handler(CommandHandler("start", handle_start))

    # Admin panel (private, protected)
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("set_employee", set_employee))

    # Admin management (superadmin only)
    app.add_handler(CommandHandler("add_admin", cmd_add_admin))
    app.add_handler(CommandHandler("remove_admin", cmd_remove_admin))
    app.add_handler(CommandHandler("admins", cmd_list_admins))

    # Group setup
    app.add_handler(CommandHandler("setup", setup_command))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(setup_callback, pattern="^setup_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))

    # Bot added to group
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_bot_added))

    # HR group messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hr_group_message))

    # Text input in private (adding BU/venue/role)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_text_input
    ))

    # Activity counter
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, count_message))

    logger.info("Bot started")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
