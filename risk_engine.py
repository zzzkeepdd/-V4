# -*- coding: utf-8 -*-
"""
Risk engine for V4.

包含:
- 仓位计算
- 4-4-2 资金/币种分配
- 风控拒单
- 满仓信号队列
- 爆后传染防御
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import math
import re


# ===== 硬编码风控常量 =====
MAX_OPEN_POSITIONS = 3
SIGNAL_QUEUE_CAPACITY = 5
MAX_POSITION_PCT = 0.05
MIN_POSITION_PCT = 0.01
HARD_CAP_LOSS_RATIO = 0.03
DAILY_LOSS_CIRCUIT_BREAKER = 0.05
ATR_CIRCUIT_BREAKER = 3.0
SAME_STRATEGY_MAX_PER_HOUR = 3
MARGIN_CONTAGION_THRESHOLD = 150.0
MARGIN_CONTAGION_TARGET = 200.0
DEFAULT_LEVERAGE_FLOOR = 2.0

_COIN_SHARE_PLAN = (0.4, 0.4, 0.2)
_KNOWN_QUOTES = (
    "USDT",
    "USDC",
    "BUSD",
    "USD",
    "BTC",
    "ETH",
    "TRY",
    "EUR",
    "JPY",
    "GBP",
    "KRW",
)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(int(value))
        return float(value)
    except Exception:
        return default


def _normalize_ratio(value: Any) -> float:
    """
    把 2 / 2% / 0.02 统一成 0.02。
    """
    ratio = _to_float(value, 0.0)
    if ratio == 0.0:
        return 0.0
    if abs(ratio) > 1.0 and abs(ratio) <= 100.0:
        return ratio / 100.0
    return ratio


def _normalize_percent_points(value: Any) -> float:
    """
    把 1.2 / 120 统一成 120。
    """
    pct = _to_float(value, 0.0)
    if pct == 0.0:
        return 0.0
    if abs(pct) <= 10.0:
        return pct * 100.0
    return pct


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        try:
            ts = float(text)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts)
        except Exception:
            return None


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in {"long", "buy", "bull", "bullish", "open_long"}:
        return "long"
    if text in {"short", "sell", "bear", "bearish", "open_short"}:
        return "short"
    if "long" in text or "buy" in text:
        return "long"
    if "short" in text or "sell" in text:
        return "short"
    return ""


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "/" in text:
        return text.split("/")[0]
    if "-" in text:
        return text.split("-")[0]
    for quote in sorted(_KNOWN_QUOTES, key=len, reverse=True):
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)]
    return text


def _signal_strength(signal: Dict[str, Any]) -> float:
    for key in ("sss", "score", "strength", "rank_score", "signal_score"):
        if key in signal:
            return _to_float(signal.get(key), 0.0)
    return 0.0


def _extract_coin(signal: Dict[str, Any]) -> str:
    for key in ("coin", "asset", "base", "symbol", "pair", "instrument"):
        raw = signal.get(key)
        if raw:
            coin = _normalize_symbol(raw)
            if coin:
                return coin
    return ""


def _extract_strategy(position: Dict[str, Any]) -> str:
    for key in ("strategy", "strategy_name", "strategyName", "algo", "name"):
        value = position.get(key)
        if value:
            return str(value).strip()
    return ""


def _extract_timestamp(position: Dict[str, Any]) -> Optional[datetime]:
    for key in ("timestamp", "time", "created_at", "createdAt", "open_time", "openTime"):
        if key in position:
            ts = _parse_datetime(position.get(key))
            if ts is not None:
                return ts
    return None


def calc_position(
    capital_limit: float,
    position_pct: float,
    leverage: int,
    multiplier: float,
    entry_price: float,
    stop_loss_price: float,
) -> Optional[Dict[str, Any]]:
    """
    计算仓位。

    返回 None 表示硬帽不通过。
    """
    capital_limit = _to_float(capital_limit, 0.0)
    position_ratio = _normalize_ratio(position_pct)
    leverage_val = _to_float(leverage, 0.0)
    multiplier_val = _to_float(multiplier, 0.0)
    entry = _to_float(entry_price, 0.0)
    stop = _to_float(stop_loss_price, 0.0)

    if capital_limit <= 0 or position_ratio <= 0 or leverage_val <= 0 or multiplier_val <= 0:
        return None
    if entry <= 0 or stop <= 0:
        return None
    if position_ratio < MIN_POSITION_PCT or position_ratio > MAX_POSITION_PCT:
        return None

    each_investment = capital_limit * position_ratio
    nominal_value = each_investment * leverage_val * multiplier_val
    stop_gap_ratio = abs(entry - stop) / entry
    max_loss = nominal_value * stop_gap_ratio * 1.15
    hard_cap_check = max_loss <= capital_limit * HARD_CAP_LOSS_RATIO
    if not hard_cap_check:
        return None

    contracts = nominal_value / entry
    margin_required = each_investment * multiplier_val
    return {
        "contracts": round(contracts, 8),
        "nominal_value": round(nominal_value, 8),
        "max_loss": round(max_loss, 8),
        "margin_required": round(margin_required, 8),
        "hard_cap_check": True,
    }


def allocate_coin_share(signals: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    4-4-2 币种分配。

    只返回活跃币种，不补空缺币种。
    同币种多策略只合并为一个币种份额。
    """
    if not signals:
        return {}

    grouped: Dict[str, Tuple[float, int]] = {}
    for idx, signal in enumerate(signals):
        if not isinstance(signal, dict):
            continue
        coin = _extract_coin(signal)
        if not coin:
            continue
        strength = _signal_strength(signal)
        previous = grouped.get(coin)
        if previous is None or strength > previous[0]:
            grouped[coin] = (strength, idx)

    if not grouped:
        return {}

    ranked = sorted(
        grouped.items(),
        key=lambda item: (-item[1][0], item[1][1], item[0]),
    )

    allocation: Dict[str, float] = {}
    for coin, (_, _) in ranked[: len(_COIN_SHARE_PLAN)]:
        allocation[coin] = _COIN_SHARE_PLAN[len(allocation)]
    return allocation


