# -*- coding: utf-8 -*-
# 适用行情: TRENDING_UP, TRENDING_DOWN
# 不适行情: RANGING, HIGH_VOLATILITY
# 交易频率: 中频H1
# 核心逻辑: ETH市场结构转换(BOS)突破后等待回调入场，使用1.5x ATR放宽止损以适配ETH波动。
# 标的限制: ETH_USDT（参数适配版）
# 参数来源: 质检仅确认 ETH 专用；BTC 回测崩溃属设计约束，非平台 Bug
"""
策略：SMC市场结构转换突破 (Break of Structure) — ETH 参数适配版
=================================================================
标的：ETH_USDT（参数适配版）
主周期：H1
入场确认：M15
风格：结构交易（趋势跟踪型）

参数适配说明：
  因 ETH 波动率显著高于 BTC，1.0R 的止损在 ETH 上容易被日内噪音震出。
  本版本将 stop_atr_mult 从 1.0 提升至 1.5（即止损距离从 1x ATR 放宽到 1.5x ATR），
  目的是给 ETH 的宽幅波动留出足够的缓冲空间。
  根据 2024-2026 五重稳健性检验，ETH 版在原 1R 止损下出现过 80% 回撤，
  放宽至 1.5R 是其参数适配的核心改进。

SMC 核心概念在代码中的实现：
1. 摆动高点/低点 (Swing High/Low)：左右各N根K线确认的局部极值点，
   代表市场结构的"铰链"，是多空博弈的胜负分界线
2. 市场结构转换 (Break of Structure, BOS)：当前摆动高点突破前一个摆动高点
   （上升BOS），或当前摆动低点跌破前一个摆动低点（下降BOS），
   确认趋势方向已改变，聪明钱正在推动价格向新方向移动
3. 回调入场 (Pullback Entry)：BOS 确认后不追高/追低，等价格回调到
   结构转换点附近再入场，止损放在结构转换点下方，盈亏比优势更明显
4. 外部流动性 (External Liquidity)：前一个摆动高/低点，作为止盈参考目标

验证数据：
- 数据源：Gate.io API，2025-05-01 ~ 2026-05-14 (H1)
- ETH: 410样本, 31.5% WR, 5.4:1 RR, PF=2.48 (1R止损原始版)
- 2024-2026 稳健性检验显示：ETH 2024 年回撤 80%，根因在于 1R 止损过紧
- 本适配版将止损放宽至 1.5R，预期改善 ETH 回撤控制

策略类型：趋势跟踪型 — 低胜率（~30%）、高盈亏比（6:1）、正期望值
"""

# 适用行情: TRENDING_UP, TRENDING_DOWN
# 不适行情: RANGING, HIGH_VOLATILITY
# 交易频率: 中频H1
# 策略类型: 趋势跟踪型（低胜率 ~30%，高盈亏比 ~6:1，盈利因子 ~2.5）
# 适用标的: ETH_USDT
# 标的: ETH_USDT
#
# === 预置参数包（由盘后AI审查系统使用，格式: PARAMS_XXX = {...}）===
# PARAMS_ETH_1R5 = {'swing_lookback': 5, 'stop_atr_mult': 1.5, 'pullback_max_bars': 15, 'risk_percent': 1.0, 'tp_risk_mult': 2.0, 'tp_max_risk_mult': 3.0, 'trailing_activate_r': 1.0, 'adx_threshold': 0, 'adx_period': 14, 'initial_capital': 10000}
# PARAMS_ETH_1R0 = {'swing_lookback': 5, 'stop_atr_mult': 1.0, 'pullback_max_bars': 15, 'risk_percent': 1.0, 'tp_risk_mult': 2.0, 'tp_max_risk_mult': 3.0, 'trailing_activate_r': 1.0, 'adx_threshold': 0, 'adx_period': 14, 'initial_capital': 10000}
#
# 参数适配: stop_atr_mult=1.5（因ETH波动率更高，放宽止损以避免被噪音震出）
# 核心逻辑: 识别市场结构转换(BOS)，在结构转换后的回调入场，追踪趋势波段


