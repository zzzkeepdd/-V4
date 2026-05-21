# -*- coding: utf-8 -*-
"""行情引擎：行情映射表 + 逐策略门控 + 信号强度评分SSS。

三层调度引擎的 Layer 1 + Layer 2：
  Layer 1 — 行情状态映射表：行情状态 → 候选策略池（粗筛）
  Layer 2 — 逐策略门控：每个策略独立激活条件检查（精筛）
  信号强度评分 SSS：多策略同时激活时择优排序

纯Python实现，仅使用 typing 标准库，无外部依赖。
"""

from typing import Dict, List

# ═══════════════════════════════════════════════════════════════════════
# Layer 1: 行情状态映射表
# ═══════════════════════════════════════════════════════════════════════

MARKET_STRATEGY_MAP: Dict[str, List[str]] = {
    "TRENDING_UP": ["trend_following", "smc_structure", "bos_trailing_stop"],
    "TRENDING_DOWN": ["trend_following", "smc_structure", "bos_trailing_stop"],
    "RANGING": ["grid_trading", "swing_range"],
    "HIGH_VOLATILITY": ["bb_breakout"],
    "TREND_EXHAUSTION": ["trend_exhaustion"],
}

# 策略名 → 内部策略类型标识（支持内部名与中文名双向映射）
STRATEGY_NAME_TO_TYPE: Dict[str, str] = {
    # 内部名 → 类型（直通）
    "trend_following": "trend_following",
    "smc_structure": "smc_structure",
    "bos_trailing_stop": "bos_trailing_stop",
    "grid_trading": "grid_trading",
    "swing_range": "swing_range",
    "bb_breakout": "bb_breakout",
    "trend_exhaustion": "trend_exhaustion",
    # 策略加载器返回的中文名 → 类型
    "趋势回调入场": "trend_following",
    "SMC_结构转换突破_ETH": "smc_structure",
    "BOS移动止损增强版": "bos_trailing_stop",
    "BB挤压突破": "bb_breakout",
    "摆动点区间反转": "swing_range",
    "趋势衰竭反转": "trend_exhaustion",
}


def market_to_candidates(market_state: str) -> List[str]:
    """行情状态 → 候选策略名列表（Layer 1 粗筛）。

    根据 DeepSeek 或本地规则引擎输出的行情状态，查表返回候选策略池。
    行情状态包括：TRENDING_UP / TRENDING_DOWN / RANGING /
                  HIGH_VOLATILITY / TREND_EXHAUSTION

    Args:
        market_state: 行情状态字符串

    Returns:
        候选策略内部名列表；未知状态返回空列表
    """
    return MARKET_STRATEGY_MAP.get(market_state, [])


# ═══════════════════════════════════════════════════════════════════════
# Layer 2: 逐策略门控
# ═══════════════════════════════════════════════════════════════════════

def _resolve_strategy_type(strategy_name: str) -> str:
    """将策略名（内部名或中文名）解析为策略类型标识。"""
    return STRATEGY_NAME_TO_TYPE.get(strategy_name, "")


def strategy_gate(strategy_name: str, coin: str, features: Dict) -> bool:
    """单策略门控条件检查（Layer 2 精筛）。

    对候选池中的每个策略执行独立激活条件检查。
    features 字典预期包含以下键（缺键按默认值处理）：
        adx, close, ema20, ema50, bb_width,
        volume, volume_ma, trend_bars, rsi_divergence

    各策略激活条件：
        trend_following:   ADX>25 AND close>EMA20
        smc_structure:     ADX>25 AND close在EMA20±2%范围内
        bos_trailing_stop: ADX>25 AND close>EMA50
        grid_trading:      ADX<20 AND bb_width<2%
        swing_range:       ADX<20 AND bb_width>0（区间存在）
        bb_breakout:       bb_width>2% AND volume>volume_ma×1.5
        trend_exhaustion:  trend_bars>=20 AND rsi_divergence==True

    Args:
        strategy_name: 策略名（内部名如 "trend_following" 或中文名如 "趋势回调入场"）
        coin: 币种标识，如 "BTC" / "ETH" / "SOL"
        features: 市场特征字典

    Returns:
        bool: True=激活，False=不激活（绝不会返回 None）
    """
    stype = _resolve_strategy_type(strategy_name)
    if not stype:
        return False

    # 提取特征值，缺键使用安全默认值
    adx: float = float(features.get("adx", 0))
    close: float = float(features.get("close", 0))
    ema20: float = float(features.get("ema20", 0))
    ema50: float = float(features.get("ema50", 0))
    bb_width: float = float(features.get("bb_width", 0))
    volume: float = float(features.get("volume", 0))
    volume_ma: float = float(features.get("volume_ma", 0))
    trend_bars: int = int(features.get("trend_bars", 0))
    rsi_divergence: bool = bool(features.get("rsi_divergence", False))

    # ── 趋势跟随类 ──
    if stype == "trend_following":
        return bool(adx > 25 and close > ema20)

    # ── SMC 结构转换 ──
    if stype == "smc_structure":
        if not (adx > 25 and ema20 > 0):
            return False
        deviation: float = abs(close - ema20) / ema20
        return bool(deviation <= 0.02)

    # ── BOS 移动止损增强 ──
    if stype == "bos_trailing_stop":
        return bool(adx > 25 and close > ema50)

    # ── 震荡/网格类 ──
    if stype == "grid_trading":
        return bool(adx < 20 and bb_width < 2.0)

    if stype == "swing_range":
        # close距区间边界<ATR — 特性暂用 bb_width>0 表示存在可辨识区间
        return bool(adx < 20 and bb_width > 0)

    # ── 突破/衰竭类 ──
    if stype == "bb_breakout":
        if volume_ma <= 0:
            return False
        volume_ratio: float = volume / volume_ma
        return bool(bb_width > 2.0 and volume_ratio > 1.5)

    if stype == "trend_exhaustion":
        return bool(trend_bars >= 20 and rsi_divergence is True)

    # 未知策略类型
    return False


