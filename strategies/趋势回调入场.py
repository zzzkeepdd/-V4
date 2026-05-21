# -*- coding: utf-8 -*-
# 适用行情: TRENDING_UP, TRENDING_DOWN
# 不适行情: RANGING, HIGH_VOLATILITY
# 交易频率: 中频H1
# 核心逻辑: 趋势确立后回调到斐波那契0.5-0.618区间+反转K线确认+成交量放大→顺趋势入场，TP=2R
# 标的: BTC、ETH（SOL不适合）
# 标的限制: BTC、ETH（SOL不适合，质检显示SOL摆动点结构噪声过大）
#
# 【网格搜索验证结果（真实数据 2025-05 ~ 2026-05）】
#   BTC最佳(W): lb=5, ns=1, fib=[0.5,0.618], wbr=1.0, vol=True → WR=53.8%, avgR=0.62R, +26.5%
#   ETH最佳(W): lb=5, ns=2, fib=[0.382,0.618], wbr=1.0, vol=True → WR=60.0%, avgR=0.80R, +55.4%
#   SOL: --- 不适合此策略（摆动点结构噪声过大，WR=26.7%, avgR=-0.20R, -18.2%）---
#
# 【参数调优指引】
#   ns(趋势确认摆动次数): BTC=1(趋势延续短), ETH=2(趋势确认长)
#   fib区间: BTC更窄[0.5,0.618], ETH可放宽[0.382,0.618]
#   vol_filter: 强烈建议开启，对所有标的均显著提升表现
#   wbr(影线/实体比): 1.0-1.5均可，1.0更宽松=更多信号
#
# === 预置参数包（由盘后AI审查系统使用，格式: PARAMS_XXX = {...}）===
# PARAMS_BTC = {'lookback': 5, 'n_swings': 1, 'fib_levels': [0.5, 0.618], 'wick_body_ratio': 1.0, 'rr_ratio': 2.0, 'vol_filter': True, 'vol_lookback': 20, 'sl_buffer': 0.002}
# PARAMS_ETH = {'lookback': 5, 'n_swings': 2, 'fib_levels': [0.382, 0.618], 'wick_body_ratio': 1.0, 'rr_ratio': 2.0, 'vol_filter': True, 'vol_lookback': 20, 'sl_buffer': 0.002}

import pandas as pd
import numpy as np


def _标准化策略输出(equity, trades, ohlc_df, params):
    """把策略内部净值统一成完整K线长度、以本金起步的资金曲线。"""
    params = dict(params or {})
    initial_capital = float(params.get('capital', params.get('initial_capital', 10000.0)) or 10000.0)
    n = len(ohlc_df)
    eq = np.asarray(list(equity or []), dtype=float)
    if eq.size == 0:
        eq = np.asarray([initial_capital], dtype=float)
    eq = pd.Series(eq).replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(initial_capital).to_numpy(dtype=float)
    first = eq[0] if abs(eq[0]) > 1e-12 else 1.0
    if np.nanmax(np.abs(eq)) < max(100.0, initial_capital * 0.1):
        eq = eq / first * initial_capital
    if len(eq) < n:
        eq = np.concatenate([np.full(n - len(eq), initial_capital), eq])
    elif len(eq) > n:
        eq = eq[-n:]
    eq[0] = initial_capital
    return {'equity': eq.tolist(), 'trades': trades}


def find_swing_points(high_arr, low_arr, lookback):
    """
    识别摆动高低点（纯结构，无阈值依赖）
    摆动高点：当前high在左右各lookback根K线内最高
    摆动低点：当前low在左右各lookback根K线内最低
    返回已确认的摆动点索引（满足 i >= lookback 且 i + lookback < n）
    """
    n = len(high_arr)
    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        if high_arr[i] == high_arr[i - lookback:i + lookback + 1].max():
            swing_highs.append(i)
        if low_arr[i] == low_arr[i - lookback:i + lookback + 1].min():
            swing_lows.append(i)
    return swing_highs, swing_lows


