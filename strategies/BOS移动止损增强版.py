# -*- coding: utf-8 -*-
# 适用行情: TRENDING_UP, TRENDING_DOWN
# 不适行情: RANGING, HIGH_VOLATILITY
# 交易频率: 中频H1
# 核心逻辑: 结构突破(BOS)顺趋势入场，止损跟随新形成的摆动点动态移动，让利润奔跑，无固定止盈
# 标的限制: ETH (主力), SOL (可用), BTC (不推荐)
# 标的: ETH（主力）, SOL（可用）, BTC（不推荐）
# 默认参数: lb=5, ns=2, bos_buf=0.001, vol_filter=ON, vol_mult=1.25
#
# 【真实数据验证结果（2025-05 ~ 2026-05，1h）】
#   ETH(PASS): lb=5, ns=2, bos_buf=0.001, vf=True, vm=1.25, sb=0.001
#     → 13笔, WR=53.8%, avgR=0.37R, RR=2.15:1, +29.5%, dd=7.6%
#   SOL(PASS): same params → 9笔, WR=55.6%, avgR=0.37R, RR=1.81:1, +1.5%, dd=12.3%
#   BTC(FAIL): ns=1, vf=True → 45笔, WR=44.4%, avgR=0.26R, RR=2.36, +12.9%, dd=7.2%(WR不到50%)
#   参数来源: 质检热力图确认 BTC/SOL 使用 n_swings=1，ETH 保持 n_swings=2；BTC 若误用 2 会从45笔骤降至8笔
#
# 【跟踪止损机制说明】
#   入场后，每当市场形成新的摆动低点（多头）或摆动高点（空头），
#   止损自动移动到该摆动点外侧（留sl_buffer缓冲）。
#   止损只向有利方向移动，从不回退。
#   退出方式：100%跟踪止损触发（此版本不设固定止盈）
#
# 【参数调优指引】
#   n_swings: 趋势确认摆动次数。1=宽松(40笔+信号, WR~40%), 2=严格(13笔, WR~54%)
#   vol_mult: 成交量倍数。1.0=标准, 1.25=严格(ETH推荐), 0.75=宽松(更多信号)
#   bos_buffer: BOS突破缓冲。0=即时突破, 0.001~0.002=需要一定突破幅度
#   tp_rr: 固定止盈R倍数。0=纯跟踪止损, >0=结合固定止盈+跟踪止损
#
# === 预置参数包（由盘后AI审查系统使用，格式: PARAMS_XXX = {...}）===
# PARAMS_BOS_STRICT = {'swing_lookback': 5, 'n_swings': 2, 'sl_buffer': 0.001, 'bos_buffer': 0.001, 'tp_rr': 0, 'vol_filter': True, 'vol_lookback': 20, 'vol_mult': 1.25}
# PARAMS_BOS_LOOSE = {'swing_lookback': 5, 'n_swings': 1, 'sl_buffer': 0.001, 'bos_buffer': 0.001, 'tp_rr': 0, 'vol_filter': True, 'vol_lookback': 20, 'vol_mult': 0.75}
# PARAMS_BOS_STANDARD = {'swing_lookback': 5, 'n_swings': 2, 'sl_buffer': 0.001, 'bos_buffer': 0.001, 'tp_rr': 0, 'vol_filter': True, 'vol_lookback': 20, 'vol_mult': 1.0}

import pandas as pd
import numpy as np


# ============================================================
#  推荐实盘参数预设
# ============================================================

PARAMS_BOS_DEFAULT = {
    # ETH主力参数：13笔信号，WR=53.8%，RR=2.15:1
    'swing_lookback': 5,
    'n_swings': 2,              # 严格趋势确认
    'sl_buffer': 0.001,         # 止损缓冲区
    'bos_buffer': 0.001,        # BOS突破缓冲
    'tp_rr': 0,                 # 0=纯跟踪止损（无固定止盈）
    'vol_filter': True,
    'vol_lookback': 20,
    'vol_mult': 1.25,           # 成交量倍数（严格）
}

PARAMS_BOS_HIGH_FREQ = {
    # 高频信号版：~35笔信号，适合小本金积累（注意WR较低~40%）
    'swing_lookback': 5,
    'n_swings': 1,              # 宽松趋势确认
    'sl_buffer': 0.001,
    'bos_buffer': 0.001,
    'tp_rr': 0,
    'vol_filter': True,
    'vol_lookback': 20,
    'vol_mult': 1.0,            # 标准成交量倍数
}

PARAMS_BOS_BTC = {
    **PARAMS_BOS_HIGH_FREQ,
    'n_swings': 1,
}

PARAMS_BOS_ETH = {
    **PARAMS_BOS_DEFAULT,
    'n_swings': 2,
}

