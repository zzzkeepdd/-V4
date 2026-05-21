# -*- coding: utf-8 -*-
"""
AI引擎 — DeepSeek行情判断 + 策略匹配 + 放大系数决策 + 降级链。

职责：
1. extract_features: 手算ADX/EMA/ATR/BB/RSI（pandas+numpy，无ta库依赖）
2. call_deepseek: openai库调用DeepSeek API
3. analyze_market: Tier1 reasoner → Tier2 chat → Tier3 本地规则兜底
4. match_strategy: 行情映射 + 门控 + 参数包选择
5. decide_multiplier: 0-3 放大系数决策
6. AI决策日志写入 logs/ai_decisions.log

参考: specs/v4-strategy-supermarket-spec.md §1.1-1.3, §2.3, §4.2, §8
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 日志 ──────────────────────────────────────────────────────────────
_LOG_DIR: Optional[Path] = None


def _get_log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is not None:
        return _LOG_DIR
    candidates = [
        os.environ.get("V4_PROJECT_ROOT"),
        str(Path(__file__).resolve().parent),
    ]
    for candidate in candidates:
        if candidate:
            p = Path(candidate) / "logs"
            try:
                p.mkdir(parents=True, exist_ok=True)
                _LOG_DIR = p
                return p
            except Exception:
                continue
    _LOG_DIR = Path("logs")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _log_decision(tag: str, **fields: Any) -> str:
    """写入 AI 决策日志。格式同 spec §8。"""
    log_path = _get_log_dir() / "ai_decisions.log"
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    extras = " | ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    line = f"[{ts}] {tag} | {extras}" if extras else f"[{ts}] {tag}"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    return line


def _log_degrade(coin: str, from_stage: str, to_stage: str, reason: str) -> str:
    """记录 AI 降级链事件。与 data_layer.log_ai_degrade 格式兼容。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log_path = _get_log_dir() / "ai_decisions.log"
    line = (
        f"[{ts}] AI_DEGRADE | coin={coin.upper()} | "
        f"from={from_stage} | to={to_stage} | reason={reason}"
    )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    return line


# ── 行情状态枚举 ───────────────────────────────────────────────────────
MARKET_STATES = [
    "TRENDING_UP",
    "TRENDING_DOWN",
    "RANGING",
    "HIGH_VOLATILITY",
    "TREND_EXHAUSTION",
    "CHAOTIC",  # 兜底/无法判断
]

# ── 行情→策略类型映射 (spec §1.1) ─────────────────────────────────────
STATE_TO_STRATEGY_TYPES: Dict[str, List[str]] = {
    "TRENDING_UP": ["trend_following", "smc_structure", "bos_trailing_stop"],
    "TRENDING_DOWN": ["trend_following", "smc_structure", "bos_trailing_stop"],
    "RANGING": ["swing_range"],
    "HIGH_VOLATILITY": ["bb_breakout"],
    "TREND_EXHAUSTION": ["trend_exhaustion"],
    "CHAOTIC": [],
}

# ── 策略类型→策略文件名映射 ──────────────────────────────────────────
STRATEGY_TYPE_TO_FILE: Dict[str, str] = {
    "trend_following": "趋势回调入场",
    "smc_structure": "SMC_结构转换突破_ETH",
    "bos_trailing_stop": "BOS移动止损增强版",
    "swing_range": "摆动点区间反转",
    "bb_breakout": "BB挤压突破",
    "trend_exhaustion": "趋势衰竭反转",
}

# 反向：文件名→类型
FILE_TO_STRATEGY_TYPE: Dict[str, str] = {
    v: k for k, v in STRATEGY_TYPE_TO_FILE.items()
}

# ── 门控条件表 (spec §1.1) ────────────────────────────────────────────
# 每个策略类型的激活条件函数签名: (features: dict, df: pd.DataFrame) -> bool
GATE_CONDITIONS: Dict[str, Callable[[Dict[str, Any], pd.DataFrame], bool]] = {}


def _register_gate(name: str):
    """装饰器注册门控函数。"""
    def decorator(fn):
        GATE_CONDITIONS[name] = fn
        return fn
    return decorator


