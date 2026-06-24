"""
LP Work Orders Telegram Bot
Receives forwarded work orders from Ryan, asks follow-up questions,
then emails a formatted PRF-ready summary to ryanzmckay@gmail.com
"""

import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = "ryanzmckay@gmail.com"

sessions = {}

DEPT_OPTIONS = [
    ["Landscaping", "Maintenance"],
    ["Housekeeping", "Operations"],
    ["Security", "Technology"],
    ["F&B (Culinary)", "Engineering"],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *LP Work Orders Bot*\n\n"
        "Forward me a message from your team (text + photos) and I'll format it into a PRF-ready work order.\n\n"
        "Just forward the Telegram message now.",
        parse_mode="Markdown"
    )

async def prompt_location(context: ContextTypes.DEFAULT_TYPE):
    """Called after delay to ask location question."""
    job = context.job
    chat_id = job.chat_id
    user_id = job.data["user_id"]
    session = sessions.get(user_id)
    if not session:
        return
    keyboard = [
        [InlineKeyboardButton("🌿 Outdoor / Landscape", callback_data="loc_outdoor")],
        [InlineKeyboardButton("🏠 Indoor / Room", callback_data="loc_indoor")],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="Got it! Is this an *outdoor/landscape* area or an *indoor room*?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    msg = update.message

    if user_id not in sessions:
        sessions[user_id] = {"photos": [], "texts": [], "caption": "", "chat_id": chat_id}

    session = sessions[user_id]

    # Don't collect more if we're already in a follow-up flow
    if session.get("awaiting_location") or session.get("waiting_for"):
        return

    # Collect photo
    if msg.photo:
        file_id = msg.photo[-1].file_id
        session["photos"].append(file_id)
        if msg.caption:
            session["caption"] = msg.caption

    # Collect text
    if msg.text and not msg.text.startswith("/"):
        session["texts"].append(msg.text)

    combined = " ".join(session["texts"]) + " " + session["caption"]

    if (session["photos"] or session["texts"]) and len(combined.strip()) > 3:
        if not session.get("job_scheduled"):
            session["job_scheduled"] = True
            # Schedule prompt after 2 seconds to catch all forwarded parts
            context.job_queue.run_once(
                prompt_location,
                when=2,
                chat_id=chat_id,
                data={"user_id": user_id},
                name=f"prompt_{user_id}"
            )

async def handle_location_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})
    session["awaiting_location"] = True

    if query.data == "loc_outdoor":
        keyboard = [
            [InlineKeyboardButton("Back Lawn", callback_data="loc_Back Lawn"),
             InlineKeyboardButton("Front Lawn", callback_data="loc_Front Lawn")],
            [InlineKeyboardButton("Pool Area", callback_data="loc_Pool Area"),
             InlineKeyboardButton("Driveway", callback_data="loc_Driveway")],
            [InlineKeyboardButton("Rose Garden", callback_data="loc_Rose Garden"),
             InlineKeyboardButton("Tennis Court", callback_data="loc_Tennis Court")],
            [InlineKeyboardButton("✏️ Other area", callback_data="loc_custom_outdoor")],
        ]
        await query.edit_message_text("Which outdoor area?", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(
            "Type the *room number*:\n_(e.g. 35 for Pool Room, 45 for Gym)_",
            parse_mode="Markdown"
        )
        session["waiting_for"] = "room_number"

async def handle_location_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})

    if query.data == "loc_custom_outdoor":
        await query.edit_message_text("Type the outdoor area name:")
        session["waiting_for"] = "custom_location"
    else:
        location = query.data.replace("loc_", "")
        session["location"] = location
        await ask_dept(query.edit_message_text)

async def ask_dept(reply_fn):
    keyboard = [[InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row]
                for row in DEPT_OPTIONS]
    await reply_fn("Which department is requesting this?", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_dept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})
    session["dept"] = query.data.replace("dept_", "")
    location = session.get("location", "")
    await query.edit_message_text(
        f"PRF number for *{location}*?\n\nType it (e.g. `PRF-001`)\n_First PRF for this location = PRF-001_",
        parse_mode="Markdown"
    )
    session["waiting_for"] = "prf_number"

