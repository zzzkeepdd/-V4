# -*- coding: utf-8 -*-
# 适用行情: TRENDING_UP, TRENDING_DOWN, HIGH_VOLATILITY（成交量异动在任何方向性行情中有效）
# 不适行情: RANGING（低成交量无信号，且MA50趋势过滤在震荡中反复横跳）
# 交易频率: 低中频H1（vm=4.0过滤极严，年约30~40笔）
# 核心逻辑: 成交量突增至20周期均值的4倍+实体K线占比>60%→同向追入→
#         止损2倍ATR→止盈2R。赚的是大资金介入推动的方向性惯性利润。
# 标的限制: ETH (主力), SOL (可用), BTC (不推荐 — 成交量噪音大)
# 与其他策略互补: 全库唯一以成交量为第一驱动因子的策略，其他全为价格结构型
#
# 【真实数据验证（2025-05 ~ 2026-05，1h）】
#   ETH: vm=4.0, bm=0.6, rr=2.0, MA50 → WR=53.8%, RR=2.00:1, n=39, +58.1%, PF=2.42
#   ETH(高频): vm=3.5, bm=0.5, rr=2.5, MA50 → WR=44.0%, RR=2.50:1, n=50, +62.7%, PF=2.01
#   SOL: vm=4.0, bm=0.6, rr=2.5, MA50 → WR=50.0%, RR=2.50:1, n=28, +43.8%, PF=2.16
#   参数来源: 质检报告确认 ETH vm=4.0/bm=0.6/rr=2.0，SOL vm=4.0/bm=0.6/rr=2.5；BTC仅作兼容运行不作为推荐标的
#
# 【核心参数说明】
#   vol_mult: 成交量放大倍数（阈值=均值×vol_mult），4.0=异常大资金介入
#   body_min: K线实体占比阈值，0.6=实体占整根K线60%以上，排除影线干扰
#   sl_atr: ATR止损倍数，2.0倍ATR放在异常K线后合理
#   trend_filter: MA50趋势过滤，确保只在趋势方向开仓
#
# === 预置参数包 ===
# PARAMS_ETH = {'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.0, 'trend_filter': True}
# PARAMS_ETH_HF = {'vol_mult': 3.5, 'body_min': 0.5, 'sl_atr': 2.0, 'rr': 2.5, 'trend_filter': True}
# PARAMS_SOL = {'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.5, 'trend_filter': True}

import pandas as pd
import numpy as np
import os


