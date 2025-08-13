#nicht vyn
#Market Order mit Hebel wird gesetzt
#Hebel muss in BINGX selber vorher eingestellt werden
#Preis, welcher im JSON übergeben wurde, wird in Firebase gespeichert
#der gewichtete Durschnittspreis wird von Firebase berechnet und entsprechend die Sell-Limit Order gesetzt
#Bei Alarm wird angegeben, ab welcher SO ein Alarm via Telegramm gesendet wird
#Verfügbares Guthaben wird ermittelt
#Ordergrösse = (Verfügbares Guthaben - Sicherheit)/Pyramiding
#Ordergrösse wird in Variable gespeichert, Firebase wird nur als Backup verwendet
#StopLoss 3% über Liquidationspreis
#Falls Firebaseverbindung fehlschlägt, wird der Durchschnittspreis aus Bingx -0.3% für die Berechnung der Sell-Limit-Order verwendet.
#Falls Status Fehler werden für den Alarm nicht die Anzahl Kaufpreise gezählt, sondern von der Variablen alarm_counter

#https://......../webhook
#{
#    "api_key": "",
#    "secret_key": "",
#    "symbol": "BABY-USDT",
#    "botname": "Baby_Bot", # muss einmalig sein
#    "position_side": "LONG",
#    "sell_percentage": 2.5,
#    "price": 0.068186,
#    "leverage": 1,
#    "FIREBASE_SECRET": "",
#    "alarm": 1,
#    "pyramiding": 8,
#    "sicherheit": 96 Sicherheit muss nicht mal Hebel gerechnet werden, wird im Code gemacht
#}
#    Berechnung Ordergrösse
#    verfügbares Guthaben x leverage
#    - (Sicherheit x leverage)
#    Eregbnis / pyramiding


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
status_fuer_alle = {} 
alarm_counter = {}

def generate_signature(secret_key: str, params: str) -> str:
    return hmac.new(secret_key.encode('utf-8'), params.encode('utf-8'), hashlib.sha256).hexdigest()

def get_server_time():
    #"""Serverzeit in Millisekunden holen"""
    resp = requests.get(f"{BASE_URL}/api/v1/common/time")
    resp.raise_for_status()
    return int(resp.json()["time"])

def get_futures_balance(api_key: str, secret_key: str):
    timestamp = int(time.time() * 1000)
    params = f"timestamp={timestamp}"
    signature = generate_signature(secret_key, params)
    url = f"{BASE_URL}{BALANCE_ENDPOINT}?{params}&signature={signature}"
    headers = {"X-BX-APIKEY": api_key}
    response = requests.get(url, headers=headers)
    return response.json()

import requests, decimal, time

def get_symbol_info(symbol):
    url = f"{BASE_URL}/openApi/swap/v2/quote/contracts"
    resp = requests.get(url)
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"Fehler beim Laden der Symbolinfos: {data}")
    for item in data.get("data", []):
        if item.get("symbol") == symbol:
            return {
                "lotSize": float(item.get("lotSize", 0)),
                "tickSize": float(item.get("tickSize", 0)),
                "minQty": float(item.get("minQty", 0)),
                "maxQty": float(item.get("maxQty", 0))
            }
    raise Exception(f"Symbol {symbol} nicht gefunden")

def round_quantity(symbol, quantity):
    info = get_symbol_info(symbol)
    lot = info["lotSize"]
    precision = abs(decimal.Decimal(str(lot)).as_tuple().exponent)
    return round(float(quantity), precision)

