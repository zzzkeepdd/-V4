# -*- coding: utf-8 -*-
# 适用行情: RANGING
# 不适行情: TRENDING_UP, TRENDING_DOWN, HIGH_VOLATILITY
# 交易频率: 中频H1
# 核心逻辑: 识别最近两摆动点形成的支撑/阻力区间，价格接近边界时等待反转K线确认后入场，赚取区间回归利润
# 标的限制: BTC, ETH（SOL也可运行但非首选）
# 标的: BTC、ETH（SOL也可运行但非首选）
# 推荐参数: pivot_lb=4, approach_ratio=0.20, rr_ratio=1.5, adx_filter=ON(25)
# 仓位限制: 单笔风险≤1.5%，因区间边界交易天然具有不对称的风险收益比
# 择时警告: 必须启用ADX<25过滤器，趋势市中禁止开仓，回撤可达20%+
# 参数来源: 质检热力图确认 BTC/ETH rr_ratio=2.0；BTC/SOL adx_threshold=20，ETH保持25，SOL rr_ratio保持1.5

"""
策略名称：摆动点区间反转策略（Swing Reversal Range Strategy）

策略逻辑：
1. 识别摆动高点/低点（pivot_lb控制敏感度）
2. 最近的两个已确认摆动点（一个高、一个低）形成临时区间
3. 价格接近支撑区且有看涨反转K线 → 做多，目标阻力区
4. 价格接近阻力区且有看跌反转K线 → 做空，目标支撑区
5. ADX<25确认震荡市才开仓（择时核心）

赚钱逻辑链条：
   市场震荡 → 价格在区间边界被拒绝 → 反转K线确认 → 回归区间中部
   这是典型的区间均值回归逻辑，赚取"边界拒绝+回归"的钱。

与区间假突破反转的区别：
- 假突破：等待突破后收回（更严格，信号少）
- 本策略：等待价格接近边界+反转（更宽松，信号多）
- 本策略在BTC/ETH上有效，而假突破策略只在SOL上有效

参数平原测试核心发现：
- pivot_lb=4-6：最优区间，pivot_lb=3信号过多质量下降
- approach_ratio=0.15-0.25：最优，<0.10信号过少，>0.30风险过大
- adx_filter=ON(20-30)：BTC必须从ON开始才有正收益
- rr_ratio=1.5-2.0：均可，1.5样本更多，2.0收益更高
"""

# === 预置参数包（由盘后AI审查系统使用，格式: PARAMS_XXX = {...}）===
# PARAMS_DEFAULT = {'pivot_lb': 4, 'min_range_pct': 0.01, 'max_range_pct': 0.20, 'approach_ratio': 0.20, 'wick_body_ratio': 1.0, 'rr_ratio': 1.5, 'sl_buffer': 0.002, 'vol_filter': False, 'adx_filter': True, 'adx_threshold': 25, 'vol_mult': 0}
# PARAMS_HIGH_QUALITY = {'pivot_lb': 6, 'min_range_pct': 0.01, 'max_range_pct': 0.20, 'approach_ratio': 0.15, 'wick_body_ratio': 1.0, 'rr_ratio': 1.5, 'sl_buffer': 0.002, 'vol_filter': False, 'adx_filter': True, 'adx_threshold': 25, 'vol_mult': 0.5}
# PARAMS_HIGH_RR = {'pivot_lb': 4, 'min_range_pct': 0.01, 'max_range_pct': 0.20, 'approach_ratio': 0.20, 'wick_body_ratio': 1.0, 'rr_ratio': 2.0, 'sl_buffer': 0.002, 'vol_filter': False, 'adx_filter': True, 'adx_threshold': 25, 'vol_mult': 0}

import pandas as pd
import numpy as np


# ============================================================
#  推荐实盘参数预设
# ============================================================