# ── 1. extract_features ──────────────────────────────────────────────
def extract_features(df: pd.DataFrame) -> Dict[str, Any]:
    """
    从 OHLCV DataFrame 手算技术指标。

    期望列: open, high, low, close, volume (小写或大写均可)
    返回 features dict:
        adx, adx_plus_di, adx_minus_di, adx_trend
        ema20, ema50, ema200
        close_vs_ema20, close_vs_ema50
        atr, atr_ma, atr_ratio
        bb_upper, bb_mid, bb_lower, bb_width_pct, bb_position_pct
        rsi, rsi_prev, rsi_divergence
        volume_ma, volume_ratio
        trend_bars, trend_direction
        swing_high, swing_low, range_width_pct, near_boundary
    """
    df = df.copy()
    # 统一列名小写
    rename = {}
    for col in list(df.columns):
        rename[col] = col.lower()
    df.rename(columns=rename, inplace=True)
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame 缺少必要列: {missing}")

    close_arr = df["close"].values.astype(np.float64)
    high_arr = df["high"].values.astype(np.float64)
    low_arr = df["low"].values.astype(np.float64)
    vol_arr = df.get("volume")
    if vol_arr is not None:
        vol_arr = vol_arr.values.astype(np.float64)
    n = len(close_arr)
    if n < 50:
        raise ValueError(f"数据量不足（{n} < 50 根K线）")

    last_close = float(close_arr[-1])

    # ── EMA ──
    def _ema(series: np.ndarray, period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1.0)
        result = np.empty_like(series)
        result[0] = series[0]
        for i in range(1, len(series)):
            result[i] = alpha * series[i] + (1.0 - alpha) * result[i - 1]
        return result

    ema20_arr = _ema(close_arr, 20)
    ema50_arr = _ema(close_arr, 50)
    ema200_arr = _ema(close_arr, 200)

    ema20 = float(ema20_arr[-1])
    ema50 = float(ema50_arr[-1])
    ema200 = float(ema200_arr[-1])
    close_vs_ema20 = "above" if last_close > ema20 else "below"
    close_vs_ema50 = "above" if last_close > ema50 else "below"

    # ── ATR ──
    def _tr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
        tr_vals = np.empty(len(high))
        tr_vals[0] = high[0] - low[0]
        for i in range(1, len(high)):
            h_l = high[i] - low[i]
            h_c = abs(high[i] - close[i - 1])
            l_c = abs(low[i] - close[i - 1])
            tr_vals[i] = max(h_l, h_c, l_c)
        return tr_vals

    tr_arr = _tr(high_arr, low_arr, close_arr)
    atr_arr = _ema(tr_arr, 14)  # Wilder's ATR uses EMA-like smoothing
    atr = float(atr_arr[-1])
    atr_ma_vals = np.convolve(atr_arr, np.ones(20) / 20, mode="valid")
    atr_ma = float(atr_ma_vals[-1]) if len(atr_ma_vals) > 0 else atr
    atr_ratio = atr / atr_ma if atr_ma > 0 else 1.0

    # ── ADX ──
    def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
        up_move = np.empty(len(high))
        down_move = np.empty(len(high))
        up_move[0] = 0.0
        down_move[0] = 0.0
        for i in range(1, len(high)):
            up = high[i] - high[i - 1]
            dw = low[i - 1] - low[i]
            up_move[i] = up if up > dw and up > 0 else 0.0
            down_move[i] = dw if dw > up and dw > 0 else 0.0

        atr_smooth = _ema(tr_arr, period)
        plus_di = 100.0 * _ema(up_move, period) / atr_smooth
        minus_di = 100.0 * _ema(down_move, period) / atr_smooth
        # Replace NaN/Inf
        plus_di = np.nan_to_num(plus_di, nan=0.0, posinf=0.0, neginf=0.0)
        minus_di = np.nan_to_num(minus_di, nan=0.0, posinf=0.0, neginf=0.0)
        denom = plus_di + minus_di
        dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)
        adx_arr = _ema(dx, period)
        return adx_arr, plus_di, minus_di

    adx_arr, plus_di_arr, minus_di_arr = _adx(high_arr, low_arr, close_arr, 14)
    adx = float(adx_arr[-1])
    adx_plus_di = float(plus_di_arr[-1])
    adx_minus_di = float(minus_di_arr[-1])
    adx_trend = "up" if adx_plus_di > adx_minus_di else "down"

    # ── Bollinger Bands ──
    bb_period = 20
    bb_std_mult = 2.0
    bb_mid_arr = _ema(close_arr, bb_period)
    # rolling std
    bb_std_arr = np.empty(n)
    bb_std_arr[:] = np.nan
    for i in range(bb_period - 1, n):
        bb_std_arr[i] = np.std(close_arr[i - bb_period + 1 : i + 1], ddof=1)
    bb_upper_arr = bb_mid_arr + bb_std_mult * bb_std_arr
    bb_lower_arr = bb_mid_arr - bb_std_mult * bb_std_arr

    bb_upper = float(bb_upper_arr[-1])
    bb_mid = float(bb_mid_arr[-1])
    bb_lower = float(bb_lower_arr[-1])
    bb_range = bb_upper - bb_lower
    bb_width_pct = (bb_range / bb_mid * 100.0) if bb_mid > 0 else 0.0
    bb_position_pct = ((last_close - bb_lower) / bb_range * 100.0) if bb_range > 0 else 50.0

    # ── RSI ──
    def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = _ema(gain, period)
        avg_loss = _ema(loss, period)
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, 0.0)
        return 100.0 - 100.0 / (1.0 + rs)

    rsi_arr = _rsi(close_arr, 14)
    rsi = float(rsi_arr[-1])
    rsi_prev = float(rsi_arr[-2]) if n >= 2 else rsi

    # RSI 背离检测（简化版：最近5根内 close 创新高/低但 RSI 未同步）
    rsi_divergence = False
    lookback_div = min(10, n - 1)
    if lookback_div >= 5:
        price_window = close_arr[-lookback_div:]
        rsi_window = rsi_arr[-lookback_div:]
        price_peak_idx = int(np.argmax(price_window))
        price_trough_idx = int(np.argmin(price_window))
        rsi_at_price_peak = rsi_window[price_peak_idx]
        rsi_at_price_trough = rsi_window[price_trough_idx]
        # 顶背离：价格新高但 RSI 更低
        if price_peak_idx == len(price_window) - 1 and rsi_at_price_peak < np.max(rsi_window) * 0.95:
            rsi_divergence = True
        # 底背离：价格新低但 RSI 更高
        elif price_trough_idx == len(price_window) - 1 and rsi_at_price_trough > np.min(rsi_window) * 1.05:
            rsi_divergence = True

    # ── 成交量 ──
    volume_ma_val = 1.0
    volume_ratio = 1.0
    if vol_arr is not None and len(vol_arr) >= 20:
        volume_ma_val = float(np.mean(vol_arr[-20:]))
        last_vol = float(vol_arr[-1])
        volume_ratio = last_vol / volume_ma_val if volume_ma_val > 0 else 1.0

    # ── 趋势 K 线计数 ──
    trend_bars = 0
    trend_direction = "neutral"
    if n >= 2:
        if last_close > close_arr[-2]:
            trend_direction = "up"
        elif last_close < close_arr[-2]:
            trend_direction = "down"
        # 统计连续同向K线数
        for i in range(n - 1, 0, -1):
            if trend_direction == "up" and close_arr[i] > close_arr[i - 1]:
                trend_bars += 1
            elif trend_direction == "down" and close_arr[i] < close_arr[i - 1]:
                trend_bars += 1
            else:
                break

    # ── 摆动点 / 区间 ──
    swing_lookback = 20
    swing_window = close_arr[-swing_lookback:] if n >= swing_lookback else close_arr
    swing_high = float(np.max(swing_window))
    swing_low = float(np.min(swing_window))
    range_width_pct = ((swing_high - swing_low) / swing_low * 100.0) if swing_low > 0 else 0.0
    # 距离边界有多近（0=正好在边界，1=在对面边界）
    range_span = swing_high - swing_low
    if range_span > 0:
        near_boundary = min(
            abs(last_close - swing_high) / range_span,
            abs(last_close - swing_low) / range_span,
        )
    else:
        near_boundary = 1.0
    # "close距区间边界<ATR" 的判断：归一化后等价于距边界距离 < ATR/range_span
    near_boundary_atr = (abs(last_close - swing_high) < atr) or (abs(last_close - swing_low) < atr)

    features = {
        "adx": round(adx, 2),
        "adx_plus_di": round(adx_plus_di, 2),
        "adx_minus_di": round(adx_minus_di, 2),
        "adx_trend": adx_trend,
        "ema20": round(ema20, 6),
        "ema50": round(ema50, 6),
        "ema200": round(ema200, 6),
        "close_vs_ema20": close_vs_ema20,
        "close_vs_ema50": close_vs_ema50,
        "atr": round(atr, 6),
        "atr_ma": round(atr_ma, 6),
        "atr_ratio": round(atr_ratio, 2),
        "bb_upper": round(bb_upper, 6),
        "bb_mid": round(bb_mid, 6),
        "bb_lower": round(bb_lower, 6),
        "bb_width_pct": round(bb_width_pct, 2),
        "bb_position_pct": round(bb_position_pct, 1),
        "rsi": round(rsi, 1),
        "rsi_prev": round(rsi_prev, 1),
        "rsi_divergence": rsi_divergence,
        "volume_ma": round(volume_ma_val, 2),
        "volume_ratio": round(volume_ratio, 2),
        "trend_bars": trend_bars,
        "trend_direction": trend_direction,
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
        "range_width_pct": round(range_width_pct, 2),
        "near_boundary": round(near_boundary, 3),
        "near_boundary_atr": near_boundary_atr,
    }
    return features


