# -*- coding: utf-8 -*-
# 适用行情: TRENDING_UP, TRENDING_DOWN（趋势末端衰竭反转）
# 不适行情: RANGING, HIGH_VOLATILITY（震荡期无趋势可供衰竭，高波动期反转确认易失真）
# 交易频率: 低中频H1
# 核心逻辑: 价格创N周期新高/低 + RSI背离（价格极值但RSI未确认）+
#         衰竭K线（小实体/长影线）→ 反向入场 → 止损极值点 → 2R止盈。
#         赚的是趋势末端动量耗尽的反转利润。
# 标的限制: BTC（主力，WR=44.3%, PF=1.88）, ETH（可用，WR=41.2%, PF=1.35）
#          SOL（不推荐，WR=34.6%, PF=1.38 回撤32.7%）
# 与其他策略互补: 全库唯一做趋势反转的策略（其他3个趋势策略全为顺势追入）
#
# 【真实数据验证（2025-05 ~ 2026-05，1h）】
#   BTC: lb=75, rr=2.0, dw=5, v OFF → WR=44.3%, RR=2.00:1, n=106, +62.7%, dd=15.9%, PF=1.88
#   BTC(高质量): lb=100, rr=2.0, dw=10, v1.5 → WR=45.8%, RR=2.00:1, n=48, +50.7%, dd=13.1%, PF=2.18
#   ETH: lb=100, rr=2.0, dw=5, v OFF → WR=41.2%, RR=2.00:1, n=114, +44.3%, dd=17.9%, PF=1.35
#   参数来源: 质检热力图确认 BTC wick_ratio=0.3 时收益约 +83.4%，ETH/SOL 保持 0.5
#
# 【核心参数说明】
#   lookback: 创N周期新高/低的窗口。越大越苛刻（BTC=75, ETH=100）
#   divergence_window: RSI背离检测窗口，当前极值与前dw根前的极值对比RSI
#   wick_ratio/body_ratio: 衰竭K线定义（影线占比>wick_r 或 实体占比<body_r）
#   vol_filter + vol_mult: 高质量版开启成交量放大确认
#
# === 预置参数包 ===
# PARAMS_BTC = {'lookback': 75, 'rr': 2.0, 'wick_ratio': 0.3, 'body_ratio': 0.3, 'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0}
# PARAMS_BTC_HQ = {'lookback': 100, 'rr': 2.0, 'wick_ratio': 0.3, 'body_ratio': 0.3, 'divergence_window': 10, 'vol_filter': True, 'vol_mult': 1.5}
# PARAMS_ETH = {'lookback': 100, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.2, 'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0}

import pandas as pd
import numpy as np
import os


