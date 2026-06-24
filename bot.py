"""
LP Work Orders Telegram Bot
Receives forwarded work orders from Ryan, asks follow-up questions,
then emails a formatted PRF-ready summary to ryanzmckay@gmail.com
"""

import os
import logging
import json
import smtplib
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "8946359984:AAFRKAgqGY0NlQMjZorkRNFK7QQ37jXhEG4")
EMAIL_FROM = os.environ.get("EMAIL_FROM")       # Gmail address to send FROM
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD") # Gmail app password
EMAIL_TO = "ryanzmckay@gmail.com"

# Conversation states
LOCATION, PRF_NUM, CONFIRM = range(3)

# Temp storage per user session
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
        "Forward me a message from your team (text + photos) and I'll format it into a PRF-ready work order email.\n\n"
        "Just forward the Telegram message now.",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message

    # Initialize session
    if user_id not in sessions:
        sessions[user_id] = {"photos": [], "texts": [], "caption": ""}

    session = sessions[user_id]

    # Collect photo
    if msg.photo:
        file_id = msg.photo[-1].file_id
        session["photos"].append(file_id)
        caption = msg.caption or ""
        if caption:
            session["caption"] = caption

    # Collect text
    if msg.text and not msg.text.startswith("/"):
        session["texts"].append(msg.text)

    # If this looks like a complete work order, ask for location
    combined_text = " ".join(session["texts"]) + " " + session["caption"]
    
    if (session["photos"] or session["texts"]) and len(combined_text.strip()) > 5:
        # Ask for location after a short delay to collect all forwarded parts
        if "awaiting_location" not in session:
            session["awaiting_location"] = True
            await asyncio.sleep(1.5)  # Brief wait for multi-part forwards
            
            keyboard = [
                [InlineKeyboardButton("📍 Outdoor / Landscape", callback_data="loc_outdoor")],
                [InlineKeyboardButton("🏠 Indoor / Room", callback_data="loc_indoor")],
            ]
            await update.message.reply_text(
                "Got it! Is this an *outdoor/landscape* area or an *indoor room*?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

async def handle_location_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})

    if query.data == "loc_outdoor":
        session["loc_type"] = "outdoor"
        keyboard = [
            [InlineKeyboardButton("Back Lawn", callback_data="loc_Back_Lawn"),
             InlineKeyboardButton("Front Lawn", callback_data="loc_Front_Lawn")],
            [InlineKeyboardButton("Pool Area", callback_data="loc_Pool_Area"),
             InlineKeyboardButton("Driveway", callback_data="loc_Driveway")],
            [InlineKeyboardButton("✏️ Type custom area", callback_data="loc_custom")],
        ]
        await query.edit_message_text(
            "Which outdoor area?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        session["loc_type"] = "indoor"
        await query.edit_message_text(
            "Which room number? (e.g. *45* for Gym, *35* for Pool Room)\n\nType the room number:",
            parse_mode="Markdown"
        )
        session["waiting_for"] = "room_number"

async def handle_location_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})

    if query.data == "loc_custom":
        await query.edit_message_text("Type the location name:")
        session["waiting_for"] = "custom_location"
    else:
        location = query.data.replace("loc_", "").replace("_", " ")
        session["location"] = location
        await ask_dept(query, session, user_id)