async def handle_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = sessions.get(user_id, {})
    text = update.message.text.strip()
    waiting_for = session.get("waiting_for")

    if waiting_for == "room_number":
        session["location"] = f"Room {text}"
        session.pop("waiting_for", None)
        await ask_dept(update.message.reply_text)

    elif waiting_for == "custom_location":
        session["location"] = text
        session.pop("waiting_for", None)
        await ask_dept(update.message.reply_text)

    elif waiting_for == "prf_number":
        prf = text.upper()
        if not prf.startswith("PRF-"):
            prf = f"PRF-{prf.zfill(3)}"
        session["prf_number"] = prf
        session.pop("waiting_for", None)
        await send_confirmation(update.message.reply_text, session)

    elif not session.get("awaiting_location") and not text.startswith("/"):
        # Still collecting text before the prompt fires
        session.setdefault("texts", []).append(text)

async def send_confirmation(reply_fn, session):
    description = " ".join(session.get("texts", [])) + " " + session.get("caption", "")
    description = description.strip()
    location = session.get("location", "Unknown")
    prf = session.get("prf_number", "PRF-001")
    dept = session.get("dept", "Maintenance")
    photos = session.get("photos", [])

    summary = (
        f"📋 *Work Order Ready to Send*\n\n"
        f"📍 *Location:* {location}\n"
        f"🔖 *PRF:* {prf}\n"
        f"🏢 *Department:* {dept}\n"
        f"📸 *Photos:* {len(photos)}\n\n"
        f"📝 *Description:*\n_{description}_"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Send to Ryan", callback_data="confirm_send"),
         InlineKeyboardButton("🗑 Cancel", callback_data="confirm_cancel")]
    ]
    session["pending"] = {
        "location": location, "prf": prf, "dept": dept,
        "description": description, "photo_ids": photos
    }
    await reply_fn(summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})

    if query.data == "confirm_send":
        pending = session.get("pending", {})
        await query.edit_message_text("📤 Sending to Ryan...")
        success = await send_email(context, pending)
        if success:
            await query.edit_message_text(
                f"✅ *Sent to ryanzmckay@gmail.com*\n\n"
                f"📍 {pending['location']} · {pending['prf']}\n"
                f"Ryan will process this into a PRF shortly.",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                f"⚠️ Email failed. Copy this into Claude manually:\n\n"
                f"Location: {pending['location']}\n"
                f"PRF: {pending['prf']}\n"
                f"Dept: {pending['dept']}\n"
                f"Description: {pending['description']}"
            )
        sessions.pop(user_id, None)

    elif query.data == "confirm_cancel":
        sessions.pop(user_id, None)
        await query.edit_message_text("Cancelled. Forward a new message whenever you're ready.")

async def send_email(context, pending):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        logger.warning("Email credentials not set")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = f"Work Order: {pending['location']} – {pending['prf']}"
        body = f"""NEW WORK ORDER — LP PROP
{'='*50}
Location:    {pending['location']}
PRF Number:  {pending['prf']}
Department:  {pending['dept']}
Date:        {datetime.date.today().strftime('%B %d, %Y')}
Photos:      {len(pending['photo_ids'])} attached

DESCRIPTION:
{pending['description']}

{'='*50}
Paste into Claude PRF Workflow to generate PDF and vendor quote emails.
"""
        msg.attach(MIMEText(body, "plain"))

        for i, file_id in enumerate(pending.get("photo_ids", [])):
            try:
                tg_file = await context.bot.get_file(file_id)
                import aiohttp
                async with aiohttp.ClientSession() as http:
                    async with http.get(tg_file.file_path) as resp:
                        photo_bytes = await resp.read()
                part = MIMEBase("application", "octet-stream")
                part.set_payload(photo_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=f"site_photo_{i+1}.jpg")
                msg.attach(part)
            except Exception as e:
                logger.error(f"Photo attach error: {e}")

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_location_type, pattern="^loc_(outdoor|indoor)$"))
    app.add_handler(CallbackQueryHandler(handle_location_selection, pattern="^loc_"))
    app.add_handler(CallbackQueryHandler(handle_dept, pattern="^dept_"))
    app.add_handler(CallbackQueryHandler(handle_confirm, pattern="^confirm_"))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_reply))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
