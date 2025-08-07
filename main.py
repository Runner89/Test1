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

saved_usdt_amounts = {}  # globales Dict für alle Coins

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

def place_stop_loss_order(api_key, secret_key, symbol, quantity, stop_price, position_side="LONG"):
    timestamp = int(time.time() * 1000)

    params_dict = {
        "symbol": symbol,
        "side": "SELL",
        "type": "STOP_MARKET",
        "stopPrice": round(stop_price, 6),
        "quantity": round(quantity, 6),
        "positionSide": position_side,
        "timestamp": timestamp,
        "timeInForce": "GTC"
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

def firebase_speichere_ordergroesse(coin, betrag, secret):
    # Speichert die Ordergröße (z. B. in USDT) für einen Coin in Firebase. Rückgabe: Bestätigungstext oder Exception bei Fehler.

    url = f"https://<dein-firebase-url>/{secret}/Ordergroesse/{coin}.json"
    try:
        response = requests.put(url, json=betrag)
        response.raise_for_status()
        return f"Ordergröße gespeichert ({coin}): {betrag}"
    except requests.RequestException as e:
        raise RuntimeError(f"Ordergröße speichern fehlgeschlagen ({coin}): {e}")

def firebase_lese_ordergroesse(coin, secret):
    # Liest die gespeicherte Ordergröße (z. B. in USDT) für einen Coin aus Firebase. Gibt die Ordergröße (float) zurück oder None, wenn keine gespeichert ist. Wirft Exception nur bei HTTP-Fehlern.

    url = f"https://<dein-firebase-url>/{secret}/Ordergroesse/{coin}.json"
    try:
        response = requests.get(url)
        response.raise_for_status()

        ordergroesse = response.json()
        return float(ordergroesse) if ordergroesse is not None else None
    except requests.RequestException as e:
        raise RuntimeError(f"Ordergröße lesen fehlgeschlagen ({coin}): {e}")
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"Ungültige Ordergröße in Firebase ({coin}): {e}")


def firebase_loesche_ordergroesse(coin, secret):
    # Löscht die gespeicherte Ordergröße eines Coins aus Firebase. Rückgabe: Bestätigungstext oder Exception bei HTTP-Fehler.
    
    url = f"https://<dein-firebase-url>/{secret}/Ordergroesse/{coin}.json"
    try:
        response = requests.delete(url)
        response.raise_for_status()
        return f"Ordergröße gelöscht ({coin})"
    except requests.RequestException as e:
        raise RuntimeError(f"Ordergröße löschen fehlgeschlagen ({coin}): {e}")

def firebase_speichere_kaufpreis(coin, preis, secret):
    #Speichert einen neuen Kaufpreis für den Coin in Firebase. Rückgabe: Firebase-Response (meist 'name': "...") oder Exception bei Fehler.
    url = f"https://<dein-firebase-url>/{secret}/Kaufpreise/{coin}.json"
    try:
        response = requests.post(url, json=preis)
        response.raise_for_status()  # ← wirft Exception bei HTTP-Fehlern
        return f"Kaufpreis gespeichert ({coin}): {response.json()}"
    except requests.RequestException as e:
        raise RuntimeError(f"Kaufpreis speichern fehlgeschlagen ({coin}): {e}")