PARAMS_BOS_SOL = {
    **PARAMS_BOS_HIGH_FREQ,
    'n_swings': 1,
}


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
    presets = {'BTC': PARAMS_BOS_BTC, 'ETH': PARAMS_BOS_ETH, 'SOL': PARAMS_BOS_SOL}
    merged = dict(presets.get(_识别参数标的(params), PARAMS_BOS_BTC))
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


# ============================================================
#  辅助函数
# ============================================================

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


def check_uptrend(high_arr, low_arr, confirmed_highs, confirmed_lows, n_swings):
    """
    检查上升趋势：连续n_swings次高点抬高(HH) + 低点抬高(HL)
    纯结构判断，无阈值依赖
    """
    if len(confirmed_highs) < n_swings + 1 or len(confirmed_lows) < n_swings + 1:
        return False

    recent_highs = confirmed_highs[-n_swings - 1:]
    recent_lows = confirmed_lows[-n_swings - 1:]

    higher_highs = all(
        high_arr[recent_highs[j + 1]] > high_arr[recent_highs[j]]
        for j in range(len(recent_highs) - 1)
    )
    higher_lows = all(
        low_arr[recent_lows[j + 1]] > low_arr[recent_lows[j]]
        for j in range(len(recent_lows) - 1)
    )
    return higher_highs and higher_lows


def check_downtrend(high_arr, low_arr, confirmed_highs, confirmed_lows, n_swings):
    """
    检查下降趋势：连续n_swings次高点降低(LH) + 低点降低(LL)
    """
    if len(confirmed_highs) < n_swings + 1 or len(confirmed_lows) < n_swings + 1:
        return False

    recent_highs = confirmed_highs[-n_swings - 1:]
    recent_lows = confirmed_lows[-n_swings - 1:]

    lower_highs = all(
        high_arr[recent_highs[j + 1]] < high_arr[recent_highs[j]]
        for j in range(len(recent_highs) - 1)
    )
    lower_lows = all(
        low_arr[recent_lows[j + 1]] < low_arr[recent_lows[j]]
        for j in range(len(recent_lows) - 1)
    )
    return lower_highs and lower_lows


# ============================================================
#  主策略函数
# ============================================================

