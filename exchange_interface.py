# -*- coding: utf-8 -*-
"""
交易所接口抽象层：ExchangeInterface 基类 → SimExchange（OKX模拟盘）+ LiveExchange（实盘预留）

V4 重构核心模块 —— 统一交易接口，支持模拟盘/实盘切换。
运行环境：Python 3.11+, PyQt6, CCXT 4.x, Windows
"""

import json
import math
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# CCXT 非异步模式（避免 Windows aiohttp 注册表代理问题）
import os

os.environ.setdefault("CCXT_NO_ASYNC", "1")

import ccxt  # noqa: E402

# ===== 工具函数 =====


def _json_safe(value: Any) -> Any:
    """递归清洗 JSON 不支持的值类型（NaN/Inf/Decimal/ndarray）。"""
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _json_safe(float(value))
    if isinstance(value, (np.ndarray,)):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, Decimal):
        return _json_safe(float(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """安全 JSON 序列化，自动处理 NaN/Inf/Datetime。"""
    class _SafeEncoder(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, pd.Timestamp):
                return o.isoformat()
            if isinstance(o, Decimal):
                return float(o)
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return _json_safe(float(o))
            if isinstance(o, np.ndarray):
                return o.tolist()
            return super().default(o)

    return json.dumps(_json_safe(obj), cls=_SafeEncoder, **kwargs)


def save_config(path: Path, new_vals: Dict[str, Any]) -> None:
    """合并模式保存 JSON 配置，不覆盖已有字段。"""
    try:
        existing: Dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                existing = loaded
        existing.update(new_vals or {})
        path.write_text(safe_json_dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"保存配置失败：{path}，错误：{exc}")
        raise


def load_config(path: Path) -> Dict[str, Any]:
    """安全加载 JSON 配置，文件不存在或损坏时返回空字典。"""
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


# ===== 项目路径常量 =====

def _get_project_root() -> Path:
    """获取项目根目录（D:/量化平台V4）。"""
    # 优先用环境变量（方便跨模块引用）
    env_root = os.environ.get("V4_PROJECT_ROOT", "")
    if env_root:
        return Path(env_root)
    # fallback：从当前文件所在位置推断
    return Path(__file__).resolve().parent


PROJECT_ROOT = _get_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
API_CONFIG_FILE = CONFIG_DIR / "api_config.json"
USER_CONFIG_FILE = CONFIG_DIR / "user_config.json"

# ===== 代理与连接常量 =====

DEFAULT_PROXY = {"type": "HTTP", "host": "127.0.0.1", "port": "0" # disabled, configure in UI}
DEFAULT_EXCHANGE_TIMEOUT_MS = 30_000
BACKUP_HOSTNAMES = ("aws.okx.com", "okx.me")
DIRECT_HOSTNAME = "okx.com"


def _replace_url_domain(urls: Any, old_domain: str, new_domain: str) -> Any:
    """递归替换 CCXT urls 嵌套字典中的域名。"""
    if isinstance(urls, dict):
        return {k: _replace_url_domain(v, old_domain, new_domain) for k, v in urls.items()}
    elif isinstance(urls, str):
        return urls.replace(old_domain, new_domain)
    return urls


# ============================================================================
#  ExchangeInterface 抽象基类
# ============================================================================


class ExchangeInterface(ABC):
    """交易接口抽象基类 —— 所有交易所实现必须继承此类。

    定义了量化交易平台所需的全部交易所操作规范。
    子类：SimExchange（模拟盘）、LiveExchange（实盘）。
    """

    def __init__(self, exchange_id: str = "okx"):
        """初始化交易所接口。

        Args:
            exchange_id: 交易所标识 ('okx' | 'binance' | 'gateio')，默认 okx
        """
        self.exchange_id = exchange_id
        self._connected = False
        self._last_error: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        """是否已连接到交易所。"""
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        """最近一次连接错误消息。"""
        return self._last_error

    @abstractmethod
    def connect(self) -> bool:
        """建立交易所连接。返回 True 表示连接成功。

        Returns:
            True 连接成功，False 连接失败
        """
        ...

    @abstractmethod
    def fetch_balance(self) -> Dict[str, Any]:
        """获取账户余额。

        Returns:
            dict 格式 {'total': {'USDT': 10000, ...}, 'free': {...}, 'used': {...}}
        """
        ...

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """获取 K 线数据。

        Args:
            symbol: 交易对，如 'BTC/USDT'
            timeframe: K线周期，如 '1h', '4h', '1d'
            limit: 获取条数（最大 1500）

        Returns:
            DataFrame，列 ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        ...

    @abstractmethod
    def create_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建订单。

        Args:
            symbol: 交易对，如 'BTC/USDT'
            side: 'buy' | 'sell'
            amount: 数量（合约张数）
            price: 限价单价格（None 则市价单）
            params: 额外参数（杠杆等）

        Returns:
            dict 格式 {'id': 'xxx', 'symbol': '...', 'side': '...', 'amount': ..., 'price': ..., 'status': '...', 'timestamp': '...'}
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单。

        Args:
            order_id: 订单 ID

        Returns:
            True 撤单成功，False 撤单失败
        """
        ...

    @abstractmethod
    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前挂单列表。

        Args:
            symbol: 可选，指定交易对筛选

        Returns:
            list[dict] 挂单列表
        """
        ...

    @abstractmethod
    def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """获取当前持仓。

        Args:
            symbols: 可选，指定交易对列表

        Returns:
            list[dict] 持仓列表，每项包含 symbol/side/size/entryPrice/unrealizedPnl
        """
        ...

    def disconnect(self) -> None:
        """断开交易所连接。"""
        self._connected = False

    def get_min_order_value(self, symbol: str = "BTC/USDT") -> float:
        """获取最小下单金额（美元）。默认 10 USDT。

        Args:
            symbol: 交易对

        Returns:
            float 最小下单金额
        """
        return 10.0


# ============================================================================
#  SimExchange —— OKX 模拟盘实现
# ============================================================================


class SimExchange(ExchangeInterface):
    """OKX 模拟盘交易接口（Demo Trading）。

    读取 config/api_config.json 中的加密凭证进行连接。
    支持代理优先的三级降级连接策略。

    Example:
        >>> ex = SimExchange()
        >>> ex.connect()
        >>> bal = ex.fetch_balance()
        >>> df = ex.fetch_ohlcv('BTC/USDT', '1h', 100)
    """

    # OKX 模拟盘标识
    EXCHANGE_NAME = "okx"
    API_NAME = "模拟"  # 显示名称

    def __init__(self, exchange_id: str = "okx"):
        """初始化模拟盘接口。

        Args:
            exchange_id: 交易所标识，默认 'okx'
        """
        super().__init__(exchange_id)
        self._exchange: Optional[ccxt.Exchange] = None
        self._api_config: Dict[str, Any] = {}
        self._connection_mode: str = "未连接"  # 记录实际使用的连接方式

    @property
    def connection_mode(self) -> str:
        """当前连接方式（代理/备用域名/直连/未连接）。"""
        return self._connection_mode

    def _load_api_config(self) -> Dict[str, Any]:
        """从 config/api_config.json 加载加密的 API 凭证。

        Returns:
            dict 包含 apiKey/secretKey/password 字段
        """
        if not API_CONFIG_FILE.exists():
            self._last_error = f"API 配置文件不存在：{API_CONFIG_FILE}"
            return {}

        try:
            raw = json.loads(API_CONFIG_FILE.read_text(encoding="utf-8"))
            # 尝试解密（与 V3 兼容的混淆方式）
            return self._decrypt_config(raw)
        except Exception as exc:
            self._last_error = f"API 配置解析失败：{exc}"
            return {}

    def _decrypt_config(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """解密 API 配置（V3 兼容格式）。

        支持两种格式：
        1. {"version":1, "data":"混淆后的base64"}
        2. 直接明文 dict 格式
        """
        # 格式1：V3 加密格式
        if "data" in raw and "version" in raw:
            try:
                import base64

                encoded = raw.get("data", "")
                if not encoded:
                    return {}
                # V3 混淆解码：反转 base64 → XOR 解码
                # 注意：这里做轻量解密，正式环境建议用 cryptography.fernet
                decoded_bytes = base64.urlsafe_b64decode(encoded.encode("ascii"))
                # 简单的按位翻转（与 V3 的 混淆文本/还原文本 对应）
                key_bytes = "lianghua-platform-v2-local-user".encode("utf-8")
                result_bytes = bytes(
                    decoded_bytes[i] ^ key_bytes[i % len(key_bytes)]
                    for i in range(len(decoded_bytes))
                )
                decoded_text = result_bytes.decode("utf-8", errors="replace")
                return json.loads(decoded_text)
            except Exception:
                pass

        # 格式2：直接明文 dict
        if "apiKey" in raw:
            return raw

        return {}

    def _load_user_config(self) -> Dict[str, Any]:
        """加载用户设置（本金、杠杆等）。"""
        return load_config(USER_CONFIG_FILE)

    def _build_exchange_with_proxy(self) -> ccxt.Exchange:
        """用固定代理（127.0.0.1:PORT）创建 OKX 实例。"""
        api_config = self._api_config
        return ccxt.okx({
            "apiKey": api_config.get("apiKey", ""),
            "secret": api_config.get("secretKey", ""),
            "password": api_config.get("password", ""),
            "enableRateLimit": True,
            "timeout": DEFAULT_EXCHANGE_TIMEOUT_MS,
            "proxies": {
                "http": f"http://{DEFAULT_PROXY['host']}:{DEFAULT_PROXY['port']}",
                "https": f"http://{DEFAULT_PROXY['host']}:{DEFAULT_PROXY['port']}",
            },
            "options": {
                "defaultType": "swap",  # 永续合约
                "sandbox": True,        # 模拟盘模式
                "demo": True,           # 兼容某些 CCXT 版本
            },
        })

    def _build_exchange_backup_domain(self) -> ccxt.Exchange:
        """用备用域名连接 OKX。"""
        api_config = self._api_config
        exchange = ccxt.okx({
            "apiKey": api_config.get("apiKey", ""),
            "secret": api_config.get("secretKey", ""),
            "password": api_config.get("password", ""),
            "enableRateLimit": True,
            "timeout": DEFAULT_EXCHANGE_TIMEOUT_MS,
            "options": {
                "defaultType": "swap",
                "sandbox": True,
                "demo": True,
            },
        })
        # 尝试替换所有 urls 中的域名为备用域名
        for backup_host in BACKUP_HOSTNAMES:
            try:
                exchange.urls = _replace_url_domain(
                    exchange.urls, "okx.com", backup_host
                )
                break
            except Exception:
                continue
        return exchange

    def _build_exchange_direct(self) -> ccxt.Exchange:
        """直连 OKX（最后手段）。"""
        api_config = self._api_config
        return ccxt.okx({
            "apiKey": api_config.get("apiKey", ""),
            "secret": api_config.get("secretKey", ""),
            "password": api_config.get("password", ""),
            "enableRateLimit": True,
            "timeout": DEFAULT_EXCHANGE_TIMEOUT_MS,
            "options": {
                "defaultType": "swap",
                "sandbox": True,
                "demo": True,
            },
        })

    # ------------------------------------------------------------------
    #  connect() —— 三级降级连接
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """建立 OKX 模拟盘连接，三级降级策略。

        降级顺序：
        1. 代理连接（127.0.0.1:PORT）— 优先
        2. 备用域名（aws.okx.com → okx.me）
        3. 直连（okx.com）— 最后尝试

        Returns:
            True 连接成功，False 所有方式均失败
        """
        # 加载 API 凭证
        self._api_config = self._load_api_config()
        if not self._api_config or "apiKey" not in self._api_config:
            self._last_error = "API 配置缺失，请先在设置页填写 API 凭证"
            self._connected = False
            return False

        # L1: 代理连接
        try:
            self._exchange = self._build_exchange_with_proxy()
            self._exchange.set_sandbox_mode(True)  # 显式启用沙盒
            self._exchange.load_markets()
            self._connected = True
            self._connection_mode = "✅已连接（代理）"
            self._last_error = None
            return True
        except Exception as e1:
            self._last_error = f"代理连接失败：{e1}"

        # L2: 备用域名
        try:
            self._exchange = self._build_exchange_backup_domain()
            self._exchange.set_sandbox_mode(True)
            self._exchange.load_markets()
            self._connected = True
            self._connection_mode = "✅已连接（备用域名）"
            self._last_error = None
            return True
        except Exception as e2:
            self._last_error = f"备用域名连接失败：{e2}"

        # L3: 直连
        try:
            self._exchange = self._build_exchange_direct()
            self._exchange.set_sandbox_mode(True)
            self._exchange.load_markets()
            self._connected = True
            self._connection_mode = "✅已连接（直连）"
            self._last_error = None
            return True
        except Exception as e3:
            self._last_error = f"直连失败：{e3}"
            self._connected = False
            self._connection_mode = "❌未连接"
            return False

    # ------------------------------------------------------------------
    #  fetch_balance() —— 获取账户余额
    # ------------------------------------------------------------------

    def fetch_balance(self) -> Dict[str, Any]:
        """获取 OKX 模拟盘账户余额。

        使用 OKX 原生的 privateGetAccountBalance 接口（更可靠），
        fallback 到 CCXT 标准 fetch_balance。

        Returns:
            dict {
                'total': {'USDT': 10000.0, ...},
                'free': {'USDT': 9500.0, ...},
                'used': {'USDT': 500.0, ...}
            }
        """
        if not self._connected or self._exchange is None:
            return {"total": {}, "free": {}, "used": {}}

        # 优先使用 OKX 原生的 privateGetAccountBalance
        if hasattr(self._exchange, "privateGetAccountBalance"):
            try:
                raw = self._exchange.privateGetAccountBalance({})
                return self._parse_okx_balance(raw)
            except Exception:
                pass

        # fallback 到 CCXT 标准接口
        try:
            bal = self._exchange.fetch_balance()
            total = {}
            free = {}
            used = {}
            for currency, info in (bal.get("total", {}) or {}).items():
                if isinstance(info, dict):
                    continue
                total[currency] = float(info or 0)
            for currency, info in (bal.get("free", {}) or {}).items():
                if isinstance(info, dict):
                    continue
                free[currency] = float(info or 0)
            for currency, info in (bal.get("used", {}) or {}).items():
                if isinstance(info, dict):
                    continue
                used[currency] = float(info or 0)
            return {"total": total, "free": free, "used": used}
        except Exception as exc:
            self._last_error = f"余额获取失败：{exc}"
            return {"total": {}, "free": {}, "used": {}}

    def _parse_okx_balance(self, raw_balance: Any) -> Dict[str, Any]:
        """解析 OKX 原生余额响应为 CCXT 格式。"""
        total: Dict[str, float] = {}
        free: Dict[str, float] = {}
        used: Dict[str, float] = {}
        data_list = raw_balance.get("data", []) if isinstance(raw_balance, dict) else []
        for account in data_list:
            for item in account.get("details", []) if isinstance(account, dict) else []:
                cur = str(item.get("ccy", "")).upper()
                if not cur:
                    continue
                total[cur] = float(item.get("eq", item.get("cashBal", 0)) or 0)
                free[cur] = float(item.get("availBal", item.get("availEq", 0)) or 0)
                used[cur] = max(0.0, total[cur] - free[cur])
        return {"total": total, "free": free, "used": used}

    # ------------------------------------------------------------------
    #  fetch_ohlcv() —— 获取 K 线数据
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """获取 K 线数据。

        Args:
            symbol: 交易对（如 'BTC/USDT'，自动转为 OKX 格式 'BTC/USDT:USDT'）
            timeframe: 周期（'1m'/'5m'/'15m'/'1h'/'4h'/'1d'）
            limit: 最大条数

        Returns:
            DataFrame 含列 ['timestamp','open','high','low','close','volume']
        """
        if not self._connected or self._exchange is None:
            return pd.DataFrame()

        try:
            # CCXT 标准格式
            ohlcv = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
            if not ohlcv:
                return pd.DataFrame()

            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as exc:
            self._last_error = f"K线数据获取失败（{symbol}/{timeframe}）：{exc}"
            return pd.DataFrame()

    # ------------------------------------------------------------------
    #  create_order() —— 创建订单
    # ------------------------------------------------------------------

    def create_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建模拟盘订单。

        Args:
            symbol: 交易对
            side: 'buy' | 'sell'
            amount: 合约张数
            price: 限价单价格（None=市价单）
            params: 额外参数（如 leverage）

        Returns:
            dict {'id': 'xxx', 'symbol': '...', 'side': '...', 'amount': ..., 'price': ..., 'status': '...'}
        """
        if not self._connected or self._exchange is None:
            return {"error": "交易所未连接"}

        try:
            order_type = "limit" if price else "market"
            order_params = params or {}
            # 模拟盘使用 reduceOnly=False（非减仓单）
            order_params.setdefault("reduceOnly", False)

            order = self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=order_params,
            )
            return {
                "id": str(order.get("id", "")),
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price or order.get("price", 0),
                "status": order.get("status", "open"),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as exc:
            self._last_error = f"下单失败：{exc}"
            return {
                "error": str(exc),
                "id": "",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price or 0,
                "status": "rejected",
                "timestamp": datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------
    #  cancel_order() —— 撤销订单
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单。

        Args:
            order_id: 订单 ID

        Returns:
            True 撤单成功，False 撤单失败
        """
        if not self._connected or self._exchange is None:
            return False
        try:
            self._exchange.cancel_order(order_id)
            return True
        except Exception as exc:
            self._last_error = f"撤单失败：{exc}"
            return False

    # ------------------------------------------------------------------
    #  fetch_open_orders() —— 挂单查询
    # ------------------------------------------------------------------

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前挂单。

        Args:
            symbol: 可选筛选交易对

        Returns:
            list[dict] 挂单列表
        """
        if not self._connected or self._exchange is None:
            return []
        try:
            orders = self._exchange.fetch_open_orders(symbol=symbol)
            if not orders:
                return []
            result = []
            for order in orders:
                result.append({
                    "id": str(order.get("id", "")),
                    "symbol": order.get("symbol", ""),
                    "side": order.get("side", ""),
                    "amount": float(order.get("amount", 0) or 0),
                    "price": float(order.get("price", 0) or 0),
                    "status": order.get("status", "open"),
                    "timestamp": order.get("datetime", ""),
                })
            return result
        except Exception as exc:
            self._last_error = f"挂单查询失败：{exc}"
            return []

    # ------------------------------------------------------------------
    #  fetch_positions() —— 持仓查询
    # ------------------------------------------------------------------

    def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """获取当前持仓。

        OKX 模拟盘使用 privateGetAccountPositions 原生接口，
        fallback 到 CCXT 标准接口。

        Args:
            symbols: 可选筛选交易对

        Returns:
            list[dict] 持仓列表
        """
        if not self._connected or self._exchange is None:
            return []

        # 优先使用 OKX 原生接口
        if hasattr(self._exchange, "privateGetAccountPositions"):
            try:
                raw = self._exchange.privateGetAccountPositions({})
                return self._parse_okx_positions(raw)
            except Exception:
                pass

        # fallback 到 CCXT 标准接口
        try:
            positions = self._exchange.fetch_positions(symbols=symbols)
            if not positions:
                return []
            result = []
            for pos in positions:
                result.append({
                    "symbol": pos.get("symbol", ""),
                    "side": pos.get("side", "long"),
                    "size": float(pos.get("contracts", 0) or 0),
                    "entryPrice": float(pos.get("entryPrice", 0) or 0),
                    "markPrice": float(pos.get("markPrice", 0) or 0),
                    "unrealizedPnl": float(pos.get("unrealizedPnl", 0) or 0),
                    "leverage": pos.get("leverage", "-"),
                })
            return result
        except Exception as exc:
            self._last_error = f"持仓查询失败：{exc}"
            return []

    def _parse_okx_positions(self, raw_positions: Any) -> List[Dict[str, Any]]:
        """解析 OKX 原生持仓数据。"""
        rows: List[Dict[str, Any]] = []
        data_list = raw_positions.get("data", []) if isinstance(raw_positions, dict) else []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            size = float(item.get("pos", item.get("availPos", 0)) or 0)
            if abs(size) <= 0:
                continue
            rows.append({
                "symbol": str(item.get("instId", "-")).replace("-SWAP", "/USDT:USDT"),
                "side": item.get("posSide") or ("long" if size > 0 else "short"),
                "size": abs(size),
                "entryPrice": float(item.get("avgPx", 0) or 0),
                "markPrice": float(item.get("markPx", item.get("last", 0)) or 0),
                "unrealizedPnl": float(item.get("upl", 0) or 0),
                "leverage": item.get("lever", "-"),
            })
        return rows

    # ------------------------------------------------------------------
    #  get_usdt_balance() —— 便捷方法
    # ------------------------------------------------------------------

    def get_usdt_balance(self) -> float:
        """快速获取 USDT 可用余额。

        Returns:
            float USDT 余额
        """
        bal = self.fetch_balance()
        return float(bal.get("free", {}).get("USDT", 0) or 0)

    def disconnect(self) -> None:
        """断开连接。"""
        self._connected = False
        self._exchange = None
        self._connection_mode = "未连接"


# ============================================================================
#  LiveExchange —— 实盘预留接口
# ============================================================================


class LiveExchange(ExchangeInterface):
    """实盘交易接口（预留，当前不可用）。

    待后续接入实盘凭证和风控系统后启用。
    当前所有方法抛出 NotImplementedError 或返回安全默认值。
    """

    EXCHANGE_NAME = "live"
    API_NAME = "实盘"

    def __init__(self, exchange_id: str = "okx"):
        super().__init__(exchange_id)
        self._enabled = False  # 实盘功能开关

    def connect(self) -> bool:
        """实盘连接尚未实现。"""
        self._last_error = "实盘接口尚未开放，请联系管理员"
        self._connected = False
        return False

    def fetch_balance(self) -> Dict[str, Any]:
        """实盘余额查询未开放。"""
        self._last_error = "实盘余额查询未开放"
        return {"total": {}, "free": {}, "used": {}}

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """实盘 K 线获取未开放。"""
        self._last_error = "实盘数据接口未开放"
        return pd.DataFrame()

    def create_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """实盘下单未开放 —— 安全拒绝。"""
        self._last_error = "实盘下单功能未开放"
        return {
            "error": "实盘下单功能未开放",
            "id": "",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price or 0,
            "status": "rejected",
            "timestamp": datetime.now().isoformat(),
        }

    def cancel_order(self, order_id: str) -> bool:
        """实盘撤单未开放。"""
        self._last_error = "实盘撤单功能未开放"
        return False

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """实盘挂单查询未开放。"""
        self._last_error = "实盘挂单查询未开放"
        return []

    def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """实盘持仓查询未开放。"""
        self._last_error = "实盘持仓查询未开放"
        return []


# ============================================================================
#  ExchangeFactory —— 交易所工厂
# ============================================================================


class ExchangeFactory:
    """交易所工厂：根据用户设置创建对应的交易所实例。

    读取 user_config.json 中的 exchange_mode 字段，决定使用 SimExchange 还是 LiveExchange。
    """

    def __init__(self, config_path: Optional[Path] = None):
        """初始化交易所工厂。

        Args:
            config_path: 用户配置文件路径（默认 CONFIG_DIR/user_config.json）
        """
        self._config_path = config_path or USER_CONFIG_FILE
        self._config: Dict[str, Any] = {}

    def load_config(self) -> Dict[str, Any]:
        """加载用户配置。"""
        self._config = load_config(self._config_path)
        return self._config

    def create_exchange(self) -> ExchangeInterface:
        """根据用户设置创建交易所实例。

        Returns:
            SimExchange 或 LiveExchange 实例
        """
        self.load_config()
        mode = self._config.get("exchange_mode", "模拟")
        exchange_id = self._config.get("exchange_id", "okx")

        if mode == "实盘":
            return LiveExchange(exchange_id=exchange_id)
        else:
            return SimExchange(exchange_id=exchange_id)


# ============================================================================
#  模块自检入口
# ============================================================================

if __name__ == "__main__":
    print("=== ExchangeInterface 模块自检 ===")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"配置目录:   {CONFIG_DIR}")
    print(f"API配置:    {API_CONFIG_FILE}")
    print(f"用户配置:   {USER_CONFIG_FILE}")

    # 测试抽象类是否可被检测
    try:
        ExchangeInterface()  # type: ignore[abstract]
        print("❌ 错误：抽象类不应可实例化")
    except TypeError:
        print("✅ ExchangeInterface 正确拒绝直接实例化")

    # 测试 SimExchange 是否可创建
    sim = SimExchange()
    print(f"✅ SimExchange 创建成功，exchange_id={sim.exchange_id}")

    # 测试 LiveExchange 预留状态
    live = LiveExchange()
    assert not live.connect(), "实盘应拒绝连接"
    print(f"✅ LiveExchange 正确预留（连接被拒绝）：{live.last_error}")

    # 测试工厂
    factory = ExchangeFactory()
    ex = factory.create_exchange()
    print(f"✅ ExchangeFactory 创建: {ex.__class__.__name__}, exchange_id={ex.exchange_id}")

    print("\n=== 自检全部通过 ===")
