from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests

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
    
    # Zeit in Millisekunden berechnen
    now_ms = int(time.time() * 1000) #jetzt
    two_weeks_ms = 14 * 24 * 60 * 60 * 1000 #vor zwei Wochen

    end_time = now_ms
    start_time = now_ms - two_weeks_ms
    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "API Key und Secret Key erforderlich"}), 400

    # API-Parameter vorbereiten
    params = {
        "symbol": symbol,
        "limit": "100"
    }

    if start_time:
        params["startTime"] = str(start_time)
    if end_time:
        params["endTime"] = str(end_time)

    # Anfrage an BingX API
    fill_orders_response = send_signed_get(api_key, secret_key, FILL_ORDERS_ENDPOINT, params)
    logs.append(f"Fill Orders Full Response: {fill_orders_response}")

    raw_orders = fill_orders_response.get("data", {})

    if isinstance(raw_orders, dict) and "orders" in raw_orders:
        orders = raw_orders["orders"]
    else:
        logs.append("Warnung: Die Orders-Daten sind nicht im erwarteten Format (Liste von Dicts).")
        orders = []

    # 🔍 Nur LONG + FILLED Orders behalten (und nur BUY Positionen)
    filtered_orders = [
        o for o in orders
        if o.get("positionSide") == "LONG"
        and o.get("status") == "FILLED"
        and o.get("side") == "BUY"
    ]

    # 📊 Sortieren nach updateTime (neueste zuerst)
    sorted_orders = sorted(filtered_orders, key=lambda o: int(o.get("updateTime", 0)), reverse=True)

    # ➕ Berechne order_size_usdt für jede Order
    for order in sorted_orders:
        try:
            executed_qty = float(order.get("executedQty", 0))
            avg_price = float(order.get("avgPrice", 0))
            order["order_size_usdt"] = round(executed_qty * avg_price, 4)
        except (ValueError, TypeError):
            order["order_size_usdt"] = None

    # ⏱️ Lokal nach Start- und Endzeit filtern (optional)
    if start_time:
        sorted_orders = [o for o in sorted_orders if int(o.get("updateTime", 0)) >= int(start_time)]
    if end_time:
        sorted_orders = [o for o in sorted_orders if int(o.get("updateTime", 0)) <= int(end_time)]

    logs.append(f"Gefilterte Orders (LONG + FILLED): {len(sorted_orders)}")

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
