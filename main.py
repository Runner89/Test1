#Market Order mit Hebel wird gesetzt
#Hebel muss in Bingx selber vorher eingestellt werden
#Preis, welcher im JSON übergeben wurde, wird in Firebase gespeichert
#Durschnittspreis wird von Firebase berechnet und entsprechend die Sell-Limit Order gesetzt
#Bei Alarm wird angegeben, ab welcher SO ein Alarm via Telegramm gesendet wird

###### Funktioniert nur, wenn alle Order die gleiche Grösse haben (Durchschnittspreis stimmt sonst nicht in Firebase) #####

#https://mexc-trading-bot-yrn9.onrender.com/webhook
#{
#  "api_key": "",
#  "secret_key": "",
#  "symbol": "PENGU-USDT",
#  "usdt_amount": 2.2,
#  "position_side": "LONG",
#  "sell_percentage": 1.5,
#  "price": 0.068186,
#  "leverage": 2,
#  "FIREBASE_SECRET": "",
#  "alarm": 1
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

def place_market_order(api_key, secret_key, symbol, usdt_amount, position_side="LONG"):
    price = get_current_price(symbol)
    if price is None:
        return {"code": 99999, "msg": "Failed to get current price"}

    quantity = round(usdt_amount / price, 6)
    timestamp = int(time.time() * 1000)

    params_dict = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quantity": quantity,
        "positionSide": position_side,
        "timestamp": timestamp
    }

    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    signature = generate_signature(secret_key, query_string)
    params_dict["signature"] = signature

    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {
        "X-BX-APIKEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json=params_dict)
    return response.json()

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
    if response.get("code") == 0:
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                if logs is not None:
                    logs.append(f"Gefundene Position: {pos}")
                try:
                    position_size = float(pos.get("size", 0))
                    if position_size == 0:
                        position_size = float(pos.get("positionAmt", 0))
                    if logs is not None:
                        logs.append(f"Position size ermittelt: {position_size}")
                except (ValueError, TypeError) as e:
                    position_size = 0
                    if logs is not None:
                        logs.append(f"Fehler beim Parsen der Positionsgröße: {e}")
                break
    else:
        if logs is not None:
            logs.append(f"API Antwort Fehlercode: {response.get('code')}")

    return position_size, raw_positions

def place_limit_sell_order(api_key, secret_key, symbol, quantity, limit_price, position_side="LONG"):
    timestamp = int(time.time() * 1000)

    params_dict = {
        "symbol": symbol,
        "side": "SELL",
        "type": "LIMIT",
        "quantity": round(quantity, 6),
        "price": round(limit_price, 6),
        "timeInForce": "GTC",
        "positionSide": position_side,
        "timestamp": timestamp
    }

    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    signature = generate_signature(secret_key, query_string)
    params_dict["signature"] = signature

    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {
        "X-BX-APIKEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json=params_dict)
    return response.json()
    
def sende_telegram_nachricht(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "Telegram nicht konfiguriert"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    response = requests.post(url, json=payload)
    return f"Telegram Antwort: {response.status_code}"

    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    signature = generate_signature(secret_key, query_string)
    params_dict["signature"] = signature

    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {
        "X-BX-APIKEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json=params_dict)
    return response.json()

def get_open_orders(api_key, secret_key, symbol):
    timestamp = int(time.time() * 1000)
    params = f"symbol={symbol}&timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{OPEN_ORDERS_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    response = requests.get(url, headers=headers)

    try:
        data = response.json()
    except ValueError:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw_response": response.text}

    return data

def cancel_order(api_key, secret_key, symbol, order_id):
    timestamp = int(time.time() * 1000)
    params = f"symbol={symbol}&orderId={order_id}&timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{ORDER_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    response = requests.delete(url, headers=headers)
    return response.json()

def firebase_speichere_ordergroesse(asset, betrag, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{asset}.json?auth={firebase_secret}"
    data = {"usdt_amount": betrag}
    response = requests.put(url, json=data)
    return f"Ordergröße für {asset} gespeichert: {betrag}, Status: {response.status_code}"

def firebase_lese_ordergroesse(asset, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{asset}.json?auth={firebase_secret}"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    data = response.json()
    return float(data.get("usdt_amount", 0)) if isinstance(data, dict) else None

def firebase_loesche_ordergroesse(asset, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{asset}.json?auth={firebase_secret}"
    response = requests.delete(url)
    return f"Ordergröße für {asset} gelöscht, Status: {response.status_code}"

def firebase_speichere_kaufpreis(asset, price, firebase_secret):
    url = f"{FIREBASE_URL}/kaufpreise/{asset}.json?auth={firebase_secret}"
    data = {"price": price}
    response = requests.post(url, json=data)
    return f"Kaufpreis gespeichert für {asset}: {price}, Status: {response.status_code}"

def firebase_loesche_kaufpreise(asset, firebase_secret):
    url = f"{FIREBASE_URL}/kaufpreise/{asset}.json?auth={firebase_secret}"
    response = requests.delete(url)
    if response.status_code == 200:
        return f"Kaufpreise für {asset} gelöscht."
    else:
        return f"Fehler beim Löschen der Kaufpreise für {asset}: Status {response.status_code}"

def firebase_lese_kaufpreise(asset, firebase_secret):
    url = f"{FIREBASE_URL}/kaufpreise/{asset}.json?auth={firebase_secret}"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    data = response.json()
    if not data:
        return []
    return [eintrag.get("price") for eintrag in data.values() if isinstance(eintrag, dict) and "price" in eintrag]

def berechne_durchschnittspreis(preise):
    preise = [float(p) for p in preise if isinstance(p, (int, float, str)) and str(p).replace('.', '', 1).isdigit()]
    return round(sum(preise) / len(preise), 6) if preise else None

def set_leverage(api_key, secret_key, symbol, leverage, position_side="LONG"):
    endpoint = "/openApi/swap/v2/trade/leverage"
    
    # mappe positionSide auf side für Hebel-Setzung
    side_map = {
        "LONG": "BUY",
        "SHORT": "SELL"
    }
    
    params = {
        "symbol": symbol,
        "leverage": int(leverage),
        "positionSide": position_side.upper(),
        "side": side_map.get(position_side.upper())  # korrektes Side-Value setzen
    }
    return send_signed_request("POST", endpoint, api_key, secret_key, params)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logs = []

    # Eingabewerte
    pyramiding = float(data.get("pyramiding", 1))
    sicherheit = float(data.get("sicherheit", 0))
    sell_percentage = data.get("sell_percentage")
    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    symbol = data.get("symbol", "BTC-USDT")
    position_side = data.get("position_side") or data.get("positionSide") or "LONG"
    firebase_secret = data.get("FIREBASE_SECRET")
    price_from_webhook = data.get("price")

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "api_key und secret_key sind erforderlich"}), 400

    base_asset = symbol.split("-")[0]
    available_usdt = 0.0

    # 0. USDT-Guthaben vor Order abrufen
    try:
        balance_response = get_futures_balance(api_key, secret_key)
        logs.append(f"Balance Response: {balance_response}")
        if balance_response.get("code") == 0:
            balance_data = balance_response.get("data", {}).get("balance", {})
            available_usdt = float(balance_data.get("availableMargin", 0))
            logs.append(f"Freies USDT Guthaben: {available_usdt}")
        else:
            logs.append("Fehler beim Abrufen der Balance.")
    except Exception as e:
        logs.append(f"Fehler bei Balance-Abfrage: {e}")
        available_usdt = None

    # 1. Hebel setzen
    try:
        logs.append(f"Setze Hebel auf {pyramiding} für {symbol} ({position_side})...")
        leverage_response = set_leverage(api_key, secret_key, symbol, pyramiding, position_side)
        logs.append(f"Hebel gesetzt: {leverage_response}")
    except Exception as e:
        logs.append(f"Fehler beim Setzen des Hebels: {e}")

    # 2. Offene Orders abrufen
    open_orders = {}
    try:
        open_orders = get_open_orders(api_key, secret_key, symbol)
        logs.append(f"Open Orders: {open_orders}")
    except Exception as e:
        logs.append(f"Fehler bei Orderprüfung: {e}")

    # 3. Ordergröße ermitteln (Compounding-Logik)
    usdt_amount = 0
    if firebase_secret:
        try:
            open_sell_orders_exist = False
            if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                for order in open_orders.get("data", {}).get("orders", []):
                    if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                        open_sell_orders_exist = True
                        break

            if open_sell_orders_exist:
                usdt_amount = firebase_lese_ordergroesse(base_asset, firebase_secret) or 0
                logs.append(f"Verwende gespeicherte Ordergröße aus Firebase: {usdt_amount}")
            else:
                logs.append(firebase_loesche_ordergroesse(base_asset, firebase_secret))
                if available_usdt is not None and pyramiding > 0:
                    usdt_amount = max((available_usdt - sicherheit) / pyramiding, 0)
                    logs.append(f"Neue Ordergröße berechnet: (Balance {available_usdt} - Sicherheit {sicherheit}) / Pyramiding {pyramiding} = {usdt_amount}")
                    logs.append(firebase_speichere_ordergroesse(base_asset, usdt_amount, firebase_secret))
        except Exception as e:
            logs.append(f"Fehler bei Ordergrößenberechnung: {e}")

    # 4. Market-Order ausführen
    logs.append(f"Plaziere Market-Order mit {usdt_amount} USDT für {symbol} ({position_side})...")
    order_response = place_market_order(api_key, secret_key, symbol, float(usdt_amount), position_side)
    time.sleep(2)
    logs.append(f"Market-Order Antwort: {order_response}")

    # 5. Positionsgröße ermitteln
    try:
        sell_quantity, positions_raw = get_current_position(api_key, secret_key, symbol, position_side, logs)
        if sell_quantity == 0:
            executed_qty_str = order_response.get("data", {}).get("order", {}).get("executedQty")
            if executed_qty_str:
                sell_quantity = float(executed_qty_str)
                logs.append(f"[Market Order] Ausgeführte Menge aus order_response genutzt: {sell_quantity}")
    except Exception as e:
        sell_quantity = 0
        logs.append(f"Fehler bei Positionsabfrage: {e}")

    # 6. Kaufpreise ggf. löschen
    if firebase_secret and not open_sell_orders_exist:
        try:
            logs.append(firebase_loesche_kaufpreise(base_asset, firebase_secret))
        except Exception as e:
            logs.append(f"Fehler beim Löschen der Kaufpreise: {e}")

    # 7. Kaufpreis speichern
    if firebase_secret and price_from_webhook:
        try:
            logs.append(firebase_speichere_kaufpreis(base_asset, float(price_from_webhook), firebase_secret))
        except Exception as e:
            logs.append(f"Fehler beim Speichern des Kaufpreises: {e}")

    # 8. Durchschnitt berechnen
    durchschnittspreis = None
    kaufpreise = []
    if firebase_secret:
        try:
            kaufpreise = firebase_lese_kaufpreise(base_asset, firebase_secret)
            durchschnittspreis = berechne_durchschnittspreis(kaufpreise or [])
            logs.append(f"[Firebase] Durchschnittspreis berechnet: {durchschnittspreis}")
        except Exception as e:
            logs.append(f"Fehler bei Durchschnittsberechnung: {e}")

    # 9. Alte Sell-Limit-Orders löschen
    try:
        if isinstance(open_orders, dict) and open_orders.get("code") == 0:
            for order in open_orders.get("data", {}).get("orders", []):
                if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                    cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                    logs.append(f"Gelöschte Order {order.get('orderId')}: {cancel_response}")
    except Exception as e:
        logs.append(f"Fehler beim Löschen der Sell-Limit-Orders: {e}")

    # 10. Neue Limit-Order setzen
    limit_order_response = None
    try:
        if durchschnittspreis and sell_percentage:
            limit_price = round(durchschnittspreis * (1 + float(sell_percentage) / 100), 6)
        else:
            limit_price = 0

        if sell_quantity > 0 and limit_price > 0:
            limit_order_response = place_limit_sell_order(api_key, secret_key, symbol, sell_quantity, limit_price, position_side)
            logs.append(f"Limit-Order gesetzt (auf Basis Durchschnittspreis {durchschnittspreis}): {limit_order_response}")
        else:
            logs.append("Ungültige Daten, keine Limit-Order gesetzt.")
    except Exception as e:
        logs.append(f"Fehler bei Limit-Order: {e}")

    # 11. Alarm senden
    alarm_trigger = int(data.get("alarm", 0))
    anzahl_käufe = len(kaufpreise or [])
    anzahl_nachkäufe = max(anzahl_käufe - 1, 0)

    if anzahl_nachkäufe >= alarm_trigger:
        try:
            nachricht = f"{base_asset}:\nNachkäufe: {anzahl_nachkäufe}"
            telegram_result = sende_telegram_nachricht(nachricht)
            logs.append(f"Telegram gesendet: {telegram_result}")

            if firebase_secret:
                firebase_speichere_alarmwert(base_asset, anzahl_käufe, firebase_secret)
                logs.append(f"Neuer Alarmwert in Firebase gespeichert: {anzahl_käufe}")
        except Exception as e:
            logs.append(f"Fehler beim Senden der Telegram-Nachricht: {e}")

    return jsonify({
        "error": False,
        "order_result": order_response,
        "limit_order_result": limit_order_response,
        "symbol": symbol,
        "usdt_amount": usdt_amount,
        "sell_quantity": sell_quantity,
        "price_from_webhook": price_from_webhook,
        "sell_percentage": sell_percentage,
        "firebase_average_price": durchschnittspreis,
        "firebase_all_prices": kaufpreise,
        "usdt_balance_before_order": available_usdt,
        "logs": logs
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
