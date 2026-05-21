# -*- coding: utf-8 -*-
"""数据层稳健性模块。

职责：
1. 断网保活与重连恢复
2. AI 降级链日志
3. 状态持久化与加载
4. 重启恢复
5. 孤儿订单/孤儿持仓检测

说明：
- 仅使用标准库，避免 GUI 依赖
- 所有接口尽量采用宽松的 duck-typing，方便测试注入假对象
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "PROJECT_ROOT",
    "CONFIG_DIR",
    "LOG_DIR",
    "SESSION_STATE_FILE",
    "AI_DECISION_LOG_FILE",
    "NetworkMonitor",
    "StateAutoSave",
    "log_ai_degrade",
    "resume_from_state",
    "orphan_check",
]


def _get_project_root() -> Path:
    """获取项目根目录。"""
    try:
        import os

        root = os.environ.get("V4_PROJECT_ROOT", "")
        if root:
            return Path(root)
    except Exception:
        pass
    return Path(__file__).resolve().parent


PROJECT_ROOT = _get_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "logs"
SESSION_STATE_FILE = CONFIG_DIR / "session_state.json"
AI_DECISION_LOG_FILE = LOG_DIR / "ai_decisions.log"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_coin(value: Any) -> str:
    raw = _safe_str(value).strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    if "-" in raw:
        raw = raw.split("-", 1)[0]
    return raw


def _normalize_side(value: Any) -> str:
    raw = _safe_str(value).strip().lower()
    if raw in {"buy", "long", "bid"}:
        return "long"
    if raw in {"sell", "short", "ask"}:
        return "short"
    return raw or "long"


def _position_key(position: Dict[str, Any]) -> str:
    coin = _normalize_coin(position.get("coin") or position.get("symbol"))
    side = _normalize_side(position.get("side"))
    return f"{coin}:{side}"


def _normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
    """将不同来源的持仓字段统一成数据层格式。"""
    item = dict(position or {})
    coin = _normalize_coin(item.get("coin") or item.get("symbol") or item.get("instId"))
    side = _normalize_side(item.get("side") or item.get("posSide"))
    size_value = (
        item.get("size")
        if item.get("size") is not None
        else item.get("amount", item.get("contracts", item.get("pos", 0)))
    )
    entry_value = (
        item.get("entry")
        if item.get("entry") is not None
        else item.get("entryPrice", item.get("avgPx", 0))
    )

    normalized = {
        "coin": coin,
        "side": side,
        "size": float(size_value or 0),
        "entry": float(entry_value or 0),
    }
    if "symbol" in item and item.get("symbol"):
        normalized["symbol"] = _safe_str(item.get("symbol"))
    elif coin:
        normalized["symbol"] = coin
    if item.get("status") is not None:
        normalized["status"] = _safe_str(item.get("status"))
    if item.get("source") is not None:
        normalized["source"] = _safe_str(item.get("source"))
    return normalized


def _normalize_order(order: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(order or {})
    order_id = _safe_str(item.get("id") or item.get("order_id") or item.get("orderId"))
    symbol = _safe_str(item.get("symbol") or item.get("instId"))
    status = _safe_str(item.get("status") or item.get("state") or "open").lower()
    return {
        "id": order_id,
        "symbol": symbol,
        "status": status,
        "side": _normalize_side(item.get("side")),
        "price": float(item.get("price", 0) or 0),
        "amount": float(item.get("amount", item.get("size", 0)) or 0),
    }


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    _ensure_parent(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def _append_log_line(path: Path, line: str) -> str:
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    return line


def log_ai_degrade(
    coin: str,
    from_stage: str,
    to_stage: str,
    reason: str,
    log_path: Path = AI_DECISION_LOG_FILE,
) -> str:
    """记录 AI 降级链事件。"""
    line = (
        f"[{_log_timestamp()}] AI_DEGRADE | coin={_safe_str(coin).upper()} | "
        f"from={_safe_str(from_stage)} | to={_safe_str(to_stage)} | reason={_safe_str(reason)}"
    )
    return _append_log_line(log_path, line)


def _emit_signal(target: Any, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """尽量兼容不同的信号/回调对象。"""
    if target is None:
        return

    payload = payload or {}
    candidates = []

    if hasattr(target, event):
        candidates.append(getattr(target, event))
    for alt in ("freeze", "resume", "emit", "send", "push"):
        if alt != event and hasattr(target, alt):
            candidates.append(getattr(target, alt))
    if callable(target):
        candidates.append(target)

    for candidate in candidates:
        if not callable(candidate):
            continue
        try:
            candidate(event)
            return
        except TypeError:
            pass
        try:
            candidate(event, payload)
            return
        except TypeError:
            pass
        try:
            candidate(payload)
            return
        except TypeError:
            pass
        try:
            candidate()
            return
        except TypeError:
            pass


def _fetch_positions_from_exchange(exchange: Any) -> List[Dict[str, Any]]:
    if exchange is None:
        return []
    fetch = getattr(exchange, "fetch_positions", None)
    if not callable(fetch):
        return []
    try:
        raw = fetch()
    except TypeError:
        raw = fetch(symbols=None)
    except Exception:
        return []
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _fetch_open_orders_from_exchange(exchange: Any) -> List[Dict[str, Any]]:
    if exchange is None:
        return []
    fetch = getattr(exchange, "fetch_open_orders", None)
    if not callable(fetch):
        return []
    try:
        raw = fetch()
    except TypeError:
        raw = fetch(symbol=None)
    except Exception:
        return []
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _local_orders(local_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    orders = local_state.get("orders")
    if isinstance(orders, list):
        return [_normalize_order(item) for item in orders if isinstance(item, dict)]
    open_orders = local_state.get("open_orders")
    if isinstance(open_orders, list):
        return [_normalize_order(item) for item in open_orders if isinstance(item, dict)]
    return []


def _local_positions(local_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    positions = local_state.get("positions")
    if not isinstance(positions, list):
        return []
    return [_normalize_position(item) for item in positions if isinstance(item, dict)]


def _merge_reconciled_state(
    exchange_positions: List[Dict[str, Any]],
    local_state: Dict[str, Any],
) -> Dict[str, Any]:
    """交易所持仓优先，本地状态作为附加元信息。"""
    normalized_exchange = [_normalize_position(item) for item in exchange_positions if isinstance(item, dict)]
    normalized_local = _local_positions(local_state)
    local_map = {_position_key(item): item for item in normalized_local}

    reconciled_positions: List[Dict[str, Any]] = []
    imported_external: List[Dict[str, Any]] = []
    removed_local: List[Dict[str, Any]] = []

    exchange_keys = set()
    for item in normalized_exchange:
        key = _position_key(item)
        exchange_keys.add(key)
        merged = dict(item)
        if key not in local_map:
            merged["source"] = "外部持仓"
            imported_external.append(dict(merged))
        else:
            local_item = local_map[key]
            if local_item.get("source"):
                merged["source"] = local_item["source"]
        reconciled_positions.append(merged)

    for key, item in local_map.items():
        if key not in exchange_keys:
            removed_local.append(dict(item))

    restored = dict(local_state or {})
    restored["positions"] = reconciled_positions
    restored["reconciliation"] = {
        "retained": len(reconciled_positions) - len(imported_external),
        "imported_external": imported_external,
        "removed_local": removed_local,
        "exchange_count": len(normalized_exchange),
        "local_count": len(normalized_local),
    }
    restored["sync_status"] = "reconciled"
    if imported_external:
        restored["external_positions"] = imported_external
    return restored


class NetworkMonitor:
    """心跳保活和断网恢复控制。"""

    def __init__(
        self,
        okx_thread_signal: Any,
        disconnect_timeout: float = 30.0,
        heartbeat_interval: float = 10.0,
        exchange: Any = None,
        local_state: Optional[Dict[str, Any]] = None,
        log_path: Path = AI_DECISION_LOG_FILE,
    ):
        self.okx_thread_signal = okx_thread_signal
        self.disconnect_timeout = float(disconnect_timeout)
        self.heartbeat_interval = float(heartbeat_interval)
        self.last_pong = time.time()
        self.last_disconnect_at: Optional[float] = None
        self.last_reconnect_at: Optional[float] = None
        self.is_connected = True
        self.accept_new_signals = True
        self.signal_frozen = False
        self.exchange = exchange
        self.local_state = local_state if isinstance(local_state, dict) else {}
        self.log_path = log_path
        self.event_history: List[Dict[str, Any]] = []

    def check_alive(self) -> bool:
        """返回 True=在线, False=断网。"""
        alive = (time.time() - self.last_pong) <= self.disconnect_timeout
        self.is_connected = alive
        if alive:
            self.accept_new_signals = not self.signal_frozen
        return alive

    def on_disconnect(self) -> None:
        """断网处理：冻结新信号，维持已有持仓。"""
        self.is_connected = False
        self.signal_frozen = True
        self.accept_new_signals = False
        self.last_disconnect_at = time.time()
        self.event_history.append(
            {
                "event": "disconnect",
                "timestamp": _utc_timestamp(),
                "reason": "network_timeout",
            }
        )
        _emit_signal(self.okx_thread_signal, "freeze_new_signals", {"reason": "network_timeout"})

    def on_reconnect(self) -> Dict[str, Any]:
        """重连后从交易所拉取持仓并恢复信号。"""
        exchange_positions = _fetch_positions_from_exchange(self.exchange)
        restored = resume_from_state(exchange_positions, self.local_state)
        if isinstance(self.local_state, dict):
            self.local_state.clear()
            self.local_state.update(restored)
        self.last_pong = time.time()
        self.last_reconnect_at = self.last_pong
        self.is_connected = True
        self.signal_frozen = False
        self.accept_new_signals = True
        self.event_history.append(
            {
                "event": "reconnect",
                "timestamp": _utc_timestamp(),
                "exchange_positions": len(exchange_positions),
            }
        )
        _emit_signal(self.okx_thread_signal, "resume_new_signals", {"positions": restored.get("positions", [])})
        return restored

    def touch(self) -> None:
        """更新最后一次心跳时间。"""
        self.last_pong = time.time()
        self.is_connected = True

    def snapshot(self) -> Dict[str, Any]:
        """返回当前监控状态快照。"""
        return {
            "is_connected": self.is_connected,
            "accept_new_signals": self.accept_new_signals,
            "signal_frozen": self.signal_frozen,
            "last_pong": self.last_pong,
            "last_disconnect_at": self.last_disconnect_at,
            "last_reconnect_at": self.last_reconnect_at,
        }


class StateAutoSave:
    """每 30 秒自动保存 config/session_state.json。"""

    def __init__(self, save_interval: float = 30.0, path: Path = SESSION_STATE_FILE):
        self.save_interval = float(save_interval)
        self.path = path
        self.last_saved_at: Optional[float] = None
        self._lock = threading.RLock()

    def save(
        self,
        positions: List[Dict[str, Any]],
        active_strategy: Any,
        last_ai_decision: Any,
        capital_data: Any,
    ) -> Dict[str, Any]:
        """保存当前状态快照。"""
        payload: Dict[str, Any] = {
            "positions": [_normalize_position(item) for item in positions if isinstance(item, dict)],
            "active_strategy": active_strategy,
            "last_ai_decision": last_ai_decision if isinstance(last_ai_decision, dict) else {},
            "last_trade_time": _utc_timestamp(),
            "capital_allocated": 0,
            "capital_remaining": 0,
        }
        if isinstance(capital_data, dict):
            payload["last_trade_time"] = _safe_str(capital_data.get("last_trade_time") or _utc_timestamp())
            payload["capital_allocated"] = capital_data.get("capital_allocated", capital_data.get("allocated", 0))
            payload["capital_remaining"] = capital_data.get("capital_remaining", capital_data.get("remaining", 0))
            for key, value in capital_data.items():
                if key not in {"capital_allocated", "capital_remaining", "last_trade_time"}:
                    payload[key] = value
        with self._lock:
            _write_json(self.path, payload)
            self.last_saved_at = time.time()
        return payload

    def load(self) -> Optional[Dict[str, Any]]:
        """加载上次状态。"""
        with self._lock:
            data = _read_json(self.path)
        return data

    def should_save(self, now: Optional[float] = None) -> bool:
        """判断是否到达下一次自动保存窗口。"""
        now = time.time() if now is None else now
        if self.last_saved_at is None:
            return True
        return (now - self.last_saved_at) >= self.save_interval

    def auto_save(
        self,
        positions: List[Dict[str, Any]],
        active_strategy: Any,
        last_ai_decision: Any,
        capital_data: Any,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """到达保存间隔时自动落盘。"""
        if not self.should_save(now=now):
            return None
        return self.save(positions, active_strategy, last_ai_decision, capital_data)


def resume_from_state(exchange_positions: List[Dict], local_state: Dict) -> Dict:
    """
    交易所状态优先的重启恢复逻辑。

    规则：
    1. 以交易所持仓为权威
    2. 交易所无 -> 删除本地记录
    3. 交易所有、本地无 -> 标记为“外部持仓”
    4. 返回恢复后的完整状态
    """
    local_state = dict(local_state or {})
    exchange_positions = exchange_positions if isinstance(exchange_positions, list) else []
    return _merge_reconciled_state(exchange_positions, local_state)


def orphan_check(
    exchange: Any,
    local_state: Dict[str, Any],
    log_path: Path = AI_DECISION_LOG_FILE,
) -> Dict[str, Any]:
    """检测假死孤儿订单与孤儿持仓。"""
    issues: List[Dict[str, Any]] = []
    local_state = dict(local_state or {})

    try:
        exchange_open_orders = _fetch_open_orders_from_exchange(exchange)
        exchange_positions = _fetch_positions_from_exchange(exchange)
        local_orders = _local_orders(local_state)
        local_positions = _local_positions(local_state)

        local_order_map = {item["id"]: item for item in local_orders if item.get("id")}
        local_position_map = {_position_key(item): item for item in local_positions}

        for order in exchange_open_orders:
            order_id = _safe_str(order.get("id"))
            local_order = local_order_map.get(order_id)
            if local_order and local_order.get("status") in {"filled", "closed", "done", "filled_done"}:
                issue = {
                    "type": "orphan_order",
                    "order_id": order_id,
                    "symbol": order.get("symbol", ""),
                    "local_status": local_order.get("status"),
                    "exchange_status": order.get("status", "open"),
                    "message": "OKX有挂单但本地已成交",
                }
                issues.append(issue)
                _append_log_line(
                    log_path,
                    (
                        f"[{_log_timestamp()}] ORPHAN_CHECK | type=orphan_order | "
                        f"order_id={order_id} | symbol={order.get('symbol', '')} | "
                        f"local={local_order.get('status')} | exchange={order.get('status', 'open')}"
                    ),
                )

        for pos in exchange_positions:
            normalized_pos = _normalize_position(pos)
            key = _position_key(normalized_pos)
            if key not in local_position_map:
                issue = {
                    "type": "external_position",
                    "coin": normalized_pos.get("coin", ""),
                    "side": normalized_pos.get("side", ""),
                    "message": "OKX有持仓但本地无",
                    "position": normalized_pos,
                }
                issues.append(issue)
                _append_log_line(
                    log_path,
                    (
                        f"[{_log_timestamp()}] ORPHAN_CHECK | type=external_position | "
                        f"coin={normalized_pos.get('coin', '')} | side={normalized_pos.get('side', '')} | "
                        f"source=exchange_only"
                    ),
                )

        reconciled = resume_from_state(exchange_positions, local_state)
        if issues:
            _append_log_line(
                log_path,
                f"[{_log_timestamp()}] ORPHAN_CHECK | status=anomaly | issues={len(issues)}",
            )
        else:
            _append_log_line(
                log_path,
                f"[{_log_timestamp()}] ORPHAN_CHECK | status=ok | issues=0",
            )

        return {
            "ok": True,
            "issues": issues,
            "exchange_open_orders": exchange_open_orders,
            "exchange_positions": exchange_positions,
            "local_orders": local_orders,
            "local_positions": local_positions,
            "reconciled_state": reconciled,
        }
    except Exception as exc:
        _append_log_line(
            log_path,
            f"[{_log_timestamp()}] ORPHAN_CHECK_ERROR | reason={_safe_str(exc)}",
        )
        return {
            "ok": False,
            "issues": [
                {
                    "type": "error",
                    "message": _safe_str(exc),
                }
            ],
            "exchange_open_orders": [],
            "exchange_positions": [],
            "local_orders": [],
            "local_positions": [],
            "reconciled_state": resume_from_state([], local_state),
        }
