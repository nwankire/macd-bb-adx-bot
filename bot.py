import os, time, logging, threading, requests
import pandas as pd
import ta
from datetime import datetime
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import schedule, pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger()

TOKEN = os.getenv("TELEGRAM_TOKEN_V2")
CHAT_ID = int(os.getenv("CHAT_ID_V2", "0"))
TD_KEY = os.getenv("TWELVEDATA_API_KEY")
TF = os.getenv("TIMEFRAME", "15min")
EXPIRY = "15 Minutes" if TF == "15min" else "5 Minutes"
NIGERIA = pytz.timezone("Africa/Lagos")

PAIRS = [("EUR/USD","EUR/USD"),("USD/JPY","USD/JPY"),("GBP/USD","GBP/USD"),
         ("USD/CAD","USD/CAD"),("AUD/USD","AUD/USD"),("EUR/JPY","EUR/JPY"),
         ("GBP/JPY","GBP/JPY"),("AUD/JPY","AUD/JPY"),("EUR/GBP","EUR/GBP")]

def now_nigeria(): return datetime.now(NIGERIA)
def in_session(t): return 13 <= t.hour < 16 and t.weekday() < 5
def market_open():
    t = now_nigeria()
    return t.weekday() < 5 and not (t.weekday() == 4 and t.hour >= 22) and not (t.weekday() == 0 and t.hour < 1)

def get_data(td_symbol):
    url = f"https://api.twelvedata.com/time_series?symbol={td_symbol}&interval={TF}&outputsize=100&apikey={TD_KEY}"
    try:
        r = requests.get(url, timeout=10).json()
        if "values" not in r: return None
        df = pd.DataFrame(r["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for c in ["open","high","low","close"]: df[c.capitalize()] = df[c].astype(float)
        return df
    except Exception as e:
        log.error(f"Data error {td_symbol}: {e}")
        return None

def check_signal(df):
    if df is None or len(df) < 50: return None
    
    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_upper"] = bb.bollinger_hband()
    
    # MACD
    macd = ta.trend.MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()
    
    # ADX
    adx = ta.trend.ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=14)
    df["adx"] = adx.adx()
    
    last, prev = df.iloc[-1], df.iloc[-2]
    if last["adx"] < 20: return None
    
    # CALL: MACD cross up + price at/below lower BB + histogram positive
    macd_cross_up = prev["macd"] < prev["macd_signal"] and last["macd"] > last["macd_signal"]
    bb_touch_low = last["Low"] <= last["bb_lower"]
    if macd_cross_up and bb_touch_low and last["macd_hist"] > 0:
        return "CALL", last["Close"], last["adx"], last["bb_lower"]
    
    # PUT: MACD cross down + price at/above upper BB + histogram negative
    macd_cross_down = prev["macd"] > prev["macd_signal"] and last["macd"] < last["macd_signal"]
    bb_touch_high = last["High"] >= last["bb_upper"]
    if macd_cross_down and bb_touch_high and last["macd_hist"] < 0:
        return "PUT", last["Close"], last["adx"], last["bb_upper"]
    return None

async def send_signal(context, pair, td_symbol, sig):
    direction, price, adx, bb_level = sig
    msg = f"""[MACD+BB+ADX] {direction}
Pair: {pair}
Price: {price:.5f}
ADX: {adx:.1f}
BB Level: {bb_level:.5f}
Expiry: {EXPIRY}
Time: {now_nigeria().strftime('%H:%M')} GMT+1"""
    await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    log.info(f"Sent {direction} {pair}")

async def scan(context):
    if not market_open(): return
    t = now_nigeria()
    if not in_session(t): return
    log.info("Scanning MACD+BB+ADX...")
    for pair, td_symbol in PAIRS:
        df = get_data(td_symbol)
        sig = check_signal(df)
        if sig: await send_signal(context, pair, td_symbol, sig)
        time.sleep(1)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = now_nigeria()
    session = "OPEN 🟢" if in_session(t) else "CLOSED 🔴"
    msg = f"""MACD+BB+ADX Bot
Time: {t.strftime('%H:%M')} GMT+1
Session: {session}
TF: {TF}
Expiry: {EXPIRY}"""
    await update.message.reply_text(msg)

def run_scheduler(app):
    loop = app.bot._application.loop
    def job(): 
        if market_open() and in_session(now_nigeria()): 
            loop.create_task(scan(app.bot._application))
    schedule.every(2).minutes.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

app = Flask(__name__)
@app.route('/')
def home(): return f"MACD+BB+ADX Bot Live | TF:{TF} | {now_nigeria().strftime('%H:%M')}"

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("status", status))
    threading.Thread(target=run_scheduler, args=(application,), daemon=True).start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    log.info(f"MACD+BB+ADX Bot Started | TF:{TF} | 1pm-4pm GMT+1")
    application.run_polling()

if __name__ == "__main__": main()
