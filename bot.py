import os, requests, pandas as pd, ta, asyncio, logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread
from datetime import datetime
import pytz

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN_V2")
CHAT_ID = os.getenv("CHAT_ID_V2")
TD_API_KEY = os.getenv("TWELVEDATA_API_KEY")
TIMEFRAME = os.getenv("TIMEFRAME", "15min")

flask_app = Flask(__name__)
LAGOS = pytz.timezone("Africa/Lagos")
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD", "AUD/USD", "EUR/JPY"]
EXPIRY = "15 Minutes" if TIMEFRAME == "15min" else "5 Minutes"
last_signal = {}

@flask_app.route('/')
def home(): 
    return f"MACD+BB+ADX Bot Live | TF:{TIMEFRAME} | {datetime.now(LAGOS).strftime('%H:%M')}"

def get_data(symbol):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={TIMEFRAME}&apikey={TD_API_KEY}&outputsize=100"
    try:
        r = requests.get(url, timeout=10).json()
        if "values" not in r: 
            logging.warning(f"No data for {symbol}")
            return None
        df = pd.DataFrame(r["values"])
        df = df.astype({"open": float, "high": float, "low": float, "close": float})
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df.sort_values("datetime")
    except Exception as e:
        logging.error(f"Data error {symbol}: {e}")
        return None

def check_signal(df):
    if df is None or len(df) < 50: return None
    bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_upper"] = bb.bollinger_hband()
    macd = ta.trend.MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    adx = ta.trend.ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=14)
    df["adx"] = adx.adx()
    last, prev = df.iloc[-1], df.iloc[-2]
    if pd.isna(last["adx"]) or last["adx"] < 20: return None
    macd_cross_up = prev["macd"] < prev["macd_signal"] and last["macd"] > last["macd_signal"]
    bb_touch_low = last["Low"] <= last["bb_lower"]
    if macd_cross_up and bb_touch_low and last["macd_hist"] > 0:
        return "CALL", last["Close"], last["adx"], last["bb_lower"]
    macd_cross_down = prev["macd"] > prev["macd_signal"] and last["macd"] < last["macd_signal"]
    bb_touch_high = last["High"] >= last["bb_upper"]
    if macd_cross_down and bb_touch_high and last["macd_hist"] < 0:
        return "PUT", last["Close"], last["adx"], last["bb_upper"]
    return None

async def send_signal(context: ContextTypes.DEFAULT_TYPE, pair, sig_type, price, adx, bb_level):
    now = datetime.now(LAGOS).strftime("%H:%M")
    msg = f"""[MACD+BB+ADX] {sig_type}
Pair: {pair}
Price: {price:.5f}
ADX: {adx:.1f} | BB: {bb_level:.5f}
Time: {now} GMT+1
Expiry: {EXPIRY}
Session: 1PM-4PM GMT+1"""
    await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    logging.info(f"Sent {sig_type} {pair}")

async def scan_market(context: ContextTypes.DEFAULT_TYPE):
    global last_signal
    now = datetime.now(LAGOS)
    if not (13 <= now.hour < 16 and now.weekday() < 5): 
        logging.info("Outside session 1PM-4PM")
        return
    logging.info("Scanning market...")
    for pair in PAIRS:
        df = get_data(pair)
        result = check_signal(df)
        if result:
            sig_type, price, adx, bb_level = result
            if last_signal.get(pair)!= sig_type:
                await send_signal(context, pair, sig_type, price, adx, bb_level)
                last_signal[pair] = sig_type
        await asyncio.sleep(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("MACD+BB+ADX Bot is live. Use /status to check session.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = datetime.now(LAGOS)
    session = "OPEN 🟢" if 13 <= t.hour < 16 and t.weekday() < 5 else "CLOSED 🔴"
    msg = f"""MACD+BB+ADX Bot
Time: {t.strftime('%H:%M')} GMT+1
Session: {session}
TF: {TIMEFRAME}
Expiry: {EXPIRY}"""
    await update.message.reply_text(msg)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

def main():
    Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    
    # JobQueue handles the scanner - no more crashes
    job_queue = application.job_queue
    job_queue.run_repeating(scan_market, interval=120, first=10)
    
    logging.info(f"MACD+BB+ADX Bot Starting | TF:{TIMEFRAME} | 1PM-4PM GMT+1")
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
