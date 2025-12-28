#17.12.2025
#nicht vyn


#Es wird zu Beginn geprüft, welcher Bot aktiv ist. Der Code wird nur ausgeführt, wenn der Botname identisch ist, wie der Botname aus dem Webhook.-> Es gibt immer nur einen vollständigen Trade.
#Market Order mit Hebel wird gesetzt
#Hebel muss in BINGX selber vorher eingestellt werden
#Preis, welcher im JSON übergeben wurde, wird in Firebase gespeichert
#der gewichtete Durchschnittspreis wird von Firebase berechnet und entsprechend die Sell-Limit Order gesetzt
#Bei Alarm wird angegeben, ab welcher SO ein Alarm via Telegramm gesendet wird
#Verfügbares Guthaben wird ermittelt
#Ordergrösse für BO = (Verfügbares Guthaben - Sicherheit) * bo_factor; SO wird dann automatisch mal Faktor gerechet
#Ordergrösse wird in Variable gespeichert, Firebase wird nur als Backup verwendet
#StopLoss sl muss angegeben werden SL Distanz zum Liquidationspreis
#StopLoss wird jedes mal neu gesetzt, es wird x Prozent vor dem Liquiationspreis gesetzt
#Falls Firebaseverbindung fehlschlägt, wird der Durchschnittspreis aus Bingx -0.2% bzw. +0.2% für die Berechnung der Sell-Limit-Order verwendet.
#Falls Status Fehler werden für den Alarm nicht die Anzahl Kaufpreise gezählt, sondern von der Variablen alarm_counter
#Wenn action=close ist, wird Position geschlossen
#Wenn action nicht gefunden wird, ist es die Baseorder
#vyn Alarm kann benutzt werden (inkl. close-Signal) und dann folgende Alarmnachricht
#Wenn Position auf BINGX schon gelöscht wurde und bei Traidingview noch nicht, wird der nächste increase-Befehl ignoriert
#Nach x Stunden seit BO oder nach x SO wird die Sell-Limit-Order auf x % gesetzt
# bot_nr = Chart
# botname = botname
#

#https://......../webhook
# action wird vom vyn genommen

#{"vyn":{{strategy.order.alert_message}}, RENDER": {"api_key": {
#    "api_key": "",
#    "secret_key": "",
#    "symbol": "BABY-USDT",
#    "botname": "Baby_Bot", # muss einmalig sein
#    "bot_nr": "1", # Nr. pro Chart
#    "position_side": "LONG",
#    "sell_percentage": 2.5,
#    "price": {{close}},
#    "leverage": 1,
#    "FIREBASE_SECRET": "",
#    "alarm": 1,
#    "pyramiding": 8, grösser als 0, wird nicht berücksichtig für Berechnung, es wird für BO gerechnet: (verfügbares Guthaben  - Sicherheit) * bo_factor
#    "sicherheit": 96, Sicherheit muss nicht mal Hebel gerechnet werden, wird im Code gemacht
#    "usdt_factor": 1.4,
#    "bo_factor": 0.001, wie viel Prozent beträgt die BO im Verhältnis zum verfügbaren Guthaben unter Berücksichtung der Gewichtung aller SO
#    "base_time2": "", darf nur beim Testen Inhalt enthalten, 2025-08-22T11:22:37.986015+00:00, simulierter Zeitpunkt der BO
#    "after_h": 48, nach x Stunden seit BO wird Sell-Limit-Order beim nächsten Kauf auf x Prozent gesetzt oder
#    "after_so": 14, nach x SO wird Sell-Limit-Order beim nächsten Kauf auf x Prozent gesetzt
#    "sell_percentage2": 0.5,
#    "sl": 10, Stop Loss bei x Prozent setzen
#    "beenden": "nein" wenn ja, wird keine neue Position nach dem Schliessen der aktuellen Position geöffnet
#    }}



#}}



from flask import Flask, request, jsonify
from datetime import datetime, timezone
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
status_fuer_alle = {} 
alarm_counter = {}
base_order_times = {}
aktueller_Bot = {}

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

def firebase_speichere_base_order_time(botname, timestamp, firebase_secret):
    url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
    data = timestamp.isoformat()  # nur der String
    response = requests.put(url, json=data)
    return f"Base-Order-Zeit für {botname} gespeichert: {timestamp}, Status: {response.status_code}"

def get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    response = requests.get(url)
    data = response.json()
    if data.get("code") == 0 and "data" in data and "price" in data["data"]:
        return float(data["data"]["price"])
    else:
        return None

def close_open_position(api_key, secret_key, symbol, position_side="LONG"):
    """
    Schließt die offene Position sofort per Market Order.
    position_side: "LONG" oder "SHORT"
    """
    logs = []

    # 1. Aktuelle Positionsgröße und Liquidationspreis abfragen
    position_size, _, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs=logs)
    
    if position_size == 0:
        logs.append(f"Keine offene Position für {symbol} ({position_side}) gefunden.")
        return {"code": 1, "msg": "Keine offene Position", "logs": logs}

    # 2. Market Sell/Buy zum Schließen der Position
    side = "SELL" if position_side.upper() == "LONG" else "BUY"

    timestamp = int(time.time() * 1000)
    params_dict = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": round(position_size, 6),
        "positionSide": position_side.upper(),
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
    try:
        result = response.json()
    except Exception as e:
        result = {"code": -1, "msg": f"Fehler beim Parsen der API-Antwort: {e}", "raw_response": response.text}

    logs.append(f"Schließen der Position: {result}")
    return {"result": result, "logs": logs}

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

def firebase_loesche_base_order_time(botname, firebase_secret):
    #    Löscht den Base-Order-Zeitpunkt eines Bots in Firebase.
    try:
        url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
        response = requests.delete(url)
        response.raise_for_status()
        return f"Base-Order-Zeitpunkt für {botname} gelöscht, Status: {response.status_code}"
    except Exception as e:
        return f"Fehler beim Löschen des Base-Order-Zeitpunkts für {botname}: {e}"
    

def sende_telegram_nachricht(botname, text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "Telegram nicht konfiguriert"
    full_text = f"[{botname}] {text}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": full_text}
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

