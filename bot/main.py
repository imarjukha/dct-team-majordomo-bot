import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)
from config import BOT_TOKEN
from db.database import init_db
from bot.handlers.group_setup import on_bot_added, setup_command, setup_callback
from bot.handlers.hr_group import (
    hr_group_message, handle_hr_clarify_callback, handle_cancel_offboard_callback
)
from bot.handlers.onboarding import handle_start
from bot.handlers.activity import count_message
from bot.handlers.commands import (
    cmd_debug, admin_menu, admin_callback, handle_text_input,
    set_employee, handle_new_venue_bu_callback,
)
from bot.handlers.admin_auth import (
    ensure_superadmin_in_db, cmd_add_admin, cmd_remove_admin, cmd_list_admins
)
from scheduler.weekly_report import send_weekly_report, process_scheduled_offboardings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(f"❌ Ошибка: {context.error}")
        except Exception:
            pass


async def post_init(application: Application):
    await init_db()
    await ensure_superadmin_in_db()
    logger.info("Database initialized, superadmin ensured")

    scheduler = AsyncIOScheduler()

    # Weekly activity report — Monday 9:00
    scheduler.add_job(
        send_weekly_report,
        trigger="cron",
        day_of_week="mon", hour=9, minute=0,
        args=[application.bot],
    )

    # Deferred offboardings — every day at 23:59
    scheduler.add_job(
        process_scheduled_offboardings,
        trigger="cron",
        hour=23, minute=59,
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

    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("set_employee", set_employee))
    app.add_handler(CommandHandler("add_admin", cmd_add_admin))
    app.add_handler(CommandHandler("remove_admin", cmd_remove_admin))
    app.add_handler(CommandHandler("admins", cmd_list_admins))
    app.add_handler(CommandHandler("setup", setup_command))
    app.add_handler(CommandHandler("debug", cmd_debug))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(setup_callback, pattern="^setup_"))
    app.add_handler(CallbackQueryHandler(handle_new_venue_bu_callback, pattern="^admin:new_venue_bu:"))
    app.add_handler(CallbackQueryHandler(handle_hr_clarify_callback, pattern="^hr_clarify:"))
    app.add_handler(CallbackQueryHandler(handle_cancel_offboard_callback, pattern="^cancel_offboard:"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))

    # Bot added to group
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_bot_added))

    # PRIVATE: text input for admin panel
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_text_input
    ))

    # GROUPS: HR group messages
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        hr_group_message
    ))

    # Activity counter
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, count_message))

    logger.info("Bot started")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
# redeploy
