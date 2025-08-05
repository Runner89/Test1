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

    # 3. Ordergröße ermitteln (angepasste Compounding-Logik)
    usdt_amount = 0
    position_value_usdt = 0.0
    try:
        # Positionen abfragen
        sell_quantity, positions_raw, liquidation_price = get_current_position(api_key, secret_key, symbol, position_side, logs)
        # Positionswert in USDT berechnen
        for pos in positions_raw:
            if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                position_qty = float(pos.get("positionAmt", 0))  # Menge (kann negativ sein)
                avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                position_value_usdt = abs(position_qty) * avg_price
                logs.append(f"Positionswert (USDT): {position_value_usdt}")
                break

        # Firebase-Logik, wenn vorhanden
        if firebase_secret:
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
                    # Neue Berechnung der Ordergröße:
                    berechnet = (position_value_usdt + available_usdt - sicherheit) / pyramiding
                    usdt_amount = max(berechnet, 0)
                    logs.append(f"Neue Ordergröße berechnet: ((Position {position_value_usdt} + Guthaben {available_usdt} - Sicherheit {sicherheit}) / Pyramiding {pyramiding}) = {usdt_amount}")
                    logs.append(firebase_speichere_ordergroesse(base_asset, usdt_amount, firebase_secret))
        else:
            # Falls kein Firebase, einfach berechnen
            if available_usdt is not None and pyramiding > 0:
                berechnet = (position_value_usdt + available_usdt - sicherheit) / pyramiding
                usdt_amount = max(berechnet, 0)
                logs.append(f"Ordergröße berechnet (kein Firebase): ((Position {position_value_usdt} + Guthaben {available_usdt} - Sicherheit {sicherheit}) / Pyramiding {pyramiding}) = {usdt_amount}")

    except Exception as e:
        logs.append(f"Fehler bei Ordergrößenberechnung: {e}")

    # 4. Market-Order ausführen
    logs.append(f"Plaziere Market-Order mit {usdt_amount} USDT für {symbol} ({position_side})...")
    order_response = place_market_order(api_key, secret_key, symbol, float(usdt_amount), position_side)
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
            stop_loss_price = round(liquidation_price * 1.02, 6)
            logs.append(f"Stop-Loss-Preis basierend auf Liquidationspreis {liquidation_price}: {stop_loss_price}")
        else:
            stop_loss_price = None
            logs.append("Liquidationspreis nicht verfügbar. Kein Stop-Loss-Berechnung möglich.")
    except Exception as e:
        sell_quantity = 0
        stop_loss_price = None
        logs.append(f"Fehler bei Positions- oder Liquidationspreis-Abfrage: {e}")

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

    # 8. Durchschnittspreis bestimmen – zuerst aus Firebase, sonst avgPrice von BingX
    durchschnittspreis = None
    kaufpreise = []

    # 1. Versuch: Firebase lesen
    try:
        if firebase_secret:
            kaufpreise = firebase_lese_kaufpreise(base_asset, firebase_secret)
            durchschnittspreis = berechne_durchschnittspreis(kaufpreise or [])
            if durchschnittspreis:
                logs.append(f"[Firebase] Durchschnittspreis berechnet: {durchschnittspreis}")
            else:
                logs.append("[Firebase] Keine gültigen Kaufpreise gefunden.")
    except Exception as e:
        logs.append(f"[Fehler] Firebase-Zugriff fehlgeschlagen: {e}")

    # 2. Fallback: avgPrice aus BingX-Position, wenn Firebase-Durchschnitt fehlt oder Fehler
    if not durchschnittspreis or durchschnittspreis == 0:
        try:
            for pos in positions_raw:
                if pos.get("symbol") == symbol and pos.get("positionSide", "").upper() == position_side.upper():
                    avg_price = float(pos.get("avgPrice", 0)) or float(pos.get("averagePrice", 0))
                    if avg_price > 0:
                        durchschnittspreis = round(avg_price * (1 - 0.002), 6)
                        sende_telegram_nachricht(f"Fehler in Firebase bei Coin: {base_asset}")
                        logs.append(f"[Fallback] avgPrice aus Position verwendet: {durchschnittspreis}")
                    else:
                        logs.append("[Fallback] Kein gültiger avgPrice in Position vorhanden.")
                    break
        except Exception as e:
            logs.append(f"[Fehler] avgPrice-Fallback fehlgeschlagen: {e}")

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

    # 11. Bestehende STOP_MARKET SL-Orders löschen
    try:
        for order in open_orders.get("data", {}).get("orders", []):
            if order.get("type") == "STOP_MARKET" and order.get("positionSide") == position_side:
                cancel_response = cancel_order(api_key, secret_key, symbol, str(order.get("orderId")))
                logs.append(f"Bestehende SL-Order gelöscht: {cancel_response}")
    except Exception as e:
        logs.append(f"Fehler beim Löschen alter Stop-Market-Orders: {e}")

    # 12. Stop-Loss Order setzen
    stop_loss_response = None
    try:
        if sell_quantity > 0 and stop_loss_price:
            stop_loss_response = place_stop_loss_order(api_key, secret_key, symbol, sell_quantity, stop_loss_price, position_side)
            logs.append(f"Stop-Loss Order gesetzt bei {stop_loss_price}: {stop_loss_response}")
        else:
            logs.append("Keine Stop-Loss Order gesetzt – unvollständige Daten.")
    except Exception as e:
        logs.append(f"Fehler beim Setzen der Stop-Loss Order: {e}")

    # 13. Alarm senden
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
        "stop_loss_price": stop_loss_price if liquidation_price else None,
        "stop_loss_response": stop_loss_response if liquidation_price else None,
        "logs": logs
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
