            # 2. Vstupná logika: Okamžitý čistý M15 vstup bez kontroly času
            if len(btc_positions) == 0:
                candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 3)
                
                if candles and len(candles) >= 2:
                    # Použijeme dĺžku zoznamu sviečok ako jedinečný identifikátor novej sviečky
                    current_candles_count = len(candles)
                    
                    if last_checked_candle_time != current_candles_count:
                        price_info = await connection.get_symbol_price(SYMBOL)
                        ask = price_info.get('ask')
                        bid = price_info.get('bid')

                        prev_candle = candles[-2] # Predchádzajúca uzavretá sviečka
                        
                        if ask and bid:
                            if prev_candle['close'] > prev_candle['open']:
                                sl = ask - SL_POINTS
                                tp = ask + TP_POINTS
                                await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = current_candles_count
                                send_telegram("🟢 BTCUSD BUY otvorený.")

                            elif prev_candle['close'] < prev_candle['open']:
                                sl = bid + SL_POINTS
                                tp = bid - TP_POINTS
                                await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = current_candles_count
                                send_telegram("🔴 BTCUSD SELL otvorený.")
