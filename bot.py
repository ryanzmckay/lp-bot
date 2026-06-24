"""
LP Work Orders Telegram Bot - Clean version
"""
import os, logging, smtplib, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

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
    user_id = update.effective_user.id
    sessions[user_id] = {"photos": [], "texts": [], "caption": "", "state": "collecting"}
    await update.message.reply_text(
        "👋 *LP Work Orders Bot*\n\n"
        "Forward your team's message and photos here, then type *DONE* when finished.",
        parse_mode="Markdown"
    )

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    if not msg:
        return

    # Auto-init session if user skipped /start
    if user_id not in sessions:
        sessions[user_id] = {"photos": [], "texts": [], "caption": "", "state": "collecting"}

    session = sessions[user_id]
    state = session.get("state", "collecting")
    text = (msg.text or "").strip() if msg.text else ""

    logger.info(f"User {user_id} | state={state} | text={repr(text)} | photo={bool(msg.photo)}")

    # --- COLLECTING STATE ---
    if state == "collecting":
        if msg.photo:
            session["photos"].append(msg.photo[-1].file_id)
            if msg.caption:
                session["caption"] = msg.caption
            n = len(session["photos"])
            await msg.reply_text(f"📸 Photo {n} received. Type *DONE* when finished forwarding.", parse_mode="Markdown")

        elif text.upper() == "DONE":
            session["state"] = "location"
            keyboard = [
                [InlineKeyboardButton("🌿 Outdoor / Landscape", callback_data="loc_outdoor")],
                [InlineKeyboardButton("🏠 Indoor / Room", callback_data="loc_indoor")],
            ]
            await msg.reply_text("Is this *outdoor* or *indoor*?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        elif text and not text.startswith("/"):
            session["texts"].append(text)
            await msg.reply_text(f"✅ Description noted. Forward photos or type *DONE* when ready.", parse_mode="Markdown")

    # --- WAITING FOR ROOM NUMBER ---
    elif state == "waiting_room":
        session["location"] = f"Room {text}"
        session["state"] = "dept"
        await ask_dept(msg.reply_text)

    # --- WAITING FOR CUSTOM LOCATION ---
    elif state == "waiting_custom_loc":
        session["location"] = text
        session["state"] = "dept"
        await ask_dept(msg.reply_text)

    # --- WAITING FOR PRF NUMBER ---
    elif state == "waiting_prf":
        prf = text.upper()
        if not prf.startswith("PRF-"):
            prf = f"PRF-{prf.zfill(3)}"
        session["prf_number"] = prf
        session["state"] = "confirm"
        await show_confirmation(msg.reply_text, session)

async def ask_dept(reply_fn):
    keyboard = [[InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row] for row in DEPT_OPTIONS]
    await reply_fn("Which department?", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = sessions.get(user_id, {})
    data = query.data

    if data == "loc_outdoor":
        keyboard = [
            [InlineKeyboardButton("Back Lawn", callback_data="loc_Back Lawn"),
             InlineKeyboardButton("Front Lawn", callback_data="loc_Front Lawn")],
            [InlineKeyboardButton("Pool Area", callback_data="loc_Pool Area"),
             InlineKeyboardButton("Driveway", callback_data="loc_Driveway")],
            [InlineKeyboardButton("Rose Garden", callback_data="loc_Rose Garden"),
             InlineKeyboardButton("Tennis Court", callback_data="loc_Tennis Court")],
            [InlineKeyboardButton("✏️ Other", callback_data="loc_custom")],
        ]
        await query.edit_message_text("Which outdoor area?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "loc_indoor":
        session["state"] = "waiting_room"
        await query.edit_message_text("Type the *room number:*", parse_mode="Markdown")

    elif data == "loc_custom":
        session["state"] = "waiting_custom_loc"
        await query.edit_message_text("Type the outdoor area name:")

    elif data.startswith("loc_"):
        session["location"] = data.replace("loc_", "")
        session["state"] = "dept"
        await query.edit_message_text("Which department?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row] for row in DEPT_OPTIONS]))

    elif data.startswith("dept_"):
        session["dept"] = data.replace("dept_", "")
        session["state"] = "waiting_prf"
        await query.edit_message_text(
            f"PRF number for *{session.get('location', '')}*?\n\nType it (e.g. `PRF-001`)",
            parse_mode="Markdown")

    elif data == "confirm_send":
        pending = session.get("pending", {})
        await query.edit_message_text("📤 Sending to Ryan...")
        success = await send_email(context, pending)
        if success:
            await query.edit_message_text(
                f"✅ *Sent!*\n📍 {pending['location']} · {pending['prf']}",
                parse_mode="Markdown")
        else:
            await query.edit_message_text(
                f"⚠️ Email failed. Tell Ryan:\n{pending['location']} | {pending['prf']}\n{pending['description']}")
        sessions.pop(user_id, None)

    elif data == "confirm_cancel":
        sessions.pop(user_id, None)
        await query.edit_message_text("Cancelled. Send /start to begin again.")

async def show_confirmation(reply_fn, session):
    description = " ".join(session.get("texts", [])) + " " + session.get("caption", "")
    description = description.strip()
    location = session.get("location", "Unknown")
    prf = session.get("prf_number", "PRF-001")
    dept = session.get("dept", "Maintenance")
    photos = session.get("photos", [])
    session["pending"] = {"location": location, "prf": prf, "dept": dept,
                          "description": description, "photo_ids": photos}
    keyboard = [[InlineKeyboardButton("✅ Send to Ryan", callback_data="confirm_send"),
                 InlineKeyboardButton("🗑 Cancel", callback_data="confirm_cancel")]]
    await reply_fn(
        f"📋 *Work Order Summary*\n\n"
        f"📍 *Location:* {location}\n🔖 *PRF:* {prf}\n🏢 *Dept:* {dept}\n📸 *Photos:* {len(photos)}\n\n"
        f"📝 _{description}_",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def send_email(context, pending):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        logger.warning("No email credentials")
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
Paste into Claude PRF Workflow to generate PDF and vendor emails.
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
                logger.error(f"Photo error: {e}")
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
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.ALL, handle_all))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
