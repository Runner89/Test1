
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

        open_sell_orders_exist = False
        if isinstance(open_orders, dict) and open_orders.get("code") == 0:
            for order in open_orders.get("data", {}).get("orders", []):
                if order.get("side") == "SELL" and order.get("positionSide") == position_side and order.get("type") == "LIMIT":
                    open_sell_orders_exist = True
                    break

        # Berechnung nach der Schleife, wenn keine offenen Sell-Limit-Orders existieren
        if not open_sell_orders_exist:
            if available_usdt is not None and pyramiding > 0:
                berechnet = (position_value_usdt + available_usdt - sicherheit) / pyramiding
                usdt_amount = max(berechnet, 0)
                logs.append(f"Neue Ordergröße berechnet: ((Position {position_value_usdt} + Guthaben {available_usdt} - Sicherheit {sicherheit}) / Pyramiding {pyramiding}) = {usdt_amount}")
                logs.append(firebase_speichere_ordergroesse(base_asset, usdt_amount, firebase_secret))

    except Exception as e:
        logs.append(f"Fehler bei Ordergrößenberechnung: {e}")
