from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
FILL_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/allOrders"
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

    # Berechne Start und Endzeit für gestern 00:00 bis heute 23:59 UTC
    now = datetime.utcnow()
    start_of_yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999000)

    start_time_ms = int(start_of_yesterday.timestamp() * 1000)
    end_time_ms = int(end_of_today.timestamp() * 1000)

    # Beispiel: hier müsstest du fill_orders_response über deine API erhalten
    fill_orders_response = send_signed_get(api_key, secret_key, FILL_ORDERS_ENDPOINT, {
        "symbol": symbol,
        "limit": "50",
        "startTime": str(start_time_ms),
        "endTime": str(end_time_ms)
    })
    logs.append(f"Fill Orders Full Response: {fill_orders_response}")

    raw_orders = fill_orders_response.get("data", {})
    if isinstance(raw_orders, dict) and "orders" in raw_orders:
        orders = raw_orders["orders"]
    else:
        logs.append("Warnung: Die Orders-Daten sind nicht im erwarteten Format.")
        orders = []

    # Erster Filter: positionSide, status, side
    filtered_orders = [
        o for o in orders
        if o.get("positionSide") == "LONG"
        and o.get("status") == "FILLED"
        and o.get("side") == "BUY"
    ]

    # Zweiter Filter: updateTime im gewünschten Zeitraum
    filtered_orders_time = []
    for order in filtered_orders:
        update_time_ms = order.get('updateTime')
        if update_time_ms and start_time_ms <= update_time_ms <= end_time_ms:
            order['updateTimeReadable'] = datetime.utcfromtimestamp(update_time_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
            filtered_orders_time.append(order)

    # Sortierung auf dem finalen Ergebnis
    sorted_orders = sorted(filtered_orders_time, key=lambda o: int(o.get("updateTime", 0)), reverse=True)

    # Berechnung order_size_usdt für die finalen Orders
    for order in sorted_orders:
        try:
            executed_qty = float(order.get("executedQty", 0))
            avg_price = float(order.get("avgPrice", 0))
            order["order_size_usdt"] = round(executed_qty * avg_price, 4)
        except (ValueError, TypeError):
            order["order_size_usdt"] = None

    return jsonify({"orders": sorted_orders})
    
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