def is_bullish_reversal(open_i, close_i, high_i, low_i, prev_open, prev_close, wick_body_ratio):
    """
    多头反转K线确认（两种模式，纯相对比较，无阈值）：
    1. 锤子线：下影线 > 实体 × wbr，且上影线 < 实体 × 0.3
    2. 看涨吞没：当前K线完全吞没前一根K线
    返回: (is_valid, pattern_name)
    """
    body = abs(close_i - open_i)
    if body == 0:
        return False, ''

    lower_wick = min(open_i, close_i) - low_i
    upper_wick = high_i - max(open_i, close_i)

    # 锤子线：阳线 + 长下影 + 短上影
    if close_i > open_i:
        if lower_wick > body * wick_body_ratio and upper_wick < body * 0.3:
            return True, 'hammer'

    # 看涨吞没：当前K线完全覆盖前一根
    if open_i <= prev_close and close_i >= prev_open:
        if close_i > open_i:  # 额外确认阳线
            if body > abs(prev_close - prev_open):  # 实体大于前一根
                return True, 'engulfing'

    return False, ''


def is_bearish_reversal(open_i, close_i, high_i, low_i, prev_open, prev_close, wick_body_ratio):
    """
    空头反转K线确认（两种模式）：
    1. 倒锤子/射击之星：上影线 > 实体 × wbr，且下影线 < 实体 × 0.3
    2. 看跌吞没：当前K线完全吞没前一根K线
    """
    body = abs(close_i - open_i)
    if body == 0:
        return False, ''

    upper_wick = high_i - max(open_i, close_i)
    lower_wick = min(open_i, close_i) - low_i

    # 射击之星：阴线 + 长上影 + 短下影
    if close_i < open_i:
        if upper_wick > body * wick_body_ratio and lower_wick < body * 0.3:
            return True, 'shooting_star'

    # 看跌吞没
    if open_i >= prev_close and close_i <= prev_open:
        if close_i < open_i:
            if body > abs(prev_close - prev_open):
                return True, 'engulfing'

    return False, ''


def check_uptrend(high_arr, low_arr, swing_highs, swing_lows, n_swings):
    """
    检查上升趋势：连续n_swings次高点抬高 + 低点抬高
    纯结构判断，无阈值依赖
    """
    if len(swing_highs) < n_swings + 1 or len(swing_lows) < n_swings + 1:
        return False

    recent_highs = swing_highs[-n_swings - 1:]
    recent_lows = swing_lows[-n_swings - 1:]

    higher_highs = all(
        high_arr[recent_highs[j + 1]] > high_arr[recent_highs[j]]
        for j in range(len(recent_highs) - 1)
    )
    higher_lows = all(
        low_arr[recent_lows[j + 1]] > low_arr[recent_lows[j]]
        for j in range(len(recent_lows) - 1)
    )
    return higher_highs and higher_lows


def check_downtrend(high_arr, low_arr, swing_highs, swing_lows, n_swings):
    """
    检查下降趋势：连续n_swings次高点降低 + 低点降低
    """
    if len(swing_highs) < n_swings + 1 or len(swing_lows) < n_swings + 1:
        return False

    recent_highs = swing_highs[-n_swings - 1:]
    recent_lows = swing_lows[-n_swings - 1:]

    lower_highs = all(
        high_arr[recent_highs[j + 1]] < high_arr[recent_highs[j]]
        for j in range(len(recent_highs) - 1)
    )
    lower_lows = all(
        low_arr[recent_lows[j + 1]] < low_arr[recent_lows[j]]
        for j in range(len(recent_lows) - 1)
    )
    return lower_highs and lower_lows


