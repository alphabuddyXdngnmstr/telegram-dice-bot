import os
import random
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Erlaubt z.B. 1d6, 2d20, 3d8+4, 1d10-1
DICE_PATTERN = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$", re.IGNORECASE)

async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Beispiel:\n/roll 1d6\n/roll 2d20+3")
        return

    expr = context.args[0].lower().strip()
    match = DICE_PATTERN.match(expr)

    if not match:
        await update.message.reply_text("Ungültiges Format.\nNutze z.B. /roll 1d6 oder /roll 2d6")
        return

    dice_count = int(match.group(1))
    sides = int(match.group(2))
    modifier = int(match.group(3)) if match.group(3) else 0

    if dice_count < 1 or dice_count > 100:
        await update.message.reply_text("Maximal 100 Würfel auf einmal.")
        return

    if sides < 2 or sides > 100000:
        await update.message.reply_text("Seitenzahl bitte zwischen 2 und 100000.")
        return

    rolls = [random.randint(1, sides) for _ in range(dice_count)]
    total = sum(rolls) + modifier

    rolls_text = ", ".join(map(str, rolls))
    mod_text = f"{modifier:+}" if modifier else ""

    await update.message.reply_text(
        f"🎲 {dice_count}d{sides}{mod_text}\n"
        f"Würfe: {rolls_text}\n"
        f"Summe: {total}"
    )

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    base_url = os.environ.get("BASE_URL")
    port = int(os.environ.get("PORT", "10000"))

    if not token or not base_url:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

    base_url = base_url.rstrip("/")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("roll", roll))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=f"{base_url}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