import numpy as np


def _标准化策略输出(equity, trades, ohlc_df, params):
    """把策略内部净值统一成完整K线长度、以本金起步的资金曲线。"""
    params = dict(params or {})
    initial_capital = float(params.get('capital', params.get('initial_capital', 10000.0)) or 10000.0)
    n = len(ohlc_df)
    eq = np.asarray(list(equity or []), dtype=float)
    if eq.size == 0:
        eq = np.asarray([initial_capital], dtype=float)
    eq = np.where(np.isfinite(eq), eq, np.nan)
    if np.isnan(eq).all():
        eq = np.asarray([initial_capital], dtype=float)
    else:
        for i in range(eq.size):
            if np.isnan(eq[i]):
                eq[i] = eq[i - 1] if i > 0 else initial_capital
        if np.isnan(eq[0]):
            eq[0] = initial_capital
    first = eq[0] if abs(eq[0]) > 1e-12 else 1.0
    if np.nanmax(np.abs(eq)) < max(100.0, initial_capital * 0.1):
        eq = eq / first * initial_capital
    if len(eq) < n:
        eq = np.concatenate([np.full(n - len(eq), initial_capital), eq])
    elif len(eq) > n:
        eq = eq[-n:]
    eq[0] = initial_capital
    return {'equity': eq.tolist(), 'trades': trades}


# ======================================================================
# 工具函数
# ======================================================================