# --- Firebase Funktionen jetzt mit botname statt asset ---
def firebase_speichere_ordergroesse(botname, betrag, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{botname}.json?auth={firebase_secret}"
    data = {"usdt_amount": betrag}
    response = requests.put(url, json=data)
    return f"Ordergröße für {botname} gespeichert: {betrag}, Status: {response.status_code}"

def firebase_set_aktueller_bot(bot_nr, botname, firebase_secret):
    url = f"{FIREBASE_URL}/aktueller_Bot/{bot_nr}.json?auth={firebase_secret}"
    data = {
        "botname": botname
    }
    response = requests.put(url, json=data)
    return f"aktueller_Bot[{bot_nr}] gesetzt: {botname}, Status: {response.status_code}"


def firebase_delete_aktueller_bot(bot_nr, firebase_secret):
    url = f"{FIREBASE_URL}/aktueller_Bot/{bot_nr}.json?auth={firebase_secret}"
    response = requests.delete(url)
    return f"aktueller_Bot[{bot_nr}] gelöscht, Status: {response.status_code}"


def firebase_bot_is_active(bot_nr, botname, firebase_secret):
    url = f"{FIREBASE_URL}/aktueller_Bot.json?auth={firebase_secret}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return False  # Fehler beim Zugriff auf Firebase

        aktueller_bot_firebase = response.json()  # sollte dict oder None sein
        if not aktueller_bot_firebase:
            return False  # kein Eintrag vorhanden

        # Prüfen, ob bot_nr vorhanden ist
        if str(bot_nr) in aktueller_bot_firebase:
            # Prüfen, ob botname übereinstimmt
            if aktueller_bot_firebase[str(bot_nr)] == botname:
                return True
            else:
                return False
        else:
            return False

    except Exception as e:
        print(f"Fehler bei Firebase-Abfrage: {e}")
        return False

def firebase_create_ma(bot_nr, firebase_secret, value):
  
    url = f"{FIREBASE_URL}/MA/{bot_nr}.json?auth={firebase_secret}"
    
    response = requests.put(url, json=value)
    
    if response.status_code == 200:
        return f"MA/{bot_nr} erfolgreich erstellt und auf '{value}' gesetzt."
    else:
        return f"Fehler beim Erstellen von MA/{bot_nr}: {response.status_code} {response.text}"

def firebase_delete_ma(bot_nr, firebase_secret):
   
    url = f"{FIREBASE_URL}/MA/{bot_nr}.json?auth={firebase_secret}"
    
    response = requests.delete(url)
    
    if response.status_code == 200:
        return f"MA/{bot_nr} erfolgreich gelöscht."
    else:
        return f"Fehler beim Löschen von MA/{bot_nr}: {response.status_code} {response.text}"



def firebase_lese_ordergroesse(botname, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{botname}.json?auth={firebase_secret}"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    try:
        data = response.json()
        if isinstance(data, dict) and "usdt_amount" in data:
            return float(data["usdt_amount"])
        elif isinstance(data, (int, float)):
            return float(data)
    except Exception as e:
        print(f"[Fehler] Firebase JSON Parsing: {e}")
    return None

def firebase_loesche_ordergroesse(botname, firebase_secret):
    url = f"{FIREBASE_URL}/ordergroesse/{botname}.json?auth={firebase_secret}"
    response = requests.delete(url)
    return f"Ordergröße für {botname} gelöscht, Status: {response.status_code}"

def firebase_speichere_kaufpreis(botname, price, usdt_amount, firebase_secret):
    import requests


    # Daten, die gespeichert werden sollen
    data = {
        "price": price,
        "usdt_amount": usdt_amount
    }

    # URL zusammenbauen mit Authentifizierung
    url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"

    # HTTP PUT oder POST, je nach Bedarf
    response = requests.post(url, json=data)

    if response.status_code == 200:
        return f"Kaufpreis für {botname} erfolgreich gespeichert."
    else:
        raise Exception(f"Fehler beim Speichern: {response.text}")

def firebase_loesche_kaufpreise(botname, firebase_secret):
    url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"
    response = requests.delete(url)
    if response.status_code == 200:
        return f"Kaufpreise für {botname} gelöscht."
    return f"Fehler beim Löschen der Kaufpreise für {botname}: Status {response.status_code}"

def firebase_lese_kaufpreise(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"
        r = requests.get(url)
        print(f"Firebase Antwort Status: {r.status_code}")
        print(f"Firebase Antwort Inhalt: {r.text}")
        daten = r.json()
        if not daten:
            print("Keine Daten unter kaufpreise/{botname} gefunden")
            return []
        # Werte in Liste umwandeln
        return [{"price": float(v.get("price", 0)), "usdt_amount": float(v.get("usdt_amount", 0))} for v in daten.values()]
    except Exception as e:
        print(f"Fehler beim Lesen der Kaufpreise: {e}")
        return []

def berechne_durchschnittspreis(käufe):
    if not käufe:
        return None

    gesamtwert = 0
    gesamtmenge = 0

    for kauf in käufe:
        preis = float(kauf.get("price", 0))
        menge = float(kauf.get("usdt_amount", 0))
        gesamtwert += preis * menge
        gesamtmenge += menge

    if gesamtmenge == 0:
        return None

    return round(gesamtwert / gesamtmenge, 6)

def firebase_lese_base_order_time(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data:
            return data.get("base_order_time")  # ISO-Zeitstring
        return None
    except Exception as e:
        print(f"Fehler beim Lesen des Base-Order-Zeitpunkts aus Firebase für {botname}: {e}")
        return None
    
def set_leverage(api_key, secret_key, symbol, leverage, position_side="LONG"):
    endpoint = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": symbol,
        "leverage": int(leverage),
        "side": position_side.upper()  # LONG oder SHORT
    }

    return send_signed_request("POST", endpoint, api_key, secret_key, params)


### SHORT Funktionen
# === Hilfsfunktionen ===
# === SHORT Hilfsfunktionen ===
def SHORT_generate_signature(secret_key: str, params: str) -> str:
    return hmac.new(secret_key.encode('utf-8'), params.encode('utf-8'), hashlib.sha256).hexdigest()

def SHORT_send_signed_request(http_method, endpoint, api_key, secret_key, params=None):
    if params is None:
        params = {}
    timestamp = int(time.time() * 1000)
    params['timestamp'] = timestamp
    # create canonical query string by sorting keys
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
    try:
        return response.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw": response.text}

def SHORT_get_futures_balance(api_key: str, secret_key: str):
    timestamp = int(time.time() * 1000)
    params = f"timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{BALANCE_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    resp = requests.get(url, headers=headers)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige Balance-Antwort", "raw": resp.text}

def SHORT_get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    resp = requests.get(url)
    try:
        data = resp.json()
        if data.get("code") == 0 and "data" in data and "price" in data["data"]:
            return float(data["data"]["price"])
    except Exception:
        pass
    return None

# === Firebase helper functions (kopiert / angepasst) ===
def SHORT_firebase_speichere_base_order_time(botname, timestamp, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
        data = {"base_order_time": timestamp.isoformat()}
        response = requests.put(url, json=data)
        return f"Base-Order-Zeit für {botname} gespeichert: {timestamp}, Status: {response.status_code}"
    except Exception as e:
        return f"Fehler beim Speichern der Base-Order-Zeit: {e}"

def SHORT_firebase_loesche_base_order_time(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
        r = requests.delete(url)
        return f"Base-Order-Zeitpunkt für {botname} gelöscht, Status: {r.status_code}"
    except Exception as e:
        return f"Fehler beim Löschen base_order_time: {e}"

def SHORT_firebase_speichere_ordergroesse(botname, betrag, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/ordergroesse/{botname}.json?auth={firebase_secret}"
        data = {"usdt_amount": betrag}
        r = requests.put(url, json=data)
        return f"Ordergröße für {botname} gespeichert: {betrag}, Status: {r.status_code}"
    except Exception as e:
        return f"Fehler beim Speichern ordergroesse: {e}"

def SHORT_firebase_lese_ordergroesse(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/ordergroesse/{botname}.json?auth={firebase_secret}"
        r = requests.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, dict) and "usdt_amount" in data:
            return float(data["usdt_amount"])
        elif isinstance(data, (int, float)):
            return float(data)
    except Exception:
        pass
    return None

def SHORT_firebase_speichere_kaufpreis(botname, price, usdt_amount, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"
        data = {"price": price, "usdt_amount": usdt_amount}
        r = requests.post(url, json=data)
        if r.status_code == 200:
            return f"Kaufpreis für {botname} erfolgreich gespeichert."
        else:
            return f"Fehler beim Speichern Kaufpreis: {r.status_code} {r.text}"
    except Exception as e:
        return f"Fehler beim Speichern Kaufpreis: {e}"

def SHORT_firebase_loesche_kaufpreise(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"
        r = requests.delete(url)
        if r.status_code == 200:
            return f"Kaufpreise für {botname} gelöscht."
        return f"Fehler beim Löschen Kaufpreise: Status {r.status_code}"
    except Exception as e:
        return f"Fehler beim Löschen Kaufpreise: {e}"

def SHORT_firebase_lese_kaufpreise(botname, firebase_secret):
    try:
        url = f"{FIREBASE_URL}/kaufpreise/{botname}.json?auth={firebase_secret}"
        r = requests.get(url)
        if r.status_code != 200:
            return []
        daten = r.json()
        if not daten:
            return []
        return [{"price": float(v.get("price", 0)), "usdt_amount": float(v.get("usdt_amount", 0))} for v in daten.values()]
    except Exception:
        return []

def SHORT_berechne_durchschnittspreis(käufe):
    if not käufe:
        return None
    gesamtwert = 0.0
    gesamtmenge = 0.0
    for kauf in käufe:
        preis = float(kauf.get("price", 0))
        menge = float(kauf.get("usdt_amount", 0))
        gesamtwert += preis * menge
        gesamtmenge += menge
    if gesamtmenge == 0:
        return None
    return round(gesamtwert / gesamtmenge, 6)

# === Telegram ===
def SHORT_sende_telegram_nachricht(botname, text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "Telegram nicht konfiguriert"
    full_text = f"[{botname}] {text}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": full_text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return f"Telegram Antwort: {r.status_code}"
    except Exception as e:
        return f"Telegram Fehler: {e}"

# === Order-Funktionen (SHORT-optimiert) ===
def SHORT_set_leverage(api_key, secret_key, symbol, leverage, position_side="LONG"):
    endpoint = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": symbol,
        "leverage": int(leverage),
        "side": position_side.upper()  # LONG oder SHORT
    }

    return send_signed_request("POST", endpoint, api_key, secret_key, params)

def SHORT_place_market_order(api_key, secret_key, symbol, usdt_amount, position_side="SHORT"):
    """
    Platzieren einer Market-Order zum ÖFFNEN einer SHORT-Position.
    Für SHORT: side="SELL"
    """
    price = get_current_price(symbol)
    if price is None:
        return {"code": 99999, "msg": "Failed to get current price"}
    quantity = round(float(usdt_amount) / price, 6)
    timestamp = int(time.time() * 1000)
    params_dict = {
        "symbol": symbol,
        "side": "SELL",  # SHORT eröffnen
        "type": "MARKET",
        "quantity": quantity,
        "positionSide": position_side.upper(),
        "timestamp": timestamp
    }
    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    params_dict["signature"] = generate_signature(secret_key, query_string)
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {"X-BX-APIKEY": api_key, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=params_dict)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw": resp.text}

def SHORT_place_market_order_close(api_key, secret_key, symbol, position_amt, position_side="SHORT"):
    """
    Schließt eine Position via MARKET (für SHORT -> BUY)
    position_amt: Menge (positiv)
    """
    side = "BUY"  # Short schließen = buy
    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": round(abs(position_amt), 6),
        "positionSide": position_side.upper(),
        "timestamp": timestamp
    }
    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["signature"] = generate_signature(secret_key, query_string)
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {"X-BX-APIKEY": api_key, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=params)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw": resp.text}

def SHORT_place_limit_buy_order(api_key, secret_key, symbol, quantity, limit_price, position_side="SHORT"):
    """
    TP für SHORT: BUY Limit (unter Entry).
    """
    timestamp = int(time.time() * 1000)
    params_dict = {
        "symbol": symbol,
        "side": "BUY",        # um Short zu schließen -> BUY
        "type": "LIMIT",
        "quantity": round(quantity, 6),
        "price": round(limit_price, 6),
        "timeInForce": "GTC",
        "positionSide": position_side.upper(),
        "timestamp": timestamp
    }
    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    params_dict["signature"] = generate_signature(secret_key, query_string)
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {"X-BX-APIKEY": api_key, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=params_dict)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw": resp.text}

def SHORT_place_stoploss_buy_order(api_key, secret_key, symbol, quantity, stop_price, position_side="SHORT"):
    """
    SL für SHORT: BUY STOP_MARKET (über Entry) zum Schließen
    """
    timestamp = int(time.time() * 1000)
    params_dict = {
        "symbol": symbol,
        "side": "BUY",  # Short schließen = buy
        "type": "STOP_MARKET",
        "quantity": round(quantity, 6),
        "stopPrice": round(stop_price, 6),
        "positionSide": position_side.upper(),
        "timestamp": timestamp
    }
    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    params_dict["signature"] = generate_signature(secret_key, query_string)
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {"X-BX-APIKEY": api_key, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=params_dict)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige API-Antwort", "raw": resp.text}

def SHORT_get_open_orders(api_key, secret_key, symbol):
    timestamp = int(time.time() * 1000)
    params = f"symbol={symbol}&timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{OPEN_ORDERS_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    r = requests.get(url, headers=headers)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige Antwort Open Orders", "raw": r.text}

def SHORT_cancel_order(api_key, secret_key, symbol, order_id):
    timestamp = int(time.time() * 1000)
    params = f"symbol={symbol}&orderId={order_id}&timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{ORDER_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    r = requests.delete(url, headers=headers)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": "Ungültige Antwort Cancel", "raw": r.text}

# === Positionsabfrage ===
def SHORT_get_current_position(api_key, secret_key, symbol, position_side, logs=None):
    endpoint = "/openApi/swap/v2/user/positions"
    params = {"symbol": symbol}
    response = send_signed_request("GET", endpoint, api_key, secret_key, params)
    positions = response.get("data", []) if isinstance(response.get("data", []), list) else []
    raw_positions = positions
    position_size = 0.0
    liquidation_price = None
    if logs is not None:
        logs.append(f"Positions Rohdaten: {raw_positions}")
    if response.get("code") == 0:
        for pos in positions:
            if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                try:
                    position_size = float(pos.get("size", 0)) or float(pos.get("positionAmt", 0))
                    liquidation_price = float(pos.get("liquidationPrice", 0)) if pos.get("liquidationPrice") else None
                    if logs is not None:
                        logs.append(f"Gefundene Position: size={position_size}, liqPrice={liquidation_price}")
                except (ValueError, TypeError) as e:
                    if logs is not None:
                        logs.append(f"Fehler beim Parsen der Position: {e}")
                    position_size = 0.0
                break
    else:
        if logs is not None:
            logs.append(f"API Positions Fehlercode: {response.get('code')}")
    return position_size, raw_positions, liquidation_price

# === Schließen aller Positionen (nur SHORT relevant) ===
def SHORT_close_all_positions(api_key, secret_key):
    logs = []
    closed_positions = []
    positions_resp = get_open_positions_for_all_symbols(api_key, secret_key)
    if positions_resp.get("error"):
        sende_telegram_nachricht("BingX Bot", f"Fehler beim Abrufen der Positionen: {positions_resp.get('msg')}")
        logs.append("Fehler beim Abrufen der Positionen")
        return {"error": True, "msg": "Konnte offene Positionen nicht abrufen", "logs": logs}
    positions = positions_resp.get("data", [])
    if not positions:
        logs.append("Keine offenen Positionen")
        return {"error": False, "msg": "Keine offenen Positionen", "closed": [], "logs": logs}
    for pos in positions:
        symbol = pos.get("symbol")
        position_side = pos.get("positionSide", "").upper()
        qty = float(pos.get("positionAmt", 0) or pos.get("size", 0) or 0)
        if position_side != "SHORT":
            continue  # nur SHORTs schließen
        if qty == 0:
            continue
        try:
            resp = place_market_order_close(api_key, secret_key, symbol, qty, position_side)
            logs.append(f"Closed {symbol} {position_side} ({qty}) → {resp}")
            closed_positions.append({
                "symbol": symbol,
                "side": position_side,
                "quantity": qty,
                "response": resp
            })
        except Exception as e:
            message = f"⚠️ Fehler beim Schließen von {symbol} {position_side}: {e}"
            sende_telegram_nachricht("BingX Bot", message)
            logs.append(message)
    return {"error": False, "closed": closed_positions, "logs": logs}

def SHORT_get_open_positions_for_all_symbols(api_key, secret_key):
    endpoint = "/openApi/swap/v2/user/positions"
    response = send_signed_request("GET", endpoint, api_key, secret_key, {})
    if response.get("code") != 0:
        return {"error": True, "msg": response.get("msg", "Fehler beim Abrufen der Positionen"), "data": []}
    positions = response.get("data", []) or []
    return {"error": False, "data": positions}

# === Close helper für webhook 'close' action ===
def SHORT_close_open_position(api_key, secret_key, symbol, position_side="SHORT"):
    logs = []
    position_size, _, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs=logs)
    if position_size == 0:
        logs.append(f"Keine offene Position für {symbol} ({position_side}) gefunden.")
        return {"code": 1, "msg": "Keine offene Position", "logs": logs}
    side = "BUY"  # Short schließen
    timestamp = int(time.time() * 1000)
    params_dict = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": round(position_size, 6),
        "positionSide": position_side.upper(),
        "timestamp": timestamp
    }
    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    params_dict["signature"] = generate_signature(secret_key, query_string)
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {"X-BX-APIKEY": api_key, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=params_dict)
    try:
        result = resp.json()
    except Exception as e:
        result = {"code": -1, "msg": f"Fehler beim Parsen der API-Antwort: {e}", "raw_response": resp.text}
    logs.append(f"Schließen der Position: {result}")
    return {"result": result, "logs": logs}



@app.route('/webhook', methods=['POST'])
def webhook():
    global saved_usdt_amounts
    global status_fuer_alle
    global alarm_counter
    global base_order_times
    global aktueller_Bot
    global ma_Inhalt

    ma_Inhalt = None


    data = request.json
    logs = []

    position_side = data.get("RENDER", {}).get("position_side") or data.get("RENDER", {}).get("positionSide") or "LONG"    #data.get("position_side") or data.get("positionSide") or "LONG"

    if position_side == "LONG":  

        data = request.json or {}
        logs = []
    

     
    
        botname = data.get("RENDER", {}).get("botname")    #data.get("botname")
        if not botname:
            return jsonify({"error": True, "msg": "botname ist erforderlich"}), 400
    
        symbol = data.get("RENDER", {}).get("symbol", "")  #data.get("symbol", "BTC-USDT")
        base_asset = symbol.split("-")[0]  # Nur für menschliche Logs
    
        # Hole den gespeicherten Wert für den Bot, falls vorhanden
        saved_usdt_amount = saved_usdt_amounts.get(botname)
    
        # Eingabewerte
        pyramiding = float(data.get("RENDER", {}).get("pyramiding", 1))  #float(data.get("pyramiding", 1))
        leverageB = float(data.get("RENDER", {}).get("leverage", 1))     #float(data.get("leverage", 1))
        sicherheit = float(data.get("RENDER", {}).get("sicherheit", 0) * leverageB)    #float(data.get("sicherheit", 0) * leverageB)
        sell_percentage = data.get("RENDER", {}).get("sell_percentage")    #data.get("sell_percentage")
        api_key = data.get("RENDER", {}).get("api_key")    #data.get("api_key")
        secret_key = data.get("RENDER", {}).get("secret_key")   #data.get("secret_key")
        position_side = data.get("RENDER", {}).get("position_side") or data.get("RENDER", {}).get("positionSide") or "LONG"    #data.get("position_side") or data.get("positionSide") or "LONG"
        firebase_secret = data.get("RENDER", {}).get("FIREBASE_SECRET")    #data.get("FIREBASE_SECRET")
        price_from_webhook = data.get("RENDER", {}).get("price")    #data.get("price")
        usdt_factor = float(data.get("RENDER", {}).get("usdt_factor", 1))    #float(data.get("usdt_factor", 1))
        bo_factor = float(data.get("RENDER", {}).get("bo_factor", 0.0001))    #float(data.get("bo_factor", 0.0001))
        action = data.get("vyn", {}).get("action", "").lower()    #KOMMT VON VYN     data.get("action", "").lower()
        base_time2 = data.get("RENDER", {}).get("base_time2")
        after_h = data.get("RENDER", {}).get("after_h")
        after_so = data.get("RENDER", {}).get("after_so")
        sell_percentage2 = data.get("RENDER", {}).get("sell_percentage2")
        beenden = data.get("RENDER", {}).get("beenden", "nein")
        sl = data.get("RENDER", {}).get("sl")
        bot_nr = data.get("RENDER", {}).get("bot_nr")
        ma = data.get("RENDER", {}).get("ma")
     

   
        print(f"DEBUG: action='{action}', botname='{botname}'")
        if action == "close" and botname:
            # Position schließen
            ergebnis = close_open_position(api_key, secret_key, symbol, position_side)

         
            if ma is not None:
                # Key existiert, hier kannst du den Wert prüfen
                if ma == "SL1":
                    ma_Inhalt = "SL1"
                    result = firebase_create_ma(bot_nr, firebase_secret, "SL1")
                    logs.append(f"Firebase-Result: {result}")
           
            else:
                # Key fehlt im Webhook
                print("ma1 ist im Webhook nicht vorhanden")
            
            # Logs ausgeben
            print(ergebnis.get("logs", []))
            print(ergebnis.get("result", None))
            
            # Nur die Daten für diesen Bot zurücksetzen
            saved_usdt_amounts.pop(botname, None)
            status_fuer_alle.pop(botname, None)
            alarm_counter.pop(botname, None)
            base_order_times.pop(botname, None)
        
            if bot_nr in aktueller_Bot and aktueller_Bot[bot_nr] == botname:
                del aktueller_Bot[bot_nr]  # Eintrag löschen
                print(f"Bot {botname} mit Nummer {bot_nr} wurde aus der globalen Variable gelöscht")
            
            
            # Kaufpreise löschen (Firebase oder lokal)
            if firebase_secret:
                try:
                    logs = []
                    logs.append(firebase_loesche_kaufpreise(botname, firebase_secret))
                    logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                    logs.append(firebase_loesche_base_order_time(botname, firebase_secret))
                    logs.append(firebase_delete_aktueller_bot(bot_nr, firebase_secret))
                    
                    print("\n".join(logs))
                except Exception as e:
                    print(f"Fehler beim Löschen von Kaufpreisen/Ordergrößen für {botname}: {e}")
    
                 # **Hier ein Response zurückgeben**
                return jsonify({
                    "status": "position_closed",
                    "botname": botname,
                    "logs": ergebnis.get("logs", []),
                    "result": ergebnis.get("result", None)
                })  # <-- alle Klammern geschlossen
        else:

            if ma_Inhalt == "SL1":
                leverageB = 4
            else:
                # Firebase abrufen
                url = f"{FIREBASE_URL}/MA/{bot_nr}.json?auth={firebase_secret}"
                response = requests.get(url)
                    
                if response.status_code == 200:
                    ma_Inhalt = response.json()  # Wert aus Firebase
                else:
                    ma_Inhalt = None
                
                # Prüfen, ob ma_Inhalt jetzt einen Wert hat
                if not ma_Inhalt:
                    # Wenn immer noch leer, Abfrage beenden
                    logs.append(f"ma_Inhalt ist leer, If-Abfrage wird beendet")
                else:
                    # Hier kommt deine ursprüngliche If-Logik
                    if ma_Inhalt == "SL1":
                        leverageB = 4
       

            
            
            

             # === Hebel NUR vor echter Base Order setzen ===
            position_size, _, _ = get_current_position(
                api_key,
                secret_key,
                symbol,
                position_side,
                logs
            )
            
            if position_size == 0 and action != "increase":
                try:
                    logs.append(
                        f"Setze Hebel VOR Base Order auf {leverageB}x "
                        f"(position_size={position_size})"
                    )
            
                    leverage_response = set_leverage(
                        api_key,
                        secret_key,
                        symbol,
                        leverageB,
                        position_side
                    )
            
                    logs.append(f"Hebel-Response: {leverage_response}")
            
                    time.sleep(0.2)  # BingX Sync
            
                    if leverage_response.get("code") != 0:
                        raise Exception(leverage_response)
            
                except Exception as e:
                    logs.append(f"❌ Hebel konnte nicht gesetzt werden: {e}")
                    return jsonify({
                        "error": True,
                        "msg": "Hebel konnte nicht gesetzt werden",
                        "logs": logs
                    })
            else:
                logs.append(
                    f"Hebel NICHT gesetzt "
                    f"(position_size={position_size}, action={action})"
                )

            
    
            available_usdt = 0.0
        
            # 0. USDT-Guthaben vor Order abrufen
            try:
                balance_response = get_futures_balance(api_key, secret_key)
                logs.append(f"Balance Response: {balance_response}")
                if balance_response.get("code") == 0:
                    
                    balance_data_temp = float(balance_response.get("data", {}).get("balance", {}).get("availableMargin", 0))
                    available_usdt = balance_data_temp * leverageB
                    logs.append(f"Freies USDT Guthaben: {available_usdt}")
                else:
                    logs.append("Fehler beim Abrufen der Balance.")
            except Exception as e:
                logs.append(f"Fehler bei Balance-Abfrage: {e}")
                available_usdt = None
        


            













            
            # 2. Offene Orders abrufen
            open_orders = {}
            try:
                open_orders = get_open_orders(api_key, secret_key, symbol)
                logs.append(f"Open Orders: {open_orders}")
            except Exception as e:
                logs.append(f"Fehler bei Orderprüfung: {e}")
                sende_telegram_nachricht(botname, f"Fehler bei Orderprüfung {botname}: {e}")
        
            # 3. Ordergröße ermitteln (Compounding-Logik)
            usdt_amount = 0
    
            
            
            open_sell_orders_exist = False
    
            
            if action == "increase":  # Nachkauforder
                position_size, _, _ = get_current_position(api_key, secret_key, symbol, position_side, logs)
                logs.append(f"position_size_A={position_size}, botname={botname}, open_sell_orders_exist={open_sell_orders_exist}")
                if position_size is None:
                    logs.append("❌ Keine Verbindung zur BingX API – Order wird NICHT gesetzt")
                    sende_telegram_nachricht(botname, f"❌❌❌ Keine Verbindung zu BingX für Bot {botname}")
                    raise Exception("Keine Verbindung zu BingX – Bot gestoppt")
    
                logs.append(f"position_size_B={position_size}, botname={botname}, open_sell_orders_exist={open_sell_orders_exist}")
                
                if position_size > 0:
                    open_sell_orders_exist = True
                else: # erste Order, wird ausgeführt wenn auf Bingx die Position bereits geschlossen wurde, aber in Traidingview noch nicht -> increase-Befehl startet keine neue Position
                    if beenden.lower() == "ja" or position_size == 0:
                        logs.append(f"⚠️ Bot {botname}: Beenden=ja → KEINE neue Base Order wird eröffnet")
                        # Nur Status zurückgeben, keine Base Order setzen
                        return jsonify({
                            "status": "no_base_order_opened",
                            "botname": botname,
                            "reason": "beenden=ja",
                            "logs": logs
                        })
                    else:
                        open_sell_orders_exist = False
                        saved_usdt_amounts.pop(botname, None)
                        status_fuer_alle.pop(botname, None)
                        alarm_counter.pop(botname, None)
                        base_order_times.pop(botname, None)
        
                        
        
                        status_fuer_alle[botname] = "OK"
                        alarm_counter[botname] = -1
                        
                        try:
                            
                            logs.append(firebase_loesche_kaufpreise(botname, firebase_secret))
                            logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                            logs.append(firebase_loesche_base_order_time(botname, firebase_secret))
                            print("\n".join(logs))
                        except Exception as e:
                            print(f"Fehler beim Löschen von Kaufpreisen/Ordergrößen für {botname}: {e}")
        
            else:  # erste Order
                open_sell_orders_exist = False
            
            logs.append(f"action={action}, botname={botname}, open_sell_orders_exist={open_sell_orders_exist}")
            
            
            # Wenn keine offene Sell-Limit-Order existiert → erste Order
            if not open_sell_orders_exist:
                if beenden.lower() == "ja":
                    logs.append(f"⚠️ Bot {botname}: Beenden=ja → KEINE neue Base Order wird eröffnet")
                    # Nur Status zurückgeben, keine Base Order setzen
                    return jsonify({
                        "status": "no_base_order_opened",
                        "botname": botname,
                        "reason": "beenden=ja",
                        "logs": logs
                    })
                else:
            
                    status_fuer_alle[botname] = "OK"
                    alarm_counter[botname] = -1
                   
                        
                    #logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                
                    if botname in saved_usdt_amounts:
                        del saved_usdt_amounts[botname]
                        logs.append(f"Ordergröße aus Cache für {botname} gelöscht (erste Order)")


                    balance_response = get_futures_balance(api_key, secret_key)
                    
                    balance_data = balance_response.get("data", {}).get("balance", {})
                    
                    available_margin = float(balance_data.get("availableMargin", 0))
                    position_margin = float(balance_data.get("usedMargin", 0))
                    
                    account_size = available_margin + position_margin

                    logs.append(f"RAW balance response: {balance_response}")
                    logs.append(f"Accountgrösse: {account_size}")
                    logs.append(f"Verfügbare Marge: {available_margin}")
                    logs.append(f"Position Marge: {position_margin}")
                                        
                    if available_usdt is not None and pyramiding > 0:
                        # Erste Order bleibt unverändert
                        #usdt_amount = max(((available_usdt - sicherheit) * bo_factor), 0)    #max(((available_usdt - sicherheit) / pyramiding), 0)
                        usdt_amount = max((account_size - sicherheit) * bo_factor, 0)
                        saved_usdt_amounts[botname] = usdt_amount
                        logs.append(f"Erste Ordergröße berechnet: {usdt_amount}")
                    
            
            # Wenn globale Variable vorhanden → nächste Orders
            else:
                saved_usdt_amount = saved_usdt_amounts.get(botname, 0)
                if saved_usdt_amount and saved_usdt_amount > 0:
                    usdt_amount = saved_usdt_amount * usdt_factor
                    saved_usdt_amounts[botname] = usdt_amount
                    logs.append(f"Nächste Ordergröße mit Faktor {usdt_factor} berechnet: {usdt_amount}")
                   
                else:
                    # Fallback aus Firebase
                    try:
                        usdt_amount = firebase_lese_ordergroesse(botname, firebase_secret) or 0
                        if usdt_amount > 0:
                            saved_usdt_amounts[botname] = usdt_amount * usdt_factor
                            usdt_amount = saved_usdt_amounts[botname]
                            logs.append(f"Ordergröße aus Firebase gelesen und mit Faktor {usdt_factor} multipliziert: {usdt_amount}")
                            sende_telegram_nachricht(botname, f"ℹ️ Ordergröße aus Firebase verwendet bei Bot: {botname}")
                        else:
                            logs.append(f"❌ Keine Ordergröße gefunden für {botname}")
                    except Exception as e:
                        status_fuer_alle[botname] = "Fehler"
                        logs.append(f"Fehler beim Lesen der Ordergröße aus Firebase: {e}")
                        sende_telegram_nachricht(botname, f"❌ Fehler beim Lesen der Ordergröße aus Firebase {botname}: {e}")
        
            # 4. Market-Order ausführen
            try:
                logs.append(f"Plaziere Market-Order mit {usdt_amount} USDT für {symbol} ({position_side})...")
                order_response = place_market_order(api_key, secret_key, symbol, float(usdt_amount), position_side)
                alarm_counter[botname] += 1
                logs.append(firebase_speichere_ordergroesse(botname, usdt_amount, firebase_secret))
                time.sleep(2)
                logs.append(f"Market-Order Antwort: {order_response}")
    
                # API-Antwort prüfen
                if not order_response or order_response.get("code") != 0:
                    status_fuer_alle[botname] = "Fehler"
                    logs.append(order_response)
                    sende_telegram_nachricht(botname, f"❌❌❌ Marketorder konnte nicht gesetzt werden für Bot: {botname}")
            except Exception as e:
                logs.append(f"Fehler bei Marketorder: {e}")
                status_fuer_alle[botname] = "Fehler"
                sende_telegram_nachricht(botname, f"❌❌❌ Marketorder konnte nicht gesetzt werden für Bot: {botname}")

            # ma zurücksetzen in Firebase und in globale Variable
            firebase_delete_ma(bot_nr, firebase_secret)
            ma_Inhalt = ""
                
            # 5. Positionsgröße und Liquidationspreis ermitteln
            try:
                sell_quantity, positions_raw, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs)
        
                if sell_quantity == 0:
                    executed_qty_str = order_response.get("data", {}).get("order", {}).get("executedQty")
                    if executed_qty_str:
                        sell_quantity = float(executed_qty_str)
                        logs.append(f"[Market Order] Ausgeführte Menge aus order_response genutzt: {sell_quantity}")
        
                if liquidation_price:
                    stop_loss_price = round(liquidation_price * (1 + sl / 100), 6)
                    logs.append(f"Stop-Loss-Preis basierend auf Liquidationspreis {liquidation_price}: {stop_loss_price}")
                else:
                    stop_loss_price = None
                    logs.append("Liquidationspreis nicht verfügbar. Kein Stop-Loss-Berechnung möglich.")
                    sende_telegram_nachricht(botname, f"❌ Liquidationspreis nicht verfügbar für Bot: {botname}")
            except Exception as e:
                sell_quantity = 0
                stop_loss_price = None
                logs.append(f"Fehler bei Positions- oder Liquidationspreis-Abfrage: {e}")
                sende_telegram_nachricht(botname, f"❌ Fehler bei Positions- oder Liquidationspreis-Abfrage {botname}: {e}")
        
            # 6. Kaufpreise ggf. löschen
            if firebase_secret and not open_sell_orders_exist:
                try:
                    logs.append(firebase_loesche_kaufpreise(botname, firebase_secret))
                except Exception as e:
                    logs.append(f"Fehler beim Löschen der Kaufpreise: {e}")
                    status_fuer_alle[botname] = "Fehler"
        
            # 7. Kaufpreis speichern 
            if firebase_secret and price_from_webhook:
                try:
                    logs.append(firebase_speichere_kaufpreis(botname, float(price_from_webhook), float(usdt_amount), firebase_secret))
                except Exception as e:
                    logs.append(f"Fehler beim Speichern des Kaufpreises: {e}")
                    status_fuer_alle[botname] = "Fehler"
        
            # 8. Durchschnittspreis bestimmen
            durchschnittspreis = None
            kaufpreise = []
        
            if status_fuer_alle.get(botname) == "Fehler":
                logs.append(f"Status für {botname} ist Fehler, Fallback auf BingX.")
                try:
                    for pos in positions_raw:
                        if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                            avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                            if avg_price > 0:
                                durchschnittspreis = round(avg_price * (1 - 0.002), 6)
                                logs.append(f"[Fallback] avgPrice von BingX verwendet: {durchschnittspreis}")
                                sende_telegram_nachricht(botname, f"ℹ️ Durchschnittspreis von BINGX verwendet für Bot: {botname}")
                            break
                except Exception as e:
                    logs.append(f"[Fehler] avgPrice-Fallback fehlgeschlagen: {e}")
            else:
                try:
                    if firebase_secret:
                        kaufpreise = firebase_lese_kaufpreise(botname, firebase_secret)
                        logs.append(f"[Firebase] Gelesene Kaufpreise Rohdaten: {kaufpreise}")
                        durchschnittspreis = berechne_durchschnittspreis(kaufpreise or [])
                        if durchschnittspreis:
                            logs.append(f"[Firebase] Durchschnittspreis berechnet: {durchschnittspreis}")
                        else:
                            logs.append("[Firebase] Keine gültigen Kaufpreise gefunden.")
                            status_fuer_alle[botname] = "Fehler"
                except Exception as e:
                    status_fuer_alle[botname] = "Fehler"
                    logs.append(f"[Fehler] Firebase-Zugriff fehlgeschlagen: {e}")
        
                if not durchschnittspreis or durchschnittspreis == 0:
                    try:
                        for pos in positions_raw:
                            if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                                avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                                if avg_price > 0:
                                    durchschnittspreis = round(avg_price * (1 - 0.002), 6)
                                    logs.append(f"Fallback avgPrice verwendet für Bot: {botname}")
                                    sende_telegram_nachricht(botname, f"ℹ️ Durchschnittspreis von BINGX verwendet für Bot: {botname}")
                                    status_fuer_alle[botname] = "Fehler"
                                else:
                                    logs.append("[Fallback] Kein gültiger avgPrice vorhanden.")
                                break
                    except Exception as e:
                        logs.append(f"[Fehler] avgPrice-Fallback fehlgeschlagen: {e}")
                        sende_telegram_nachricht(botname, f"❌ Fallback von BINGX fehlgeschlagen für Bot: {botname}")
        
            # 9. Alte Sell-Limit-Orders löschen
            try:
                if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                    for order in open_orders.get("data", {}).get("orders", []):
                        if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                            cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                            logs.append(f"Gelöschte Order {order.get('orderId')}: {cancel_response}")
            except Exception as e:
                logs.append(f"Fehler beim Löschen der Sell-Limit-Orders: {e}")
                sende_telegram_nachricht(botname, f"Fehler beim Löschen der Sell-Limit-Order {botname}: {e}")
    
    
            if not open_sell_orders_exist: #Zeitpunkt der BO speichern und Botname ergänzen
                # Firebase löschen
                try:
                    logs.append(firebase_delete_aktueller_bot(bot_nr, firebase_secret))


                except Exception as e:
                    logs.append(f"Fehler beim Löschen von aktueller_Bot in Firebase: {e}")
                    sende_telegram_nachricht(botname, f"Fehler beim Löschen von aktueller_Bot in Firebase {botname}: {e}")
                    
                aktueller_Bot[bot_nr] = botname
                
                logs.append(
                    f"Aktuelle Baseorder ausgeführt → "
                    f"bot_nr={bot_nr}, botname={botname}"
                )

                
                
                # 🔥 In Firebase unter aktueller_Bot speichern
                try:
                    logs.append(firebase_set_aktueller_bot(bot_nr, botname, firebase_secret))
                except Exception as e:
                    logs.append(f"Fehler beim Schreiben von aktueller_Bot in Firebase: {e}")
                    sende_telegram_nachricht(botname, f"Fehler beim Schreiben von aktueller_Bot in Firebase {botname}: {e}")


                
                 # 1. Zeitpunkt merken
                now = datetime.now(timezone.utc)
                base_order_times[botname] = now
                base_time = now
                logs.append(f"Base-Order Zeitpunkt gespeichert (global): {now}")
                #print(firebase_speichere_base_order_time("TEST_BOT", now, firebase_secret))
                print(logs[-1])
    
                
                # 2. Zeitpunkt in Firebase speichern
                try:
                    firebase_loesche_base_order_time
                    firebase_speichere_base_order_time(botname, now, firebase_secret)  # du musst diese Funktion anlegen
                    logs.append(f"Base-Order Zeitpunkt in Firebase gespeichert: {now}")
                    print(logs[-1])
                except Exception as e:
                    logs.append(f"Fehler beim Speichern des Base-Order-Zeitpunkts in Firebase: {e}")
                    print(logs[-1])
            else:
    
                # 1. Zeitpunkt aus globaler Variable prüfen #Bei Test wird aus JSON-Webhook genommen
                if not base_time2:  # leer oder None
                    base_time = base_order_times.get(botname)
                else:
                    try:
                        # falls base_time2 ein ISO-String ist, in datetime konvertieren
                        base_time = datetime.fromisoformat(base_time2)
                
                        # falls ohne Zeitzone -> auf UTC setzen
                        if base_time.tzinfo is None:
                            base_time = base_time.replace(tzinfo=timezone.utc)
                
                    except Exception as e:
                        logs.append(f"Fehler beim Umwandeln von base_time2: {e}")
                        base_time = None
                
                
                # 2. Wenn nichts in globaler Variable, aus Firebase laden
                if base_time is None:
                    try:
                        base_time_str = firebase_lese_base_order_time(botname, firebase_secret)  # ISO-String zurückgeben
                        if base_time_str:
                            base_time = datetime.fromisoformat(base_time_str)
                
                            # falls ohne Zeitzone gespeichert -> auf UTC setzen
                            if base_time.tzinfo is None:
                                base_time = base_time.replace(tzinfo=timezone.utc)
                
                            base_order_times[botname] = base_time  # wieder in global speichern
                            logs.append(f"Base-Order Zeitpunkt aus Firebase geladen: {base_time}")
                            print(logs[-1])
                        else:
                            logs.append("Keine Base-Order-Zeit in Firebase gefunden.")
                            print(logs[-1])
                            base_time = None
                    except Exception as e:
                        logs.append(f"Fehler beim Laden des Base-Order-Zeitpunkts aus Firebase: {e}")
                        print(logs[-1])
                        base_time = None
        
                # Alarm-Infos
                alarm_trigger = int(data.get("RENDER", {}).get("alarm", 0))
                if status_fuer_alle.get(botname) == "Fehler":
                    anzahl_nachkäufe = alarm_counter.get(botname, -1)
                else:
                    anzahl_käufe = len(kaufpreise or [])
                    anzahl_nachkäufe = max(anzahl_käufe - 1, 0)     
                    
                logs.append(f"Alarm2 {alarm_trigger - 4}")
                logs.append(f"Alarm3 {anzahl_nachkäufe}") 


                if anzahl_nachkäufe >= alarm_trigger:
                    try:
                        nachricht = f"{botname}:\nNachkäufe: {anzahl_nachkäufe}"
                        telegram_result = sende_telegram_nachricht(botname, nachricht)
                        logs.append(f"Telegram gesendet: {telegram_result}")
                    except Exception as e:
                        logs.append(f"Fehler beim Senden der Telegram-Nachricht: {e}")
                        sende_telegram_nachricht(botname, f"Fehler beim Senden der Telegram-Nachricht {botname}: {e}")

                
                # 3. Prüfen, ob 48 Stunden seit Base-Order vergangen sind oder Nachkauforder erreicht ist
                if base_time is not None:
                    delta = datetime.now(timezone.utc) - base_time   # immer UTC-aware
                    if delta.total_seconds() >= after_h * 3600 or anzahl_nachkäufe >= after_so:
                        sell_percentage = sell_percentage2
                        try:
                            for pos in positions_raw:
                                if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                                    avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                                    if avg_price > 0:
                                        durchschnittspreis = round(avg_price, 6)
                                        logs.append(f"[Fallback] avgPrice von BingX verwendet: {durchschnittspreis}")
                                    break
                        except Exception as e:
                            logs.append(f"[Fehler] fehlgeschlagen: {e}")
                        
                        logs.append(f"Zeit überschritten oder Nachkaufgrenze erreicht → sell_percentage verringert.")
                        print(logs[-1])
                    
            # 10. Neue Limit-Order setzen
            limit_order_response = None
        
            position_size, _, _ = get_current_position(api_key, secret_key, symbol, position_side, logs)
         
            try:
                if durchschnittspreis and sell_percentage:
                    limit_price = round(durchschnittspreis * (1 + float(sell_percentage) / 100), 6)
                else:
                    limit_price = 0
        
                sell_quantity = min(sell_quantity, position_size)
        
                if sell_quantity > 0 and limit_price > 0:
                    limit_order_response = place_limit_sell_order(api_key, secret_key, symbol, sell_quantity, limit_price, position_side)
                    logs.append(f"Limit-Order gesetzt für Bot {botname} (Basis Durchschnittspreis {durchschnittspreis}): {limit_order_response}")
                else:
                    logs.append("Ungültige Daten, keine Limit-Order gesetzt.")
                    sende_telegram_nachricht(botname, f"❌ Ungültige Daten, keine Limit-Order gesetzt für Bot: {botname}")
            except Exception as e:
                logs.append(f"Fehler bei Limit-Order: {e}")
                sende_telegram_nachricht(botname, f"❌ Fehler bei Limit-Order für Bot: {botname}")
        
            # 11. Bestehende STOP_MARKET SL-Orders löschen
            try:
                for order in open_orders.get("data", {}).get("orders", []):
                    if order.get("type") == "STOP_MARKET" and order.get("positionSide") == position_side:
                        cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                        logs.append(f"Bestehende SL-Order gelöscht: {cancel_response}")
            except Exception as e:
                logs.append(f"Fehler beim Löschen alter Stop-Market-Orders: {e}")
                sende_telegram_nachricht(botname, f"❌ Fehler beim Löschen des Stop Loss für Bot: {botname}")
        
           # Stop Loss: BUY STOP_MARKET über entry
            sl_order_resp = None
            try:
                if sell_quantity > 0 and stop_loss_price:
                    sl_order_resp = place_stop_loss_order(api_key, secret_key, symbol, sell_quantity, stop_loss_price, "LONG")
                    logs.append(f"SL Stop-Market(BUY) Order gesetzt @ {stop_loss_price}: {sl_order_resp}")
                    if sl_order_resp.get("code") != 0 or sl_order_resp.get("data", {}).get("order", {}).get("status") not in (None, "NEW",):
                        logs.append("SL Stop-Market konnte nicht gesetzt werden.")
                        sende_telegram_nachricht(botname, f"⚠️ SL Stop-Market-Order konnte nicht gesetzt werden!\nSymbol: {symbol}\nResponse: {sl_order_resp}")
                else:
                    logs.append("Keine SL gesetzt – fehlende Parameter (sell_quantity oder stop_loss_price).")
                    sende_telegram_nachricht(botname, f"⚠️ Keine SL gesetzt (fehlende Daten) für Bot: {botname}")
            except Exception as e:
                logs.append(f"Fehler beim Setzen der SL-Order: {e}")
                sende_telegram_nachricht(botname, f"❌ Fehler beim Setzen der SL für Bot: {botname}: {e}")


        
            return jsonify({
                "error": False,
                "order_result": order_response,
                "limit_order_result": limit_order_response,
                "symbol": symbol,
                "botname": botname,
                "usdt_amount": usdt_amount,
                "sell_quantity": sell_quantity,
                "price_from_webhook": price_from_webhook,
                "sell_percentage": sell_percentage,
                "firebase_average_price": durchschnittspreis,
                "firebase_all_prices": kaufpreise,
                "usdt_balance_before_order": available_usdt,
                "stop_loss_price": stop_loss_price if liquidation_price else None,
                "stop_loss_price": stop_loss_price if 'stop_loss_price' in locals() else None,
                "saved_usdt_amount": saved_usdt_amounts,
                "status_fuer_alle": status_fuer_alle,
                "Botname": botname,
                "logs": logs
            })
        
#     #      #      #      #      #      #      #      #     #      #      #      #      #      #      #   #     #      #      #      #      #      #      #   #     #      #      #      #      #      #      #   

    if position_side == "SHORT":
        data = request.json or {}
        
        logs = []
    
        # Pflicht: botname
        botname = data.get("RENDER", {}).get("botname")
        if not botname:
            return jsonify({"error": True, "msg": "botname ist erforderlich"}), 400
    
        # Basis-Parameter
        symbol = data.get("RENDER", {}).get("symbol", "")
        api_key = data.get("RENDER", {}).get("api_key")
        secret_key = data.get("RENDER", {}).get("secret_key")
        firebase_secret = data.get("RENDER", {}).get("FIREBASE_SECRET")
        position_side = (data.get("RENDER", {}).get("position_side") or data.get("RENDER", {}).get("positionSide") or "SHORT").upper()
        # Erzwinge SHORT-only
        if position_side != "SHORT":
            return jsonify({"error": True, "msg": "Nur position_side=SHORT erlaubt. Abgebrochen."}), 400
    
        # Weitere parameter
        pyramiding = float(data.get("RENDER", {}).get("pyramiding", 1))
        leverageB = float(data.get("RENDER", {}).get("leverage", 1))     #float(data.get("leverage", 1))
        sicherheit = float(data.get("RENDER", {}).get("sicherheit", 0) * leverageB)    #float(data.get("sicherheit", 0) * leverageB)
        leverage = float(data.get("RENDER", {}).get("leverage", 1))
        sicherheit_param = float(data.get("RENDER", {}).get("sicherheit", 0))
        # Hinweis: in vielen deiner bisherigen Codes wurde Sicherheiten mit Hebel multipliziert -> beibehalten falls gewünscht
        sicherheit = sicherheit_param * leverage
        sell_percentage = data.get("RENDER", {}).get("sell_percentage")
        price_from_webhook = data.get("RENDER", {}).get("price")
        usdt_factor = float(data.get("RENDER", {}).get("usdt_factor", 1))
        bo_factor = float(data.get("RENDER", {}).get("bo_factor", 0.0001))
        action = data.get("vyn", {}).get("action", "").lower()
        base_time2 = data.get("RENDER", {}).get("base_time2")
        after_h = data.get("RENDER", {}).get("after_h", 48)
        after_so = data.get("RENDER", {}).get("after_so", 14)
        sell_percentage2 = data.get("RENDER", {}).get("sell_percentage2")
        beenden = data.get("RENDER", {}).get("beenden", "nein")
        sl = data.get("RENDER", {}).get("sl")
        bot_nr = data.get("RENDER", {}).get("bot_nr")
        ma = data.get("RENDER", {}).get("ma")
        
        
    
        if not api_key or not secret_key:
            return jsonify({"error": True, "msg": "api_key und secret_key sind erforderlich"}), 400
    
            # Check Offene LONG-Position
        # ------------------------------
       

    
        # action == "close" -> sofort close der SHORT position
        if action == "close":
            ergebnis = SHORT_close_open_position(api_key, secret_key, symbol, position_side)
            # reset cache für diesen bot
            saved_usdt_amounts.pop(botname, None)
            status_fuer_alle.pop(botname, None)
            alarm_counter.pop(botname, None)
            base_order_times.pop(botname, None)
            
            if bot_nr in aktueller_Bot and aktueller_Bot[bot_nr] == botname:
                del aktueller_Bot[bot_nr]  # Eintrag löschen
                print(f"Bot {botname} mit Nummer {bot_nr} wurde aus der globalen Variable gelöscht")
            
            # optional: firebase löschen
            if firebase_secret:
                try:
                    logs.append(SHORT_firebase_loesche_kaufpreise(botname, firebase_secret))
                    logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                    logs.append(SHORT_firebase_loesche_base_order_time(botname, firebase_secret))
                    logs.append(firebase_delete_aktueller_bot(bot_nr, firebase_secret))


                except Exception as e:
                    logs.append(f"Fehler beim Löschen in Firebase: {e}")
            return jsonify({
                "status": "position_closed",
                "botname": botname,
                "logs": ergebnis.get("logs", []),
                "result": ergebnis.get("result", None)
            })
    
        # sonst: Base Order / Increase / sonstiges (nur SHORT)
        # 0. Guthaben abfragen
        available_usdt = 0.0
        try:
            balance_response = SHORT_get_futures_balance(api_key, secret_key)
            logs.append(f"Balance Response: {balance_response}")
            if balance_response.get("code") == 0:
                available_margin = float(balance_response.get("data", {}).get("balance", {}).get("availableMargin", 0))
                available_usdt = available_margin * leverage
                logs.append(f"Freies USDT Guthaben (mit Hebel): {available_usdt}")
            else:
                logs.append("Fehler beim Abrufen der Balance.")
        except Exception as e:
            logs.append(f"Fehler bei Balance-Abfrage: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌❌❌ Keine Verbindung zu BingX bei Balance-Abfrage für Bot: {botname}")            
            available_usdt = None


        


        # === SHORT: Hebel NUR vor echter Base Order setzen ===
        position_size, _, _ = get_current_position(
            api_key,
            secret_key,
            symbol,
            "SHORT",
            logs
        )
        
        if position_size == 0 and action != "increase":
            try:
                logs.append(
                    f"[SHORT] Setze Hebel VOR Base Order auf {leverageB}x "
                    f"(position_size={position_size})"
                )
        
                leverage_response = SHORT_set_leverage(
                    api_key,
                    secret_key,
                    symbol,
                    leverageB,
                    "SHORT"  # <<< ENTSCHEIDEND
                )
        
                logs.append(f"[SHORT] Hebel-Response: {leverage_response}")
        
                time.sleep(0.2)
        
                if leverage_response.get("code") != 0:
                    raise Exception(leverage_response)
        
            except Exception as e:
                logs.append(f"❌ [SHORT] Hebel konnte nicht gesetzt werden: {e}")
                return jsonify({
                    "error": True,
                    "msg": "SHORT Hebel konnte nicht gesetzt werden",
                    "logs": logs
                })
        else:
            logs.append(
                f"[SHORT] Hebel NICHT gesetzt "
                f"(position_size={position_size}, action={action})"
            )

    

        # 2. Offene Orders abrufen (um alte TP/SL/Limit zu handhaben)
        open_orders = {}
        try:
            open_orders = SHORT_get_open_orders(api_key, secret_key, symbol)
            logs.append(f"Open Orders: {open_orders}")
        except Exception as e:
            logs.append(f"Fehler bei Orderprüfung: {e}")
            SHORT_sende_telegram_nachricht(botname, f"Fehler bei Orderprüfung {botname}: {e}")
    
        # 3. Ordergrößen-Logik (Compounding / BO factor)
        usdt_amount = 0
        saved_usdt_amount = saved_usdt_amounts.get(botname)
        open_sell_orders_exist = False
    
        if action == "increase":
            # Nachkauforder: prüfen ob Position noch offen
            position_size, _, _ = SHORT_get_current_position(api_key, secret_key, symbol, "SHORT", logs)
            logs.append(f"position_size bei increase: {position_size}")
            if position_size is None:
                SHORT_sende_telegram_nachricht(botname, f"Keine Verbindung zu BingX für Bot {botname} - increase aborted")
                return jsonify({"error": True, "msg": "Keine Verbindung zu BingX - increase aborted", "logs": logs}), 500
            if position_size > 0:
                open_sell_orders_exist = True
            else:
                # Position bereits geschlossen -> treat as new BO if beenden != "ja"
                if beenden.lower() == "ja" or position_size == 0:
                    logs.append("Beenden=ja → Keine neue Base Order")
                    return jsonify({"status": "no_base_order_opened", "botname": botname, "reason": "beenden=ja", "logs": logs})
                else:
                    # Reset caches, proceed to set new BO
                    saved_usdt_amounts.pop(botname, None)
                    status_fuer_alle.pop(botname, None)
                    alarm_counter.pop(botname, None)
                    base_order_times.pop(botname, None)
                    status_fuer_alle[botname] = "OK"
                    alarm_counter[botname] = -1
                    try:
                        logs.append(SHORT_firebase_loesche_kaufpreise(botname, firebase_secret))
                        logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                        logs.append(SHORT_firebase_loesche_base_order_time(botname, firebase_secret))
                    except Exception as e:
                        logs.append(f"Fehler beim Löschen in Firebase: {e}")
                    open_sell_orders_exist = False
        else:
            open_sell_orders_exist = False
    
        logs.append(f"action={action}, open_sell_orders_exist={open_sell_orders_exist}")
    
        # First Base Order
        if not open_sell_orders_exist:
            if beenden.lower() == "ja":
                logs.append("Beenden=ja → Keine neue Base Order")
                return jsonify({"status": "no_base_order_opened", "botname": botname, "reason": "beenden=ja", "logs": logs})
            else:
                status_fuer_alle[botname] = "OK"
                alarm_counter[botname] = -1
                if botname in saved_usdt_amounts:
                    del saved_usdt_amounts[botname]
                    logs.append("Ordergröße im Cache gelöscht (erste Order)")
                if available_usdt is not None and pyramiding > 0:


                    balance_response = get_futures_balance(api_key, secret_key)
                    
                    balance_data = balance_response.get("data", {}).get("balance", {})
                    
                    available_margin = float(balance_data.get("availableMargin", 0))
                    position_margin = float(balance_data.get("usedMargin", 0))
                    
                    account_size = available_margin + position_margin

                    logs.append(f"RAW balance response: {balance_response}")
                    logs.append(f"Accountgrösse: {account_size}")
                    logs.append(f"Verfügbare Marge: {available_margin}")
                    logs.append(f"Position Marge: {position_margin}")
                    
                    usdt_amount = max((account_size - sicherheit) * bo_factor, 0)   #usdt_amount = max(((available_usdt - sicherheit) * bo_factor), 0)
                    saved_usdt_amounts[botname] = usdt_amount
                    logs.append(f"Erste Ordergröße berechnet: {usdt_amount}")
        else:
            # Folgeorders: multiplizieren mit usdt_factor
            saved_usdt_amount = saved_usdt_amounts.get(botname, 0)
            if saved_usdt_amount and saved_usdt_amount > 0:
                usdt_amount = saved_usdt_amount * usdt_factor
                saved_usdt_amounts[botname] = usdt_amount
                logs.append(f"Nächste Ordergröße mit Faktor {usdt_factor} berechnet: {usdt_amount}")
            else:
                # Fallback Firebase
                try:
                    usdt_amount = SHORT_firebase_lese_ordergroesse(botname, firebase_secret) or 0
                    if usdt_amount > 0:
                        saved_usdt_amounts[botname] = usdt_amount * usdt_factor
                        usdt_amount = saved_usdt_amounts[botname]
                        logs.append(f"Ordergröße aus Firebase verwendet und skaliert: {usdt_amount}")
                        SHORT_sende_telegram_nachricht(botname, f"ℹ️ Ordergröße aus Firebase verwendet bei Bot: {botname}")
                    else:
                        logs.append("Keine Ordergröße gefunden (Firebase fallback)")
                except Exception as e:
                    status_fuer_alle[botname] = "Fehler"
                    logs.append(f"Fehler beim Lesen der Ordergröße aus Firebase: {e}")
                    SHORT_sende_telegram_nachricht(botname, f"❌ Fehler beim Lesen der Ordergröße aus Firebase {botname}: {e}")
    
        # 4. Market-Order platzieren (SHORT open)
        order_response = None
        try:
            logs.append(f"Plaziere Market-Order (SHORT) mit {usdt_amount} USDT für {symbol}...")
            order_response = SHORT_place_market_order(api_key, secret_key, symbol, float(usdt_amount), "SHORT")
            alarm_counter[botname] = alarm_counter.get(botname, -1) + 1
            logs.append(SHORT_firebase_speichere_ordergroesse(botname, usdt_amount, firebase_secret))
            time.sleep(1.5)
            logs.append(f"Market-Order Antwort: {order_response}")
            if not order_response or order_response.get("code") != 0:
                status_fuer_alle[botname] = "Fehler"
                logs.append("Marketorder konnte nicht gesetzt werden.")
                SHORT_sende_telegram_nachricht(botname, f"❌❌❌ Marketorder konnte nicht gesetzt werden für Bot: {botname}")
        except Exception as e:
            logs.append(f"Fehler bei Marketorder: {e}")
            status_fuer_alle[botname] = "Fehler"
            SHORT_sende_telegram_nachricht(botname, f"❌❌❌ Marketorder konnte nicht gesetzt werden für Bot: {botname}")
    
        # 5. Positionsgröße & liq price
        try:
            sell_quantity, positions_raw, liquidation_price = SHORT_get_current_position(api_key, secret_key, symbol, "SHORT", logs)
            if sell_quantity == 0:
                executed_qty_str = order_response.get("data", {}).get("order", {}).get("executedQty") if order_response else None
                if executed_qty_str:
                    sell_quantity = float(executed_qty_str)
                    logs.append(f"[Market Order] Ausgeführte Menge aus order_response genutzt: {sell_quantity}")
    
            if liquidation_price:
                # Stop-Loss ist 3% über Liquidationspreis per Vorgabe (short -> SL > entry)
                stop_loss_price = round(liquidation_price * (1 - sl / 100), 6)
                logs.append(f"Stop-Loss-Preis basierend auf Liquidationspreis {liquidation_price}: {stop_loss_price}")
            else:
                stop_loss_price = None
                logs.append("Liquidationspreis nicht verfügbar.")
                SHORT_sende_telegram_nachricht(botname, f"❌ Liquidationspreis nicht verfügbar für Bot: {botname}")
        except Exception as e:
            sell_quantity = 0
            stop_loss_price = None
            logs.append(f"Fehler bei Positions-/Liquidationsabfrage: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌ Fehler bei Positions-/Liquidationsabfrage {botname}: {e}")
    
        # 6. Kaufpreise ggf. löschen (bei neuer BO)
        if firebase_secret and not open_sell_orders_exist:
            try:
                logs.append(SHORT_firebase_loesche_kaufpreise(botname, firebase_secret))
            except Exception as e:
                logs.append(f"Fehler beim Löschen der Kaufpreise: {e}")
                status_fuer_alle[botname] = "Fehler"
    
        # 7. Kaufpreis speichern in Firebase (falls vorhanden)
        if firebase_secret and price_from_webhook:
            try:
                logs.append(SHORT_firebase_speichere_kaufpreis(botname, float(price_from_webhook), float(usdt_amount), firebase_secret))
            except Exception as e:
                logs.append(f"Fehler beim Speichern Kaufpreis: {e}")
                status_fuer_alle[botname] = "Fehler"
    
        # 8. Durchschnittspreis (Firebase oder BingX fallback)
        durchschnittspreis = None
        kaufpreise = []
        if status_fuer_alle.get(botname) == "Fehler":
            logs.append("Status Fehler -> Fallback auf BingX avgPrice")
            try:
                for pos in positions_raw:
                    if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == "SHORT":
                        avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                        if avg_price > 0:
                            durchschnittspreis = round(avg_price * (1 - 0.002), 6)
                            logs.append(f"[Fallback] Durchschnittspreis (BingX, adjust): {durchschnittspreis}")
                            SHORT_sende_telegram_nachricht(botname, f"ℹ️ Durchschnittspreis von BINGX verwendet für Bot: {botname}")
                            break
            except Exception as e:
                logs.append(f"[Fehler] avgPrice-Fallback fehlgeschlagen: {e}")
        else:
            try:
                if firebase_secret:
                    kaufpreise = SHORT_firebase_lese_kaufpreise(botname, firebase_secret)
                    logs.append(f"[Firebase] Kaufpreise: {kaufpreise}")
                    durchschnittspreis = SHORT_berechne_durchschnittspreis(kaufpreise or [])
                    if durchschnittspreis:
                        logs.append(f"[Firebase] Durchschnittspreis berechnet: {durchschnittspreis}")
                    else:
                        logs.append("[Firebase] Keine gültigen Kaufpreise gefunden.")
                        status_fuer_alle[botname] = "Fehler"
            except Exception as e:
                status_fuer_alle[botname] = "Fehler"
                logs.append(f"[Fehler] Firebase-Zugriff fehlgeschlagen: {e}")
    
            if not durchschnittspreis or durchschnittspreis == 0:
                try:
                    for pos in positions_raw:
                        if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == "SHORT":
                            avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                            if avg_price > 0:
                                durchschnittspreis = round(avg_price * (1 + 0.002), 6)
                                logs.append(f"Fallback avgPrice verwendet: {durchschnittspreis}")
                                SHORT_sende_telegram_nachricht(botname, f"ℹ️ Durchschnittspreis von BINGX verwendet für Bot: {botname}")
                                status_fuer_alle[botname] = "Fehler"
                                break
                except Exception as e:
                    logs.append(f"[Fehler] avgPrice-Fallback fehlgeschlagen: {e}")
                    SHORT_sende_telegram_nachricht(botname, f"❌ Fallback avgPrice fehlgeschlagen für Bot: {botname}")
            
        # Alte TP (BUY LIMIT) Orders löschen (nur Limit-Buy, Stoploss bleibt)
        try:
            if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                for order in open_orders.get("data", {}).get("orders", []):
                    # Nur BUY LIMIT für SHORT löschen, STOP_MARKET bleibt erhalten
                    if order.get("positionSide") == "SHORT" and order.get("type") == "LIMIT" and order.get("side") == "BUY":
                        cancel_resp = SHORT_cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                        logs.append(f"Gelöschte Limit-Buy-Order {order.get('orderId')}: {cancel_resp}")
        except Exception as e:
            logs.append(f"Fehler beim Löschen alter Limit-Buy-Orders: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌ Fehler beim Löschen alter Limit-Buy-Orders {botname}: {e}")

        # Alte SL (BUY STOP_MARKET) Orders löschen – wichtig für sauberes Update
        try:
            if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                for order in open_orders.get("data", {}).get("orders", []):
                    # Nur BUY STOP_MARKET (Stoploss) für SHORT löschen
                    if order.get("positionSide") == "SHORT" and order.get("type") == "STOP_MARKET" and order.get("side") == "BUY":
                        cancel_resp = SHORT_cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                        logs.append(f"Gelöschte SL Stop-Market-Order {order.get('orderId')}: {cancel_resp}")
        except Exception as e:
            logs.append(f"Fehler beim Löschen alter SL Orders: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌ Fehler beim Löschen alter SL Orders {botname}: {e}")
    
        # Base Order Zeit speichern, falls neue BO
        if not open_sell_orders_exist:           

            aktueller_Bot[bot_nr] = botname
            
            logs.append(
                f"Aktuelle Baseorder ausgeführt → "
                f"bot_nr={bot_nr}, botname={botname}"
            )

            
            # 🔥 In Firebase unter aktueller_Bot speichern
            try:
                logs.append(firebase_set_aktueller_bot(bot_nr, botname, firebase_secret))
            except Exception as e:
                logs.append(f"Fehler beim Schreiben von aktueller_Bot in Firebase: {e}")
                sende_telegram_nachricht(botname, f"Fehler beim Schreiben von aktueller_Bot in Firebase {botname}: {e}")
            
            now = datetime.now(timezone.utc)
            base_order_times[botname] = now
            logs.append(f"Base-Order Zeitpunkt gespeichert: {now}")
            try:
                logs.append(SHORT_firebase_speichere_base_order_time(botname, now, firebase_secret))
            except Exception as e:
                logs.append(f"Fehler beim Speichern base_order_time in Firebase: {e}")
    
        else:
            # Falls Folgeorders: Load/Check base_time
            if not base_time2:
                base_time = base_order_times.get(botname)
            else:
                try:
                    base_time = datetime.fromisoformat(base_time2)
                    if base_time.tzinfo is None:
                        base_time = base_time.replace(tzinfo=timezone.utc)
                except Exception:
                    base_time = None
            if base_time is None and firebase_secret:
                try:
                    base_time_str = None
                    # read from firebase
                    url = f"{FIREBASE_URL}/base_order_time/{botname}.json?auth={firebase_secret}"
                    r = requests.get(url)
                    if r.status_code == 200 and r.text:
                        d = r.json()
                        base_time_str = d.get("base_order_time") if isinstance(d, dict) else None
                    if base_time_str:
                        base_time = datetime.fromisoformat(base_time_str)
                        if base_time.tzinfo is None:
                            base_time = base_time.replace(tzinfo=timezone.utc)
                        base_order_times[botname] = base_time
                        logs.append(f"Base-Order Zeitpunkt aus Firebase geladen: {base_time}")
                except Exception as e:
                    logs.append(f"Fehler beim Laden base_time aus Firebase: {e}")
            # prüfen after_h / after_so & ggf sell_percentage anpassen
            alarm_trigger = int(data.get("RENDER", {}).get("alarm", 0))
            if status_fuer_alle.get(botname) == "Fehler":
                anzahl_nachkäufe = alarm_counter.get(botname, -1)
            else:
                anzahl_käufe = len(kaufpreise or [])
                anzahl_nachkäufe = max(anzahl_käufe - 1, 0)


            if anzahl_nachkäufe >= alarm_trigger:
                try:
                    nachricht = f"{botname}:\nNachkäufe: {anzahl_nachkäufe}"
                    telegram_result = SHORT_sende_telegram_nachricht(botname, nachricht)
                    logs.append(f"Telegram gesendet: {telegram_result}")
                except Exception as e:
                    logs.append(f"Fehler beim Senden der Telegram-Nachricht: {e}")
                    SHORT_sende_telegram_nachricht(botname, f"Fehler beim Senden der Telegram-Nachricht {botname}: {e}")

            
            if base_time is not None:
                delta = datetime.now(timezone.utc) - base_time
                if delta.total_seconds() >= int(after_h) * 3600 or anzahl_nachkäufe >= int(after_so):
                    sell_percentage = sell_percentage2
                    logs.append("Zeit oder Nachkaufgrenze überschritten -> sell_percentage reduziert (sell_percentage2 verwendet).")
    
        # 10. Take-Profit (TP) und Stop-Loss (SL) setzen (SHORT)
        limit_order_response = None
        try:
            position_size_now, _, _ = SHORT_get_current_position(api_key, secret_key, symbol, "SHORT", logs)
            sell_quantity = min(sell_quantity if 'sell_quantity' in locals() else 0, position_size_now)
            # Für Short: average (durchschnittspreis) sollte > 0
            if durchschnittspreis and sell_percentage:
                # sell_percentage for short means how much above/below? In your earlier code sell was for LONG.
                # For SHORT TP should be under the avg price => TP_price = avg * (1 - sell_percentage/100)
                limit_price = round(float(durchschnittspreis) * (1 - float(sell_percentage) / 100), 6)
            elif durchschnittspreis and sell_percentage is None:
                # fallback if sell_percentage not set: small profit target
                limit_price = round(float(durchschnittspreis) * 0.99, 6)
            else:
                limit_price = 0
    
            if sell_quantity > 0 and limit_price > 0:
                limit_order_response = SHORT_place_limit_buy_order(api_key, secret_key, symbol, sell_quantity, limit_price, "SHORT")
                logs.append(f"TP Limit(BUY) Order gesetzt @ {limit_price}: {limit_order_response}")
                # Prüfen ob Limit erfolgreich erstellt
                if limit_order_response.get("code") != 0 or limit_order_response.get("data", {}).get("order", {}).get("status") not in (None, "NEW",): 
                    # Abhängig von API kann die Struktur variieren; wir prüfen code != 0 als Fehler
                    logs.append("TP Limit-Order möglicherweise nicht erfolgreich gesetzt.")
                    SHORT_sende_telegram_nachricht(botname, f"⚠️ TP Limit-Order konnte nicht gesetzt werden!\nSymbol: {symbol}\nResponse: {limit_order_response}")
            else:
                logs.append("Ungültige Daten für TP (kein limit_price oder sell_quantity=0).")
        except Exception as e:
            logs.append(f"Fehler bei TP Limit-Order: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌ Fehler bei TP Limit-Order für Bot: {botname}")


        
    
        # Stop Loss: BUY STOP_MARKET über entry
        sl_order_resp = None

        try:
            if sell_quantity > 0 and stop_loss_price:
                sl_order_resp = SHORT_place_stoploss_buy_order(api_key, secret_key, symbol, sell_quantity, stop_loss_price, "SHORT")
                logs.append(f"SL Stop-Market(BUY) Order gesetzt @ {stop_loss_price}: {sl_order_resp}")
                if sl_order_resp.get("code") != 0 or sl_order_resp.get("data", {}).get("order", {}).get("status") not in (None, "NEW",):
                    logs.append("SL Stop-Market konnte nicht gesetzt werden.")
                    SHORT_sende_telegram_nachricht(botname, f"⚠️ SL Stop-Market-Order konnte nicht gesetzt werden!\nSymbol: {symbol}\nResponse: {sl_order_resp}")
            else:
                logs.append("Keine SL gesetzt – fehlende Parameter (sell_quantity oder stop_loss_price).")
                SHORT_sende_telegram_nachricht(botname, f"⚠️ Keine SL gesetzt (fehlende Daten) für Bot: {botname}")
        except Exception as e:
            logs.append(f"Fehler beim Setzen der SL-Order: {e}")
            SHORT_sende_telegram_nachricht(botname, f"❌ Fehler beim Setzen der SL für Bot: {botname}: {e}")
    
        # Wenn TP oder SL nicht gesetzt wurden -> Position schließen & Telegram
        tp_ok = (limit_order_response and limit_order_response.get("code") == 0) or (limit_order_response is None and limit_price == 0)
        sl_ok = (sl_order_resp and sl_order_resp.get("code") == 0) or open_sell_orders_exist
        # Defensive check: wenn neither properly set and we have a position -> close & notify
        if sell_quantity > 0 and (not tp_ok or not sl_ok):
            SHORT_sende_telegram_nachricht(botname, f"⚠️ TP oder SL konnte(n) nicht gesetzt werden. Symbol: {symbol}")
            #close_resp = SHORT_close_all_positions(api_key, secret_key)
            #logs.append(f"Positionen geschlossen weil TP/SL nicht gesetzt: {close_resp}")
            return jsonify({
                "error": True,
                "msg": "TP/SL konnte nicht gesetzt werden.",
                "logs": logs
            }), 500
    
        # final response
        return jsonify({
            "error": False,
            "order_result": order_response,
            "limit_order_result": limit_order_response,
            "sl_order_result": sl_order_resp,
            "symbol": symbol,
            "botname": botname,
            "usdt_amount": usdt_amount,
            "sell_quantity": sell_quantity if 'sell_quantity' in locals() else 0,
            "price_from_webhook": price_from_webhook,
            "sell_percentage": sell_percentage,
            "firebase_average_price": durchschnittspreis,
            "firebase_all_prices": kaufpreise,
            "usdt_balance_before_order": available_usdt,
            "stop_loss_price": stop_loss_price if 'stop_loss_price' in locals() else None,
            "saved_usdt_amount": saved_usdt_amounts.get(botname),
            "status_fuer_alle": status_fuer_alle.get(botname),
            "Botname": botname,
            "logs": logs
        })



        

if __name__ == "__main__":
    # Achtung: debug=True in Produktion ausschalten
    app.run(debug=True, host="0.0.0.0", port=5000)
        
        