def risk_check(
    position: Dict[str, Any],
    current_positions: List[Dict[str, Any]],
    daily_pnl: float,
    atr_ratio: float,
) -> Tuple[bool, str]:
    """
    统一风控拒单。

    返回 (通过, 原因)。
    """
    position = position or {}
    current_positions = current_positions or []

    new_symbol = _normalize_symbol(position.get("symbol") or position.get("coin"))
    new_side = _normalize_side(
        position.get("side")
        or position.get("direction")
        or position.get("posSide")
        or position.get("positionSide")
    )
    new_strategy = _extract_strategy(position)
    anchor_time = _extract_timestamp(position) or datetime.utcnow()

    # 1) 同币种不双向
    for current in current_positions:
        if not isinstance(current, dict):
            continue
        cur_symbol = _normalize_symbol(current.get("symbol") or current.get("coin"))
        cur_side = _normalize_side(
            current.get("side")
            or current.get("direction")
            or current.get("posSide")
            or current.get("positionSide")
        )
        if new_symbol and cur_symbol and new_symbol == cur_symbol and new_side and cur_side and new_side != cur_side:
            return False, f"反向信号：{new_symbol} 已有 {cur_side} 持仓，需先平仓再开仓"

    # 2) 逐仓要求
    isolated_flag = position.get("isolated")
    if isolated_flag is not None and not bool(isolated_flag):
        return False, "非逐仓模式，拒绝开仓"

    # 3) 日内亏损熔断
    pnl_ratio = _normalize_ratio(daily_pnl)
    if pnl_ratio <= -DAILY_LOSS_CIRCUIT_BREAKER:
        return False, "日内亏损熔断：杠杆降至2x后再评估"

    # 4) ATR 波动熔断
    atr_value = _to_float(atr_ratio, 0.0)
    if atr_value > ATR_CIRCUIT_BREAKER:
        return False, "ATR波动熔断：暂停新开仓"

    # 5) 同策略频率限制
    if new_strategy:
        window_start = anchor_time - timedelta(hours=1)
        same_strategy_count = 0
        for current in current_positions:
            if not isinstance(current, dict):
                continue
            if _extract_strategy(current) != new_strategy:
                continue
            ts = _extract_timestamp(current)
            if ts is None or window_start <= ts <= anchor_time:
                same_strategy_count += 1
        if same_strategy_count >= SAME_STRATEGY_MAX_PER_HOUR:
            return False, f"同策略每小时最多 {SAME_STRATEGY_MAX_PER_HOUR} 次信号"

    # 6) 总持仓限制
    active_count = sum(1 for item in current_positions if isinstance(item, dict))
    if active_count >= MAX_OPEN_POSITIONS:
        return False, f"总持仓超过限制：最多 {MAX_OPEN_POSITIONS} 笔"

    return True, "通过"


