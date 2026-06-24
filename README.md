# LP Work Orders Bot

Telegram bot that receives forwarded work orders and emails formatted PRF summaries to Ryan.

## Environment Variables (set in Railway)
- BOT_TOKEN — Telegram bot token from BotFather
- EMAIL_FROM — Gmail address to send FROM (e.g. ryanzmckay@gmail.com)
- EMAIL_PASSWORD — Gmail App Password (not your regular password)

## Getting a Gmail App Password
1. Go to myaccount.google.com
2. Security → 2-Step Verification (must be ON)
3. Search "App passwords"
4. Create one for "Mail" → copy the 16-character password
