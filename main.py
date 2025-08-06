from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
from datetime import datetime, timedelta

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
    
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logs = []

    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    symbol = data.get("symbol", "BTC-USDT")

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "API Key und Secret Key erforderlich"}), 400

    params = {
        "symbol": symbol,
        "limit": "300"  # API Limit, das wir trotzdem lokal weiter filtern
    }

    fill_orders_response = send_signed_get(api_key, secret_key, FILL_ORDERS_ENDPOINT, params)
    logs.append(f"Fill Orders Full Response: {fill_orders_response}")

    raw_orders = fill_orders_response.get("data", {})
    if isinstance(raw_orders, dict) and "orders" in raw_orders:
        orders = raw_orders["orders"]
    else:
        logs.append("Warnung: Die Orders-Daten sind nicht im erwarteten Format (Liste von Dicts).")
        orders = []

    # Hilfsfunktion: Timestamp (ms) zu datetime konvertieren
    def timestamp_to_datetime(ts):
        return datetime.utcfromtimestamp(int(ts) / 1000)

    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    # Filter auf Orders von gestern und heute
    orders_from_yesterday_and_today = []
    for order in orders:
        update_time = order.get("updateTime")
        if update_time is None:
            continue
        dt = timestamp_to_datetime(update_time)
        if dt.date() == today or dt.date() == yesterday:
            orders_from_yesterday_and_today.append(order)

    # Filter: Nur LONG + FILLED + BUY Positionen
    filtered_orders = [
        o for o in orders_from_yesterday_and_today
        if o.get("positionSide") == "LONG"
        and o.get("status") == "FILLED"
        and o.get("side") == "BUY"
    ]

    # Sortieren nach updateTime (neueste zuerst)
    sorted_orders = sorted(filtered_orders, key=lambda o: int(o.get("updateTime", 0)), reverse=True)

    # Limit auf 50 Ergebnisse
    sorted_orders = sorted_orders[:50]

    # Berechne order_size_usdt für jede Order
    for order in sorted_orders:
        try:
            executed_qty = float(order.get("executedQty", 0))
            avg_price = float(order.get("avgPrice", 0))
            order["order_size_usdt"] = round(executed_qty * avg_price, 4)
        except (ValueError, TypeError):
            order["order_size_usdt"] = None
        
        # updateTime in lesbares Format wandeln
        try:
            ts = int(order.get("updateTime", 0))
            dt = datetime.utcfromtimestamp(ts / 1000)  # ms zu s
            order["updateTime_readable"] = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            order["updateTime_readable"] = "unbekannt"
    logs.append(f"Gefilterte Orders (LONG + FILLED) von gestern und heute: {len(sorted_orders)}")

    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "long_filled_orders": sorted_orders,
        "logs": logs
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
