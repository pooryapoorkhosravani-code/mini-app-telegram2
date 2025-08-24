import os
import asyncio
import logging
import aiosqlite
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ------------------ تنظیمات ------------------
TOKEN = os.getenv("7841165333:AAFI2Jm65AMNnGAVkF28DPXp_i9oB7LxQo8")
DB_FILE = "exchange.db"
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin,ethereum&vs_currencies=usd"
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ------------------ دیتابیس ------------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS balances (
                 user_id INTEGER PRIMARY KEY,
                 usdt REAL NOT NULL DEFAULT 1000,
                 btc  REAL NOT NULL DEFAULT 0,
                 eth  REAL NOT NULL DEFAULT 0
               );"""
        )
        await db.commit()

async def get_balance(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT usdt, btc, eth FROM balances WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO balances (user_id) VALUES (?)", (user_id,)
            )
            await db.commit()
            return {"USDT": 1000.0, "BTC": 0.0, "ETH": 0.0}
        return {"USDT": row[0], "BTC": row[1], "ETH": row[2]}

async def update_balance(user_id: int, usdt: float, btc: float, eth: float):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """UPDATE balances
               SET usdt = ?, btc = ?, eth = ?
               WHERE user_id = ?""",
            (usdt, btc, eth, user_id),
        )
        await db.commit()

# ------------------ قیمت لحظه‌ای ------------------
async def fetch_prices() -> dict[str, float]:
    async with aiohttp.ClientSession() as session:
        async with session.get(COINGECKO_URL) as resp:
            data = await resp.json()
            return {
                "BTC": data["bitcoin"]["usd"],
                "ETH": data["ethereum"]["usd"],
            }

# ------------------ دستورات ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 به صرافی مینی خوش آمدید!\n"
        "/wallet – دارایی‌ها\n"
        "/price – قیمت لحظه‌ای\n"
        "/trade – معامله"
    )

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = await get_balance(user_id)
    await update.message.reply_text(
        f"💰 دارایی شما:\n"
        f"USDT: {bal['USDT']:.2f}\n"
        f"BTC : {bal['BTC']:.6f}\n"
        f"ETH : {bal['ETH']:.6f}"
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await fetch_prices()
    await update.message.reply_text(
        f"📈 قیمت لحظه‌ای:\n"
        f"BTC: {prices['BTC']:,.2f} USDT\n"
        f"ETH: {prices['ETH']:,.2f} USDT"
    )

# ------------------ معامله ------------------
TRADE_STATE: dict[int, dict] = {}  # user_id -> {"action": "buy|sell", "coin": "BTC|ETH"}

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("خرید BTC", callback_data="buy_BTC"),
         InlineKeyboardButton("فروش BTC", callback_data="sell_BTC")],
        [InlineKeyboardButton("خرید ETH", callback_data="buy_ETH"),
         InlineKeyboardButton("فروش ETH", callback_data="sell_ETH")],
    ]
    await update.message.reply_text(
        "لطفاً نوع معامله را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, coin = query.data.split("_")
    user_id = update.effective_user.id
    TRADE_STATE[user_id] = {"action": action, "coin": coin}

    await query.edit_message_text(
        f"{'چند' if action == 'buy' else 'چقدر'} {coin} "
        f"{'می‌خرید' if action == 'buy' else 'می‌فروشید'}؟\n"
        f"لطفاً فقط عدد وارد کنید:"
    )
    context.user_data["awaiting_amount"] = True

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get("awaiting_amount") or user_id not in TRADE_STATE:
        return

    text = update.message.text.strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد مثبت وارد کنید.")
        return

    state = TRADE_STATE.pop(user_id)
    action, coin = state["action"], state["coin"]
    prices = await fetch_prices()
    price_now = prices[coin]

    bal = await get_balance(user_id)
    if action == "buy":
        cost = amount * price_now
        if bal["USDT"] < cost:
            await update.message.reply_text("❌ موجودی USDT کافی نیست.")
        else:
            bal["USDT"] -= cost
            bal[coin] += amount
            await update.message.reply_text(
                f"✅ خرید {amount:.6f} {coin} با قیمت {price_now:,.2f} انجام شد."
            )
    else:  # sell
        if bal[coin] < amount:
            await update.message.reply_text(f"❌ موجودی {coin} کافی نیست.")
        else:
            bal[coin] -= amount
            bal["USDT"] += amount * price_now
            await update.message.reply_text(
                f"✅ فروش {amount:.6f} {coin} با قیمت {price_now:,.2f} انجام شد."
            )

    await update_balance(user_id, bal["USDT"], bal["BTC"], bal["ETH"])
    context.user_data["awaiting_amount"] = False

# ------------------ اجرا ------------------
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("trade", trade))
    app.add_handler(CallbackQueryHandler(trade_callback, pattern="^(buy|sell)_(BTC|ETH)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount))

    # راه‌اندازی دیتابیس قبل از polling
    asyncio.get_event_loop().run_until_complete(init_db())

    logger.info("ربات استارت خورد.")
    app.run_polling()

if __name__ == "__main__":
    main()
