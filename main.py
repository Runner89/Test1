
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

    orders_data = fill_orders_response.get("data", {})
    logs.append(f"Orders-Typ: {type(orders_data)}")
    logs.append(f"Orders-Inhalt: {orders_data}")

    orders = orders_data.get("orders", [])

    if isinstance(orders, list) and all(isinstance(o, dict) for o in orders):
        logs.append(f"Unsortierte Orders (updateTime): {[o.get('updateTime') for o in orders]}")
        
        # Sortieren nach updateTime absteigend
        orders_sorted = sorted(
            orders,
            key=lambda o: int(o.get("updateTime", 0)),
            reverse=True
        )
        logs.append(f"Sortierte Orders (updateTime): {[o.get('updateTime') for o in orders_sorted]}")

        # Berechne order_size_usdt für jede Order
        for order in orders_sorted:
            try:
                executed_qty = float(order.get("executedQty", 0))
                avg_price = float(order.get("avgPrice", 0))
                order_size_usdt = executed_qty * avg_price
                order["order_size_usdt"] = round(order_size_usdt, 4)
            except (ValueError, TypeError):
                order["order_size_usdt"] = None
    else:
        logs.append("Warnung: Die Orders-Daten sind nicht im erwarteten Format (Liste von Dicts).")
        orders_sorted = []

    current_price = get_current_price(symbol)
    if current_price:
        logs.append(f"Aktueller Preis für {symbol}: {current_price}")

    return jsonify({
        "error": False,
        "last_fill_orders": orders_sorted,
        "logs": logs
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