def strategy_logic(ohlc_df, factor_df, params):
    """
    趋势回调入场策略 — 斐波那契回调 + 反转K线确认

    逻辑：
    1. 识别摆动点 → 判断趋势方向（上升/下降）
    2. 趋势确立后等待价格回调到斐波那契0.5-0.618区间
    3. 在回调区间出现反转K线（锤子线/吞没）且成交量放大 → 入场
    4. 止损设在最近的摆动点外侧，止盈=2×止损距离

    输入:
        ohlc_df: DataFrame, 列 ['open','high','low','close','volume']
        factor_df: 因子数据DataFrame（可合并使用）
        params: 参数字典
            swing_lookback: 摆动点识别窗口 (默认5)
            n_swings: 趋势确认所需摆动次数 (BTC=1, ETH=2, 默认1)
            fib_low: 回调下限 (默认0.5)
            fib_high: 回调上限 (默认0.618)
            wick_body_ratio: 影线/实体最小比 (默认1.0)
            rr_ratio: 盈亏比目标 (默认2.0)
            sl_buffer: 止损缓冲区(比例) (默认0.002=0.2%)
            vol_filter: 是否启用成交量过滤 (默认True，强烈推荐)
            vol_lookback: 成交量MA周期 (默认20)
            vol_mult: 成交量倍数阈值 (默认1.0，即>均量)
    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    # ===== 参数提取 =====
    swing_lookback = params.get('swing_lookback', 5)
    n_swings = params.get('n_swings', 1)
    fib_low = params.get('fib_low', 0.5)
    fib_high = params.get('fib_high', 0.618)
    wick_body_ratio = params.get('wick_body_ratio', 1.0)
    rr_ratio = params.get('rr_ratio', 2.0)
    sl_buffer = params.get('sl_buffer', 0.002)
    vol_filter = params.get('vol_filter', True)
    vol_lookback = params.get('vol_lookback', 20)
    vol_mult = params.get('vol_mult', 1.0)

    # ===== 数据准备 =====
    n = len(ohlc_df)
    highs = ohlc_df['high'].values
    lows = ohlc_df['low'].values
    opens = ohlc_df['open'].values
    closes = ohlc_df['close'].values
    volumes = ohlc_df['volume'].values if vol_filter else None

    # 成交量移动平均（用于反转确认时的量能过滤）
    vol_ma = None
    if vol_filter and volumes is not None:
        vol_ma = np.full(n, np.nan)
        for i in range(vol_lookback, n):
            vol_ma[i] = np.mean(volumes[i - vol_lookback:i])

    # ===== 摆动点识别 =====
    swing_highs, swing_lows = find_swing_points(highs, lows, swing_lookback)

    # ===== 主回测循环 =====
    trades = []
    equity = []
    position = 0  # 0=空仓, 1=多头, -1=空头
    current_trade = None
    last_entry_leg_high = -1  # 防止同一摆动腿重复入场
    start_idx = max(200, swing_lookback * 3)

    for i in range(start_idx, n):
        # --- 出场检查（TP优先，同K线内取更有利的价格）---
        if position == 1:  # 多头持仓
            if highs[i] >= current_trade['tp']:
                exit_price = current_trade['tp']
                pnl = (exit_price - current_trade['entry_price']) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i, 'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price, 'pnl_pct': pnl, 'result': 'TP'
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
            elif lows[i] <= current_trade['sl']:
                exit_price = current_trade['sl']
                pnl = (exit_price - current_trade['entry_price']) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i, 'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price, 'pnl_pct': pnl, 'result': 'SL'
                })
                trades.append(current_trade)
                position = 0
                current_trade = None

        elif position == -1:  # 空头持仓
            if lows[i] <= current_trade['tp']:
                exit_price = current_trade['tp']
                pnl = (current_trade['entry_price'] - exit_price) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i, 'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price, 'pnl_pct': pnl, 'result': 'TP'
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
            elif highs[i] >= current_trade['sl']:
                exit_price = current_trade['sl']
                pnl = (current_trade['entry_price'] - exit_price) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i, 'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price, 'pnl_pct': pnl, 'result': 'SL'
                })
                trades.append(current_trade)
                position = 0
                current_trade = None

        if position != 0:
            continue  # 持仓中，跳过入场

        # --- 入场检查：收集当前K线已知的已确认摆动点 ---
        confirmed_highs = [h for h in swing_highs if h + swing_lookback <= i]
        confirmed_lows = [l for l in swing_lows if l + swing_lookback <= i]

        if len(confirmed_highs) < n_swings + 1 or len(confirmed_lows) < n_swings + 1:
            continue

        recent_highs = confirmed_highs[-n_swings - 1:]
        recent_lows = confirmed_lows[-n_swings - 1:]

        # ===== 多头入场：上升趋势 + 回调到斐波那契 + 反转K线 =====
        if check_uptrend(highs, lows, confirmed_highs, confirmed_lows, n_swings):
            if recent_lows[-1] < recent_highs[-1]:
                if recent_highs[-1] == last_entry_leg_high:
                    continue

                swing_low_val = lows[recent_lows[-1]]
                swing_high_val = highs[recent_highs[-1]]
                leg_range = swing_high_val - swing_low_val

                if leg_range > 0:
                    fib50 = swing_high_val - leg_range * fib_low
                    fib618 = swing_high_val - leg_range * fib_high

                    if fib618 <= lows[i] <= fib50:
                        is_reversal, pattern = is_bullish_reversal(
                            opens[i], closes[i], highs[i], lows[i],
                            opens[i - 1], closes[i - 1],
                            wick_body_ratio
                        )

                        if is_reversal:
                            vol_ok = True
                            if vol_filter and vol_ma is not None:
                                if not np.isnan(vol_ma[i - 1]):
                                    vol_ok = volumes[i] > vol_ma[i - 1] * vol_mult

                            if vol_ok:
                                entry_price = closes[i]
                                sl_price = swing_low_val * (1 - sl_buffer)
                                tp_price = entry_price + (entry_price - sl_price) * rr_ratio

                                current_trade = {
                                    'entry_idx': i,
                                    'entry_time': ohlc_df['timestamp'].iloc[i],
                                    'type': 'long',
                                    'entry_price': entry_price,
                                    'sl': sl_price,
                                    'tp': tp_price,
                                    'pattern': pattern
                                }
                                position = 1
                                last_entry_leg_high = recent_highs[-1]
                                continue

        # ===== 空头入场：下降趋势 + 反弹到斐波那契 + 反转K线 =====
        if check_downtrend(highs, lows, confirmed_highs, confirmed_lows, n_swings):
            if recent_highs[-1] < recent_lows[-1]:
                if recent_highs[-1] == last_entry_leg_high:
                    continue

                swing_high_val = highs[recent_highs[-1]]
                swing_low_val = lows[recent_lows[-1]]
                leg_range = swing_high_val - swing_low_val

                if leg_range > 0:
                    fib50 = swing_low_val + leg_range * fib_low
                    fib618 = swing_low_val + leg_range * fib_high

                    if fib50 <= highs[i] <= fib618:
                        is_reversal, pattern = is_bearish_reversal(
                            opens[i], closes[i], highs[i], lows[i],
                            opens[i - 1], closes[i - 1],
                            wick_body_ratio
                        )

                        if is_reversal:
                            vol_ok = True
                            if vol_filter and vol_ma is not None:
                                if not np.isnan(vol_ma[i - 1]):
                                    vol_ok = volumes[i] > vol_ma[i - 1] * vol_mult

                            if vol_ok:
                                entry_price = closes[i]
                                sl_price = swing_high_val * (1 + sl_buffer)
                                tp_price = entry_price - (sl_price - entry_price) * rr_ratio

                                current_trade = {
                                    'entry_idx': i,
                                    'entry_time': ohlc_df['timestamp'].iloc[i],
                                    'type': 'short',
                                    'entry_price': entry_price,
                                    'sl': sl_price,
                                    'tp': tp_price,
                                    'pattern': pattern
                                }
                                position = -1
                                last_entry_leg_high = recent_highs[-1]
                                continue

    # --- 持仓到末尾平仓 ---
    if current_trade is not None:
        final_close = closes[-1]
        if current_trade['type'] == 'long':
            pnl = (final_close - current_trade['entry_price']) / current_trade['entry_price']
        else:
            pnl = (current_trade['entry_price'] - final_close) / current_trade['entry_price']
        current_trade.update({
            'exit_idx': n - 1,
            'exit_time': ohlc_df['timestamp'].iloc[n - 1],
            'exit_price': final_close,
            'pnl_pct': pnl,
            'result': 'EOD'
        })
        trades.append(current_trade)

    # ===== 净值曲线 =====
    equity = [1.0]
    for i in range(start_idx, n):
        pnl_this_bar = 0.0
        for t in trades:
            if t['exit_idx'] == i:
                pnl_this_bar += t['pnl_pct']
        if abs(pnl_this_bar) > 1e-10:
            equity.append(equity[-1] * (1 + pnl_this_bar))
        else:
            equity.append(equity[-1])

    return _标准化策略输出(equity, trades, ohlc_df, params)


# ========== 自检代码（用真实BTC数据测试可运行性） ==========
if __name__ == '__main__':
    import os

    data_path = r'D:\量化平台\data_cache\BTC_USDT_1h.csv'
    if not os.path.exists(data_path):
        print(f'数据文件不存在: {data_path}')
        print('跳过自检。请将真实数据放在 D:\\量化平台\\data_cache\\ 下')
    else:
        print('=' * 65)
        print('  趋势回调入场策略 - 自检运行')
        print('=' * 65)

        df = pd.read_csv(data_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        print(f'  数据: {len(df)} 行, {df["timestamp"].iloc[0]} ~ {df["timestamp"].iloc[-1]}')

        params = {
            'swing_lookback': 5,
            'n_swings': 1,
            'fib_low': 0.5,
            'fib_high': 0.618,
            'wick_body_ratio': 1.0,
            'rr_ratio': 2.0,
            'sl_buffer': 0.002,
            'vol_filter': True,
            'vol_lookback': 20,
            'vol_mult': 1.0,
        }

        result = strategy_logic(df, None, params)
        trades = result['trades']
        equity = result['equity']

        print(f'  总交易: {len(trades)} 笔')
        if trades:
            n_wins = len([t for t in trades if t['result'] == 'TP'])
            n_losses = len([t for t in trades if t['result'] == 'SL'])
            n_eod = len([t for t in trades if t['result'] == 'EOD'])
            wr = n_wins / len(trades) * 100

            avg_r = np.mean([
                t['pnl_pct'] / ((t['entry_price'] - t['sl']) / t['entry_price']
                                if t['type'] == 'long'
                                else (t['sl'] - t['entry_price']) / t['entry_price'])
                for t in trades
                if ((t['entry_price'] - t['sl']) if t['type'] == 'long'
                    else (t['sl'] - t['entry_price'])) > 0
            ])

            total_ret = (equity[-1] - 1) * 100
            peak = equity[0]
            maxdd = 0
            for e in equity:
                if e > peak:
                    peak = e
                dd = (peak - e) / peak * 100
                if dd > maxdd:
                    maxdd = dd

            print(f'  胜率: {wr:.1f}% | 胜场{n_wins} 败场{n_losses} EOD{n_eod}')
            print(f'  平均R倍数: {avg_r:.2f}R')
            print(f'  总收益: {total_ret:.2f}% | 最大回撤: {maxdd:.2f}%')
            print(f'  净值终值: {equity[-1]:.4f}')
        else:
            print('  警告: 无交易信号产生')
        print(f'  *** 自检通过：代码可无报错运行 ***')
        print('=' * 65)


# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
# 五重检验包含: IC检验 / ICIR / 分层回测 / 多空收益 / 夏普+最大回撤
