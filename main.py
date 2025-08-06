#Market Order mit Hebel wird gesetzt
#Hebel muss in Bingx selber vorher eingestellt werden
#Preis, welcher im JSON übergeben wurde, wird in Firebase gespeichert
#Durschnittspreis wird von Firebase berechnet und entsprechend die Sell-Limit Order gesetzt
#Bei Alarm wird angegeben, ab welcher SO ein Alarm via Telegramm gesendet wird
#Verfügbares Guthaben wird ermittelt
#Ordergrösse = (Verfügbares Guthaben - Sicherheit)/Pyramiding
#StopLoss 2% über Liquidationspreis
#Falls Firebaseverbindung fehlschlägt, wird der Durchschnittspreis aus Bingx -0.02% für die Berechnung der Sell-Limit-Order verwendet.

###### Funktioniert nur, wenn alle Order die gleiche Grösse haben (Durchschnittspreis stimmt sonst nicht in Firebase) #####

#https://......../webhook
#{
#    "api_key": "",
#    "secret_key": "",
#    "symbol": "BABY-USDT",
#    "position_side": "LONG",
#    "sell_percentage": 2.5,
#    "price": 0.068186,
#    "leverage": 1,
#    "FIREBASE_SECRET": "",
#    "alarm": 1,
#    "pyramiding": 8,
#    "sicherheit": 96
#}

from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
BALANCE_ENDPOINT = "/openApi/swap/v2/user/balance"
ORDER_ENDPOINT = "/openApi/swap/v2/trade/order"
PRICE_ENDPOINT = "/openApi/swap/v2/quote/price"
OPEN_ORDERS_ENDPOINT = "/openApi/swap/v2/trade/openOrders"
FIREBASE_URL = os.environ.get("FIREBASE_URL", "")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def generate_signature(secret_key: str, params: str) -> str:
    return hmac.new(secret_key.encode('utf-8'), params.encode('utf-8'), hashlib.sha256).hexdigest()

def get_last_fill_orders(api_key, secret_key, symbol, limit=2):
    endpoint = "/openApi/swap/v2/trade/allFillOrders"
    params = {
        "symbol": symbol,
        "limit": limit
    }
    response = send_signed_request("GET", endpoint, api_key, secret_key, params)
    if response.get("code") == 0:
        return response.get("data", [])
    else:
        return []

def get_futures_balance(api_key: str, secret_key: str):
    timestamp = int(time.time() * 1000)
    params = f"timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{BALANCE_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    response = requests.get(url, headers=headers)
    return response.json()

def get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    response = requests.get(url)
    data = response.json()
    if data.get("code") == 0 and "data" in data and "price" in data["data"]:
        return float(data["data"]["price"])
    else:
        return None



def send_signed_request(http_method, endpoint, api_key, secret_key, params=None):
    if params is None:
        params = {}

    timestamp = int(time.time() * 1000)
    params['timestamp'] = timestamp

    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    signature = hmac.new(secret_key.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    params['signature'] = signature

    url = f"{BASE_URL}{endpoint}"
    headers = {"X-BX-APIKEY": api_key}

    if http_method == "GET":
        response = requests.get(url, headers=headers, params=params)
    elif http_method == "POST":
        response = requests.post(url, headers=headers, json=params)
    elif http_method == "DELETE":
        response = requests.delete(url, headers=headers, params=params)
    else:
        raise ValueError("Unsupported HTTP method")

    return response.json()

def get_current_position(api_key, secret_key, symbol, position_side, logs=None):
    endpoint = "/openApi/swap/v2/user/positions"
    params = {"symbol": symbol}
    response = send_signed_request("GET", endpoint, api_key, secret_key, params)

    positions = response.get("data", [])
    raw_positions = positions if isinstance(positions, list) else []

    if logs is not None:
        logs.append(f"Positions Rohdaten: {raw_positions}")

    position_size = 0
    liquidation_price = None

    if response.get("code") == 0:
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                if logs is not None:
                    logs.append(f"Gefundene Position: {pos}")
                try:
                    position_size = float(pos.get("size", 0)) or float(pos.get("positionAmt", 0))
                    liquidation_price = float(pos.get("liquidationPrice", 0))
                    if logs is not None:
                        logs.append(f"Position size: {position_size}, Liquidation price: {liquidation_price}")
                except (ValueError, TypeError) as e:
                    position_size = 0
                    if logs is not None:
                        logs.append(f"Fehler beim Parsen: {e}")
                break
    else:
        if logs is not None:
            logs.append(f"API Antwort Fehlercode: {response.get('code')}")

    return position_size, raw_positions, liquidation_price


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logs = []

    # Eingabewerte zuerst auslesen!
    pyramiding = float(data.get("pyramiding", 1))
    sicherheit = float(data.get("sicherheit", 0))
    sell_percentage = data.get("sell_percentage")
    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    symbol = data.get("symbol", "IDOL-USDT")
    position_side = data.get("position_side") or data.get("positionSide") or "LONG"
    firebase_secret = data.get("FIREBASE_SECRET")
    price_from_webhook = data.get("price")

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "api_key und secret_key sind erforderlich"}), 400

    # Jetzt erst auf API-Key etc. zugreifen
    fill_orders = get_last_fill_orders(api_key, secret_key, symbol, limit=2)
    logs.append(f"Letzte 2 Fill Orders: {fill_orders}")

    return jsonify({
        "error": False,
        "logs": logs,
        "last_fill_orders": fill_orders
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