async def ask_dept(query_or_msg, session, user_id):
    keyboard = [[InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row] 
                for row in DEPT_OPTIONS]
    text = "Which department is requesting this?"
    try:
        await query_or_msg.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        await query_or_msg.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_dept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})
    
    dept = query.data.replace("dept_", "")
    session["dept"] = dept

    # Ask for PRF number
    # Auto-suggest PRF-001 for new locations
    location = session.get("location", "")
    await query.edit_message_text(
        f"PRF number for *{location}*?\n\n"
        f"Reply with the number (e.g. `PRF-001`)\n"
        f"_(First PRF for this location = PRF-001)_",
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
        await ask_dept(update.message, session, user_id)

    elif waiting_for == "custom_location":
        session["location"] = text
        session.pop("waiting_for", None)
        await ask_dept(update.message, session, user_id)

    elif waiting_for == "prf_number":
        prf = text.upper()
        if not prf.startswith("PRF-"):
            prf = f"PRF-{prf.zfill(3)}"
        session["prf_number"] = prf
        session.pop("waiting_for", None)
        await send_confirmation(update.message, session, user_id)

    elif "awaiting_location" not in session:
        # Collect additional text before location prompt
        if not text.startswith("/"):
            session["texts"].append(text)

async def send_confirmation(msg, session, user_id):
    description = " ".join(session.get("texts", [])) + " " + session.get("caption", "")
    description = description.strip()
    location = session.get("location", "Unknown")
    prf = session.get("prf_number", "PRF-001")
    dept = session.get("dept", "Maintenance")
    photos = session.get("photos", [])

    summary = (
        f"📋 *Work Order Summary*\n\n"
        f"📍 *Location:* {location}\n"
        f"🔖 *PRF:* {prf}\n"
        f"🏢 *Department:* {dept}\n"
        f"📸 *Photos:* {len(photos)} attached\n\n"
        f"📝 *Description:*\n{description}\n\n"
        f"Send this to Ryan?"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Send to Ryan", callback_data="confirm_send"),
         InlineKeyboardButton("✏️ Edit", callback_data="confirm_edit")],
        [InlineKeyboardButton("🗑️ Cancel", callback_data="confirm_cancel")]
    ]

    session["pending_summary"] = {
        "location": location,
        "prf": prf,
        "dept": dept,
        "description": description,
        "photo_ids": photos
    }

    await msg.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})

    if query.data == "confirm_send":
        pending = session.get("pending_summary", {})
        await query.edit_message_text("📤 Sending work order to Ryan...")
        
        success = await send_email(context, pending)
        
        if success:
            await query.edit_message_text(
                f"✅ *Work order sent to ryanzmckay@gmail.com*\n\n"
                f"📍 {pending['location']} · {pending['prf']}\n\n"
                f"Ryan will process this into a PRF shortly.",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "⚠️ Email sending failed — check EMAIL_FROM and EMAIL_PASSWORD env vars.\n\n"
                f"*Summary for manual entry:*\n"
                f"Location: {pending['location']}\n"
                f"PRF: {pending['prf']}\n"
                f"Dept: {pending['dept']}\n"
                f"Description: {pending['description']}",
                parse_mode="Markdown"
            )
        # Clear session
        sessions.pop(user_id, None)

    elif query.data == "confirm_edit":
        sessions.pop(user_id, None)
        await query.edit_message_text("OK, starting over. Forward the message again.")

    elif query.data == "confirm_cancel":
        sessions.pop(user_id, None)
        await query.edit_message_text("Cancelled. Forward a new message whenever you're ready.")

async def send_email(context, pending):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        logger.warning("EMAIL_FROM or EMAIL_PASSWORD not set")
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
Date:        {__import__('datetime').date.today().strftime('%B %d, %Y')}
Photos:      {len(pending['photo_ids'])} attached

DESCRIPTION:
{pending['description']}

{'='*50}
Paste this into Claude PRF Workflow to generate the PRF PDF and vendor quote emails.
"""
        msg.attach(MIMEText(body, "plain"))

        # Download and attach photos from Telegram
        for i, file_id in enumerate(pending.get("photo_ids", [])):
            try:
                tg_file = await context.bot.get_file(file_id)
                import io, aiohttp
                async with aiohttp.ClientSession() as http:
                    async with http.get(tg_file.file_path) as resp:
                        photo_bytes = await resp.read()
                part = MIMEBase("application", "octet-stream")
                part.set_payload(photo_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment", filename=f"site_photo_{i+1}.jpg")
                msg.attach(part)
            except Exception as e:
                logger.error(f"Photo {i+1} attach error: {e}")

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
    app.add_handler(MessageHandler(filters.PHOTO | filters.CAPTION, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_reply))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
