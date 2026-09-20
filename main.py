            # 2. Vstupná logika: Okamžitý čistý M15 vstup
            if len(btc_positions) == 0:
                candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 2)
                
                if candles and len(candles) >= 1:
                    latest_candle = candles[-1] # Aktuálne otvorená M15 sviečka
                    candle_time = latest_candle['time']
                    c_open = latest_candle['open']
                    
                    if last_checked_candle_time != candle_time:
                        price_info = await connection.get_symbol_price(SYMBOL)
                        ask = price_info.get('ask')
                        bid = price_info.get('bid')

                        # Pozrieme sa na trend predchádzajúcej sviečky (iloc[-2])
                        prev_candle = candles[-2] if len(candles) >= 2 else latest_candle
                        
                        if ask and bid:
                            if prev_candle['close'] > prev_candle['open']: # Predchádzajúca bola zelená -> BUY
                                sl = ask - SL_POINTS
                                tp = ask + TP_POINTS
                                await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = candle_time
                                send_telegram("🟢 BTCUSD BUY otvorený.")

                            elif prev_candle['close'] < prev_candle['open']: # Predchádzajúca bola červená -> SELL
                                sl = bid + SL_POINTS
                                tp = bid - TP_POINTS
                                await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = candle_time
                                send_telegram("🔴 BTCUSD SELL otvorený.")