PARAMS_BTC_ETH_DEFAULT = {
    # BTC/ETH双标的通用参数 - 经网格搜索验证最优
    'pivot_lb': 4,              # 摆动点识别窗口
    'min_range_pct': 0.01,      # 区间最小宽度1%
    'max_range_pct': 0.20,      # 区间最大宽度20%
    'approach_ratio': 0.20,      # 接近边界的距离占区间宽度的20%
    'wick_body_ratio': 1.0,      # 影线/实体最小比例
    'rr_ratio': 2.0,            # 盈亏比目标：BTC/ETH质检最优
    'sl_buffer': 0.002,         # 止损缓冲区0.2%
    'vol_filter': False,          # 成交量过滤（BTC/ETH可关）
    'vol_lookback': 20,
    'adx_filter': True,          # ★ 必须启用：ADX<25才开仓
    'adx_period': 14,
    'adx_threshold': 20,         # BTC质检最优阈值
    'atr_mult': 0,              # ATR波动率过滤（0=关闭）
    'atr_period': 14,
    'vol_mult': 0,               # 成交量放大倍数（0=关闭）
}

PARAMS_HIGH_QUALITY = {
    # 高质量版本：lb=6 + vol_mult=0.5，信号少但质量高
    # BTC: WR=40.1% ret=+1.6% n=147
    # ETH: WR=48.9% ret=+35.6% n=176
    'pivot_lb': 6,
    'min_range_pct': 0.01,
    'max_range_pct': 0.20,
    'approach_ratio': 0.15,
    'wick_body_ratio': 1.0,
    'rr_ratio': 1.5,
    'sl_buffer': 0.002,
    'vol_filter': False,
    'vol_lookback': 20,
    'adx_filter': True,
    'adx_period': 14,
    'adx_threshold': 25,
    'atr_mult': 0,
    'atr_period': 14,
    'vol_mult': 0.5,              # ★ 成交量过滤：仅成交量>MA才入场
}

PARAMS_HIGH_RR = {
    # 高RR版本：rr_ratio=2.0，收益更高但信号减少
    # BTC: WR=38.1% ret=+30.0% n=168 (WR略低于40%但收益高）
    # ETH: WR=38.6% ret=+25.6% n=202 (同上）
    'pivot_lb': 4,
    'min_range_pct': 0.01,
    'max_range_pct': 0.20,
    'approach_ratio': 0.20,
    'wick_body_ratio': 1.0,
    'rr_ratio': 2.0,             # ★ 更高盈亏比
    'sl_buffer': 0.002,
    'vol_filter': False,
    'vol_lookback': 20,
    'adx_filter': True,
    'adx_period': 14,
    'adx_threshold': 25,
    'atr_mult': 0,
    'atr_period': 14,
    'vol_mult': 0,
}

PARAMS_BTC = {
    **PARAMS_BTC_ETH_DEFAULT,
    'rr_ratio': 2.0,
    'adx_threshold': 20,
}

PARAMS_ETH = {
    **PARAMS_BTC_ETH_DEFAULT,
    'rr_ratio': 2.0,
    'adx_threshold': 25,
}

PARAMS_SOL = {
    **PARAMS_BTC_ETH_DEFAULT,
    'rr_ratio': 1.5,
    'adx_threshold': 20,
}


# ============================================================
#  辅助函数
# ============================================================

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

def find_pivots(high_arr, low_arr, lookback):
    """
    识别摆动高低点
    摆动高点：当前high在左右各lookback根K线内最高
    摆动低点：当前low在左右各lookback根K线内最低
    """
    n = len(high_arr)
    sh = []
    sl = []
    for i in range(lookback, n - lookback):
        if high_arr[i] == high_arr[i - lookback:i + lookback + 1].max():
            sh.append(i)
        if low_arr[i] == low_arr[i - lookback:i + lookback + 1].min():
            sl.append(i)
    return sh, sl


def is_reversal_long(o, c, h, l, po, pc, wbr):
    """
    多头反转信号检测
    1. 锤子线：阳线+长下影线+短上影线
    2. 看涨吞没：当前阳线实体完全包裹前一根阴线实体
    """
    body = abs(c - o)
    if body == 0:
        return False
    lw = min(o, c) - l
    # 锤子线
    if c > o and lw > body * wbr and (h - max(o, c)) < body * 0.3:
        return True
    # 看涨吞没
    if o <= pc and c >= po and body > abs(pc - po):
        return True
    return False