class SignalQueue:
    """容量 5 的满仓信号队列，按 SSS 降序。"""

    def __init__(self, capacity: int = SIGNAL_QUEUE_CAPACITY):
        self.capacity = max(1, int(capacity))
        self._items: List[Dict[str, Any]] = []
        self._seq = 0

    def __len__(self) -> int:
        return len(self._items)

    def _sort_items(self) -> None:
        self._items.sort(key=lambda item: (-_signal_strength(item), item["_seq"]))

    def push(self, signal: Dict[str, Any]) -> bool:
        if not isinstance(signal, dict):
            return False
        item = dict(signal)
        item["_seq"] = self._seq
        item["_sss"] = _signal_strength(item)
        self._seq += 1

        if len(self._items) < self.capacity:
            self._items.append(item)
            self._sort_items()
            return True

        self._sort_items()
        weakest = self._items[-1]
        if item["_sss"] <= _signal_strength(weakest):
            return False

        self._items.pop(-1)
        self._items.append(item)
        self._sort_items()
        return True

    def pop_best(self) -> Optional[Dict[str, Any]]:
        if not self._items:
            return None
        self._sort_items()
        item = self._items.pop(0)
        item.pop("_seq", None)
        item.pop("_sss", None)
        return item

    def snapshot(self) -> List[Dict[str, Any]]:
        self._sort_items()
        result: List[Dict[str, Any]] = []
        for item in self._items:
            clone = dict(item)
            clone.pop("_seq", None)
            clone.pop("_sss", None)
            result.append(clone)
        return result


def check_margin_contagion(
    positions: List[Dict[str, Any]],
    exchange_margin_data: Dict[str, Any],
) -> List[str]:
    """
    爆后传染防御。

    返回需要执行的动作列表。
    """
    actions: List[str] = []
    positions = positions or []
    exchange_margin_data = exchange_margin_data or {}

    account_force_close = bool(exchange_margin_data.get("force_close_all"))
    global_can_reduce = exchange_margin_data.get("can_reduce", True)
    per_symbol_margin = exchange_margin_data.get("positions")
    if not isinstance(per_symbol_margin, dict):
        per_symbol_margin = {}

    if account_force_close:
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or position.get("coin") or "").strip() or "UNKNOWN"
            side = str(position.get("side") or position.get("direction") or "").strip()
            actions.append(f"平仓 {symbol} {side}".strip())
        return actions

    normalized_positions: List[Tuple[float, Dict[str, Any]]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        margin_rate = _normalize_percent_points(
            position.get("margin_rate")
            or position.get("marginRatio")
            or position.get("margin_ratio")
            or position.get("position_margin_rate")
        )
        normalized_positions.append((margin_rate, position))

    normalized_positions.sort(key=lambda item: item[0])

    for margin_rate, position in normalized_positions:
        if margin_rate >= MARGIN_CONTAGION_THRESHOLD:
            continue

        symbol = str(position.get("symbol") or position.get("coin") or "").strip() or "UNKNOWN"
        side = str(position.get("side") or position.get("direction") or "").strip()
        size = position.get("size") or position.get("amount") or position.get("qty") or ""
        symbol_state = per_symbol_margin.get(symbol, {})
        can_reduce = bool(global_can_reduce)
        if isinstance(symbol_state, dict) and "can_reduce" in symbol_state:
            can_reduce = bool(symbol_state.get("can_reduce"))
        if "can_reduce" in position:
            can_reduce = bool(position.get("can_reduce"))

        if can_reduce:
            actions.append(
                f"减仓 {symbol} {side} size={size}，将保证金率提升至>={MARGIN_CONTAGION_TARGET:.0f}%"
            )
        else:
            actions.append(f"平仓 {symbol} {side} size={size}（无法安全减仓）")

    return actions


__all__ = [
    "MAX_OPEN_POSITIONS",
    "SIGNAL_QUEUE_CAPACITY",
    "HARD_CAP_LOSS_RATIO",
    "DAILY_LOSS_CIRCUIT_BREAKER",
    "ATR_CIRCUIT_BREAKER",
    "SAME_STRATEGY_MAX_PER_HOUR",
    "MARGIN_CONTAGION_THRESHOLD",
    "MARGIN_CONTAGION_TARGET",
    "calc_position",
    "allocate_coin_share",
    "risk_check",
    "SignalQueue",
    "check_margin_contagion",
]
