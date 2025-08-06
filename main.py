from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
FILL_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/allFillOrders"
PRICE_ENDPOINT = "/openApi/swap/v2/quote/price"

# ENV Variablen, falls benötigt
FIREBASE_URL = os.environ.get("FIREBASE_URL", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 🔐 Signatur richtig generieren
def generate_signature(secret_key: str, params: dict) -> str:
    query_string = '&'.join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

# 📡 Signierte Anfrage senden
def send_signed_request(http_method, endpoint, api_key, secret_key, params=None):
    if params is None:
        params = {}

    params["timestamp"] = str(int(time.time() * 1000))
    params["recvWindow"] = "5000"  # Optional, aber empfohlen

    # Signatur erzeugen
    params["signature"] = generate_signature(secret_key, params)
    headers = {
        "X-BX-APIKEY": api_key
    }

    if http_method == "GET":
        response = requests.get(BASE_URL + endpoint, headers=headers, params=params)
    else:
        raise ValueError("Nur GET wird aktuell unterstützt")

    return response.json()

# 🔍 Aktuellen Preis abrufen
def get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    response = requests.get(url)
    data = response.json()
    if data.get("code") == 0 and "data" in data and "price" in data["data"]:
        return float(data["data"]["price"])
    return None

# 🧾 Letzte gefüllte Orders abrufen
def get_last_fill_orders(api_key, secret_key, symbol, limit=2, logs=None):
    params = {
        "symbol": symbol,
        "limit": limit
    }
    response = send_signed_request("GET", FILL_ORDERS_ENDPOINT, api_key, secret_key, params)
    
    if logs is not None:
        logs.append(f"Fill Orders Full Response: {response}")

    if response.get("code") == 0:
        return response.get("data", [])
    else:
        return []

# 📣 Webhook-Route
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logs = []

    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    symbol = data.get("symbol", "BTC-USDT")

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "API Key und Secret Key erforderlich"}), 400

    # Fills abrufen
    fill_orders = get_last_fill_orders(api_key, secret_key, symbol, limit=2, logs=logs)

    # Preis holen
    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "last_fill_orders": fill_orders,
        "logs": logs
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