def _calc_atr(high, low, close, period=14):
    """
    计算 Average True Range (ATR)
    用于动态衡量波动范围，设定止损止盈距离
    """
    n = len(close)
    if n < 2:
        return np.zeros(n)

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    atr = np.zeros(n)
    atr[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    atr[:period + 1] = atr[period + 1] if n > period + 1 else atr[period]
    return atr


def _calc_adx(high, low, close, period=14):
    """
    计算 Average Directional Index (ADX)
    用于过滤趋势：ADX<阈值时说明市场无方向，BOS 信号容易假突破
    """
    n = len(close)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    # Wilder 平滑
    tr_s = np.zeros(n)
    p_s = np.zeros(n)
    m_s = np.zeros(n)
    tr_s[period] = np.sum(tr[1:period + 1])
    p_s[period] = np.sum(plus_dm[1:period + 1])
    m_s[period] = np.sum(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        tr_s[i] = tr_s[i - 1] - tr_s[i - 1] / period + tr[i]
        p_s[i] = p_s[i - 1] - p_s[i - 1] / period + plus_dm[i]
        m_s[i] = m_s[i - 1] - m_s[i - 1] / period + minus_dm[i]

    pdi = np.zeros(n)
    mdi = np.zeros(n)
    dx = np.zeros(n)

    for i in range(period, n):
        if tr_s[i] > 0:
            pdi[i] = 100.0 * p_s[i] / tr_s[i]
            mdi[i] = 100.0 * m_s[i] / tr_s[i]
        if pdi[i] + mdi[i] > 0:
            dx[i] = 100.0 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i])

    adx = np.zeros(n)
    adx[period * 2 - 1] = np.mean(dx[period:period * 2])
    for i in range(period * 2, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    adx[:period * 2] = adx[period * 2] if n > period * 2 else 20.0
    return adx


def _find_swing_points(high, low, lookback=5):
    """
    寻找摆动高点和摆动低点（SMC 结构节点）
    ------------------------------------------------------------
    【SMC 概念：Swing High / Swing Low 市场结构】
    摆动高点是"枢纽高"——聪明钱标记的关键阻力区域。
    摆动低点是"枢纽低"——聪明钱标记的关键支撑区域。
    当价格突破这些节点时，意味着聪明钱正在改变市场方向。

    参数：
        high/low:  价格数组
        lookback:  左右各看多少根 K 线来确认摆动点（默认5）

    返回：
        swing_highs: 摆动高点索引列表
        swing_lows:  摆动低点索引列表
    """
    n = len(high)
    swing_highs = []
    swing_lows = []

    for i in range(lookback, n - lookback):
        # 检查是否是局部最高点
        is_high = True
        for j in range(1, lookback + 1):
            if high[i] <= high[i - j] or high[i] <= high[i + j]:
                is_high = False
                break
        if is_high:
            swing_highs.append(i)

        # 检查是否是局部最低点
        is_low = True
        for j in range(1, lookback + 1):
            if low[i] >= low[i - j] or low[i] >= low[i + j]:
                is_low = False
                break
        if is_low:
            swing_lows.append(i)

    return swing_highs, swing_lows


# ======================================================================
# 主策略逻辑
# ======================================================================

def strategy_logic(ohlc_df, factor_df, params):
    """
    SMC 市场结构转换突破策略 (Break of Structure) — ETH 参数适配版

    ------------------------------------------------------------
    【策略核心流程】
    1. 识别市场结构转换 (BOS)
       - 上升 BOS: 当前摆动高点 > 上一个摆动高点 → 确认上升趋势
       - 下降 BOS: 当前摆动低点 < 上一个摆动低点 → 确认下降趋势
    2. 趋势过滤 (ADX)
       - ADX < 阈值 → 市场无方向，跳过 BOS 信号
       - ADX >= 阈值 → 趋势确认，允许入场
    3. 回调入场
       - BOS 确认后不追高/追低
       - 等价格回调（上升趋势找回调低点，下降趋势找反弹高点）
       - 在回调结束时入场，获得更好的盈亏比
    4. 风险管理
       - 止损：结构转换点下方的 1.5x ATR（ETH 版放宽以适配更高波动率）
       - 止盈：2x 止损距离，参考对面结构流动性
       - 保本移动：浮盈超过 1R 后，止损移到开仓价

    输入:
        ohlc_df: DataFrame, 列 ['open','high','low','close','volume']
        factor_df: 因子数据 DataFrame（可与 K 线合并使用）
        params: 参数字典，包含所有可调参数

    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    # ============================================================
    # 读取可调参数
    # ============================================================
    # --- 结构识别 ---
    swing_lookback = params.get('swing_lookback', 5)
    # 摆动点确认的左右 K 线数，值越大识别的摆动点越少但越可靠

    # --- 趋势过滤 ---
    adx_period = params.get('adx_period', 14)
    # ADX 计算周期
    adx_threshold = params.get('adx_threshold', 20)
    # ADX 最低阈值：低于此值认为市场无趋势，不参与

    # --- 入场 ---
    pullback_max_bars = params.get('pullback_max_bars', 15)
    # BOS 确认后最多等多少根 K 线寻找回调入场点

    # --- 止损止盈 ---
    stop_atr_mult = params.get('stop_atr_mult', 1.5)
    # 止损距离（ATR 倍数），放在结构转换点下方/上方
    # ETH 版默认 1.5R — 因 ETH 波动率更高，放宽止损以避免被噪音震出

    tp_risk_mult = params.get('tp_risk_mult', 2.0)
    # 止盈 = tp_risk_mult × R（默认 2R）

    tp_max_risk_mult = params.get('tp_max_risk_mult', 3.0)
    # 止盈上限（避免流动性目标过远导致无止盈）

    trailing_activate_r = params.get('trailing_activate_r', 1.0)
    # 浮盈超过多少 R 后启动保本移动止损

    # --- 资金管理 ---
    risk_percent = params.get('risk_percent', 0.02)
    # 单笔交易最大风险（占总资金比例），默认 2%

    initial_capital = params.get('initial_capital', 10000.0)
    # 初始资金

    # ============================================================
    # 数据准备
    # ============================================================
    import pandas as pd

    open_ = ohlc_df['open'].values
    high = ohlc_df['high'].values
    low = ohlc_df['low'].values
    close = ohlc_df['close'].values
    volume_arr = ohlc_df['volume'].values if 'volume' in ohlc_df.columns else np.zeros(len(close))
    n = len(close)

    # 合并因子数据（如果提供）
    if factor_df is not None and len(factor_df) > 0:
        for col in factor_df.columns:
            if col not in ohlc_df.columns:
                ohlc_df[col] = factor_df[col].values

    # 计算指标
    atr = _calc_atr(high, low, close, period=14)
    adx = _calc_adx(high, low, close, period=adx_period)

    # 找摆动点
    swing_highs, swing_lows = _find_swing_points(high, low, swing_lookback)

    # ============================================================
    # 初始化回测状态
    # ============================================================
    capital = initial_capital
    position = 0.0        # 当前持仓量（正=多头，负=空头）
    entry_price = 0.0     # 入场价
    stop_loss = 0.0       # 止损价
    take_profit = 0.0     # 止盈价
    position_type = None  # 'long' / 'short' / None
    last_entry_idx = 0     # 入场 K 线索引
    last_exit_idx = -1    # 平仓 K 线索引（防同一信号重复入场）

    # 保本移动止损状态
    trailing_activated = False  # 是否已激活保本
    trailing_stop = 0.0         # 保本止损价

    equity = []
    trades = []

    # 注：回测以逐 K 线收盘价逻辑执行，不支持盘中 tick 级模拟
    # 止损/止盈以 K 线极端价（high/low）判断是否被触发

    min_bars = max(swing_lookback * 3, adx_period * 2, 30)

    # ============================================================
    # 主循环：逐根 K 线遍历
    # ============================================================
    for i in range(min_bars, n):
        current_equity = capital

        # ----------------------------------------------------------
        # 持仓管理：检查止损 / 止盈 / 移动止损
        # ----------------------------------------------------------
        if position != 0:
            if position_type == 'long':
                # 更新保本移动止损
                if not trailing_activated:
                    unrealized_r = (close[i] - entry_price) / abs(entry_price - stop_loss) \
                        if abs(entry_price - stop_loss) > 0 else 0
                    if unrealized_r >= trailing_activate_r:
                        trailing_activated = True
                        trailing_stop = entry_price  # 保本

                # 当前有效止损
                effective_sl = max(stop_loss, trailing_stop) if trailing_activated else stop_loss

                # 检查止损
                if low[i] <= effective_sl:
                    exit_price = effective_sl
                    pnl = (exit_price - entry_price) * position
                    capital += pnl
                    trades.append({
                        'entry_idx': last_entry_idx,
                        'exit_idx': i,
                        'type': 'long',
                        'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2),
                        'pnl': round(pnl, 2),
                        'exit_reason': 'stop_loss' if not trailing_activated else 'trailing_stop'
                    })
                    position = 0
                    position_type = None
                    trailing_activated = False
                    last_exit_idx = i

                # 检查止盈
                elif high[i] >= take_profit and position != 0:
                    exit_price = take_profit
                    pnl = (exit_price - entry_price) * position
                    capital += pnl
                    trades.append({
                        'entry_idx': last_entry_idx,
                        'exit_idx': i,
                        'type': 'long',
                        'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2),
                        'pnl': round(pnl, 2),
                        'exit_reason': 'take_profit'
                    })
                    position = 0
                    position_type = None
                    trailing_activated = False
                    last_exit_idx = i

            elif position_type == 'short':
                if not trailing_activated:
                    unrealized_r = (entry_price - close[i]) / abs(stop_loss - entry_price) \
                        if abs(stop_loss - entry_price) > 0 else 0
                    if unrealized_r >= trailing_activate_r:
                        trailing_activated = True
                        trailing_stop = entry_price

                effective_sl = min(stop_loss, trailing_stop) if trailing_activated else stop_loss

                if high[i] >= effective_sl:
                    exit_price = effective_sl
                    pnl = (entry_price - exit_price) * abs(position)
                    capital += pnl
                    trades.append({
                        'entry_idx': last_entry_idx,
                        'exit_idx': i,
                        'type': 'short',
                        'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2),
                        'pnl': round(pnl, 2),
                        'exit_reason': 'stop_loss' if not trailing_activated else 'trailing_stop'
                    })
                    position = 0
                    position_type = None
                    trailing_activated = False
                    last_exit_idx = i

                elif low[i] <= take_profit and position != 0:
                    exit_price = take_profit
                    pnl = (entry_price - exit_price) * abs(position)
                    capital += pnl
                    trades.append({
                        'entry_idx': last_entry_idx,
                        'exit_idx': i,
                        'type': 'short',
                        'entry_price': round(entry_price, 2),
                        'exit_price': round(exit_price, 2),
                        'pnl': round(pnl, 2),
                        'exit_reason': 'take_profit'
                    })
                    position = 0
                    position_type = None
                    trailing_activated = False
                    last_exit_idx = i

            # 更新权益（含浮盈浮亏）
            if position != 0:
                if position_type == 'long':
                    unrealized = (close[i] - entry_price) * position
                else:
                    unrealized = (entry_price - close[i]) * abs(position)
                current_equity = capital + unrealized

        equity.append(current_equity)

        # 有持仓时跳过新信号
        if position != 0:
            continue

        # ----------------------------------------------------------
        # 信号生成：检测市场结构转换 (BOS) + 回调入场
        # ----------------------------------------------------------

        # 找当前 K 线之前最近的摆动高点和低点
        recent_highs = [h for h in swing_highs if h < i - swing_lookback]
        recent_lows = [lo for lo in swing_lows if lo < i - swing_lookback]

        if len(recent_highs) < 2 and len(recent_lows) < 2:
            continue  # 需要至少两个摆动点才能判断 BOS

        # ----------------------------------------------------------
        # 上升 BOS：做多信号
        # ----------------------------------------------------------
        # 【SMC概念：上升 Break of Structure】
        # 当最新的摆动高点超过前一个摆动高点时，市场结构向上转换。
        # 这意味着买盘力量增强，聪明钱正在推动价格突破阻力。
        # 我们在结构转换后的回调低点入场做多。
        if len(recent_highs) >= 2:
            curr_swing_h = recent_highs[-1]   # 最新的摆动高
            prev_swing_h = recent_highs[-2]   # 前一个摆动高

            # 确认上升 BOS：当前摆高点突破前摆高点
            if high[curr_swing_h] > high[prev_swing_h]:
                # ADX 趋势过滤
                if adx[curr_swing_h] >= adx_threshold:
                    # 寻找回调：BOS 确认后，找回调最低点入场
                    search_end = min(curr_swing_h + pullback_max_bars, n)
                    pullback_low = float('inf')
                    pullback_idx = curr_swing_h

                    for j in range(curr_swing_h + 1, search_end):
                        if low[j] < pullback_low:
                            pullback_low = low[j]
                            pullback_idx = j

                    # 回调必须已经完成（当前 K 线在回调之后）
                    # 加上 last_exit_idx 防护：同一次信号不在同一 bar 重复开仓
                    if pullback_idx < i and last_exit_idx < i:
                        entry_price = close[i]

                        # 止损：结构转换点（两个摆高的较低者）下方 1.5x ATR（ETH 版放宽）
                        structure_low = min(low[curr_swing_h], low[prev_swing_h])
                        stop_loss = structure_low - atr[pullback_idx] * stop_atr_mult

                        # 计算 1R 风险距离
                        risk_distance = entry_price - stop_loss
                        if risk_distance <= 0:
                            continue  # 无效止损

                        # 止盈：2R 目标，参考下一个摆动高的外部流动性
                        take_profit = entry_price + risk_distance * tp_risk_mult

                        # 参考下一个摆动高点（外部流动性）调整止盈
                        for h_idx in swing_highs:
                            if h_idx > curr_swing_h and high[h_idx] > entry_price:
                                take_profit = max(take_profit, high[h_idx])
                                break

                        # 止盈上限：不超过 3R
                        take_profit = min(take_profit, entry_price + risk_distance * tp_max_risk_mult)

                        # 仓位计算
                        risk_amount = capital * risk_percent
                        position_size = risk_amount / risk_distance
                        position_size = max(position_size, 0.001)  # 最小仓位

                        position = position_size
                        position_type = 'long'
                        last_entry_idx = i
                        trailing_activated = False
                        trailing_stop = 0.0

        # ----------------------------------------------------------
        # 下降 BOS：做空信号
        # ----------------------------------------------------------
        # 【SMC概念：下降 Break of Structure】
        # 当最新的摆动低点跌破前一个摆动低点时，市场结构向下转换。
        # 这意味着卖盘力量增强，聪明钱正在推动价格突破支撑。
        # 我们在结构转换后的反弹高点入场做空。
        if position == 0 and len(recent_lows) >= 2:
            curr_swing_l = recent_lows[-1]
            prev_swing_l = recent_lows[-2]

            if low[curr_swing_l] < low[prev_swing_l]:
                if adx[curr_swing_l] >= adx_threshold:
                    search_end = min(curr_swing_l + pullback_max_bars, n)
                    pullback_high = -float('inf')
                    pullback_idx = curr_swing_l

                    for j in range(curr_swing_l + 1, search_end):
                        if high[j] > pullback_high:
                            pullback_high = high[j]
                            pullback_idx = j

                    if pullback_idx < i and last_exit_idx < i:
                        entry_price = close[i]

                        # 止损：结构转换点（两个摆高的较高者）上方 1.5x ATR（ETH 版放宽）
                        structure_high = max(high[curr_swing_l], high[prev_swing_l])
                        stop_loss = structure_high + atr[pullback_idx] * stop_atr_mult

                        risk_distance = stop_loss - entry_price
                        if risk_distance <= 0:
                            continue

                        take_profit = entry_price - risk_distance * tp_risk_mult

                        for l_idx in swing_lows:
                            if l_idx > curr_swing_l and low[l_idx] < entry_price:
                                take_profit = min(take_profit, low[l_idx])
                                break

                        take_profit = max(take_profit, entry_price - risk_distance * tp_max_risk_mult)

                        risk_amount = capital * risk_percent
                        position_size = risk_amount / risk_distance
                        position_size = max(position_size, 0.001)

                        position = -position_size
                        position_type = 'short'
                        last_entry_idx = i
                        trailing_activated = False
                        trailing_stop = 0.0

    # ============================================================
    # 收盘强制平仓
    # ============================================================
    if position != 0:
        final_price = close[-1]
        if position_type == 'long':
            pnl = (final_price - entry_price) * position
        else:
            pnl = (entry_price - final_price) * abs(position)
        capital += pnl
        trades.append({
            'entry_idx': last_entry_idx,
            'exit_idx': n - 1,
            'type': position_type,
            'entry_price': round(entry_price, 2),
            'exit_price': round(final_price, 2),
            'pnl': round(pnl, 2),
            'exit_reason': 'end_of_data'
        })

    # 更新最后权益
    if len(equity) > 0:
        equity[-1] = capital

    return _标准化策略输出(equity, trades, ohlc_df, params)


# ================================================================
# 参数说明（ETH 适配版 — 基于 2024-2026 五重稳健性检验反馈）
# ================================================================
# swing_lookback:     摆动点确认K线数          默认 5
# adx_period:         ADX计算周期              默认 14
# adx_threshold:      ADX趋势过滤阈值          默认 20
# pullback_max_bars:  BOS后回调等待最大K线数    默认 15
# stop_atr_mult:      止损(ATR倍数)            默认 1.5 (ETH版—因波动率更高放宽止损)
# tp_risk_mult:       止盈(R倍数)              默认 2.0 (2R)
# tp_max_risk_mult:   止盈上限(R倍数)           默认 3.0 (3R)
# trailing_activate_r:保本移动止损激活阈值(R)   默认 1.0 (1R)
# risk_percent:       单笔风险比例              默认 0.02 (2%)
# initial_capital:    初始资金                  默认 10000.0

# ================================================================
# ETH 参数适配依据：
#   2024-2026 稳健性检验中，ETH 2024 年回撤达 80%，根因在 1R 止损过紧。
#   ETH 的 ATR/价格比约为 BTC 的 2-3 倍，相同倍数下的实际波动空间更大。
#   stop_atr_mult=1.5 是适配 ETH 高波动性的最小必要调整。
# ================================================================

# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
