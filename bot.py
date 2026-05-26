import os
import requests
import pandas as pd
import pandas_ta as ta
from telegram import Bot
from flask import Flask
from threading import Thread
import schedule
import time
from datetime import datetime
import pytz

# ====== CONFIG ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN_V2")
CHAT_ID = os.getenv("CHAT_ID_V2") 
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]
TIMEFRAME = os.getenv("TIMEFRAME", "15min")
TIMEZONE = "Africa/Lagos"
SESSION_START = 13
SESSION_END = 16

EXPIRY_MAP = {"5min": "5 Minutes", "15min": "15 Minutes", "1h": "1 Hour"}
EXPIRY = EXPIRY_MAP.get(TIMEFRAME, "15 Minutes")

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

def get_twelvedata(pair, interval=TIMEFRAME, outputsize=100):
    symbol = pair.replace("/", "")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVEDATA_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if "values" not in data: return None
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        df = df.astype(float)
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        return df[["Open", "High", "Low", "Close"]]
    except: return None

def check_signal(df):
    if df is None or len(df) < 50: return None
    bb = ta.bbands(df["Close"], length=20, std=2.0)
    macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
    adx = ta.adx(df["High"], df["Low"], df["Close"], length=14)
    df["bb_lower"] = bb["BBL_20_2.0"]
    df["bb_upper"] = bb["BBU_20_2.0"]
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]
    df["adx"] = adx["ADX_14"]
    last, prev = df.iloc[-1], df.iloc[-2]
    if last["adx"] < 20: return None
    macd_cross_up = prev["macd"] < prev["macd_signal"] and last["macd"] > last["macd_signal"]
    bb_touch_low = last["Low"] <= last["bb_lower"]
    if macd_cross_up and bb_touch_low and last["macd_hist"] > 0:
        return "CALL", last["Close"], last["adx"], last["bb_lower"]
    macd_cross_down = prev["macd"] > prev["macd_signal"] and last["macd"] < last["macd_signal"]
    bb_touch_high = last["High"] >= last["bb_upper"]
    if macd_cross_down and bb_touch_high and last["macd_hist"] < 0:
        return "PUT", last["Close"], last["adx"], last["bb_upper"]
    return None

def scan_pairs():
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    if not (SESSION_START <= now.hour < SESSION_END): return
    for pair in PAIRS:
        df = get_twelvedata(pair, TIMEFRAME)
        result = check_signal(df)
        if result:
            direction, price, adx_val, bb_level = result
            msg = f"[MACD+BB+ADX]\n{pair.replace('/', '')}\n{direction} ✅\nExpiry: {EXPIRY}\nPrice: {price:.5f}\nADX: {adx_val:.1f} | BB: {bb_level:.5f}\nTF: {TIMEFRAME}"
            bot.send_message(chat_id=CHAT_ID, text=msg)

schedule.every(5).minutes.do(scan_pairs)
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

@app.route('/')
def home(): return f"MACD+BB+ADX Bot alive - TF: {TIMEFRAME}"

@app.route('/status')
def status():
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    session = "OPEN 🟢" if SESSION_START <= now.hour < SESSION_END else "CLOSED 🔴"
    return f"MACD+BB+ADX Bot\nTime: {now.strftime('%H:%M')} GMT+1\nSession: {session}\nTF: {TIMEFRAME}\nExpiry: {EXPIRY}"

if __name__ == "__main__":
    Thread(target=run_scheduler).start()
    app.run(host="0.0.0.0", port=10000)
