from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests

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
        "limit": "50"
    }

    fill_orders_response = send_signed_get(api_key, secret_key, FILL_ORDERS_ENDPOINT, params)
    logs.append(f"Fill Orders Full Response: {fill_orders_response}")

    raw_data = fill_orders_response.get("data", {})
    orders = raw_data.get("orders", []) if isinstance(raw_data, dict) else []

    logs.append(f"Orders-Typ: {type(orders)}")
    logs.append(f"Orders-Inhalt: {orders}")

    # Filter: nur LONG, FILLED, BUY
    filtered_orders = [
        o for o in orders
        if o.get("positionSide") == "LONG"
        and o.get("status") == "FILLED"
        and o.get("side") == "BUY"
    ]

    logs.append(f"Gefilterte Orders: {[o.get('orderId') for o in filtered_orders]}")

    # Sortieren nach updateTime (neueste zuerst)
    sorted_orders = sorted(
        filtered_orders,
        key=lambda o: int(o.get("updateTime", 0)),
        reverse=True
    )

    logs.append(f"Sortierte Orders nach updateTime: {[o.get('updateTime') for o in sorted_orders]}")

    # Nur die erste Order nehmen (die aktuellste gültige)
    if sorted_orders:
        latest_order = sorted_orders[0]

        try:
            executed_qty = float(latest_order.get("executedQty", 0))
            avg_price = float(latest_order.get("avgPrice", 0))
            order_size_usdt = round(executed_qty * avg_price, 4)
        except (ValueError, TypeError):
            order_size_usdt = None

        latest_order["order_size_usdt"] = order_size_usdt

        logs.append(f"Erste Order-ID: {latest_order.get('orderId')}")
        logs.append(f"Ordergröße (USDT): {order_size_usdt}")
        result_orders = [latest_order]
    else:
        logs.append("Keine gültige Order gefunden.")
        result_orders = []

    # Aktuellen Preis abrufen (optional)
    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "last_fill_orders": result_orders,
        "logs": logs
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