PARAMS_BTC = {'lookback': 75, 'rr': 2.0, 'wick_ratio': 0.3, 'body_ratio': 0.3, 'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0}
PARAMS_ETH = {'lookback': 100, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.2, 'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0}
PARAMS_SOL = {'lookback': 75, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.3, 'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0}


def _识别参数标的(params):
    """从参数中的交易对字段识别标的；缺省按BTC数据集处理。"""
    text = str(params.get('symbol') or params.get('pair') or params.get('market') or params.get('asset') or 'BTC').upper()
    if 'ETH' in text:
        return 'ETH'
    if 'SOL' in text:
        return 'SOL'
    return 'BTC'


def _合并分标参数(params):
    """先装载质检最优分标参数，再用界面传入参数覆盖。"""
    params = dict(params or {})
    presets = {'BTC': PARAMS_BTC, 'ETH': PARAMS_ETH, 'SOL': PARAMS_SOL}
    merged = dict(presets.get(_识别参数标的(params), PARAMS_BTC))
    merged.update(params)
    return merged


def _标准化策略输出(equity, trades, ohlc_df, params):
    """把策略内部净值统一成完整K线长度、以本金起步的资金曲线。"""
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


def strategy_logic(ohlc_df, factor_df, params):
    """
    趋势衰竭反转策略 (Trend Exhaustion Reversal)

    逻辑：
    1. 检测价格创N周期新高（潜在顶部衰竭）或新低（潜在底部衰竭）
    2. RSI背离确认：价格新高但RSI不创新高（顶背离），或价格新低RSI不创新低（底背离）
    3. 衰竭K线确认：创极值的那根K线实体占比小（<body_ratio）或影线长（>wick_ratio）
    4. 确认K线收盘突破衰竭K线中点 → 反转入场
    5. 止损 = 衰竭极值点（±0.1%缓冲）
    6. 止盈 = entry + RR × (entry - sl)

    赚钱逻辑：趋势末端动量耗尽→价格+RSI双重背离→衰竭K线→反转方向。

    输入:
        ohlc_df: DataFrame，列 ['open','high','low','close','volume']
        factor_df: 因子数据DataFrame（本策略不使用）
        params: 参数字典
            lookback: 创N周期新高/低的窗口（默认75）
            rsi_period: RSI周期（默认14）
            rr: 盈亏比（默认2.0）
            wick_ratio: 影线占比阈值（默认0.5）
            body_ratio: 实体占比阈值（默认0.3，越小越衰竭）
            divergence_window: RSI背离检测窗口（默认5）
            vol_filter: 成交量过滤开关（默认False）
            vol_mult: 成交量放大倍数（默认0即关闭）

    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    params = _合并分标参数(params)
    lookback = params.get('lookback', 75)
    rsi_period = params.get('rsi_period', 14)
    rr = params.get('rr', 2.0)
    wick_ratio = params.get('wick_ratio', 0.5)
    body_ratio = params.get('body_ratio', 0.3)
    divergence_window = params.get('divergence_window', 5)
    vol_filter = params.get('vol_filter', False)
    vol_mult = params.get('vol_mult', 0)

    n = len(ohlc_df)
    high_arr = ohlc_df['high'].values
    low_arr = ohlc_df['low'].values
    open_arr = ohlc_df['open'].values
    close_arr = ohlc_df['close'].values
    volume_arr = ohlc_df['volume'].values

    # ====== 预计算RSI ======
    rsi = np.full(n, np.nan)
    gain = np.zeros(n)
    loss_ = np.zeros(n)
    for i in range(1, n):
        delta = close_arr[i] - close_arr[i - 1]
        if delta > 0:
            gain[i] = delta
        else:
            loss_[i] = -delta

    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    avg_gain[rsi_period] = np.mean(gain[1:rsi_period + 1])
    avg_loss[rsi_period] = np.mean(loss_[1:rsi_period + 1])
    for i in range(rsi_period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (rsi_period - 1) + gain[i]) / rsi_period
        avg_loss[i] = (avg_loss[i - 1] * (rsi_period - 1) + loss_[i]) / rsi_period
    for i in range(rsi_period, n):
        if avg_loss[i] == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)

    # ====== 成交量MA ======
    vol_ma = np.full(n, np.nan)
    if vol_filter and vol_mult > 0:
        for i in range(20, n):
            vol_ma[i] = np.mean(volume_arr[i - 20:i])

    # ====== 回测主循环 ======
    trades = []
    equity = [1.0]
    position = 0
    current_trade = None
    start_idx = max(400, lookback * 3, rsi_period * 3)

    for i in range(start_idx, n):
        # --- 持仓管理 ---
        if position == 1:
            if high_arr[i] >= current_trade['tp']:
                pnl = (current_trade['tp'] - current_trade['entry']) / current_trade['entry']
                current_trade.update({'ei': i, 'pnl': pnl, 'exit_reason': 'TP'})
                trades.append(current_trade)
                position = 0
                current_trade = None
            elif low_arr[i] <= current_trade['sl']:
                pnl = (current_trade['sl'] - current_trade['entry']) / current_trade['entry']
                current_trade.update({'ei': i, 'pnl': pnl, 'exit_reason': 'SL'})
                trades.append(current_trade)
                position = 0
                current_trade = None
        elif position == -1:
            if low_arr[i] <= current_trade['tp']:
                pnl = (current_trade['entry'] - current_trade['tp']) / current_trade['entry']
                current_trade.update({'ei': i, 'pnl': pnl, 'exit_reason': 'TP'})
                trades.append(current_trade)
                position = 0
                current_trade = None
            elif high_arr[i] >= current_trade['sl']:
                pnl = (current_trade['entry'] - current_trade['sl']) / current_trade['entry']
                current_trade.update({'ei': i, 'pnl': pnl, 'exit_reason': 'SL'})
                trades.append(current_trade)
                position = 0
                current_trade = None

        # --- 入场检测 ---
        if position == 0:
            if np.isnan(rsi[i]):
                pnl_day = sum(t['pnl'] for t in trades if t['ei'] == i)
                equity.append(equity[-1] * (1 + pnl_day) if abs(pnl_day) > 1e-10 else equity[-1])
                continue

            # 顶背离检测（bearish reversal setup）
            # 左侧窗口: [i-lb-dw, i-dw)，右侧窗口: [i-dw, i]
            left_end = i - divergence_window
            left_start = i - lookback - divergence_window

            if left_start >= divergence_window:
                # 左侧最高价
                left_high = np.max(high_arr[left_start:left_end])
                left_high_idx = left_start + np.argmax(high_arr[left_start:left_end])
                # 右侧最高价
                right_high = np.max(high_arr[left_end:i])
                right_high_idx = left_end + np.argmax(high_arr[left_end:i])

                # 顶背离: 价格右侧高 >= 左侧高，但RSI右侧 < 左侧*0.98
                bear_divergence = (
                    right_high >= left_high and
                    not np.isnan(rsi[left_high_idx]) and
                    not np.isnan(rsi[right_high_idx]) and
                    rsi[right_high_idx] < rsi[left_high_idx] * 0.98
                )

                if bear_divergence:
                    extreme_idx = right_high_idx
                    bar_range = high_arr[extreme_idx] - low_arr[extreme_idx]
                    if bar_range > 0:
                        body_abs = abs(close_arr[extreme_idx] - open_arr[extreme_idx])
                        body_pct = body_abs / bar_range
                        wick_pct = 1.0 - body_pct
                        is_exhaustion = (body_pct < body_ratio) or (wick_pct > wick_ratio)

                        if is_exhaustion:
                            # 成交量确认
                            vol_ok = True
                            if vol_filter and vol_mult > 0:
                                if not np.isnan(vol_ma[i - 1]):
                                    vol_ok = volume_arr[i - 1] >= vol_ma[i - 1] * vol_mult
                                else:
                                    vol_ok = False

                            # 确认: 收盘跌破衰竭K线中点
                            if vol_ok:
                                mid_price = (high_arr[extreme_idx] + low_arr[extreme_idx]) / 2.0
                                if close_arr[i - 1] < mid_price:
                                    entry_price = close_arr[i]
                                    sl_price = high_arr[extreme_idx] * 1.002
                                    tp_price = entry_price - (sl_price - entry_price) * rr
                                    if tp_price < entry_price:
                                        current_trade = {
                                            'ei': i, 'type': 'short',
                                            'entry': entry_price, 'sl': sl_price, 'tp': tp_price
                                        }
                                        position = -1
                                        pnl_day = sum(t['pnl'] for t in trades if t['ei'] == i)
                                        equity.append(equity[-1] * (1 + pnl_day) if abs(pnl_day) > 1e-10 else equity[-1])
                                        continue

            # 底背离检测（bullish reversal setup）
            if position == 0:
                left_low = np.min(low_arr[left_start:left_end])
                left_low_idx = left_start + np.argmin(low_arr[left_start:left_end])
                right_low = np.min(low_arr[left_end:i])
                right_low_idx = left_end + np.argmin(low_arr[left_end:i])

                bull_divergence = (
                    right_low <= left_low and
                    not np.isnan(rsi[left_low_idx]) and
                    not np.isnan(rsi[right_low_idx]) and
                    rsi[right_low_idx] > rsi[left_low_idx] * 1.02
                )

                if bull_divergence:
                    extreme_idx = right_low_idx
                    bar_range = high_arr[extreme_idx] - low_arr[extreme_idx]
                    if bar_range > 0:
                        body_abs = abs(close_arr[extreme_idx] - open_arr[extreme_idx])
                        body_pct = body_abs / bar_range
                        wick_pct = 1.0 - body_pct
                        is_exhaustion = (body_pct < body_ratio) or (wick_pct > wick_ratio)

                        if is_exhaustion:
                            vol_ok = True
                            if vol_filter and vol_mult > 0:
                                if not np.isnan(vol_ma[i - 1]):
                                    vol_ok = volume_arr[i - 1] >= vol_ma[i - 1] * vol_mult
                                else:
                                    vol_ok = False

                            if vol_ok:
                                mid_price = (high_arr[extreme_idx] + low_arr[extreme_idx]) / 2.0
                                if close_arr[i - 1] > mid_price:
                                    entry_price = close_arr[i]
                                    sl_price = low_arr[extreme_idx] * 0.998
                                    tp_price = entry_price + (entry_price - sl_price) * rr
                                    if tp_price > entry_price:
                                        current_trade = {
                                            'ei': i, 'type': 'long',
                                            'entry': entry_price, 'sl': sl_price, 'tp': tp_price
                                        }
                                        position = 1

        # --- 更新净值 ---
        pnl_day = sum(t['pnl'] for t in trades if t['ei'] == i)
        equity.append(equity[-1] * (1 + pnl_day) if abs(pnl_day) > 1e-10 else equity[-1])

    # --- 未平仓处理 ---
    if current_trade is not None:
        if current_trade['type'] == 'long':
            pnl = (close_arr[-1] - current_trade['entry']) / current_trade['entry']
        else:
            pnl = (current_trade['entry'] - close_arr[-1]) / current_trade['entry']
        current_trade.update({'ei': n - 1, 'pnl': pnl, 'exit_reason': 'EOD'})
        trades.append(current_trade)

    return _标准化策略输出(equity, trades, ohlc_df, params)


# ============================================================
#  自检程序
# ============================================================

if __name__ == '__main__':
    print("=" * 70)
    print("  趋势衰竭反转 - self-test")
    print("=" * 70)

    data_dir = r'D:\量化平台\data_cache'
    symbols = {
        'BTC': (os.path.join(data_dir, 'BTC_USDT_1h.csv'), {
            'lookback': 75, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.3,
            'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0
        }),
        'ETH': (os.path.join(data_dir, 'ETH_USDT_1h.csv'), {
            'lookback': 100, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.2,
            'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0
        }),
        'SOL': (os.path.join(data_dir, 'SOL_USDT_1h.csv'), {
            'lookback': 75, 'rr': 2.0, 'wick_ratio': 0.5, 'body_ratio': 0.2,
            'divergence_window': 5, 'vol_filter': False, 'vol_mult': 0
        }),
    }

    for sym, (pth, params) in symbols.items():
        if not os.path.exists(pth):
            print(f"\n  [SKIP] {sym}: data not found")
            continue

        df = pd.read_csv(pth)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        n = len(df)
        print(f"\n  --- [{sym}] rows={n}, {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]} ---")

        result = strategy_logic(df, None, params)
        trades = result['trades']
        equity = result['equity']

        ntr = len(trades)
        if ntr < 5:
            print(f"  [FAIL] n_trades={ntr} < 5")
            continue

        nw = len([t for t in trades if t['pnl'] > 0])
        wr = nw / ntr * 100

        rm = []
        for t in trades:
            r = (t['entry'] - t['sl']) / t['entry'] if t['type'] == 'long' else (t['sl'] - t['entry']) / t['entry']
            if r > 0:
                rm.append(t['pnl'] / r)
        avgR = np.mean(rm) if rm else 0
        aw = np.mean([r for r in rm if r > 0]) if any(r > 0 for r in rm) else 0
        al = np.mean([abs(r) for r in rm if r <= 0]) if any(r <= 0 for r in rm) else 1
        rr_val = aw / al if al > 0 else 0
        ret = (equity[-1] - 1) * 100
        peak = equity[0]
        maxdd = 0
        for e in equity:
            if e > peak:
                peak = e
            d = (peak - e) / peak * 100
            if d > maxdd:
                maxdd = d
        pw = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        pl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0)) if any(t['pnl'] < 0 for t in trades) else 1
        pf = pw / pl
        ls = [t for t in trades if t['type'] == 'long']
        ss = [t for t in trades if t['type'] == 'short']
        lwr = len([t for t in ls if t['pnl'] > 0]) / len(ls) * 100 if ls else 0
        swr = len([t for t in ss if t['pnl'] > 0]) / len(ss) * 100 if ss else 0

        status = "PASS" if wr >= 40 and pf > 1.5 else "WARN" if wr >= 35 and pf >= 1.3 else "FAIL"
        print(f"  {status} n={ntr} WR={wr:.1f}% avgR={avgR:.2f}R RR={rr_val:.2f}:1")
        print(f"  ret={ret:+.1f}% dd={maxdd:.1f}% PF={pf:.2f} L{len(ls)}/S{len(ss)} LWR={lwr:.0f}% SWR={swr:.0f}%")

    print("\n" + "=" * 70)
    print("  [OK] self-test done")
    print("=" * 70)

# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
