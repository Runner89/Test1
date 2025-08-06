from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
FILL_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/allFillOrders" #"/openApi/swap/v2/trade/allFillOrders"
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

    # 📥 Parameter für allFillOrders
    params = {
        "symbol": symbol,
        "limit": "1"
    }

    # Anfrage senden
    fill_orders = send_signed_get(api_key, secret_key, FILL_ORDERS_ENDPOINT, params)
    logs.append(f"Fill Orders Full Response: {fill_orders}")
    
    orders_data = fill_orders.get("data", {}).get("orders", [])
    if isinstance(orders_data, list) and orders_data:
        # Nach Zeitstempel sortieren (neueste zuerst)
        orders_data.sort(key=lambda x: x.get("time", 0), reverse=True)
        raw_order = orders_data[0]
        # Optional: Lesbare Felder extrahieren
        last_order = {
            "symbol": raw_order.get("symbol"),
            "order_id": raw_order.get("orderId"),
            "side": raw_order.get("side"),
            "position": raw_order.get("positionSide"),
            "type": raw_order.get("type"),
            "status": raw_order.get("status"),
            "orig_qty": raw_order.get("origQty"),
            "executed_qty": raw_order.get("executedQty"),
            "avg_price": raw_order.get("avgPrice"),
            "quote_amount": raw_order.get("cumQuote"),
            "commission": raw_order.get("commission"),
            "leverage": raw_order.get("leverage"),
            "timestamp": raw_order.get("time")
        }
    else:
        last_order = {}
        logs.append(f"Letzte Order extrahiert: {last_order}")

    
    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")
    
    return jsonify({
        "error": False,
        "last_fill_order": last_order,
        "logs": logs
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
