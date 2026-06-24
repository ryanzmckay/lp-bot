"""
LP Work Orders Telegram Bot - Sends formatted summary back via Telegram
No email required. Ryan forwards the summary message to Claude.
"""
import os, logging, datetime, json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
sessions = {}
PRF_COUNTER_FILE = "/tmp/prf_counters.json"

def load_counters():
    try:
        with open(PRF_COUNTER_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def next_prf(location: str) -> str:
    counters = load_counters()
    key = location.lower().strip()
    counters[key] = counters.get(key, 0) + 1
    try:
        with open(PRF_COUNTER_FILE, "w") as f:
            json.dump(counters, f)
    except Exception as e:
        logger.error(f"Counter save error: {e}")
    return f"PRF-{str(counters[key]).zfill(3)}"

def release_prf(location: str):
    counters = load_counters()
    key = location.lower().strip()
    if key in counters and counters[key] > 0:
        counters[key] -= 1
        try:
            with open(PRF_COUNTER_FILE, "w") as f:
                json.dump(counters, f)
        except:
            pass

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
        "Forward your team's message and photos, then type *DONE* when finished.",
        parse_mode="Markdown"
    )

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    if not msg:
        return

    if user_id not in sessions:
        sessions[user_id] = {"photos": [], "texts": [], "caption": "", "state": "collecting"}

    session = sessions[user_id]
    state = session.get("state", "collecting")
    text = (msg.text or "").strip() if msg.text else ""

    logger.info(f"User {user_id} | state={state} | text={repr(text)} | photo={bool(msg.photo)}")

    if state == "collecting":
        if msg.photo:
            session["photos"].append(msg.photo[-1].file_id)
            if msg.caption:
                session["caption"] = msg.caption
            n = len(session["photos"])
            await msg.reply_text(
                f"📸 Photo {n} received. Forward more or type *DONE* when finished.",
                parse_mode="Markdown"
            )
        elif text.upper() == "DONE":
            session["state"] = "location"
            keyboard = [
                [InlineKeyboardButton("🌿 Outdoor / Landscape", callback_data="loc_outdoor")],
                [InlineKeyboardButton("🏠 Indoor / Room", callback_data="loc_indoor")],
            ]
            await msg.reply_text(
                "Is this *outdoor* or *indoor*?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        elif text and not text.startswith("/"):
            session["texts"].append(text)
            await msg.reply_text(
                "✅ Got it. Forward photos or type *DONE* when ready.",
                parse_mode="Markdown"
            )

    elif state == "waiting_room":
        session["location"] = f"Room {text}"
        session["state"] = "dept"
        await ask_dept(msg.reply_text)

    elif state == "waiting_custom_loc":
        session["location"] = text
        session["state"] = "dept"
        await ask_dept(msg.reply_text)

async def ask_dept(reply_fn):
    keyboard = [
        [InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row]
        for row in DEPT_OPTIONS
    ]
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
        await query.edit_message_text(
            "Which department?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(d, callback_data=f"dept_{d}") for d in row]
                 for row in DEPT_OPTIONS]
            )
        )

    elif data.startswith("dept_"):
        session["dept"] = data.replace("dept_", "")
        location = session.get("location", "")
        prf = next_prf(location)
        session["prf_number"] = prf
        session["state"] = "confirm"
        await query.edit_message_text(f"🔖 PRF assigned: *{prf}*", parse_mode="Markdown")
        await show_confirmation(context.bot.send_message, session, user_id)

    elif data == "confirm_send":
        pending = session.get("pending", {})
        year = datetime.date.today().year
        today = datetime.date.today().strftime("%B %d, %Y")

        # Send the formatted work order back as a Telegram message
        # Ryan forwards this to Claude to generate the PRF
        summary = (
            f"📋 *NEW WORK ORDER — LP PROP*\n"
            f"{'─' * 30}\n"
            f"📍 *Location:* {pending['location']}\n"
            f"🔖 *PRF:* {pending['prf']}\n"
            f"🏢 *Department:* {pending['dept']}\n"
            f"📅 *Date:* {today}\n"
            f"📸 *Photos:* {len(pending['photo_ids'])}\n"
            f"💾 *Filename:* `{pending['filename']}`\n\n"
            f"📝 *Description:*\n{pending['description']}\n"
            f"{'─' * 30}\n"
            f"_Forward this message to Claude to generate the PRF PDF and vendor quote emails._"
        )

        await query.edit_message_text("✅ Work order formatted!")

        # Send the summary as a clean standalone message to forward to Claude
        await context.bot.send_message(
            chat_id=user_id,
            text=summary,
            parse_mode="Markdown"
        )

        # Also forward the photos so Ryan can include them
        if pending["photo_ids"]:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📸 *{len(pending['photo_ids'])} site photo(s) — forward these to Claude too:*",
                parse_mode="Markdown"
            )
            for file_id in pending["photo_ids"]:
                await context.bot.send_photo(chat_id=user_id, photo=file_id)

        sessions.pop(user_id, None)

    elif data == "confirm_cancel":
        release_prf(session.get("location", ""))
        sessions.pop(user_id, None)
        await query.edit_message_text("Cancelled. PRF number released. Send /start to begin again.")

async def show_confirmation(send_fn, session, chat_id):
    description = " ".join(session.get("texts", [])) + " " + session.get("caption", "")
    description = description.strip()
    location = session.get("location", "Unknown")
    prf = session.get("prf_number", "PRF-001")
    dept = session.get("dept", "Maintenance")
    photos = session.get("photos", [])
    year = datetime.date.today().year
    filename = (
        f"{year}_{location.replace(' ','_')}_"
        f"{prf.replace('-','_')}_"
        f"{description[:25].replace(' ','_').replace('/','_')}.pdf"
    )

    session["pending"] = {
        "location": location, "prf": prf, "dept": dept,
        "description": description, "photo_ids": photos,
        "filename": filename
    }

    keyboard = [[
        InlineKeyboardButton("✅ Confirm & send to Claude", callback_data="confirm_send"),
        InlineKeyboardButton("🗑 Cancel", callback_data="confirm_cancel")
    ]]

    await send_fn(
        chat_id=chat_id,
        text=(
            f"📋 *Confirm work order:*\n\n"
            f"📍 *Location:* {location}\n"
            f"🔖 *PRF:* {prf}\n"
            f"🏢 *Dept:* {dept}\n"
            f"📸 *Photos:* {len(photos)}\n\n"
            f"📝 _{description}_"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.ALL, handle_all))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