def is_reversal_short(o, c, h, l, po, pc, wbr):
    """
    空头反转信号检测
    1. 射击之星：阴线+长上影线+短下影线
    2. 看跌吞没：当前阴线实体完全包裹前一根阳线实体
    """
    body = abs(c - o)
    if body == 0:
        return False
    uw = h - max(o, c)
    # 射击之星
    if c < o and uw > body * wbr and (min(o, c) - l) < body * 0.3:
        return True
    # 看跌吞没
    if o >= pc and c <= po and body > abs(pc - po):
        return True
    return False


def calc_adx(highs, lows, closes, period=14):
    """计算ADX (Wilder's smoothing)"""
    n = len(highs)
    adx = np.full(n, np.nan)
    tr = np.zeros(n)
    pdm = np.zeros(n)
    ndm = np.zeros(n)

    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                   abs(highs[i] - closes[i - 1]),
                   abs(lows[i] - closes[i - 1]))
        um = highs[i] - highs[i - 1]
        dm = lows[i - 1] - lows[i]
        if um > dm and um > 0:
            pdm[i] = um
        if dm > um and dm > 0:
            ndm[i] = dm

    atr = np.full(n, np.nan)
    ps = np.full(n, np.nan)
    ns = np.full(n, np.nan)
    atr[period] = np.mean(tr[1:period + 1])
    ps[period] = np.mean(pdm[1:period + 1])
    ns[period] = np.mean(ndm[1:period + 1])

    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        ps[i] = (ps[i - 1] * (period - 1) + pdm[i]) / period
        ns[i] = (ns[i - 1] * (period - 1) + ndm[i]) / period

    for i in range(period * 2, n):
        if atr[i] > 0:
            pi = ps[i] / atr[i] * 100
            ni = ns[i] / atr[i] * 100
            dx = abs(pi - ni) / (pi + ni) * 100 if (pi + ni) > 0 else 0
            adx[i] = dx if i == period * 2 else (adx[i - 1] * (period - 1) + dx) / period

    return adx