# ═══════════════════════════════════════════════════════════════════════
# 信号强度评分 (SSS)
# ═══════════════════════════════════════════════════════════════════════

def calc_sss(features: Dict, num_active_strategies: int) -> int:
    """信号强度评分（Signal Strength Score，范围 0-100）。

    评分公式：
        SSS = ADX贡献(0-30) + 均线对齐(0-30) + 成交量确认(0-20) + 策略共振(0-20)

    ADX贡献:
        ADX>40 = 30分, ADX>30 = 20分, ADX>25 = 10分, ADX≤25 = 0分
    均线对齐:
        close同时高于EMA20和EMA50 = 30分, 仅高于一条 = 10分, 均不高于 = 0分
    成交量确认:
        volume > volume_ma×2 = 20分, volume > volume_ma×1.5 = 10分, 其余 = 0分
    策略共振:
        3个及以上策略同时激活 = 20分, 2个策略 = 10分, 1个策略 = 0分

    Args:
        features: 市场特征字典（同 strategy_gate 的 features）
        num_active_strategies: 同一币种上通过门控的策略数量

    Returns:
        int: SSS 评分，0-100
    """
    score: int = 0

    # ── ADX 贡献 (0-30) ──
    adx: float = float(features.get("adx", 0))
    if adx > 40:
        score += 30
    elif adx > 30:
        score += 20
    elif adx > 25:
        score += 10

    # ── 均线对齐 (0-30) ──
    close: float = float(features.get("close", 0))
    ema20: float = float(features.get("ema20", 0))
    ema50: float = float(features.get("ema50", 0))
    align_count: int = 0
    if ema20 > 0 and close > ema20:
        align_count += 1
    if ema50 > 0 and close > ema50:
        align_count += 1
    if align_count >= 2:
        score += 30
    elif align_count == 1:
        score += 10

    # ── 成交量确认 (0-20) ──
    volume: float = float(features.get("volume", 0))
    volume_ma: float = float(features.get("volume_ma", 0))
    if volume_ma > 0:
        ratio: float = volume / volume_ma
        if ratio > 2.0:
            score += 20
        elif ratio > 1.5:
            score += 10

    # ── 策略共振 (0-20) ──
    if num_active_strategies >= 3:
        score += 20
    elif num_active_strategies >= 2:
        score += 10

    return min(score, 100)


# ═══════════════════════════════════════════════════════════════════════
# 主调度函数
# ═══════════════════════════════════════════════════════════════════════

def schedule_signals(
    strategies: List[Dict], features_per_coin: Dict[str, Dict]
) -> List[Dict]:
    """主调度函数：遍历策略与币种，逐策略门控，计算 SSS，择优输出。

    调度流程：
        1. 对每个币种，遍历所有策略，执行门控检查
        2. 统计每个币种通过门控的策略数量
        3. 对每个通过门控的策略计算 SSS
        4. 同一币种多个策略激活时，选 SSS 最高者
        5. 组装输出信号列表

    Args:
        strategies: strategy_loader.load_strategies() 返回的策略记录列表，
                    每条含 name / tags / params_packs / best_params 等字段
        features_per_coin: 币种→特征映射，如：
            {"BTC": {"adx": 32.5, "close": 70000, ...},
             "ETH": {...}, "SOL": {...}}

    Returns:
        信号列表，每条字典含：
            coin:       币种标识
            strategy:   策略名
            sss:        信号强度评分 (0-100)
            params_pack: 参数包标识字符串（如 "PARAMS_BTC"）
    """
    # coin → 通过门控的策略列表 [{strategy_name, params}]
    coin_active: Dict[str, List[Dict]] = {}

    for coin, features in features_per_coin.items():
        passing: List[Dict] = []

        for strategy in strategies:
            strategy_name: str = strategy.get("name", "")
            if not strategy_name:
                continue

            # 可选：按 tags.coins 过滤不适用币种
            tags: Dict = strategy.get("tags", {})
            strategy_coins: List[str] = tags.get("coins", [])
            if strategy_coins and coin not in strategy_coins:
                continue

            # 门控检查
            if not strategy_gate(strategy_name, coin, features):
                continue

            # 提取参数包
            params: Dict = (
                strategy.get("best_params", {}).get(coin)
                or strategy.get("params_packs", {}).get(coin)
                or {}
            )

            passing.append({
                "strategy_name": strategy_name,
                "params": params,
            })

        if passing:
            coin_active[coin] = passing

    # 第二遍：计算 SSS + 择优
    result: List[Dict] = []

    for coin, passing_list in coin_active.items():
        features = features_per_coin[coin]
        num_active: int = len(passing_list)

        for item in passing_list:
            item["sss"] = calc_sss(features, num_active)

        # 按 SSS 降序排列，取最高者
        passing_list.sort(key=lambda x: x["sss"], reverse=True)
        best = passing_list[0]

        result.append({
            "coin": coin,
            "strategy": best["strategy_name"],
            "sss": best["sss"],
            "params_pack": f"PARAMS_{coin}",
        })

    return result


__all__ = [
    "MARKET_STRATEGY_MAP",
    "market_to_candidates",
    "strategy_gate",
    "calc_sss",
    "schedule_signals",
]