def close_position(api_key, secret_key, symbol, position_side, quantity, logs):
    try:
        # 1. Serverzeit holen
        timestamp = get_server_time()

        # 2. Parameter vorbereiten
        params = {
            "symbol": symbol,
            "side": "SELL" if position_side == "LONG" else "BUY",
            "type": "MARKET",
            "quantity": str(quantity),
            "positionSide": position_side,
            "reduceOnly": "true",
            "timestamp": timestamp
        }

        # 3. Signatur erstellen
        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)
        params["signature"] = generate_signature(secret_key, query)

        logs.append(f"DEBUG Order-Params: {params}")

        # 4. Request senden
        headers = {
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        response = requests.post(f"{BASE_URL}{ORDER_ENDPOINT}", headers=headers, data=params).json()

        if response.get("code") == 0:
            logs.append(f"{position_side.upper()} Position geschlossen: {response}")
        else:
            logs.append(f"Fehler beim Schließen der Position: {response}")

    except Exception as e:
        logs.append(f"Fehler beim Schließen der Position: {e}")



def get_current_price(symbol: str):
    url = f"{BASE_URL}{PRICE_ENDPOINT}?symbol={symbol}"
    response = requests.get(url)
    data = response.json()
    if data.get("code") == 0 and "data" in data and "price" in data["data"]:
        return float(data["data"]["price"])
    else:
        return None

def place_market_order(api_key, secret_key, symbol, quantity, side, position_side, reduce_only=False, is_contract_qty=False):
    price = get_current_price(symbol)
    if price is None:
        return {"code": 99999, "msg": "Failed to get current price"}

    # Falls Menge in USDT angegeben ist, umrechnen
    if not is_contract_qty:
        quantity = round(quantity / price, 6)
    else:
        quantity = round(quantity, 6)

    timestamp = int(time.time() * 1000)

    params_dict = {
        "symbol": symbol,
        "side": side.upper(),             # "BUY" oder "SELL"
        "type": "MARKET",
        "quantity": quantity,
        "positionSide": position_side.upper(),  # "LONG" oder "SHORT"
        "timestamp": timestamp
    }

    if reduce_only:
        params_dict["reduceOnly"] = "true"  # String!

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



    # Nur in Hedge Mode setzen
    if position_side:
        params_dict["positionSide"] = position_side.upper()

    if reduce_only:
        params_dict["reduceOnly"] = "true"  # String, klein geschrieben

    # Signieren
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

    # Nur setzen, wenn reduce_only True ist
    if reduce_only:
        params_dict["reduceOnly"] = "true"  # bei vielen APIs klein geschrieben

    # Signatur erstellen
    query_string = "&".join(f"{k}={params_dict[k]}" for k in sorted(params_dict))
    signature = generate_signature(secret_key, query_string)
    params_dict["signature"] = signature

    # API-Request absenden
    url = f"{BASE_URL}{ORDER_ENDPOINT}"
    headers = {
        "X-BX-APIKEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json=params_dict)
    return response.json()


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
    global saved_usdt_amounts
    global status_fuer_alle
    global alarm_counter

    data = request.json
    logs = []

    botname = data.get("botname")
    if not botname:
        return jsonify({"error": True, "msg": "botname ist erforderlich"}), 400

    symbol = data.get("symbol", "BTC-USDT")
    base_asset = symbol.split("-")[0]  # Nur für menschliche Logs

    # Hole den gespeicherten Wert für den Bot, falls vorhanden
    saved_usdt_amount = saved_usdt_amounts.get(botname)

    # Eingabewerte
    pyramiding = float(data.get("pyramiding", 1))
    leverageB = float(data.get("leverage", 1))
    sicherheit = float(data.get("sicherheit", 0) * leverageB)
    sell_percentage = data.get("sell_percentage")
    api_key = data.get("api_key")
    secret_key = data.get("secret_key")
    position_side = data.get("position_side") or data.get("positionSide") or "LONG"
    firebase_secret = data.get("FIREBASE_SECRET")
    price_from_webhook = data.get("price")
    usdt_factor = float(data.get("usdt_factor", 1))
    action = data.get("action", "").lower()
    

    if not api_key or not secret_key:
        return jsonify({"error": True, "msg": "api_key und secret_key sind erforderlich"}), 400

    available_usdt = 0.0

    #falls close im Webhook, dann alle Positionen des coin schliessen
    if action == "close":
        try:
            logs.append(f"Schließe alle offenen Orders und Positionen für {symbol}...")
        
            # 1. Offene Orders löschen
            try:
                open_orders = get_open_orders(api_key, secret_key, symbol)
                if isinstance(open_orders, dict) and open_orders.get("code") == 0:
                    for order in open_orders.get("data", {}).get("orders", []):
                        cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                        logs.append(f"Gelöschte Order {order.get('orderId')}: {cancel_response}")
            except Exception as e:
                logs.append(f"Fehler beim Löschen der Orders: {e}")
        
            # 2. Position(en) schließen 
            try:   
                position_side = "LONG"  # oder "SHORT", je nachdem welche Position offen ist
            
                # Positionsgröße holen
                position_size, _, _ = get_current_position(api_key, secret_key, symbol, position_side, logs)
            
                # Positionsgröße runden
                qty = round_quantity(symbol, position_size)
            
                # Position schließen
                close_position(api_key, secret_key, symbol, position_side, qty, logs)
            
            except Exception as e:
                logs.append(f"Fehler beim Schließen der Position: {e}")
                            

        
            # 3. Lokale Variablen & Alarm zurücksetzen
            if botname in saved_usdt_amounts:
                del saved_usdt_amounts[botname]
                logs.append(f"Lokale Ordergröße für {botname} gelöscht.")
        
            status_fuer_alle[botname] = "OK"
            alarm_counter[botname] = 0
        
            # 4. Firebase-Daten löschen
            try:
                if firebase_secret:
                    logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
                    logs.append(firebase_loesche_kaufpreise(botname, firebase_secret))
            except Exception as e:
                logs.append(f"Fehler beim Löschen aus Firebase: {e}")
        
            return jsonify({
                "error": False,
                "msg": f"Alle offenen Orders, Positionen, Cache und Alarm-Zähler für {symbol} gelöscht",
                "logs": logs
            })
        except Exception as e:
            return jsonify({
                "error": True,
                "msg": f"Fehler bei close: {e}",
                "logs": logs
            }), 500

    else:    

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
            sende_telegram_nachricht(botname, f"Fehler bei Orderprüfung {botname}: {e}")
    
        # 3. Ordergröße ermitteln (Compounding-Logik)
        usdt_amount = 0
        open_sell_orders_exist = False
        
        # Prüfen, ob es offene SELL-Limit Orders gibt
        if isinstance(open_orders, dict) and open_orders.get("code") == 0:
            for order in open_orders.get("data", {}).get("orders", []):
                if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                    open_sell_orders_exist = True
                    break
        
        # Wenn keine offene Sell-Limit-Order existiert → erste Order
        if not open_sell_orders_exist:
            status_fuer_alle[botname] = "OK"
            alarm_counter[botname] = -1
        
            #logs.append(firebase_loesche_ordergroesse(botname, firebase_secret))
        
            if botname in saved_usdt_amounts:
                del saved_usdt_amounts[botname]
                logs.append(f"Ordergröße aus Cache für {botname} gelöscht (erste Order)")
        
            if available_usdt is not None and pyramiding > 0:
                # Erste Order bleibt unverändert
                usdt_amount = max(((available_usdt - sicherheit) / pyramiding), 0)
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
        
        logs.append(f"Plaziere Market-Order mit {usdt_amount} USDT für {symbol} ({position_side})...")
        order_response = place_market_order(api_key, secret_key, symbol, float(usdt_amount), position_side)
        if order_response.get("code") != 0:
            logs.append(f"Market-Order konnte nicht platziert werden: {order_response}")
        alarm_counter[botname] += 1
        logs.append(firebase_speichere_ordergroesse(botname, usdt_amount, firebase_secret))
        time.sleep(2)
        logs.append(f"Market-Order Antwort: {order_response}")
    
        # 5. Positionsgröße und Liquidationspreis ermitteln
        try:
            sell_quantity, positions_raw, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs)
    
            if sell_quantity == 0:
                executed_qty_str = order_response.get("data", {}).get("order", {}).get("executedQty")
                if executed_qty_str:
                    sell_quantity = float(executed_qty_str)
                    logs.append(f"[Market Order] Ausgeführte Menge aus order_response genutzt: {sell_quantity}")
    
            if liquidation_price:
                stop_loss_price = round(liquidation_price * 1.03, 6)
                logs.append(f"Stop-Loss-Preis basierend auf Liquidationspreis {liquidation_price}: {stop_loss_price}")
            else:
                stop_loss_price = None
                logs.append("Liquidationspreis nicht verfügbar. Kein Stop-Loss-Berechnung möglich.")
                sende_telegram_nachricht(botname, f"❌ Liquidationspreis nicht verfügbar für Bot: {botname}")
        except Exception as e:
            sell_quantity = 0
            stop_loss_price = None
            logs.append(f"Fehler bei Positions- oder Liquidationspreis-Abfrage: {e}")
            sende_telegram_nachricht(botname, f"Fehler bei Positions- oder Liquidationspreis-Abfrage {botname}: {e}")
    
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
                            durchschnittspreis = round(avg_price * (1 - 0.003), 6)
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
    
        # 12. Stop-Loss Order setzen
        stop_loss_response = None
        try:
            if sell_quantity > 0 and stop_loss_price:
                stop_loss_response = place_stop_loss_order(api_key, secret_key, symbol, sell_quantity, stop_loss_price, position_side)
                logs.append(f"Stop-Loss Order gesetzt bei {stop_loss_price} für Bot {botname}: {stop_loss_response}")
            else:
                logs.append("Keine Stop-Loss Order gesetzt – unvollständige Daten.")
        except Exception as e:
            logs.append(f"Fehler beim Setzen der Stop-Loss Order: {e}")
            sende_telegram_nachricht(botname, f"❌ Fehler beim Setzen des Stop Loss für Bot: {botname}")
    
        alarm_trigger = int(data.get("alarm", 0))
    
        if status_fuer_alle.get(botname) == "Fehler":
            anzahl_nachkäufe = alarm_counter[botname] 
        else:
            anzahl_käufe = len(kaufpreise or [])
            anzahl_nachkäufe = max(anzahl_käufe - 1, 0)
        
        if anzahl_nachkäufe >= alarm_trigger:
            try:
                nachricht = f"{botname}:\nNachkäufe: {anzahl_nachkäufe}"
                telegram_result = sende_telegram_nachricht(botname, nachricht)
                logs.append(f"Telegram gesendet: {telegram_result}")
            except Exception as e:
                logs.append(f"Fehler beim Senden der Telegram-Nachricht: {e}")
                sende_telegram_nachricht(botname, f"Fehler beim Senden der Telegram-Nachricht {botname}: {e}")
    
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
            "stop_loss_response": stop_loss_response if liquidation_price else None,
            "saved_usdt_amount": saved_usdt_amounts,
            "status_fuer_alle": status_fuer_alle,
            "Botname": botname,
            "logs": logs
        })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
