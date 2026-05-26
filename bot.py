import os, requests, pandas as pd, ta
from telegram import Bot
from flask import Flask
from threading import Thread
import schedule, time
from datetime import datetime
import pytz

# === CONFIG ===
TOKEN = os.getenv("TELEGRAM_TOKEN_V2")
CHAT_ID = os.getenv("CHAT_ID_V2")
TD_API_KEY = os.getenv("TWELVEDATA_API_KEY")
TIMEFRAME = os.getenv("TIMEFRAME", "15min")

bot = Bot(token=TOKEN)
app = Flask(__name__)
LAGOS = pytz.timezone("Africa/Lagos")
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CAD", "AUD/USD", "EUR/JPY"]
last_signal = {}

@app.route('/')
def home(): return "MACD+BB+ADX Bot Live"

def get_data(symbol):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={TIMEFRAME}&apikey={TD_API_KEY}&outputsize=100"
    try:
        r = requests.get(url, timeout=10).json()
        if "values" not in r: return None
        df = pd.DataFrame(r["values"])
        df = df.astype({"open": float, "high": float, "low": float, "close": float})
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df.sort_values("datetime")
    except: return None

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
    if last["adx"] < 20: return None # ADX filter: only trade strong trends
    
    # CALL: MACD bullish cross + price at/below lower BB + histogram positive
    macd_cross_up = prev["macd"] < prev["macd_signal"] and last["macd"] > last["macd_signal"]
    bb_touch_low = last["Low"] <= last["bb_lower"]
    if macd_cross_up and bb_touch_low and last["macd_hist"] > 0:
        return "CALL", last["Close"], last["adx"], last["bb_lower"]
    
    # PUT: MACD bearish cross + price at/above upper BB + histogram negative
    macd_cross_down = prev["macd"] > prev["macd_signal"] and last["macd"] < last["macd_signal"]
    bb_touch_high = last["High"] >= last["bb_upper"]
    if macd_cross_down and bb_touch_high and last["macd_hist"] < 0:
        return "PUT", last["Close"], last["adx"], last["bb_upper"]
    return None

def send_signal(pair, sig_type, price, adx, bb_level):
    now = datetime.now(LAGOS).strftime("%H:%M")
    expiry_time = TIMEFRAME.replace("min", " Minutes")
    msg = f"""[MACD+BB+ADX] {sig_type} SIGNAL
Pair: {pair}
Price: {price:.5f}
ADX: {adx:.1f} | BB: {bb_level:.5f}
Time: {now} GMT+1
Expiry: {expiry_time}
Session: 1PM-4PM GMT+1"""
    bot.send_message(chat_id=CHAT_ID, text=msg)

def run_bot():
    global last_signal
    now = datetime.now(LAGOS)
    if now.hour < 13 or now.hour >= 16: return # 1PM-4PM only
    
    for pair in PAIRS:
        df = get_data(pair)
        result = check_signal(df)
        if result:
            sig_type, price, adx, bb_level = result
            if last_signal.get(pair)!= sig_type:
                send_signal(pair, sig_type, price, adx, bb_level)
                last_signal[pair] = sig_type
        time.sleep(2)

schedule.every(5).minutes.do(run_bot)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    Thread(target=run_schedule, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
