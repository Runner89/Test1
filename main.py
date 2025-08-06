from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
FILL_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/allOrders"  # Falls alle Orders, auch gefüllte, genutzt werden sollen
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

    # Debug Response-Code & Nachricht
    logs.append(f"Response Code: {fill_orders_response.get('code')}")
    logs.append(f"Response Msg: {fill_orders_response.get('msg')}")

    orders = fill_orders_response.get("data", [])

    logs.append(f"Orders-Typ: {type(orders)}")
    if isinstance(orders, list):
        logs.append(f"Erste 3 Orders und deren Typen: {[(o, type(o)) for o in orders[:3]]}")

        if all(isinstance(o, dict) for o in orders):
            logs.append(f"Unsortierte Orders (updateTime): {[o.get('updateTime') for o in orders]}")

            orders_sorted = sorted(
                orders,
                key=lambda o: int(o.get("updateTime", 0)) if o.get("updateTime") is not None else 0,
                reverse=True
            )
            logs.append(f"Sortierte Orders (updateTime): {[o.get('updateTime') for o in orders_sorted]}")
        else:
            logs.append("Warnung: Nicht alle Orders sind Dicts, Sortierung übersprungen.")
            orders_sorted = orders
    else:
        logs.append(f"Orders-Inhalt: {orders}")
        logs.append("Warnung: Die Orders-Daten sind nicht im erwarteten Format (Liste von Dicts).")
        orders_sorted = orders

    current_price = get_current_price(symbol)
    if current_price is not None:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "last_fill_orders": orders_sorted,
        "logs": logs
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