# ── 2. call_deepseek ──────────────────────────────────────────────────
def _get_deepseek_client():
    """获取 DeepSeek openai 客户端。API Key 从环境变量读取。"""
    import openai

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def call_deepseek(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
    temperature: float = 0.3,
    max_tokens: int = 512,
    timeout: float = 10.0,
) -> Tuple[str, bool]:
    """
    调用 DeepSeek API。

    返回 (response_text, success)。
    超时/网络错误返回 ("", False)。
    """
    client = _get_deepseek_client()
    try:
        import openai

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        content = response.choices[0].message.content or ""
        return content.strip(), True
    except Exception as exc:
        return str(exc), False


def _parse_json_response(text: str) -> Dict[str, Any]:
    """从 DeepSeek 响应中提取 JSON。"""
    if not text:
        return {}
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试找第一个 {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# ── 3. analyze_market ────────────────────────────────────────────────
MARKET_ANALYSIS_SYSTEM = """You are a crypto trading market analyst. You receive structured OHLCV-derived technical features and must output STRICT JSON only.

Output format:
{"market_state": "TRENDING_UP|TRENDING_DOWN|RANGING|HIGH_VOLATILITY|TREND_EXHAUSTION|CHAOTIC", "confidence": 0-100, "reasoning": "short explanation"}

Classification rules:
- TRENDING_UP: ADX>25, price above EMA20/EMA50, positive momentum
- TRENDING_DOWN: ADX>25, price below EMA20/EMA50, negative momentum
- RANGING: ADX<20, price oscillating in a narrow range
- HIGH_VOLATILITY: ATR/ATR_MA > 1.5, BB width expanding, volume spike
- TREND_EXHAUSTION: trend running 20+ bars, RSI divergence detected
- CHAOTIC: conflicting signals, cannot determine

Do NOT include any text outside the JSON."""


def analyze_market(
    features: Dict[str, Any],
    coin: str = "BTC",
    enable_deepseek: bool = True,
) -> Dict[str, Any]:
    """
    三层降级判断行情状态。

    Tier 1: DeepSeek reasoner (deepseek-reasoner)
    Tier 2: DeepSeek chat (deepseek-chat)
    Tier 3: 本地规则引擎

    返回:
        {
            "market_state": str,
            "confidence": float,
            "reasoning": str,
            "tier": "reasoner" | "chat" | "local",
            "degrade_path": [str],
        }
    """
    degrade_path: List[str] = []
    result: Dict[str, Any] = {}

    # 构造结构化输入
    input_json = json.dumps({
        "coin": coin.upper(),
        "features": {
            "adx": features["adx"],
            "close_vs_ema20": features["close_vs_ema20"],
            "close_vs_ema50": features["close_vs_ema50"],
            "atr_ratio": features["atr_ratio"],
            "bb_width_pct": features["bb_width_pct"],
            "volume_ratio": features["volume_ratio"],
            "trend_bars": features["trend_bars"],
            "rsi_divergence": features["rsi_divergence"],
        },
    }, ensure_ascii=False)

    # ── Tier 1: reasoner ──
    if enable_deepseek:
        degrade_path.append("reasoner")
        text, ok = call_deepseek(
            system_prompt=MARKET_ANALYSIS_SYSTEM,
            user_message=input_json,
            model="deepseek-reasoner",
            timeout=10.0,
        )
        if ok:
            parsed = _parse_json_response(text)
            if parsed.get("market_state") in MARKET_STATES:
                result = {
                    "market_state": parsed["market_state"],
                    "confidence": float(parsed.get("confidence", 50)),
                    "reasoning": parsed.get("reasoning", text[:200]),
                    "tier": "reasoner",
                    "degrade_path": list(degrade_path),
                }
                _log_decision(
                    "MARKET_ANALYSIS",
                    coin=coin.upper(),
                    state=result["market_state"],
                    confidence=result["confidence"],
                    tier="reasoner",
                )
                return result
        _log_degrade(coin, "reasoner", "chat", f"reasoner failed: {text[:100]}")

    # ── Tier 2: chat ──
    if enable_deepseek:
        degrade_path.append("chat")
        text, ok = call_deepseek(
            system_prompt=MARKET_ANALYSIS_SYSTEM,
            user_message=input_json,
            model="deepseek-chat",
            timeout=10.0,
        )
        if ok:
            parsed = _parse_json_response(text)
            if parsed.get("market_state") in MARKET_STATES:
                result = {
                    "market_state": parsed["market_state"],
                    "confidence": float(parsed.get("confidence", 50)),
                    "reasoning": parsed.get("reasoning", text[:200]),
                    "tier": "chat",
                    "degrade_path": list(degrade_path),
                }
                _log_decision(
                    "MARKET_ANALYSIS",
                    coin=coin.upper(),
                    state=result["market_state"],
                    confidence=result["confidence"],
                    tier="chat",
                )
                return result
        _log_degrade(coin, "chat", "local", f"chat failed: {text[:100]}")

    # ── Tier 3: 本地规则引擎 ──
    degrade_path.append("local")
    state, confidence, reasoning = _local_market_rules(features)
    result = {
        "market_state": state,
        "confidence": confidence,
        "reasoning": reasoning,
        "tier": "local",
        "degrade_path": list(degrade_path),
    }
    _log_decision(
        "MARKET_ANALYSIS",
        coin=coin.upper(),
        state=state,
        confidence=confidence,
        tier="local",
    )
    _log_degrade(coin, "chat", "local", "DeepSeek unavailable, using local rules")
    return result


def _local_market_rules(features: Dict[str, Any]) -> Tuple[str, float, str]:
    """
    本地规则引擎 (spec §1.1 兜底)。
    规则: ADX>25→TRENDING, ADX<20→RANGING, ATR/ATR_MA>1.5→HIGH_VOL
    """
    adx = features["adx"]
    atr_ratio = features["atr_ratio"]
    rsi_div = features["rsi_divergence"]
    trend_bars = features["trend_bars"]
    close_vs_ema20 = features["close_vs_ema20"]

    # 1) 趋势衰竭
    if trend_bars >= 20 and rsi_div:
        return "TREND_EXHAUSTION", 70, f"趋势运行{trend_bars}根K线+RSI背离"
    # 2) 高波动
    if atr_ratio > 1.5:
        return "HIGH_VOLATILITY", 65, f"ATR比率{atr_ratio}>1.5"
    # 3) 震荡
    if adx < 20:
        return "RANGING", 60, f"ADX={adx}<20"
    # 4) 趋势
    if adx >= 25:
        if close_vs_ema20 == "above":
            return "TRENDING_UP", 70, f"ADX={adx}>=25,价格在EMA20上方"
        else:
            return "TRENDING_DOWN", 70, f"ADX={adx}>=25,价格在EMA20下方"
    # 5) 混沌兜底
    return "CHAOTIC", 30, f"ADX={adx}在20-25之间无法判"


# ── 4. match_strategy ─────────────────────────────────────────────────
# ── 门控实现 ──
@_register_gate("trend_following")
def _gate_trend_following(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    return features["adx"] > 25 and features["close_vs_ema20"] == "above"


@_register_gate("smc_structure")
def _gate_smc_structure(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    # ADX>25 + 检测BOS结构(简化：趋势方向明确+近收有突破)
    if features["adx"] <= 25:
        return False
    # 简化BOS检测：close突破近期摆动点
    close_arr = df["close"].values.astype(np.float64) if "close" in df.columns else None
    if close_arr is None:
        return features["close_vs_ema50"] == "above"
    n = len(close_arr)
    if n < 5:
        return False
    bos = False
    if features["adx_trend"] == "up":
        # 多头BOS：close突破前5根高点
        prev_high = np.max(close_arr[-6:-1]) if n >= 6 else close_arr[-2]
        bos = close_arr[-1] > prev_high
    else:
        prev_low = np.min(close_arr[-6:-1]) if n >= 6 else close_arr[-2]
        bos = close_arr[-1] < prev_low
    # 简化fib回调检测：价格距近期极值
    if not bos:
        return False
    swing_high = features["swing_high"]
    swing_low = features["swing_low"]
    last_close = float(close_arr[-1])
    if swing_high > swing_low:
        retrace_pct = (last_close - swing_low) / (swing_high - swing_low)
        return 0.35 <= retrace_pct <= 0.75  # 放宽至fib 0.382-0.786
    return bos


@_register_gate("bos_trailing_stop")
def _gate_bos_trailing(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    return features["adx"] > 25 and features["close_vs_ema50"] == "above"


@_register_gate("swing_range")
def _gate_swing_range(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    return features["adx"] < 20 and features["near_boundary_atr"]


@_register_gate("bb_breakout")
def _gate_bb_breakout(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    return features["bb_width_pct"] > 2.0 and features["volume_ratio"] > 1.5


@_register_gate("trend_exhaustion")
def _gate_trend_exhaustion(features: Dict[str, Any], df: pd.DataFrame) -> bool:
    return features["trend_bars"] >= 20 and features["rsi_divergence"]


def _compute_sss(
    features: Dict[str, Any],
    active_strategy_types: List[str],
    strategy_type: str,
) -> float:
    """计算信号强度评分 SSS (spec §1.2)，范围 0-100。"""
    adx = features["adx"]
    # ADX 贡献 0-30
    if adx > 40:
        adx_score = 30.0
    elif adx > 30:
        adx_score = 20.0
    elif adx > 25:
        adx_score = 10.0
    else:
        adx_score = 0.0

    # 均线对齐贡献 0-30
    ma_score = 0.0
    close = features.get("last_close", 0)
    ema20 = features["ema20"]
    ema50 = features["ema50"]
    ema200 = features["ema200"]
    aligned_count = 0
    if features["close_vs_ema20"] == "above":
        aligned_count += 1
    if features["close_vs_ema50"] == "above":
        aligned_count += 1
    if ema200 > 0 and close > ema200:
        aligned_count += 1
    if aligned_count >= 3:
        ma_score = 30.0
    elif aligned_count >= 2:
        ma_score = 20.0
    elif aligned_count >= 1:
        ma_score = 10.0

    # 成交量确认 0-20
    vol_ratio = features["volume_ratio"]
    if vol_ratio > 2.0:
        vol_score = 20.0
    elif vol_ratio > 1.5:
        vol_score = 10.0
    else:
        vol_score = 0.0

    # 策略共振 0-20
    active_count = len(active_strategy_types)
    if active_count >= 3:
        res_score = 20.0
    elif active_count >= 2:
        res_score = 10.0
    else:
        res_score = 0.0

    return adx_score + ma_score + vol_score + res_score


def match_strategy(
    market_state: str,
    features: Dict[str, Any],
    coin: str,
    strategies: Optional[List[Dict[str, Any]]] = None,
    df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    行情映射 + 门控 + 选参数包。

    参数:
        market_state: DeepSeek/本地规则输出的行情状态
        features: extract_features 输出
        coin: 币种 (BTC/ETH/SOL)
        strategies: strategy_loader.load_strategies 输出（可选，不传则使用空默认）
        df: 原始 OHLCV DataFrame（门控可能需要）

    返回:
        {
            "matched": bool,
            "strategy": str | None,       # 策略名（文件名stem）
            "strategy_type": str | None,  # 策略类型
            "params_pack": dict | None,   # 参数包
            "sss": float,
            "active_strategies": [str],   # 所有候选并激活的类型
            "market_state": str,
            "tier": str,
        }
    """
    strategies = strategies or []
    df = df or pd.DataFrame()
    # 建立 文件名→策略记录 索引
    strat_map: Dict[str, Dict[str, Any]] = {}
    for s in strategies:
        if isinstance(s, dict) and s.get("name"):
            strat_map[s["name"]] = s

    candidate_types = STATE_TO_STRATEGY_TYPES.get(market_state, [])
    active_types: List[str] = []

    for stype in candidate_types:
        gate_fn = GATE_CONDITIONS.get(stype)
        if gate_fn is None:
            continue
        try:
            if gate_fn(features, df):
                active_types.append(stype)
        except Exception:
            continue

    if not active_types:
        _log_decision(
            "STRATEGY_MATCH",
            coin=coin.upper(),
            matched="NONE",
            sss=0,
            market_state=market_state,
        )
        return {
            "matched": False,
            "strategy": None,
            "strategy_type": None,
            "params_pack": None,
            "sss": 0,
            "active_strategies": [],
            "market_state": market_state,
        }

    # 按 SSS 排序选最高分
    scored: List[Tuple[str, float, str, Optional[Dict]]] = []
    for stype in active_types:
        file_name = STRATEGY_TYPE_TO_FILE.get(stype)
        sss = _compute_sss(features, active_types, stype)
        params_pack = None
        if file_name and file_name in strat_map:
            packs = strat_map[file_name].get("params_packs", {})
            params_pack = packs.get(coin) or packs.get("BTC", {})
        scored.append((stype, sss, file_name or stype, params_pack))

    scored.sort(key=lambda x: (-x[1], x[0]))
    best = scored[0]
    best_type, best_sss, best_file, best_params = best

    _log_decision(
        "STRATEGY_MATCH",
        coin=coin.upper(),
        matched=best_file,
        sss=round(best_sss),
        params=best_params.get("__name__", f"PARAMS_{coin}") if best_params else f"PARAMS_{coin}",
    )

    return {
        "matched": True,
        "strategy": best_file,
        "strategy_type": best_type,
        "params_pack": best_params,
        "sss": round(best_sss, 1),
        "active_strategies": [t for t, _, _, _ in scored],
        "market_state": market_state,
    }


# ── 5. decide_multiplier ──────────────────────────────────────────────
MULTIPLIER_SYSTEM = """You are a crypto risk manager. Based on market confidence and strategy resonance, output a position multiplier (0-3).

Output STRICT JSON:
{"multiplier": <0-3 float>, "reasoning": "short explanation"}

Guidelines:
- 3+ strategies active + ADX>30 + volume surge → 2.5-3.0
- 2 strategies active + ADX>25 → 1.5-2.5
- 1 strategy + ADX>25 → 1.0-1.5
- Mixed/conflicting signals → 0.5-1.0
- Extreme volatility, unclear direction → 0 (sit out)

Do NOT include any text outside the JSON."""


def decide_multiplier(
    features: Dict[str, Any],
    match_result: Dict[str, Any],
    coin: str = "BTC",
    sss: Optional[float] = None,
) -> float:
    """
    决定 DeepSeek 放大系数 (0-3)。

    优先使用 DeepSeek 决策，降级到 SSS 规则。

    返回: 0.0-3.0 的 float
    """
    coin = coin.upper()
    sss = sss if sss is not None else match_result.get("sss", 0)
    active_count = len(match_result.get("active_strategies", []))

    # 构造 DeepSeek 输入
    input_json = json.dumps({
        "coin": coin,
        "features": {
            "adx": features["adx"],
            "atr_ratio": features["atr_ratio"],
            "volume_ratio": features["volume_ratio"],
            "rsi": features["rsi"],
            "trend_bars": features["trend_bars"],
            "rsi_divergence": features["rsi_divergence"],
        },
        "active_strategies": match_result.get("active_strategies", []),
        "active_count": active_count,
        "sss": sss,
    }, ensure_ascii=False)

    # Tier 1: reasoner
    text, ok = call_deepseek(
        system_prompt=MULTIPLIER_SYSTEM,
        user_message=input_json,
        model="deepseek-reasoner",
        timeout=10.0,
    )
    if ok:
        parsed = _parse_json_response(text)
        mult = parsed.get("multiplier")
        if isinstance(mult, (int, float)) and 0 <= mult <= 3:
            _log_decision(
                "POSITION_SIZE",
                coin=coin,
                base="from_ai",
                multiplier=f"{mult}x",
                sss=round(sss, 1),
            )
            return round(float(mult), 2)
        _log_degrade(coin, "reasoner_mult", "chat_mult", f"parse failed: {text[:100]}")

    # Tier 2: chat
    text, ok = call_deepseek(
        system_prompt=MULTIPLIER_SYSTEM,
        user_message=input_json,
        model="deepseek-chat",
        timeout=10.0,
    )
    if ok:
        parsed = _parse_json_response(text)
        mult = parsed.get("multiplier")
        if isinstance(mult, (int, float)) and 0 <= mult <= 3:
            _log_decision(
                "POSITION_SIZE",
                coin=coin,
                base="from_ai",
                multiplier=f"{mult}x",
                sss=round(sss, 1),
            )
            return round(float(mult), 2)
        _log_degrade(coin, "chat_mult", "local_mult", f"parse failed: {text[:100]}")

    # Tier 3: 本地 SSS 规则 (spec §2.3)
    mult = _local_multiplier_rules(features, active_count, sss)
    _log_decision(
        "POSITION_SIZE",
        coin=coin,
        base="from_sss",
        multiplier=f"{mult}x",
        sss=round(sss, 1),
        active_count=active_count,
    )
    return mult


def _local_multiplier_rules(
    features: Dict[str, Any], active_count: int, sss: float
) -> float:
    """本地规则决策放大系数 (spec §2.3)。"""
    adx = features["adx"]
    vol_ratio = features["volume_ratio"]
    atr_ratio = features["atr_ratio"]

    # 剧烈波动 + 方向不明 → 0
    if atr_ratio > 2.5 and features.get("rsi", 50) > 40 and features.get("rsi", 50) < 60:
        return 0.0

    # 行情混沌/策略冲突 → 保守
    if active_count == 0 or sss < 30:
        return 0.5

    # 3策略共振 + ADX>30 + 量能放大
    if active_count >= 3 and adx > 30 and vol_ratio > 1.5:
        return 3.0

    # 2策略共振 + ADX>25
    if active_count >= 2 and adx > 25:
        return 2.0

    # 单策略 + ADX>25
    if active_count >= 1 and adx > 25:
        return 1.5

    # 默认保守
    return 1.0


# ── 6. 一站式流程 ─────────────────────────────────────────────────────
def run_ai_pipeline(
    df: pd.DataFrame,
    coin: str = "BTC",
    strategies: Optional[List[Dict[str, Any]]] = None,
    enable_deepseek: bool = True,
) -> Dict[str, Any]:
    """
    完整 AI 管道：特征提取→行情判断→策略匹配→放大系数。

    返回:
        {
            "coin": str,
            "features": dict,
            "market_analysis": dict,
            "strategy_match": dict,
            "multiplier": float,
        }
    """
    features = extract_features(df)
    market = analyze_market(features, coin=coin, enable_deepseek=enable_deepseek)
    match = match_strategy(market["market_state"], features, coin, strategies, df)
    mult = decide_multiplier(features, match, coin=coin)
    return {
        "coin": coin.upper(),
        "features": features,
        "market_analysis": market,
        "strategy_match": match,
        "multiplier": mult,
    }


# ── 公开接口 ──────────────────────────────────────────────────────────
__all__ = [
    "extract_features",
    "call_deepseek",
    "analyze_market",
    "match_strategy",
    "decide_multiplier",
    "run_ai_pipeline",
    "MARKET_STATES",
    "STATE_TO_STRATEGY_TYPES",
    "STRATEGY_TYPE_TO_FILE",
    "FILE_TO_STRATEGY_TYPE",
    "GATE_CONDITIONS",
]