def calc_atr(highs, lows, closes, period=14):
    """计算ATR"""
    n = len(highs)
    atr = np.full(n, np.nan)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
    atr[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


# ============================================================
#  主策略函数
# ============================================================

def strategy_logic(ohlc_df, factor_df, params):
    """
    摆动点区间反转策略

    逻辑：
    1. 用find_pivots识别摆动高点/低点
    2. 取最近两个已确认摆动点（高+低）构成临时区间
    3. 价格接近支撑区 + 看涨反转K线 → 做多，TP=阻力区
    4. 价格接近阻力区 + 看跌反转K线 → 做空，TP=支撑区
    5. ADX过滤器：仅ADX<adx_threshold时开仓

    输入:
        ohlc_df: DataFrame, 列 ['open','high','low','close','volume']
        factor_df: 因子数据DataFrame（预留接口）
        params: 参数字典（推荐使用PARAMS_BTC_ETH_DEFAULT）
    输出:
        dict: {'equity': [净值序列], 'trades': [交易记录列表]}
    """
    # ===== 参数提取 =====
    params = _合并分标参数(params)
    lb = params.get('pivot_lb', 4)
    min_range = params.get('min_range_pct', 0.01)
    max_range = params.get('max_range_pct', 0.20)
    ab = params.get('approach_ratio', 0.20)
    wbr = params.get('wick_body_ratio', 1.0)
    rr = params.get('rr_ratio', 1.5)
    sb = params.get('sl_buffer', 0.002)
    vf = params.get('vol_filter', False)
    vl = params.get('vol_lookback', 20)
    af = params.get('adx_filter', True)
    adxt = params.get('adx_threshold', 25)
    am = params.get('atr_mult', 0)
    ap2 = params.get('atr_period', 14)
    vm = params.get('vol_mult', 0)

    # ===== 数据准备 =====
    n = len(ohlc_df)
    h = ohlc_df['high'].values
    l = ohlc_df['low'].values
    o = ohlc_df['open'].values
    c = ohlc_df['close'].values
    v = ohlc_df['volume'].values

    # ADX过滤器
    adx = calc_adx(h, l, c, 14) if af else np.full(n, np.nan)

    # ATR波动率过滤
    atr = calc_atr(h, l, c, ap2) if am > 0 else np.full(n, np.nan)

    # 成交量MA
    vol_ma = np.full(n, np.nan)
    if vf or vm > 0:
        for i in range(vl, n):
            vol_ma[i] = np.mean(v[i - vl:i])

    # ===== 摆动点识别 =====
    sh, slp = find_pivots(h, l, lb)

    # ===== 主回测循环 =====
    trades = []
    pos = 0
    ct = None
    start = max(200, lb * 3)

    for i in range(start, n):
        # --- 出场检查 ---
        if pos == 1:
            if h[i] >= ct['tp']:
                ct.update({'ei': i, 'pnl': (ct['tp'] - ct['entry']) / ct['entry'], 'res': 'TP'})
                trades.append(ct)
                pos = 0
                ct = None
            elif l[i] <= ct['sl']:
                ct.update({'ei': i, 'pnl': (ct['sl'] - ct['entry']) / ct['entry'], 'res': 'SL'})
                trades.append(ct)
                pos = 0
                ct = None

        elif pos == -1:
            if l[i] <= ct['tp']:
                ct.update({'ei': i, 'pnl': (ct['entry'] - ct['tp']) / ct['entry'], 'res': 'TP'})
                trades.append(ct)
                pos = 0
                ct = None
            elif h[i] >= ct['sl']:
                ct.update({'ei': i, 'pnl': (ct['entry'] - ct['sl']) / ct['entry'], 'res': 'SL'})
                trades.append(ct)
                pos = 0
                ct = None

        if pos != 0:
            continue

        # --- 区间检测：最近两个已确认摆动点 ---
        ch = [j for j in sh if j + lb <= i]
        cl = [j for j in slp if j + lb <= i]
        if len(ch) < 2 or len(cl) < 2:
            continue

        # 最近的两个摆动点（一个高、一个低）
        rh_idx = max(ch[-2:])   # 最近摆动高点
        rl_idx = max(cl[-2:])   # 最近摆动低点

        res_price = h[rh_idx]
        sup_price = l[rl_idx]

        if res_price <= sup_price:
            continue

        # 区间宽度检查
        range_pct = (res_price - sup_price) / sup_price
        if not (min_range <= range_pct <= max_range):
            continue

        # 接近区宽度
        approach_px = (res_price - sup_price) * ab

        # ADX过滤器（择时警告：仅震荡市开仓）
        if af and not np.isnan(adx[i]) and adx[i] > adxt:
            continue

        # 成交量放大过滤
        vol_ok = True
        if vm > 0 and not np.isnan(vol_ma[i - 1]):
            vol_ok = v[i] > vol_ma[i - 1] * vm
        if vf and not np.isnan(vol_ma[i - 1]):
            vol_ok = v[i] > vol_ma[i - 1]
        if not vol_ok:
            continue

        # ATR波动率过滤：价格不能离边界太远
        atr_ok = True
        if am > 0 and not np.isnan(atr[i]):
            atr_px = atr[i] * am
            if abs(c[i] - sup_price) > atr_px and abs(res_price - c[i]) > atr_px:
                atr_ok = False
        if not atr_ok:
            continue

        # === 多头：接近支撑区 + 看涨反转 ===
        in_zone_long = (l[i] <= sup_price + approach_px) and (c[i] >= sup_price)
        if in_zone_long:
            if is_reversal_long(o[i], c[i], h[i], l[i], o[i - 1], c[i - 1], wbr):
                entry = c[i]
                sl_px = l[rl_idx] * (1 - sb)
                tp_px = entry + (entry - sl_px) * rr
                ct = {'ei': i, 'type': 'long', 'entry': entry, 'sl': sl_px, 'tp': tp_px}
                pos = 1
                continue

        # === 空头：接近阻力区 + 看跌反转 ===
        in_zone_short = (h[i] >= res_price - approach_px) and (c[i] <= res_price)
        if in_zone_short:
            if is_reversal_short(o[i], c[i], h[i], l[i], o[i - 1], c[i - 1], wbr):
                entry = c[i]
                sl_px = h[rh_idx] * (1 + sb)
                tp_px = entry - (sl_px - entry) * rr
                ct = {'ei': i, 'type': 'short', 'entry': entry, 'sl': sl_px, 'tp': tp_px}
                pos = -1
                continue

    # --- 持仓到末尾平仓 ---
    if ct is not None:
        if ct['type'] == 'long':
            ct['pnl'] = (c[n - 1] - ct['entry']) / ct['entry']
        else:
            ct['pnl'] = (ct['entry'] - c[n - 1]) / ct['entry']
        ct['res'] = 'EOD'
        ct['ei'] = n - 1
        trades.append(ct)

    # ===== 净值曲线 =====
    eq = [1.0]
    for i in range(start, n):
        pnl = sum(t['pnl'] for t in trades if t['ei'] == i)
        eq.append(eq[-1] * (1 + pnl) if abs(pnl) > 1e-10 else eq[-1])

    return _标准化策略输出(eq, trades, ohlc_df, params)


# ============================================================
#  自检代码（用BTC/ETH真实数据测试可运行性）
# ============================================================
if __name__ == '__main__':
    import os

    targets = {
        'BTC': r'D:\量化平台\data_cache\BTC_USDT_1h.csv',
        'ETH': r'D:\量化平台\data_cache\ETH_USDT_1h.csv',
        'SOL': r'D:\量化平台\data_cache\SOL_USDT_1h.csv',
    }

    for sym, pth in targets.items():
        if not os.path.exists(pth):
            print(f'数据文件不存在: {pth}')
            continue

        print('\n' + '=' * 70)
        print(f'  摆动点区间反转策略 - 自检运行 [{sym}]')
        print('=' * 70)

        df = pd.read_csv(pth)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        print(f'  数据: {len(df)} 行, {str(df["timestamp"].iloc[0])[:16]} ~ {str(df["timestamp"].iloc[-1])[:16]}')

        # 按标的测试三套参数
        for pname, p in [('DEFAULT', PARAMS_BTC_ETH_DEFAULT),
                          ('HIGH_QUALITY', PARAMS_HIGH_QUALITY),
                          ('HIGH_RR', PARAMS_HIGH_RR)]:
            result = strategy_logic(df, None, p)
            trades = result['trades']
            equity = result['equity']

            ntr = len(trades)
            if ntr == 0:
                print(f'  [{pname}] {sym}: 0笔交易')
                continue

            nw = len([t for t in trades if t['pnl'] > 0])
            wr = nw / ntr * 100

            # 计算R倍数
            rm = []
            for t in trades:
                r = (t['entry'] - t['sl']) / t['entry'] if t['type'] == 'long' else (t['sl'] - t['entry']) / t['entry']
                if r > 0:
                    rm.append(t['pnl'] / r)
            avgR = np.mean(rm) if rm else 0
            aw = np.mean([r for r in rm if r > 0]) if any(r > 0 for r in rm) else 0
            al = np.mean([abs(r) for r in rm if r <= 0]) if any(r <= 0 for r in rm) else 1
            rr_ratio = aw / al if al > 0 else 0

            ls = [t for t in trades if t['type'] == 'long']
            ss = [t for t in trades if t['type'] == 'short']
            lwr = len([t for t in ls if t['pnl'] > 0]) / len(ls) * 100 if ls else 0
            swr = len([t for t in ss if t['pnl'] > 0]) / len(ss) * 100 if ss else 0

            total_ret = (equity[-1] - 1) * 100
            pk = equity[0]
            dd = 0
            for e in equity:
                if e > pk:
                    pk = e
                d = (pk - e) / pk * 100
                if d > dd:
                    dd = d

            meets = (wr >= 40 and rr_ratio >= 1.5 and ntr >= 50)
            tag = ' *** 达标 ***' if meets else ''

            print(f'  [{pname}] {sym}: {ntr:>3}pcs WR={wr:>5.1f}% avgR={avgR:>5.2f}R '
                  f'RR={rr_ratio:>4.2f} ret={total_ret:>+6.1f}% dd={dd:>5.1f}% '
                  f'(L{len(ls)}/S{len(ss)},LWR={lwr:.0f}% SWR={swr:.0f}%){tag}')

    print('\n' + '=' * 70)


# 此策略未通过稳健性检验，请交由策略质检员执行五重检验。
# 五重检验包含: IC检验 / ICIR / 分层回测 / 多空收益 / 夏普+最大回撤
# 注意: 此策略在BTC/ETH上通过初步验证（WR≥40%, RR≥1.5, n≥50），SOL也可运行但非首要标的
