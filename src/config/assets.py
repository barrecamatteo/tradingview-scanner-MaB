"""
Asset list and timeframe configuration for TradingView scraper.
"""

# All timeframes (TradingView interval values)
TIMEFRAMES = {
    "4H": "240",
    "1H": "60",
    "15min": "15",
    "5min": "5",
    "1min": "1",
}

# Timeframe groups for different scan schedules
WEEKLY_TIMEFRAMES = ["4H", "1H", "15min"]
DAILY_TIMEFRAMES = ["5min", "1min"]

# Assets organized by category
ASSETS = {
    "Yen Crosses": [
        {"symbol": "OANDA:USDJPY", "name": "USDJPY"},
        {"symbol": "OANDA:GBPJPY", "name": "GBPJPY"},
        {"symbol": "OANDA:AUDJPY", "name": "AUDJPY"},
        {"symbol": "OANDA:EURJPY", "name": "EURJPY"},
        {"symbol": "OANDA:CADJPY", "name": "CADJPY"},
    ],
    "Commodity Currencies": [
        {"symbol": "OANDA:AUDUSD", "name": "AUDUSD"},
        {"symbol": "OANDA:AUDCAD", "name": "AUDCAD"},
        {"symbol": "OANDA:AUDCHF", "name": "AUDCHF"},
        {"symbol": "OANDA:GBPAUD", "name": "GBPAUD"},
        {"symbol": "OANDA:EURAUD", "name": "EURAUD"},
        {"symbol": "OANDA:EURCAD", "name": "EURCAD"},
        {"symbol": "OANDA:GBPCAD", "name": "GBPCAD"},
    ],
    "Safe Haven": [
        {"symbol": "OANDA:USDCHF", "name": "USDCHF"},
        {"symbol": "OANDA:EURCHF", "name": "EURCHF"},
        {"symbol": "OANDA:GBPCHF", "name": "GBPCHF"},
        {"symbol": "OANDA:CADCHF", "name": "CADCHF"},
    ],
    "Europe Economy": [
        {"symbol": "OANDA:EURUSD", "name": "EURUSD"},
        {"symbol": "OANDA:EURGBP", "name": "EURGBP"},
        {"symbol": "OANDA:GBPUSD", "name": "GBPUSD"},
    ],
    "Crypto": [
        {"symbol": "BITSTAMP:ETHUSD", "name": "ETHUSD"},
    ],
    "Commodities": [
        {"symbol": "OANDA:XAUUSD", "name": "XAUUSD"},
        {"symbol": "OANDA:XAGUSD", "name": "XAGUSD"},
        {"symbol": "OANDA:XPTUSD", "name": "XPTUSD"},
    ],
    "Indices": [
        {"symbol": "FXCM:SPX500", "name": "SPX500"},
        {"symbol": "FXCM:NAS100", "name": "NAS100"},
    ],
}


def get_all_assets():
    """Return flat list of (category, symbol, name) tuples."""
    result = []
    for category, assets in ASSETS.items():
        for asset in assets:
            result.append((category, asset["symbol"], asset["name"]))
    return result


def get_timeframes(filter_names=None):
    """Return timeframes dict, optionally filtered by name list.

    Args:
        filter_names: List of timeframe names, e.g. ["5min", "1min"].
                      If None, returns all timeframes.
    """
    if filter_names is None:
        return TIMEFRAMES
    return {k: v for k, v in TIMEFRAMES.items() if k in filter_names}


def get_total_combinations(timeframe_filter=None):
    """Return total number of asset/timeframe combinations."""
    tfs = get_timeframes(timeframe_filter)
    return len(get_all_assets()) * len(tfs)


# Scraper settings
SCRAPER_CONFIG = {
    "page_load_timeout": 30,
    "indicator_wait_timeout": 15,
    "retry_count": 3,
    "delay_between_requests_min": 2.0,
    "delay_between_requests_max": 5.0,
    "screenshot_region": {
        "description": "Analysis panel - top right corner",
    },
}

# TradingView chart URL template
TV_CHART_URL = "https://www.tradingview.com/chart/?symbol={symbol}&interval={interval}"
TV_LOGIN_URL = "https://www.tradingview.com/accounts/signin/"