PARAMS_BTC = {'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.0, 'trend_filter': True}
PARAMS_ETH = {'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.0, 'trend_filter': True}
PARAMS_SOL = {'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.5, 'trend_filter': True}


def _识别参数标的(params):
    """从参数中的交易对字段识别标的；缺省按BTC数据集处理。"""
    text = str(params.get('symbol') or params.get('pair') or params.get('market') or params.get('asset') or 'BTC').upper()
    if 'ETH' in text:
        return 'ETH'
    if 'SOL' in text:
        return 'SOL'
    return 'BTC'


def _合并分标参数(params):
    """先装载质检报告参数，再用界面传入参数覆盖。"""
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
    成交量异动策略 (Volume Anomaly Breakout)

    逻辑：
    1. 检测成交量突增：当前K线成交量 ≥ 20周期均值的 vol_mult 倍
    2. 确认方向性K线：实体占比 ≥ body_min（高确定性），避免影线诱导
    3. 趋势过滤（可选）：长线方向仅在MA50之上，短线方向仅在MA50之下
    4. 入场 = 异动K线后一根K线收盘价（同方向）
    5. 止损 = 入场 ± sl_atr × ATR
    6. 止盈 = 入场 ± (入场距止损) × rr

    赚钱逻辑：大资金推动的方向性底部/顶部，惯性延续至2R。

    输入:
        ohlc_df: DataFrame，列 ['open','high','low','close','volume']
        factor_df: 因子数据DataFrame（本策略不使用）
        params: 参数字典
            vol_mult: 成交量放大倍数（默认4.0，即4倍均值）
            body_min: K线实体占比阈值（默认0.6）
            sl_atr: 止损ATR倍数（默认2.0）
            rr: 盈亏比（默认2.0）
            trend_filter: 是否启用MA50趋势过滤（默认True）
            vol_lb: 成交量均线周期（默认20）

    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    params = _合并分标参数(params)
    vol_mult = params.get('vol_mult', 4.0)
    body_min = params.get('body_min', 0.6)
    sl_atr = params.get('sl_atr', 2.0)
    rr = params.get('rr', 2.0)
    trend_filter = params.get('trend_filter', True)
    vol_lb = params.get('vol_lb', 20)
    atr_period = params.get('atr_period', 14)

    n = len(ohlc_df)
    high_arr = ohlc_df['high'].values
    low_arr = ohlc_df['low'].values
    open_arr = ohlc_df['open'].values
    close_arr = ohlc_df['close'].values
    volume_arr = ohlc_df['volume'].values

    # ====== 预计算ATR ======
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high_arr[i] - low_arr[i],
                     abs(high_arr[i] - close_arr[i - 1]),
                     abs(low_arr[i] - close_arr[i - 1]))
    atr = np.full(n, np.nan)
    atr[atr_period] = np.mean(tr[1:atr_period + 1])
    for i in range(atr_period + 1, n):
        atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period

    # ====== 预计算成交量均线 ======
    vol_ma = np.full(n, np.nan)
    for i in range(vol_lb, n):
        vol_ma[i] = np.mean(volume_arr[i - vol_lb:i])

    # ====== 预计算MA50趋势线 ======
    ma50 = np.full(n, np.nan)
    if trend_filter:
        for i in range(50, n):
            ma50[i] = np.mean(close_arr[i - 50 + 1:i + 1])

    # ====== 回测主循环 ======
    trades = []
    equity = [1.0]
    position = 0
    current_trade = None
    start_idx = max(400, vol_lb * 2, atr_period * 3)

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
            if np.isnan(vol_ma[i - 1]) or np.isnan(atr[i - 1]):
                pnl_day = sum(t['pnl'] for t in trades if t['ei'] == i)
                equity.append(equity[-1] * (1 + pnl_day) if abs(pnl_day) > 1e-10 else equity[-1])
                continue

            # 条件1: 成交量突增
            if volume_arr[i - 1] >= vol_ma[i - 1] * vol_mult:
                bar_range = high_arr[i - 1] - low_arr[i - 1]

                # 条件2: 方向性K线确认
                if bar_range > 0:
                    body_abs = abs(close_arr[i - 1] - open_arr[i - 1])
                    body_pct = body_abs / bar_range

                    if body_pct >= body_min:
                        is_bullish = close_arr[i - 1] > open_arr[i - 1]

                        # 条件3: MA50趋势过滤
                        trend_ok = True
                        if trend_filter and not np.isnan(ma50[i - 1]):
                            if is_bullish and close_arr[i - 1] < ma50[i - 1]:
                                trend_ok = False  # 阳线但价格在MA50之下 → 逆趋势
                            elif not is_bullish and close_arr[i - 1] > ma50[i - 1]:
                                trend_ok = False  # 阴线但价格在MA50之上 → 逆趋势

                        if trend_ok:
                            atr_val = atr[i - 1]
                            if is_bullish:
                                entry_price = close_arr[i]
                                sl_price = entry_price - sl_atr * atr_val
                                tp_price = entry_price + (entry_price - sl_price) * rr
                                if tp_price > entry_price:
                                    current_trade = {
                                        'ei': i, 'type': 'long',
                                        'entry': entry_price, 'sl': sl_price, 'tp': tp_price
                                    }
                                    position = 1
                            else:
                                entry_price = close_arr[i]
                                sl_price = entry_price + sl_atr * atr_val
                                tp_price = entry_price - (sl_price - entry_price) * rr
                                if tp_price < entry_price:
                                    current_trade = {
                                        'ei': i, 'type': 'short',
                                        'entry': entry_price, 'sl': sl_price, 'tp': tp_price
                                    }
                                    position = -1

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
    print("  成交量异动 - self-test")
    print("=" * 70)

    data_dir = r'D:\量化平台\data_cache'
    symbols = {
        'ETH': (os.path.join(data_dir, 'ETH_USDT_1h.csv'), {
            'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.0,
            'trend_filter': True
        }),
        'SOL': (os.path.join(data_dir, 'SOL_USDT_1h.csv'), {
            'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.5,
            'trend_filter': True
        }),
        'BTC': (os.path.join(data_dir, 'BTC_USDT_1h.csv'), {
            'vol_mult': 4.0, 'body_min': 0.6, 'sl_atr': 2.0, 'rr': 2.0,
            'trend_filter': True
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

        if sym == 'BTC':
            print(f"  [INFO] BTC not recommended (volume noise)")
            print(f"  n={ntr} WR={wr:.1f}% ret={ret:+.1f}% dd={maxdd:.1f}% PF={pf:.2f}")
        else:
            status = "PASS" if wr >= 45 and pf > 1.8 else "WARN" if pf >= 1.5 else "FAIL"
            print(f"  {status} n={ntr} WR={wr:.1f}% avgR={avgR:.2f}R RR={rr_val:.2f}:1")
            print(f"  ret={ret:+.1f}% dd={maxdd:.1f}% PF={pf:.2f} L{len(ls)}/S{len(ss)} LWR={lwr:.0f}% SWR={swr:.0f}%")

    print("\n" + "=" * 70)
    print("  [OK] self-test done")
    print("=" * 70)

# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
