from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
FILL_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/allOrders"  # Falls alle Orders, auch gefüllte, genutzt werden sollen
TICKER_ENDPOINT = "https://contract.mexc.com/api/v1/contract/ticker"
# Alternativ: "/openApi/swap/v2/trade/allFillOrders" wenn nur gefüllte Orders

PRICE_ENDPOINT = "/openApi/swap/v2/quote/price"


def generate_signature(secret_key: str, query_string: str) -> str:
    return hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()


def send_signed_get(api_key, secret_key, endpoint, params):
    # Timestamp & recvWindow anhängen
    params["timestamp"] = str(int(time.time() * 1000))
    params["recvWindow"] = "5000"

    # Alphabetisch sortieren und Query-String bauen
    sorted_query = '&'.join(f"{k}={params[k]}" for k in sorted(params))

    # Signatur erzeugen
    signature = generate_signature(secret_key, sorted_query)

    # Vollständige URL
    full_url = f"{BASE_URL}{endpoint}?{sorted_query}&signature={signature}"

    headers = {
        "X-BX-APIKEY": api_key
    }

    response = requests.get(full_url, headers=headers)
    return response.json()


def get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    response = requests.get(url)
    data = response.json()
    if data.get("code") == 0 and "data" in data and "price" in data["data"]:
        return float(data["data"]["price"])
    return None

def get_latest_long_buy_filled_order_by_time(api_key, secret_key, symbol, lookback_hours=72):
    now = int(time.time() * 1000)
    hour_ms = 60 * 60 * 1000
    interval = 12 * hour_ms

    for i in range(0, lookback_hours, 12):
        end_time = now - (i * hour_ms)
        start_time = end_time - interval

        params = {
            "symbol": symbol,
            "limit": "50",
            "startTime": str(start_time),
            "endTime": str(end_time)
        }

        response = send_signed_get(api_key, secret_key, "/openApi/swap/v2/trade/allFillOrders", params)
        data = response.get("data", {})

        # Sicherstellen, dass orders eine Liste von Dicts ist
        if isinstance(data, dict) and "orders" in data:
            orders = data["orders"]
        elif isinstance(data, list):
            orders = data
        else:
            orders = []

        # Falls orders unerwartete Form haben, leer machen
        if not isinstance(orders, list):
            orders = []

        for order in sorted(orders, key=lambda o: int(o.get("updateTime", 0)), reverse=True):
            if (
                order.get("positionSide") == "LONG" and
                order.get("status") == "FILLED" and
                order.get("side") == "BUY"
            ):
                try:
                    executed_qty = float(order.get("executedQty", 0))
                    avg_price = float(order.get("avgPrice", 0))
                    order["order_size_usdt"] = round(executed_qty * avg_price, 4)
                except (ValueError, TypeError):
                    order["order_size_usdt"] = None

                try:
                    ts = int(order.get("updateTime", 0))
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    order["updateTime_readable"] = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    order["updateTime_readable"] = "unbekannt"

                return order

    return None
    
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logs = []

    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    symbol = data.get("symbol", "BTC-USDT")

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "API Key und Secret Key erforderlich"}), 400

    latest_order = get_latest_long_buy_filled_order_by_time(api_key, secret_key, symbol, lookback_hours=72)

    if latest_order:
        logs.append("Jüngste LONG + BUY + FILLED Order gefunden.")
    else:
        logs.append("Keine passende Order gefunden.")

    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "latest_long_buy_filled_order": latest_order,
        "logs": logs
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