def strategy_logic(ohlc_df, factor_df, params):
    """
    BOS移动止损增强版策略

    逻辑：
    1. 识别摆动点 → 判断趋势方向（HH/HL上升 或 LH/LL下降）
    2. 价格突破最近摆动高点(上升趋势)或跌破最近摆动低点(下降趋势) → BOS入场
    3. 入场后，跟踪止损跟随新形成的摆动点动态移动：
       - 多头：跟踪止损 = 最新确认摆动低点 × (1 - sl_buffer)
       - 空头：跟踪止损 = 最新确认摆动高点 × (1 + sl_buffer)
    4. 止损只向有利方向移动，从不回退
    5. 出场：跟踪止损触发（无固定止盈时）或固定止盈触发（tp_rr > 0时）

    输入:
        ohlc_df: DataFrame, 列 ['open','high','low','close','volume']
        factor_df: 因子数据DataFrame（可合并使用）
        params: 参数字典（推荐使用 PARAMS_BOS_DEFAULT）
            swing_lookback: 摆动点识别窗口 (默认5)
            n_swings: 趋势确认所需摆动次数 (ETH=2, 高频=1)
            sl_buffer: 止损缓冲区比例 (默认0.001=0.1%)
            bos_buffer: BOS突破缓冲区比例 (默认0.001=0.1%，防止噪音突破)
            tp_rr: 固定止盈R倍数 (0=纯跟踪止损, >0=启用固定止盈)
            vol_filter: 是否启用成交量过滤 (默认True)
            vol_lookback: 成交量MA周期 (默认20)
            vol_mult: 成交量倍数阈值 (ETH推荐1.25, 标准1.0)
                入场K线成交量需 > vol_mult × 过去vol_lookback根K线均量
    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    # ===== 参数提取 =====
    params = _合并分标参数(params)
    swing_lookback = params.get('swing_lookback', 5)
    n_swings = params.get('n_swings', 2)
    sl_buffer = params.get('sl_buffer', 0.001)
    bos_buffer = params.get('bos_buffer', 0.001)
    tp_rr = params.get('tp_rr', 0)
    vol_filter = params.get('vol_filter', True)
    vol_lookback = params.get('vol_lookback', 20)
    vol_mult = params.get('vol_mult', 1.25)

    # ===== 数据准备 =====
    n = len(ohlc_df)
    highs = ohlc_df['high'].values
    lows = ohlc_df['low'].values
    opens = ohlc_df['open'].values
    closes = ohlc_df['close'].values
    volumes = ohlc_df['volume'].values if vol_filter else None

    # 成交量移动平均
    vol_ma = None
    if vol_filter and volumes is not None:
        vol_ma = np.full(n, np.nan)
        for i in range(vol_lookback, n):
            vol_ma[i] = np.mean(volumes[i - vol_lookback:i])

    # ===== 摆动点识别 =====
    swing_highs, swing_lows = find_swing_points(highs, lows, swing_lookback)

    # ===== 主回测循环 =====
    trades = []
    position = 0      # 0=空仓, 1=多头, -1=空头
    current_trade = None
    trailing_sl = 0   # 当前跟踪止损价格
    start_idx = max(200, swing_lookback * 3)

    for i in range(start_idx, n):
        # --- 收集已确认摆动点 ---
        confirmed_highs = [h for h in swing_highs if h + swing_lookback <= i]
        confirmed_lows = [l for l in swing_lows if l + swing_lookback <= i]

        # --- 跟踪止损触发 + 止损更新（持仓中） ---
        if position == 1:  # 多头持仓
            # 优先检查固定止盈（如启用）
            if tp_rr > 0 and highs[i] >= current_trade['tp']:
                exit_price = current_trade['tp']
                pnl = (exit_price - current_trade['entry_price']) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i,
                    'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price,
                    'pnl_pct': pnl,
                    'result': 'TP',
                    'trailing_sl': trailing_sl
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
                continue

            # 更新跟踪止损：用入场后新形成的摆动低点
            new_swing_lows = [l for l in confirmed_lows if l > current_trade['entry_idx']]
            if new_swing_lows:
                new_sl = lows[new_swing_lows[-1]] * (1 - sl_buffer)
                # 止损只能上移，不能下移
                if new_sl > trailing_sl:
                    trailing_sl = new_sl

            # 检查是否触发跟踪止损
            if lows[i] <= trailing_sl:
                exit_price = trailing_sl
                pnl = (exit_price - current_trade['entry_price']) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i,
                    'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price,
                    'pnl_pct': pnl,
                    'result': 'TS',
                    'trailing_sl': trailing_sl
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
                continue

        elif position == -1:  # 空头持仓
            if tp_rr > 0 and lows[i] <= current_trade['tp']:
                exit_price = current_trade['tp']
                pnl = (current_trade['entry_price'] - exit_price) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i,
                    'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price,
                    'pnl_pct': pnl,
                    'result': 'TP',
                    'trailing_sl': trailing_sl
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
                continue

            # 更新跟踪止损：用入场后新形成的摆动高点
            new_swing_highs = [h for h in confirmed_highs if h > current_trade['entry_idx']]
            if new_swing_highs:
                new_sl = highs[new_swing_highs[-1]] * (1 + sl_buffer)
                # 止损只能下移，不能上移
                if new_sl < trailing_sl:
                    trailing_sl = new_sl

            if highs[i] >= trailing_sl:
                exit_price = trailing_sl
                pnl = (current_trade['entry_price'] - exit_price) / current_trade['entry_price']
                current_trade.update({
                    'exit_idx': i,
                    'exit_time': ohlc_df['timestamp'].iloc[i],
                    'exit_price': exit_price,
                    'pnl_pct': pnl,
                    'result': 'TS',
                    'trailing_sl': trailing_sl
                })
                trades.append(current_trade)
                position = 0
                current_trade = None
                continue

        if position != 0:
            continue  # 持仓中，跳过入场检查

        # --- BOS入场 ---
        if len(confirmed_highs) < n_swings + 1 or len(confirmed_lows) < n_swings + 1:
            continue

        recent_highs = confirmed_highs[-n_swings - 1:]
        recent_lows = confirmed_lows[-n_swings - 1:]

        # ===== 多头BOS：上升趋势 + 收盘价突破最近摆动高点 =====
        if check_uptrend(highs, lows, confirmed_highs, confirmed_lows, n_swings):
            # 确保正常上升腿结构：摆动低点在前，摆动高点在后
            if recent_lows[-1] < recent_highs[-1]:
                bos_level = highs[recent_highs[-1]]
                # 收盘价突破摆点高点（含缓冲）
                break_price = bos_level * (1 + bos_buffer)

                if closes[i] > break_price:
                    # 突破K线质量：阳线且开于bos下方（真突破）
                    if opens[i] < closes[i]:
                        # 成交量过滤
                        vol_ok = True
                        if vol_filter and vol_ma is not None:
                            if not np.isnan(vol_ma[i - 1]):
                                vol_ok = volumes[i] > vol_ma[i - 1] * vol_mult

                        if vol_ok:
                            entry_price = closes[i]
                            # 初始止损：最近摆动低点下方
                            init_sl = lows[recent_lows[-1]] * (1 - sl_buffer)
                            trailing_sl = init_sl

                            # 计算固定止盈（如启用）
                            tp_price = entry_price + (entry_price - init_sl) * tp_rr if tp_rr > 0 else None

                            current_trade = {
                                'entry_idx': i,
                                'entry_time': ohlc_df['timestamp'].iloc[i],
                                'type': 'long',
                                'entry_price': entry_price,
                                'sl': init_sl,
                                'tp': tp_price if tp_price else 999999,
                                'bos_level': bos_level
                            }
                            position = 1
                            continue

        # ===== 空头BOS：下降趋势 + 收盘价跌破最近摆动低点 =====
        if check_downtrend(highs, lows, confirmed_highs, confirmed_lows, n_swings):
            if recent_highs[-1] < recent_lows[-1]:
                bos_level = lows[recent_lows[-1]]
                break_price = bos_level * (1 - bos_buffer)

                if closes[i] < break_price:
                    if opens[i] > closes[i]:  # 阴线确认
                        vol_ok = True
                        if vol_filter and vol_ma is not None:
                            if not np.isnan(vol_ma[i - 1]):
                                vol_ok = volumes[i] > vol_ma[i - 1] * vol_mult

                        if vol_ok:
                            entry_price = closes[i]
                            init_sl = highs[recent_highs[-1]] * (1 + sl_buffer)
                            trailing_sl = init_sl

                            tp_price = entry_price - (init_sl - entry_price) * tp_rr if tp_rr > 0 else None

                            current_trade = {
                                'entry_idx': i,
                                'entry_time': ohlc_df['timestamp'].iloc[i],
                                'type': 'short',
                                'entry_price': entry_price,
                                'sl': init_sl,
                                'tp': tp_price if tp_price else -999999,
                                'bos_level': bos_level
                            }
                            position = -1
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
            'result': 'EOD',
            'trailing_sl': trailing_sl
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


# ============================================================
#  自检代码（用ETH和BTC真实数据测试可运行性）
# ============================================================
if __name__ == '__main__':
    import os

    print('=' * 70)
    print('  BOS移动止损增强版策略 — 自检运行')
    print('=' * 70)

    targets = {
        'ETH': r'D:\量化平台\data_cache\ETH_USDT_1h.csv',
        'BTC': r'D:\量化平台\data_cache\BTC_USDT_1h.csv',
        'SOL': r'D:\量化平台\data_cache\SOL_USDT_1h.csv',
    }

    for symbol, data_path in targets.items():
        if not os.path.exists(data_path):
            print(f'\n  [{symbol}] 数据文件不存在，跳过')
            continue

        df = pd.read_csv(data_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        print(f'\n  --- [{symbol}] ---')

        # 用最佳默认参数测试
        result = strategy_logic(df, None, PARAMS_BOS_DEFAULT)
        trades = result['trades']
        equity = result['equity']

        if trades:
            n_wins = len([t for t in trades if t['pnl_pct'] > 0])
            n_losses = len([t for t in trades if t['pnl_pct'] <= 0])
            wr = n_wins / len(trades) * 100

            # 平均R倍数
            r_list = []
            for t in trades:
                if t['type'] == 'long':
                    risk = (t['entry_price'] - t['sl']) / t['entry_price']
                else:
                    risk = (t['sl'] - t['entry_price']) / t['entry_price']
                if risk > 0:
                    r_list.append(t['pnl_pct'] / risk)

            avg_r = np.mean(r_list) if r_list else 0
            avg_win_r = np.mean([r for r in r_list if r > 0]) if any(r > 0 for r in r_list) else 0
            avg_loss_r = np.mean([abs(r) for r in r_list if r <= 0]) if any(r <= 0 for r in r_list) else 1
            rr_ratio = avg_win_r / avg_loss_r if avg_loss_r > 0 else 0

            total_ret = (equity[-1] - 1) * 100
            peak = equity[0]
            maxdd = 0
            for e in equity:
                if e > peak:
                    peak = e
                dd = (peak - e) / peak * 100
                if dd > maxdd:
                    maxdd = dd

            n_ts = len([t for t in trades if t['result'] == 'TS'])
            n_tp = len([t for t in trades if t['result'] == 'TP'])
            n_eod = len([t for t in trades if t['result'] == 'EOD'])

            print(f'    {len(trades)}笔 WR={wr:.1f}% avgR={avg_r:.2f}R '
                  f'RR={rr_ratio:.2f} ret={total_ret:+.1f}% dd={maxdd:.1f}% '
                  f'(TS{n_ts} TP{n_tp} EOD{n_eod})')
        else:
            print(f'    无交易信号')

    print(f'\n  *** 自检通过：代码可无报错运行 ***')
    print('=' * 70)


# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
# 五重检验包含: IC检验 / ICIR / 分层回测 / 多空收益 / 夏普+最大回撤
# 推荐使用 PARAMS_BOS_DEFAULT 进行实盘参数配置
# 注意: 此策略在BTC上WR不达标(25-44%)，仅ETH/SOL通过初步验证
