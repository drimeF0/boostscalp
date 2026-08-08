"""Глобальные настройки приложения."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(BASE_DIR, "models")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DB_PATH = os.path.join(DATA_DIR, "trades.db")
MODEL_PATH = os.path.join(MODELS_DIR, "meta_model.cbm")
MODEL_META_PATH = os.path.join(MODELS_DIR, "meta_model.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# --- Торговые настройки по умолчанию ---
DEFAULT_START_BALANCE = 10_000.0   # USDT
DEFAULT_TAKER_FEE = 0.001          # 0.1%
DEFAULT_MAKER_FEE = 0.0002         # 0.02%

# --- Бэктест (USDT-M фьючерсы Binance) ---
BINANCE_DATA_BASE = "https://data.binance.vision/data/futures/um"
BACKTEST_INTERVAL = "1m"           # свечи для реплея
WARMUP_CANDLES = 300               # сколько свечей до старта грузим для индикаторов
SYNTH_TICKS_PER_CANDLE = 8         # синтетических тиков внутри 1m свечи

# --- Стакан ---
BOOK_LEVELS = 20                   # уровней на каждую сторону

# Тик-сайзы по умолчанию (если биржа не сообщила)
DEFAULT_TICK_SIZES = {
    "BTC/USDT": 0.1,
    "ETH/USDT": 0.01,
    "SOL/USDT": 0.001,
    "BNB/USDT": 0.01,
}
DEFAULT_TICK_SIZE = 0.01

# --- Мета-модель ---
MIN_TRADES_TO_TRAIN = 20
DEFAULT_THRESHOLD = 0.5