def firebase_loesche_kaufpreise(coin, secret):
    #Löscht alle Kaufpreise zu einem Coin in Firebase. Rückgabe: 'Gelöscht' oder Exception bei Fehler.
    url = f"https://<dein-firebase-url>/{secret}/Kaufpreise/{coin}.json"
    try:
        response = requests.delete(url)
        response.raise_for_status()
        return f"Kaufpreise gelöscht ({coin})"
    except requests.RequestException as e:
        raise RuntimeError(f"Kaufpreise löschen fehlgeschlagen ({coin}): {e}"

def firebase_lese_kaufpreise(coin, secret):
    #Liest alle gespeicherten Kaufpreise für einen Coin aus Firebase. Gibt eine Liste von Preisen zurück oder eine leere Liste, falls keine vorhanden. Wirft Exception bei HTTP-Fehlern.
    url = f"https://<dein-firebase-url>/{secret}/Kaufpreise/{coin}.json"
    try:
        response = requests.get(url)
        response.raise_for_status()

        daten = response.json()
        if daten is None:
            return []  # Kein Eintrag vorhanden → leere Liste zurückgeben

        # Firebase gibt ein dict mit IDs zurück → Werte extrahieren
        kaufpreise = list(daten.values())
        return kaufpreise
    except requests.RequestException as e:
        raise RuntimeError(f"Kaufpreise lesen fehlgeschlagen ({coin}): {e}")
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

def firebase_setze_status(coin, status, secret):
    # Setzt den Status (z. B. 'Fehler') für den Coin. Rückgabe: Bestätigung oder Exception bei Fehler.

    url = f"https://<dein-firebase-url>/{secret}/Status/{coin}.json"
    try:
        response = requests.put(url, json=status)
        response.raise_for_status()
        return f"Status '{status}' gesetzt ({coin})"
    except requests.RequestException as e:
        raise RuntimeError(f"Status setzen fehlgeschlagen ({coin}): {e}")
def firebase_lese_status(coin, secret):
    #    Liest den Status eines Coins aus Firebase. Gibt den Status als String zurück oder None, wenn kein Status gesetzt ist. Wirft Exception nur bei HTTP-Fehlern.
  
    url = f"https://<dein-firebase-url>/{secret}/Status/{coin}.json"
    try:
        response = requests.get(url)
        response.raise_for_status()

        status = response.json()
        return status  # Kann auch None sein, wenn nichts vorhanden ist
    except requests.RequestException as e:
        raise RuntimeError(f"Status lesen fehlgeschlagen ({coin}): {e}")

def firebase_loesche_status(coin, secret):
    #  Löscht den Status eines Coins in Firebase. Gibt eine Bestätigung zurück oder wirft Exception bei HTTP-Fehlern.
    url = f"https://<dein-firebase-url>/{secret}/Status/{coin}.json"
    try:
        response = requests.delete(url)
        response.raise_for_status()
        return f"Status gelöscht ({coin})"
    except requests.RequestException as e:
        raise RuntimeError(f"Status löschen fehlgeschlagen ({coin}): {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    global saved_usdt_amounts
    data = request.json
    logs = []

    # Symbol-Infos extrahieren
    base_asset = data.get("symbol", "BTC-USDT").split("-")[0]

    # Gespeicherte Ordergröße aus Dict (lokal)
    saved_usdt_amount = saved_usdt_amounts.get(base_asset)

    # Eingabeparameter
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

    available_usdt = 0.0

    # 0. USDT-Guthaben abfragen
    try:
        balance_response = get_futures_balance(api_key, secret_key)
        logs.append(f"Balance Response: {balance_response}")
        if balance_response.get("code") == 0:
            available_usdt = float(balance_response.get("data", {}).get("balance", {}).get("availableMargin", 0))
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

    # 2. Offene Orders abfragen
    open_orders = {}
    try:
        open_orders = get_open_orders(api_key, secret_key, symbol)
        logs.append(f"Open Orders: {open_orders}")
    except Exception as e:
        logs.append(f"Fehler bei Orderprüfung: {e}")
        sende_telegram_nachricht(f"Fehler bei Orderprüfung {base_asset}: {e}")

    # 3. Ordergröße berechnen (nur wenn keine offene SELL-Limit Order existiert)
    usdt_amount = 0

    if firebase_secret:
        try:
            open_sell_orders_exist = False
            if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                for order in open_orders.get("data", {}).get("orders", []):
                    if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                        open_sell_orders_exist = True
                        break

            # Wenn keine offene SELL-Limit-Order existiert → Firebase-Ordergröße löschen und neu berechnen
            if not open_sell_orders_exist:
                logs.append(firebase_loesche_ordergroesse(base_asset, firebase_secret))
                logs.append(firebase_loesche_status(base_asset, firebase_secret))
                if available_usdt is not None and pyramiding > 0:
                    usdt_amount = max((available_usdt - sicherheit) / pyramiding, 0)
                    saved_usdt_amounts[base_asset] = usdt_amount
                    logs.append(f"Neue Ordergrösse berechnet: {usdt_amount}")
                    logs.append(firebase_speichere_ordergroesse(base_asset, usdt_amount, firebase_secret))

            # Falls bereits gespeichert
            saved_usdt_amount = saved_usdt_amounts.get(base_asset, 0)

            if not saved_usdt_amount or saved_usdt_amount == 0:
                usdt_amount = firebase_lese_ordergroesse(base_asset, firebase_secret) or 0
                if usdt_amount > 0:
                    saved_usdt_amounts[base_asset] = usdt_amount
                    logs.append(f"Ordergrösse aus Firebase gelesen: {usdt_amount}")
                    sende_telegram_nachricht(f"Ordergrösse aus Firebase gelesen bei Coin: {base_asset}")
                else:
                    logs.append(f"⚠️ Keine Ordergrösse in Variable oder Firebase gefunden.")
                    sende_telegram_nachricht(f"keine Ordergrösse gefunden bei Coin: {base_asset}")
            else:
                usdt_amount = saved_usdt_amount
                logs.append(f"Verwende gespeicherte Ordergrösse aus Dict: {usdt_amount}")

        except Exception as e:
            logs.append(f"Fehler bei Ordergrössenberechnung: {e}")
            sende_telegram_nachricht(f"❌ Ausnahmefehler bei Ordergrössenberechnung für {base_asset}: {e}")

    # 4. Market-Order platzieren
    logs.append(f"Plaziere Market-Order mit {usdt_amount} USDT für {symbol} ({position_side})...")
    order_response = place_market_order(api_key, secret_key, symbol, float(usdt_amount), position_side)
    time.sleep(2)
    logs.append(f"Market-Order Antwort: {order_response}")

    # 5. Position, Menge, Liquidationspreis abrufen
    try:
        sell_quantity, positions_raw, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs)
        if sell_quantity == 0:
            executed_qty_str = order_response.get("data", {}).get("order", {}).get("executedQty")
            if executed_qty_str:
                sell_quantity = float(executed_qty_str)
                logs.append(f"[Market Order] Ausgeführte Menge: {sell_quantity}")

        if liquidation_price:
            stop_loss_price = round(liquidation_price * 1.02, 6)
            logs.append(f"Stop-Loss-Preis: {stop_loss_price}")
        else:
            stop_loss_price = None
            logs.append("Kein Liquidationspreis vorhanden.")
    except Exception as e:
        sell_quantity = 0
        stop_loss_price = None
        logs.append(f"Fehler bei Positions-/Liquidationsabfrage: {e}")
        sende_telegram_nachricht(f"Fehler bei Positions-/Liquidationsabfrage {base_asset}: {e}")

    # 6. Kaufpreise löschen (wenn keine SELL-Limit-Order offen)
    if firebase_secret and not open_sell_orders_exist:
        try:
            logs.append(firebase_loesche_kaufpreise(base_asset, firebase_secret))
            logs.append(firebase_loesche_status(base_asset, firebase_secret))
        except Exception as e:
            logs.append(f"Fehler beim Löschen der Kaufpreise: {e}")
            sende_telegram_nachricht(f"Fehler beim Löschen der Kaufpreise {base_asset}: {e}")

    # 7. Kaufpreis speichern inkl. Status setzen
    if firebase_secret and price_from_webhook:
        try:
            logs.append(firebase_speichere_kaufpreis(base_asset, float(price_from_webhook), firebase_secret))
            logs.append(firebase_setze_status(base_asset, "Fehler", firebase_secret))
        except Exception as e:
            logs.append(f"Fehler beim Speichern des Kaufpreises: {e}")
            sende_telegram_nachricht(f"Fehler beim Speichern des Kaufpreises {base_asset}: {e}")

    # 8. Durchschnittspreis ermitteln – NUR wenn kein Fehler-Status
    durchschnittspreis = None
    kaufpreise = []

    try:
        status = firebase_lese_status(base_asset, firebase_secret)
        if status != "Fehler":
            kaufpreise = firebase_lese_kaufpreise(base_asset, firebase_secret)
            durchschnittspreis = berechne_durchschnittspreis(kaufpreise or [])
            if durchschnittspreis:
                logs.append(f"[Firebase] Durchschnittspreis: {durchschnittspreis}")
            else:
                logs.append("[Firebase] Keine gültigen Kaufpreise.")
                sende_telegram_nachricht(f"Keine gültigen Kaufpreise gefunden {base_asset}")
    except Exception as e:
        logs.append(f"Firebase-Fehler (Durchschnittspreis): {e}")
        sende_telegram_nachricht(f"Firebase-Zugriff fehlgeschlagen {base_asset}: {e}")

    # Fallback: avgPrice aus BingX
    if not durchschnittspreis or durchschnittspreis == 0:
        try:
            for pos in positions_raw:
                if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                    avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                    if avg_price > 0:
                        durchschnittspreis = round(avg_price * (1 - 0.002), 6)
                        logs.append(f"[Fallback] avgPrice genutzt: {durchschnittspreis}")
                        sende_telegram_nachricht(f"Fehler in Firebase (Kaufpreis) bei Coin: {base_asset}")
                    break
        except Exception as e:
            logs.append(f"[Fehler] avgPrice-Fallback: {e}")

    # 9. Alte SELL-Limit Orders löschen
    try:
        if open_orders.get("code") == 0:
            for order in open_orders["data"]["orders"]:
                if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                    cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                    logs.append(f"SELL-Limit Order gelöscht: {cancel_response}")
    except Exception as e:
        logs.append(f"Fehler beim Löschen der SELL-Limit-Orders: {e}")
        sende_telegram_nachricht(f"Fehler beim Löschen der SELL-Limit-Order {base_asset}: {e}")

    # 10. Neue SELL-Limit Order platzieren
    limit_order_response = None
    try:
        if durchschnittspreis and sell_percentage:
            limit_price = round(durchschnittspreis * (1 + float(sell_percentage) / 100), 6)
        else:
            limit_price = 0

        if sell_quantity > 0 and limit_price > 0:
            limit_order_response = place_limit_sell_order(api_key, secret_key, symbol, sell_quantity, limit_price, position_side)
            logs.append(f"Neue Limit-Order gesetzt: {limit_order_response}")
        else:
            logs.append("Limit-Order nicht gesetzt – unvollständige Daten.")
    except Exception as e:
        logs.append(f"Fehler bei Limit-Order: {e}")
        sende_telegram_nachricht(f"Fehler bei Limit-Order {base_asset}: {e}")

    # 11. Alte Stop-Market SL-Orders löschen
    try:
        for order in open_orders.get("data", {}).get("orders", []):
            if order.get("type") == "STOP_MARKET" and order.get("positionSide") == position_side:
                cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                logs.append(f"SL-Order gelöscht: {cancel_response}")
    except Exception as e:
        logs.append(f"Fehler beim Löschen alter Stop-Market-Orders: {e}")
        sende_telegram_nachricht(f"Fehler beim Löschen SL {base_asset}: {e}")

    # 12. Neue Stop-Loss Order setzen
    stop_loss_response = None
    try:
        if sell_quantity > 0 and stop_loss_price:
            stop_loss_response = place_stop_loss_order(api_key, secret_key, symbol, sell_quantity, stop_loss_price, position_side)
            logs.append(f"Stop-Loss Order gesetzt: {stop_loss_response}")
        else:
            logs.append("Keine Stop-Loss Order gesetzt – unvollständige Daten.")
    except Exception as e:
        logs.append(f"Fehler bei SL-Order: {e}")
        sende_telegram_nachricht(f"Fehler beim Setzen der SL-Order {base_asset}: {e}")

    # 13. Telegram-Alarm bei Nachkaufüberschreitung
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
                logs.append(f"Alarmwert in Firebase gespeichert: {anzahl_käufe}")
        except Exception as e:
            logs.append(f"Fehler bei Telegram-Alarm: {e}")
            sende_telegram_nachricht(f"Fehler bei Telegram-Nachricht {base_asset}: {e}")

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
        "stop_loss_price": stop_loss_price if liquidation_price else None,
        "stop_loss_response": stop_loss_response if liquidation_price else None,
        "saved_usdt_amount": saved_usdt_amounts,
        "logs": logs
    })

    
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
