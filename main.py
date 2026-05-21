# -*- coding: utf-8 -*-
"""
加密货币量化交易桌面应用主程序。
运行方式：python main.py
"""

import ast
import base64
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import socket
import sys
import traceback
import uuid
import winreg
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any, Dict, List, Optional, Tuple

# 禁用CCXT异步/aiohttp路径，避免Windows注册表代理被aiohttp自动读取。
os.environ.setdefault("CCXT_NO_ASYNC", "1")

import ccxt
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import QDate, QMutex, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTabWidget,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import httpx
except Exception:
    httpx = None


# ===== Base paths =====
def get_project_root() -> Path:
    """Return project root for both source and PyInstaller exe under dist/."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir.parent / "strategies").exists() or (exe_dir.parent / "data_cache").exists():
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent


ROOT_DIR = get_project_root()
STRATEGY_DIR = ROOT_DIR / "strategies"
DATA_CACHE_DIR = ROOT_DIR / "data_cache"
CONFIG_DIR = ROOT_DIR / "config"
LOG_FILE = CONFIG_DIR / "trade_logs.csv"
AI_LOG_FILE = CONFIG_DIR / "ai_decision_logs.csv"
SIM_ORDER_FILE = CONFIG_DIR / "sim_orders.csv"
API_CONFIG_FILE = CONFIG_DIR / "api.json"
MAX_LEVERAGE = 20.0
AUTO_TRADE_MAX_TOTAL_LEVERAGE = 3.0
FUNDING_REVERSAL_STRATEGY_NAME = "资金费率反转"
CROSS_SECTION_MOMENTUM_STRATEGY_NAME = "横截面动量选币"
CROSS_SECTION_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT")

# ============ 行情-策略映射（硬编码，AI不可修改） ============
# 这些常量定义市场状态到激活策略的映射关系，仅供系统读取。
# AI和自动交易模块只能读取这些值做决策，绝不能改写。
MARKET_STRATEGY_MAP: Dict[str, Dict[str, Any]] = {
    "TRENDING": {
        "trigger": "ADX>25 且价格在EMA50同侧",
        "strategies": ["趋势回调入场", "BOS移动止损增强版", "SMC_结构转换突破_ETH"],
    },
    "RANGING": {
        "trigger": "ADX<20 且布林带收窄",
        "strategies": ["摆动点区间反转"],
    },
    "HIGH_VOLATILITY": {
        "trigger": "ATR > 20日均ATR × 1.5",
        "strategies": ["BB挤压突破", "成交量异动"],
    },
}
BASE_STRATEGIES: List[str] = ["资金费率反转"]  # 始终运行，非方向性基座策略
INDEPENDENT_STRATEGIES: List[str] = ["趋势衰竭反转"]  # 独立信号触发，不绑定市场状态
WEEKLY_STRATEGIES: List[str] = ["横截面动量选币"]  # 每周自动调仓
DEFAULT_EXCHANGE_TIMEOUT_MS = 30_000
BACKTEST_FEE_RATE = 0.0004
AI_MAIN_ANALYSIS_INTERVAL_SECONDS = 4 * 60 * 60
AI_AUX_CONFIRM_INTERVAL_SECONDS = 30 * 60
DEEPSEEK_TIMEOUT_SECONDS = 60.0

# ===== DeepSeek AI 直连配置（集中管理） =====
# 所有 AI 模块统一从这里读取模型和超时配置，避免各处散落硬编码。
# 用户在 AI 设置页的选择会覆盖模型选择，但 base_url 和 timeout 保持集中管理。
DEEPSEEK_CONFIG = {
    'base_url': 'https://api.deepseek.com',      # API基础地址（openai SDK 兼容）
    'chat_model': 'deepseek-chat',               # 市场分类/快速分析/复盘/策略匹配
    'reasoner_model': 'deepseek-reasoner',       # 深度分析/参数优化/复杂推理
    'timeout': 30,                                # 单次请求超时秒数（默认30s）
}
API_OBFUSCATION_KEY = "lianghua-platform-v2-local-user"
EXCHANGE_FALLBACKS = ("gateio", "binance", "okx")
OKX_BACKUP_HOSTNAMES = ("aws.okx.com", "okx.me")
OKX_DIRECT_HOSTNAME = "okx.com"
COMMON_LOCAL_PROXY_PORTS = (7897, 7890, 10809, 10808, 1080, 20171, 2080)
EXCHANGE_DIRECT_TIMEOUT_MS = 20_000
OKX_PRIMARY_PROXY = {"type": "HTTP", "host": "127.0.0.1", "port": "7897", "source": "fixed:7897", "enabled": True}
_LAST_REAL_CLOSE: Dict[str, float] = {}
_DEFAULT_PRICE_ANCHORS = {
    "BTC": 50_000.0,
    "ETH": 3_000.0,
    "SOL": 150.0,
    "BNB": 600.0,
    "XRP": 0.6,
    "ADA": 0.45,
    "DOGE": 0.12,
    "AVAX": 35.0,
    "DOT": 7.0,
    "LINK": 15.0,
}


# ===== MUJI 极简配色 =====
COLORS = {
    "bg": "#f5f0e8",         # 卡其白背景
    "card": "#ffffff",       # 纯白卡片
    "text_primary": "#4a4a4a",  # 深灰主文字
    "text_secondary": "#8c8c8c", # 浅灰次要文字
    "accent": "#7d9d7a",     # 灰绿强调色
    "warning": "#c4a35a",    # 芥末黄警告
    "danger": "#c46b5a",     # 锈红危险
}

# ===== 兼容旧引用（映射到MUJI色系）=====
COLOR_BG = COLORS["bg"]
COLOR_PANEL = COLORS["card"]
COLOR_PANEL_2 = COLORS["bg"]
COLOR_BORDER = "#d9d9d9"
COLOR_TEXT = COLORS["text_primary"]
COLOR_MUTED = COLORS["text_secondary"]
COLOR_GREEN = "#6b9e6b"
COLOR_RED = COLORS["danger"]
COLOR_BLUE = "#5a8a7a"
COLOR_CORAL = COLORS["accent"]
COLOR_AMBER = COLORS["warning"]
COLOR_ACCENT = COLORS["accent"]


def _json_safe(value: Any) -> Any:
    """递归清洗JSON不支持的NaN/Inf和常见数值类型。"""
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


class SafeJSONEncoder(json.JSONEncoder):
    """自定义JSON编码器：NaN写null，Inf写可读字符串。"""

    def encode(self, value: Any) -> str:
        return super().encode(_json_safe(value))

    def iterencode(self, value: Any, _one_shot: bool = False):
        return super().iterencode(_json_safe(value), _one_shot)


def safe_json_dumps(value: Any, *args: Any, **kwargs: Any) -> str:
    """安全序列化JSON，避免NaN/Inf导致保存或提示生成失败。"""
    kwargs.setdefault("cls", SafeJSONEncoder)
    return getattr(json, "dumps")(_json_safe(value), *args, **kwargs)


def safe_json_dump(value: Any, fp: Any, *args: Any, **kwargs: Any) -> None:
    """安全写入JSON文件，统一走NaN/Inf清洗逻辑。"""
    kwargs.setdefault("cls", SafeJSONEncoder)
    return getattr(json, "dump")(_json_safe(value), fp, *args, **kwargs)


def 格式化数字(value: Any, digits: int = 2) -> str:
    """把数字格式化成适合界面展示的文本。"""
    try:
        num = float(value)
        return f"{num:,.{digits}f}"
    except Exception:
        return "-"


def 规范化市场状态(current_market: str) -> str:
    """把AI和本地规则产生的中文/英文行情状态统一成硬编码风控状态。"""
    text = str(current_market or "").upper()
    if "TREND_END" in text or "EXHAUSTION" in text or "趋势末端" in str(current_market) or "趋势衰竭" in str(current_market):
        return "TREND_END"
    if "TRENDING_UP" in text or "上升" in str(current_market):
        return "TRENDING_UP"
    if "TRENDING_DOWN" in text or "下降" in str(current_market):
        return "TRENDING_DOWN"
    if "HIGH_VOLATILITY" in text or "高波动" in str(current_market):
        return "HIGH_VOLATILITY"
    if "RANGING" in text or "区间" in str(current_market) or "震荡" in str(current_market):
        return "RANGING"
    return "RANGING"



def parse_ai_market_response(text: str, fallback_state: str, fallback_confidence: float) -> Tuple[str, float, str, bool]:
    """解析DeepSeek行情判断，格式异常时返回本地兜底结果。"""
    raw = str(text or "").strip()
    allowed = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "趋势末端"]
    try:
        cleaned = raw
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S | re.I)
        if fenced:
            cleaned = fenced.group(1)
        elif "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
        payload = json.loads(cleaned)
        state = str(payload.get("market_state") or payload.get("state") or "").strip()
        confidence = payload.get("confidence", fallback_confidence)
        confidence = float(confidence)
        if confidence > 1:
            confidence = confidence / 100.0
        confidence = max(0.0, min(1.0, confidence))
        normalized = 规范化市场状态(state)
        if normalized == "TREND_END":
            state = "趋势末端"
        elif normalized in allowed:
            state = normalized
        if state not in allowed:
            raise ValueError(f"invalid market_state: {state}")
        reason = str(payload.get("reason") or payload.get("summary") or raw).strip()
        return state, confidence, reason, True
    except Exception:
        reason = f"AI返回格式异常，已降级为本地规则。原始返回：{raw[:300]}"
        return fallback_state, fallback_confidence, reason, False


def calculate_risk_percent(active_strategies: list, current_market: str) -> float:
    """
    根据当前激活的策略数量和市场状态，返回单策略风险比例。

    参数:
        active_strategies: 当前激活的策略名称列表
        current_market: 当前市场状态 (TRENDING_UP/DOWN, RANGING, HIGH_VOLATILITY)
    返回:
        单策略风险比例 (0.005 ~ 0.015)
    """
    # 规则1 & 3：单策略独立运行时的基准风险，AI只能调用，不能修改内部规则。
    if len(active_strategies) == 1:
        strategy_name = active_strategies[0]
        if FUNDING_REVERSAL_STRATEGY_NAME in strategy_name:
            return 0.010  # 非方向性资金费率反转，独立占用 1%
        elif "趋势衰竭反转" in strategy_name:
            return 0.015  # 反转信号稀缺，提高至 1.5%
        elif "BB挤压突破" in strategy_name:
            return 0.010  # 高波动市保持标准风险
        else:
            return 0.010  # 基准 1%

    # 规则2：双策略共振，总风险不变，每个策略 0.5%。
    if len(active_strategies) == 2:
        return 0.005

    # 兜底：不应出现 3 个及以上策略同时激活。
    return 0.005


def 选择自动交易激活策略(strategies: List["StrategyInfo"], current_market: str, fallback_name: str = "") -> List[str]:
    """根据硬编码行情-策略映射表选择当前自动交易允许激活的策略名称。"""
    state = 规范化市场状态(current_market)
    names = [item.name for item in strategies]

    def find_first(*keywords: str) -> Optional[str]:
        """按关键词从策略库中查找第一个匹配策略。"""
        for name in names:
            if all(key in name for key in keywords):
                return name
        return None

    if state in ("TRENDING_UP", "TRENDING_DOWN"):
        active = []
        trend_pullback = find_first("趋势", "回调") or find_first("趋势回调")
        bos = find_first("BOS") or find_first("移动止损")
        if trend_pullback:
            active.append(trend_pullback)
        if bos and bos not in active:
            active.append(bos)
        return active[:2] or ([fallback_name] if fallback_name else [])

    if state == "TREND_END":
        exhaustion_strategy = find_first("趋势衰竭", "反转") or find_first("衰竭反转")
        return [exhaustion_strategy] if exhaustion_strategy else ([fallback_name] if fallback_name else [])

    if state == "RANGING":
        range_strategy = find_first("摆动", "区间") or find_first("区间反转")
        return [range_strategy] if range_strategy else ([fallback_name] if fallback_name else [])

    if state == "HIGH_VOLATILITY":
        active = []
        bb_strategy = find_first("BB", "挤压") or find_first("挤压突破")
        volume_strategy = find_first("成交量", "异动")
        if bb_strategy:
            active.append(bb_strategy)
        if volume_strategy and volume_strategy not in active:
            active.append(volume_strategy)
        return active[:2] or ([fallback_name] if fallback_name else [])

    return [fallback_name] if fallback_name else []


def 查找资金费率反转策略(strategies: List["StrategyInfo"], symbol: str = "") -> Optional["StrategyInfo"]:
    """资金费率反转是非方向性策略，独立于市场状态常驻。"""
    for item in strategies:
        if FUNDING_REVERSAL_STRATEGY_NAME in item.name and 策略覆盖交易对(item, symbol):
            return item
    return None


def 评估资金费率反转信号(df: pd.DataFrame, strategy: Optional["StrategyInfo"], symbol: str) -> Dict[str, Any]:
    """用策略内 SYMBOL_PARAMS 评估最新一根K线是否触发资金费率反转入场。"""
    if strategy is None:
        return {"enabled": False, "status": "未加载", "has_signal": False, "reason": "未扫描到资金费率反转策略"}
    params = 选择策略参数包(strategy.params, symbol, allow_fallback=True)
    if not params:
        return {"enabled": True, "status": "无信号", "has_signal": False, "reason": "资金费率策略参数缺失"}

    n_lb = int(params.get("n_lookback", 90) or 90)
    entry_pct = float(params.get("entry_percentile", params.get("entry", 0.60)) or 0.60)
    require_reversal = bool(params.get("require_reversal", True))
    extreme_filter = bool(params.get("extreme_filter", False))
    min_premium = float(params.get("min_premium", 0.0) or 0.0)
    leverage = min(AUTO_TRADE_MAX_TOTAL_LEVERAGE, max(1.0, float(params.get("leverage", 1.0) or 1.0)))

    if df is None or df.empty or len(df) < n_lb + 3:
        return {
            "enabled": True,
            "status": "无信号",
            "has_signal": False,
            "params": params,
            "leverage": leverage,
            "reason": f"数据不足，资金费率反转需要至少 {n_lb + 3} 根K线",
        }

    work = df.copy()
    if "premium" in work.columns:
        premium = pd.Series(work["premium"], dtype="float64")
    elif "close_perp" in work.columns and "close_spot" in work.columns:
        perp = pd.Series(work["close_perp"], dtype="float64")
        spot = pd.Series(work["close_spot"], dtype="float64")
        premium = (perp - spot) / spot
    else:
        premium = pd.Series(work["close"], dtype="float64").pct_change(1).fillna(0.0)
    premium = premium.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)

    i = len(premium) - 1
    window = premium[i - n_lb:i]
    current = float(premium[i])
    pct_rank = float(np.searchsorted(np.sort(window), current) / max(1, n_lb))
    cond_extreme = pct_rank >= entry_pct
    cond_magnitude = abs(current) >= min_premium
    cond_reversal = True
    if require_reversal and i > n_lb + 2:
        cond_reversal = bool(premium[i - 1] > premium[i - 2] and current < premium[i - 1])
    cond_not_extreme = True
    if extreme_filter and len(window) > 0:
        cond_not_extreme = bool(abs(current) <= np.max(np.abs(window)) * 1.05)

    has_signal = bool(cond_extreme and cond_magnitude and cond_reversal and cond_not_extreme)
    reason = (
        f"premium={current:.6f}, 分位={pct_rank:.2f}/{entry_pct:.2f}, "
        f"反转确认={'是' if cond_reversal else '否'}"
    )
    return {
        "enabled": True,
        "status": "运行中" if has_signal else "无信号",
        "has_signal": has_signal,
        "params": params,
        "risk_percent": calculate_risk_percent([FUNDING_REVERSAL_STRATEGY_NAME], "ALL"),
        "leverage": leverage,
        "direction": "资金费率套利",
        "reason": reason,
        "strategy": strategy.name,
    }


def 查找横截面动量策略(strategies: List["StrategyInfo"]) -> Optional["StrategyInfo"]:
    """横截面动量选币只输出权重，不直接产生买卖信号。"""
    for item in strategies:
        if CROSS_SECTION_MOMENTUM_STRATEGY_NAME in item.name:
            return item
    return None


def 标准化横截面参数(params: Dict[str, Any]) -> Dict[str, Any]:
    """兼容报告参数命名和策略源码参数命名。"""
    normalized = dict(params or {})
    if "rebalance_freq" in normalized and "rebalance_mode" not in normalized:
        normalized["rebalance_mode"] = normalized["rebalance_freq"]
    if "top_k" in normalized and "top_n" not in normalized:
        normalized["top_n"] = normalized["top_k"]
    if "crash_sigma" in normalized and "crash_std_mult" not in normalized:
        normalized["crash_std_mult"] = normalized["crash_sigma"]
    normalized.setdefault("rebalance_freq", normalized.get("rebalance_mode", "biweekly"))
    normalized.setdefault("rebalance_mode", normalized.get("rebalance_freq", "biweekly"))
    normalized.setdefault("top_k", normalized.get("top_n", 5))
    normalized.setdefault("top_n", normalized.get("top_k", 5))
    normalized.setdefault("momentum_period", 7)
    normalized.setdefault("vol_adjust", True)
    normalized.setdefault("crash_sigma", normalized.get("crash_std_mult", 2.5))
    normalized.setdefault("crash_std_mult", normalized.get("crash_sigma", 2.5))
    return normalized


def _转日线(df: pd.DataFrame) -> pd.DataFrame:
    """横截面策略使用低频日线，实盘/本地缓存都统一转成日线。"""
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "timestamp" in work.columns:
        work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce")
        work = work.dropna(subset=["timestamp"]).set_index("timestamp")
    else:
        work.index = pd.to_datetime(work.index, errors="coerce")
        work = work[~work.index.isna()]
    for col in ["open", "high", "low", "close", "volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    daily = work.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return daily.tail(260)


def 评估横截面动量选币(
    strategy_manager: "StrategyManager",
    strategy: Optional["StrategyInfo"],
    preferred_exchange: str,
    api_config: Dict[str, Any],
    directional_strategies: List["StrategyInfo"],
) -> Dict[str, Any]:
    """运行横截面动量选币，返回方向性策略可用的目标币和崩盘状态。"""
    if strategy is None:
        return {"enabled": False, "status": "未加载", "weights": {}, "target_symbol": "", "cash_only": False, "reason": "未扫描到横截面动量选币策略"}
    params = 标准化横截面参数(选择策略参数包(strategy.params, "BTC", allow_fallback=True))
    ohlc_dict: Dict[str, pd.DataFrame] = {}
    source_map: Dict[str, str] = {}
    for symbol in CROSS_SECTION_SYMBOLS:
        try:
            raw_df, source, _ = 获取市场数据(symbol, "1d", preferred_exchange, api_config, allow_local=True)
            daily = _转日线(raw_df)
            if len(daily) >= 80:
                ohlc_dict[symbol] = daily
                source_map[symbol] = source
        except Exception:
            continue
    if "BTC/USDT" not in ohlc_dict or not ohlc_dict:
        return {"enabled": True, "status": "无数据", "weights": {}, "target_symbol": "", "cash_only": False, "params": params, "reason": "横截面选币缺少BTC日线数据，已跳过权重门控"}

    try:
        module = strategy_manager.load_module(strategy.path)
        result = module.strategy_logic(ohlc_dict, None, params)
    except Exception as exc:
        return {"enabled": True, "status": "异常", "weights": {}, "target_symbol": "", "cash_only": False, "params": params, "reason": f"横截面选币执行失败：{exc}"}

    raw_weights = result.get("weights", {}) if isinstance(result, dict) else {}
    weights = {str(k): float(v or 0.0) for k, v in raw_weights.items()}
    cash_only = bool(weights) and all(value <= 0 for value in weights.values())
    meta = result.get("meta", {}) if isinstance(result, dict) else {}
    eligible = [
        (symbol, weight)
        for symbol, weight in weights.items()
        if weight > 0 and any(策略覆盖交易对(item, symbol) for item in directional_strategies)
    ]
    eligible.sort(key=lambda item: item[1], reverse=True)
    target_symbol = eligible[0][0] if eligible else ""
    status = "全现金" if cash_only else ("运行中" if target_symbol else "无可交易权重")
    reason = str(meta.get("selection_logic") or meta.get("mode") or f"已生成 {len([v for v in weights.values() if v > 0])} 个正权重")
    return {
        "enabled": True,
        "status": status,
        "weights": weights,
        "target_symbol": target_symbol,
        "cash_only": cash_only,
        "params": params,
        "reason": reason,
        "meta": meta,
        "trades": result.get("trades", []) if isinstance(result, dict) else [],
        "source_map": source_map,
    }


def 策略覆盖交易对(strategy: "StrategyInfo", symbol: str) -> bool:
    if not strategy or not symbol:
        return True
    coin = 识别标的文本(symbol)
    label = 获取策略标签(strategy, "标的限制", "適用標的", "适用标的", "标的")
    if not label or label == "-":
        return True
    upper_label = label.upper()
    if coin not in upper_label and "三标的" not in label and "BTC/ETH/SOL" not in upper_label:
        return False
    # 只拒绝明确标注为当前币种不适合/不推荐的情况，避免“BTC、ETH（SOL不适合）”误伤BTC。
    negative_patterns = [
        f"{coin}不适合",
        f"{coin} 不适合",
        f"{coin}（不适合",
        f"{coin}(不适合",
        f"{coin} (不适合",
        f"{coin}不推荐",
        f"{coin} 不推荐",
        f"{coin}（不推荐",
        f"{coin}(不推荐",
        f"{coin} (不推荐",
    ]
    if any(pattern in upper_label or pattern in label for pattern in negative_patterns):
        return False
    return True


def 读取文本(path: Path) -> str:
    """用兼容编码读取策略源码。"""
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=encoding).lstrip("\ufeff")
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def 设置表格样式(table: QTableWidget) -> None:
    """统一表格样式和行为。"""
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)


def 创建按钮(text: str, primary: bool = False) -> QPushButton:
    """创建统一风格按钮。"""
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        button.setProperty("class", "primary")
    return button


def 混淆文本(text: str) -> str:
    """使用简单密钥混淆后做base64编码，满足本地单用户保存需求。"""
    key = API_OBFUSCATION_KEY.encode("utf-8")
    raw = text.encode("utf-8")
    data = bytes(raw[i] ^ key[i % len(key)] for i in range(len(raw)))
    return base64.urlsafe_b64encode(data).decode("utf-8")


def 还原文本(token: str) -> str:
    """还原由混淆文本生成的配置内容。"""
    key = API_OBFUSCATION_KEY.encode("utf-8")
    raw = base64.urlsafe_b64decode(token.encode("utf-8"))
    data = bytes(raw[i] ^ key[i % len(key)] for i in range(len(raw)))
    return data.decode("utf-8")


def 构建代理地址(proxy: Dict[str, Any]) -> str:
    """根据代理配置拼出CCXT可识别的代理URL。"""
    host = str(proxy.get("host", "")).strip()
    port = str(proxy.get("port", "")).strip()
    if not host or not port:
        return ""
    proxy_type = str(proxy.get("type", "HTTP")).lower()
    scheme = "socks5" if "socks" in proxy_type else "http"
    username = str(proxy.get("username", "")).strip()
    password = str(proxy.get("password", "")).strip()
    auth = f"{username}:{password}@" if username or password else ""
    return f"{scheme}://{auth}{host}:{port}"


def _解析代理URL(url: str) -> Dict[str, str]:
    """把环境变量中的代理地址解析成表单字段。"""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        return {}
    proxy_type = "SOCKS5" if "socks" in parsed.scheme.lower() else "HTTP"
    return {
        "type": proxy_type,
        "host": parsed.hostname,
        "port": str(parsed.port),
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }



def _Windows系统代理配置() -> Dict[str, str]:
    """读取Windows当前用户系统代理，解决浏览器能访问但CCXT直连超时的问题。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return {}
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        return {}
    text = str(server or "").strip()
    if not text:
        return {}
    if ";" in text:
        parts = {}
        for chunk in text.split(";"):
            if "=" in chunk:
                name, value = chunk.split("=", 1)
                parts[name.lower()] = value
        text = parts.get("https") or parts.get("http") or parts.get("socks") or next(iter(parts.values()), "")
    if "://" not in text:
        text = "http://" + text
    proxy = _解析代理URL(text)
    if proxy:
        proxy["source"] = "Windows系统代理"
    return proxy

def _环境代理配置() -> Dict[str, str]:
    """优先读取进程环境变量代理，未配置时读取Windows系统代理。"""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        proxy = _解析代理URL(value)
        if proxy:
            proxy["source"] = key
            return proxy
    return _Windows系统代理配置()


def system_proxy_config() -> Dict[str, Any]:
    """读取环境/Windows系统代理，仅用于直连失败后的自动重试。"""
    proxy = _环境代理配置()
    if not proxy:
        return {}
    proxy = dict(proxy)
    proxy["enabled"] = True
    return proxy


def _本机端口打开(host: str, port: int, timeout: float = 0.25) -> bool:
    """检查本机常见代理端口是否在监听。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def okx_hostname_candidates() -> List[str]:
    """按固定降级顺序返回OKX域名：备用域名优先，okx.com直连最后。"""
    return [*OKX_BACKUP_HOSTNAMES, OKX_DIRECT_HOSTNAME]


def auto_proxy_candidates(include_primary_proxy: bool = False) -> List[Dict[str, Any]]:
    """返回自动发现的代理候选：环境/Windows代理 + 本机常见端口。"""
    candidates: List[Dict[str, Any]] = []
    seen = set()

    def add(proxy: Dict[str, Any]) -> None:
        proxy = dict(proxy or {})
        host = str(proxy.get("host", "")).strip()
        port = str(proxy.get("port", "")).strip()
        if not host or not port:
            return
        key = (host, port, str(proxy.get("type", "HTTP")).upper())
        if key in seen:
            return
        seen.add(key)
        proxy["enabled"] = True
        candidates.append(proxy)

    if include_primary_proxy:
        # OKX优先尝试本机固定HTTP代理，端口未监听时由后续备用域名和直连兜底。
        add(OKX_PRIMARY_PROXY)
    add(system_proxy_config())
    for port in COMMON_LOCAL_PROXY_PORTS:
        if _本机端口打开("127.0.0.1", port):
            add({"type": "HTTP", "host": "127.0.0.1", "port": str(port), "source": f"local:{port}"})
            add({"type": "SOCKS5", "host": "127.0.0.1", "port": str(port), "source": f"local-socks:{port}"})
    return candidates


def network_payload_candidates(payload: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """生成统一网络候选：OKX按代理、备用域名、直连的顺序降级。"""
    exchange_id = payload.get("exchange", "okx")
    candidates: List[Tuple[str, Dict[str, Any]]] = []

    if exchange_id == "okx":
        # 国内网络优先走固定本机代理，再试OKX备用域名，最后直连okx.com。
        candidates.append(("代理/127.0.0.1:7897", 构建连接尝试配置(with_proxy_config(payload, OKX_PRIMARY_PROXY), use_proxy=True)))
        for hostname in OKX_BACKUP_HOSTNAMES:
            candidates.append((f"备用域名/{hostname}", 构建连接尝试配置(payload, use_proxy=False, okx_hostname=hostname)))
        candidates.append(("直连/okx.com", 构建连接尝试配置(payload, use_proxy=False, okx_hostname=OKX_DIRECT_HOSTNAME)))
    else:
        candidates.append(("直连", 构建连接尝试配置(payload, use_proxy=False)))
        if not 用户已配置代理(payload):
            for proxy in auto_proxy_candidates():
                candidates.append((f"自动代理/{proxy.get('source', 'auto')}", 构建连接尝试配置(with_proxy_config(payload, proxy), use_proxy=True)))

    if 用户已配置代理(payload):
        # OKX固定代理仍保持第一优先级，用户代理作为同类代理候选紧随其后。
        insert_at = 1 if exchange_id == "okx" and candidates else 0
        candidates.insert(insert_at, ("用户代理", 构建连接尝试配置(payload, use_proxy=True)))
    return candidates

def with_proxy_config(payload: Dict[str, Any], proxy: Dict[str, Any]) -> Dict[str, Any]:
    """为单次重试注入代理，不改写用户保存的配置。"""
    trial = dict(payload)
    merged = dict(proxy or {})
    merged["enabled"] = bool(merged.get("host") and merged.get("port"))
    trial["proxy"] = merged
    return trial


def deepseek_http_client_with_proxy():
    """DeepSeek也复用自动代理候选，避免AI页面卡在网络超时。"""
    if httpx is None:
        return None
    for proxy in auto_proxy_candidates():
        proxy_url = 构建代理地址(proxy)
        if not proxy_url:
            continue
        try:
            return httpx.Client(proxy=proxy_url, timeout=DEEPSEEK_TIMEOUT_SECONDS)
        except TypeError:
            return httpx.Client(proxies=proxy_url, timeout=DEEPSEEK_TIMEOUT_SECONDS)
        except Exception:
            continue
    return None


def extract_deepseek_message_text(response: Any) -> str:
    """兼容DeepSeek推理模型：content为空时尝试取reasoning_content。"""
    try:
        if not response or not response.choices:
            return ""
        message = response.choices[0].message
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content
        reasoning = str(getattr(message, "reasoning_content", "") or "").strip()
        return reasoning
    except Exception:
        return ""


def deepseek_model_candidates(model: str) -> List[str]:
    """结构化输出优先用deepseek-chat，避免推理模型只返回reasoning导致JSON解析失败。"""
    items: List[str] = []
    for item in ["deepseek-chat", model]:
        item = str(item or "").strip()
        if item and item not in items:
            items.append(item)
    return items or ["deepseek-chat"]

def deepseek_chat_text(api_key: str, model: str, messages: List[Dict[str, str]], max_tokens: int = 360) -> Tuple[str, str]:
    """调用DeepSeek并在空返回时自动换用deepseek-chat重试。"""
    if OpenAI is None:
        raise RuntimeError("未安装 openai SDK")
    if not api_key:
        raise RuntimeError("未配置DeepSeek API Key")
    last_error: Optional[Exception] = None
    for candidate in deepseek_model_candidates(model):
        try:
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=DEEPSEEK_TIMEOUT_SECONDS, max_retries=0, http_client=deepseek_http_client_with_proxy())
            response = client.chat.completions.create(model=candidate, messages=messages, max_tokens=max_tokens)
            text = extract_deepseek_message_text(response)
            if text:
                return text, candidate
            last_error = RuntimeError(f"DeepSeek模型 {candidate} 返回为空")
        except Exception as exc:
            last_error = exc
            continue
    raise last_error or RuntimeError("DeepSeek返回为空")


def 构建交易所参数(payload: Dict[str, Any], include_keys: bool = True, use_configured_proxy: bool = True) -> Dict[str, Any]:
    """统一构建CCXT交易所参数，禁用aiohttp读取系统代理。"""
    options: Dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": int(payload.get("timeout_ms") or DEFAULT_EXCHANGE_TIMEOUT_MS),
        "options": {"defaultType": "swap"},
        # 禁止aiohttp/requests从系统环境或Windows注册表自动继承代理。
        "trust_env": False,
        "aiohttp_trust_env": False,
        "enable_aiohttp": False,
    }
    if include_keys:
        options.update(
            {
                "apiKey": payload.get("api_key", ""),
                "secret": payload.get("secret", ""),
                "password": payload.get("password", ""),
            }
        )
    if payload.get("okx_hostname"):
        # OKX CCXT使用hostname组装REST地址，用于直连备用域名重试。
        options["hostname"] = str(payload.get("okx_hostname")).replace("https://", "").rstrip("/")
    proxy_config = dict(payload.get("proxy", {}) or {})
    proxy_type = str(proxy_config.get("type", "不使用代理"))
    proxy_enabled = bool(proxy_config.get("enabled", False)) and "不使用" not in proxy_type
    if use_configured_proxy and proxy_enabled:
        proxy_url = 构建代理地址(proxy_config)
        if proxy_url:
            options["proxies"] = {"http": proxy_url, "https": proxy_url}
    options["proxy_config"] = proxy_config
    return options


def 用户已配置代理(payload: Dict[str, Any]) -> bool:
    """判断当前payload是否携带可用代理配置。"""
    proxy = dict(payload.get("proxy", {}) or {})
    proxy_type = str(proxy.get("type", "不使用代理"))
    if "不使用" in proxy_type:
        return False
    if proxy.get("enabled") is False:
        return False
    return bool(str(proxy.get("host", "")).strip() and str(proxy.get("port", "")).strip())


def 构建连接尝试配置(payload: Dict[str, Any], use_proxy: bool = False, okx_hostname: str = "") -> Dict[str, Any]:
    """为三级降级连接创建独立配置，避免上一次尝试污染下一次。"""
    trial = dict(payload)
    trial["timeout_ms"] = EXCHANGE_DIRECT_TIMEOUT_MS
    trial["okx_hostname"] = okx_hostname
    proxy = dict(payload.get("proxy", {}) or {})
    proxy["enabled"] = bool(use_proxy and 用户已配置代理(payload))
    if not proxy["enabled"]:
        proxy["type"] = "不使用代理"
    trial["proxy"] = proxy
    return trial


def 创建交易所实例(exchange_id: str, payload: Dict[str, Any], include_keys: bool = True, use_configured_proxy: bool = True):
    """创建CCXT交易所实例：按候选payload决定代理、备用域名或直连。"""
    exchange_class = getattr(ccxt, exchange_id)
    options = 构建交易所参数(payload, include_keys=include_keys, use_configured_proxy=use_configured_proxy)
    if exchange_id == "okx" and payload.get("sandbox_mode") and include_keys:
        headers = dict(options.get("headers", {}) or {})
        headers["x-simulated-trading"] = "1"
        options["headers"] = headers
    exchange = exchange_class(options)
    # 双保险：同步实例上也关闭环境代理信任，避免底层库读取系统代理。
    for attr in ("trust_env", "aiohttp_trust_env", "enable_aiohttp"):
        try:
            setattr(exchange, attr, False)
        except Exception:
            pass
    if exchange_id == "okx" and payload.get("okx_hostname"):
        # OKX备用域名通过hostname生效，不改动风控逻辑。
        setattr(exchange, "hostname", str(payload.get("okx_hostname")).replace("https://", "").rstrip("/"))
    try:
        exchange.timeout = int(options.get("timeout") or DEFAULT_EXCHANGE_TIMEOUT_MS)
    except Exception:
        pass
    if payload.get("sandbox_mode") and exchange_id != "okx" and hasattr(exchange, "set_sandbox_mode"):
        exchange.set_sandbox_mode(True)
    if exchange_id == "okx" and payload.get("sandbox_mode") and include_keys:
        exchange.headers = dict(getattr(exchange, "headers", {}) or {})
        exchange.headers["x-simulated-trading"] = "1"
    return exchange

def build_exchange_options(payload: Dict[str, Any], include_keys: bool = True, use_configured_proxy: bool = True) -> Dict[str, Any]:
    return 构建交易所参数(payload, include_keys=include_keys, use_configured_proxy=use_configured_proxy)


def create_exchange_instance(exchange_id: str, payload: Dict[str, Any], include_keys: bool = True, use_configured_proxy: bool = True):
    return 创建交易所实例(exchange_id, payload, include_keys=include_keys, use_configured_proxy=use_configured_proxy)


def 创建私有接口交易所(exchange_id: str, payload: Dict[str, Any]):
    return 创建交易所实例(exchange_id, payload, include_keys=True)


def 分类连接错误(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    dns_hint = "如持续超时，可尝试将DNS改为 8.8.8.8 或 1.1.1.1。"
    if "timeout" in lowered or "timed out" in lowered:
        return f"连接超时：直连在10秒内未返回。{dns_hint}原始信息：{text}"
    if "sandbox" in lowered or "simulated" in lowered or "current environment" in lowered:
        return f"API环境不匹配：请确认当前为模拟盘并且API Key来自OKX模拟交易环境。原始信息：{text}"
    if "timestamp" in lowered or "request time" in lowered:
        return f"签名时间异常：请同步本机时间。原始信息：{text}"
    if any(k in lowered for k in ["authentication", "invalid api", "permission", "signature", "api-key", "apikey", "50105", "50113", "50119"]):
        return f"API拒绝或密钥错误：请检查 API Key、Secret、Passphrase、权限和IP白名单。原始信息：{text}"
    if any(k in lowered for k in ["network", "resolve", "dns", "connection", "proxy", "10013", "10060", "10061", "ssl"]):
        return f"网络不通或连接被拒绝：请先确认直连网络和DNS。{dns_hint}原始信息：{text}"
    if any(k in lowered for k in ["not supported", "has no", "symbol"]):
        return f"交易所或交易对不支持：请确认交易所支持当前接口或交易对。原始信息：{text}"
    return f"连接失败：{text}"

def _截断诊断文本(text: Any, limit: int = 1000) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit] + "..."


def 提取交易所调试信息(exc: Exception, exchange: Any = None, payload: Optional[Dict[str, Any]] = None) -> str:
    payload = payload or {}
    proxy = dict(payload.get("proxy", {}) or {})
    proxy_host = str(proxy.get("host", "")).strip()
    proxy_port = str(proxy.get("port", "")).strip()
    proxy_text = "未启用"
    if proxy_host and proxy_port and proxy.get("enabled", False):
        proxy_text = f"{proxy.get('type', 'HTTP')} {proxy_host}:{proxy_port}"
    headers = getattr(exchange, "headers", {}) if exchange is not None else {}
    simulated_header = "已携带" if str((headers or {}).get("x-simulated-trading", "")) == "1" else "未检测到"
    lines = [
        分类连接错误(exc),
        "",
        "连接诊断：",
        f"- 交易所：{payload.get('exchange', 'okx')}",
        f"- 交易环境：{'模拟盘' if payload.get('sandbox_mode') else '实盘'}",
        f"- OKX模拟盘请求头：{simulated_header}",
        f"- 代理：{proxy_text}",
        f"- 超时：{payload.get('timeout_ms', DEFAULT_EXCHANGE_TIMEOUT_MS)} ms",
        f"- 异常类型：{exc.__class__.__name__}",
    ]
    status = getattr(exchange, "last_http_status", None) if exchange is not None else None
    if status:
        lines.append(f"- HTTP状态：{status}")
    response = getattr(exchange, "last_http_response", "") if exchange is not None else ""
    if response:
        lines.append(f"- 交易所原始响应：{_截断诊断文本(response)}")
    args_text = " | ".join(_截断诊断文本(arg, 320) for arg in getattr(exc, "args", []) if arg)
    if args_text and args_text != str(exc):
        lines.append(f"- 异常参数：{args_text}")
    lines.append("")
    lines.append("说明：OKX会先尝试127.0.0.1:7897代理，再尝试备用域名，最后才直连okx.com。")
    return "\n".join(lines)


def connection_error_detail(exc: Exception) -> str:
    return 分类连接错误(exc)


def exchange_debug_info(exc: Exception, exchange: Any = None, payload: Optional[Dict[str, Any]] = None) -> str:
    return 提取交易所调试信息(exc, exchange, payload)


def save_config(path: Path, new_vals: Dict[str, Any]) -> None:
    """合并保存普通JSON配置：读取旧值后用新值覆盖，失败时打印错误。"""
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


def read_okx_demo_balance(exchange: Any) -> Any:
    return 读取OKX模拟盘余额(exchange)


def 读取OKX模拟盘余额(exchange: Any) -> Any:
    last_exc: Optional[Exception] = None
    original_timeout = int(getattr(exchange, "timeout", DEFAULT_EXCHANGE_TIMEOUT_MS) or DEFAULT_EXCHANGE_TIMEOUT_MS)
    timeout_candidates: List[int] = []
    for value in (original_timeout, DEFAULT_EXCHANGE_TIMEOUT_MS, 60_000):
        value = int(value)
        if value not in timeout_candidates:
            timeout_candidates.append(value)
    for timeout_ms in timeout_candidates:
        try:
            exchange.timeout = timeout_ms
            return exchange.privateGetAccountBalance({})
        except ccxt.RequestTimeout as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    return exchange.privateGetAccountBalance({})


def 解析OKX余额条目(raw_balance: Any) -> int:
    if not isinstance(raw_balance, dict):
        return 0
    count = 0
    for account in raw_balance.get("data", []) or []:
        if isinstance(account, dict):
            count += len(account.get("details", []) or [])
    return count


def 获取策略标签(info: "StrategyInfo", *keys: str) -> str:
    for key in keys:
        value = getattr(info, "labels", {}).get(key)
        if value:
            return str(value)
    return "-"


class ConfigManager:
    """管理配置和日志文件。"""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.key_path = self.config_dir / ".secret.key"
        self.secure_path = self.config_dir / "secure_config.bin"
        self.ui_path = self.config_dir / "ui_state.json"
        self.key = self._load_key()

    def _load_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key() if Fernet else base64.urlsafe_b64encode(os.urandom(32))
        self.key_path.write_bytes(key)
        return key

    def _encrypt(self, text: str) -> str:
        if Fernet:
            return Fernet(self.key).encrypt(text.encode("utf-8")).decode("utf-8")
        digest = hashlib.sha256(self.key).digest()
        raw = text.encode("utf-8")
        encrypted = bytes(raw[i] ^ digest[i % len(digest)] for i in range(len(raw)))
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")

    def _decrypt(self, token: str) -> str:
        if Fernet:
            return Fernet(self.key).decrypt(token.encode("utf-8")).decode("utf-8")
        digest = hashlib.sha256(self.key).digest()
        raw = base64.urlsafe_b64decode(token.encode("utf-8"))
        decrypted = bytes(raw[i] ^ digest[i % len(digest)] for i in range(len(raw)))
        return decrypted.decode("utf-8")

    def load_secure(self) -> Dict[str, Any]:
        if not self.secure_path.exists():
            return {}
        try:
            payload = self.secure_path.read_text(encoding="utf-8")
            return json.loads(self._decrypt(payload)) if payload.strip() else {}
        except Exception:
            return {}

    def save_secure(self, data: Dict[str, Any]) -> None:
        existing = self.load_secure()
        existing.update(data or {})
        self.secure_path.write_text(self._encrypt(safe_json_dumps(existing, ensure_ascii=False, indent=2)), encoding="utf-8")

    def load_ui_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.ui_path.read_text(encoding="utf-8")) if self.ui_path.exists() else {}
        except Exception:
            return {}

    def save_ui_state(self, data: Dict[str, Any]) -> None:
        save_config(self.ui_path, data or {})

    def load_api_config(self) -> Dict[str, Any]:
        if not API_CONFIG_FILE.exists():
            return {}
        try:
            payload = json.loads(API_CONFIG_FILE.read_text(encoding="utf-8"))
            encoded = payload.get("data", "")
            return json.loads(还原文本(encoded)) if encoded else {}
        except Exception:
            return {}

    def save_api_config(self, data: Dict[str, Any]) -> None:
        try:
            # API配置带本地混淆，仍先读取旧值合并，避免新表单只覆盖部分字段时丢配置。
            existing = self.load_api_config()
            existing.update(data or {})
            encoded = 混淆文本(safe_json_dumps(existing, ensure_ascii=False))
            API_CONFIG_FILE.write_text(safe_json_dumps({"version": 1, "data": encoded}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"保存API配置失败：{API_CONFIG_FILE}，错误：{exc}")
            raise

    def ensure_log_file(self) -> None:
        if not LOG_FILE.exists():
            with LOG_FILE.open("w", newline="", encoding="utf-8-sig") as file:
                csv.writer(file).writerow(["时间", "交易所", "交易对", "方向", "类型", "价格", "数量", "状态", "备注"])

    def ensure_ai_log_file(self) -> None:
        if not AI_LOG_FILE.exists():
            with AI_LOG_FILE.open("w", newline="", encoding="utf-8-sig") as file:
                csv.writer(file).writerow(["时间", "类型", "交易对", "周期", "市场状态", "策略", "参数JSON", "决策理由", "风控结果", "订单ID", "后续盈亏", "状态"])

    def ensure_sim_order_file(self) -> None:
        if not SIM_ORDER_FILE.exists():
            with SIM_ORDER_FILE.open("w", newline="", encoding="utf-8-sig") as file:
                csv.writer(file).writerow(["订单ID", "时间", "交易所", "交易对", "方向", "类型", "价格", "数量", "杠杆", "状态", "来源", "备注"])

    def append_trade_log(self, row: List[Any]) -> None:
        self.ensure_log_file()
        with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as file:
            csv.writer(file).writerow(row)

    def append_ai_decision_log(self, row: List[Any]) -> None:
        self.ensure_ai_log_file()
        with AI_LOG_FILE.open("a", newline="", encoding="utf-8-sig") as file:
            csv.writer(file).writerow(row)

    def append_ai_decision_bundle(self, decision: Dict[str, Any], future_pnl: str = "", order_id: str = "", status: str = "已完成") -> None:
        base_row = [decision.get("time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")), "", decision.get("symbol", ""), decision.get("timeframe", ""), decision.get("market_state", ""), decision.get("strategy", ""), safe_json_dumps(decision.get("params", {}), ensure_ascii=False), decision.get("reason", ""), decision.get("risk_result", ""), order_id or decision.get("order_id", ""), future_pnl, status]
        for decision_type in ["市场分析", "策略选择", "参数建议"]:
            row = list(base_row)
            row[1] = decision_type
            self.append_ai_decision_log(row)

    def append_sim_order(self, row: List[Any]) -> None:
        self.ensure_sim_order_file()
        with SIM_ORDER_FILE.open("a", newline="", encoding="utf-8-sig") as file:
            csv.writer(file).writerow(row)

    def load_logs(self) -> List[List[str]]:
        self.ensure_log_file()
        with LOG_FILE.open("r", newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        return rows[1:] if len(rows) > 1 else []

    def load_ai_decision_logs(self) -> List[List[str]]:
        self.ensure_ai_log_file()
        with AI_LOG_FILE.open("r", newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        return rows[1:] if len(rows) > 1 else []

    def load_sim_orders(self) -> List[List[str]]:
        self.ensure_sim_order_file()
        with SIM_ORDER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        return rows[1:] if len(rows) > 1 else []



def 识别标的文本(text: Any) -> str:
    """从交易对、缓存文件名或参数文本中识别 BTC/ETH/SOL。"""
    upper = str(text or "").upper()
    if "ETH" in upper:
        return "ETH"
    if "SOL" in upper:
        return "SOL"
    return "BTC"


def 选择策略参数包(params_map: Dict[str, Dict[str, Any]], symbol_hint: Any = "BTC", allow_fallback: bool = False) -> Dict[str, Any]:
    params_map = params_map or {}
    coin = 识别标的文本(symbol_hint)
    preferred = [key for key in params_map if coin in key.upper()]
    if preferred:
        key = sorted(preferred, key=lambda item: ("HQ" in item.upper(), len(item)), reverse=True)[0]
        params = dict(params_map.get(key, {}) or {})
    elif allow_fallback:
        non_empty = [(key, value) for key, value in params_map.items() if isinstance(value, dict) and value]
        params = dict(non_empty[0][1]) if non_empty else {}
    else:
        generic = [
            (key, value)
            for key, value in params_map.items()
            if isinstance(value, dict) and value and not any(asset in key.upper() for asset in ("BTC", "ETH", "SOL"))
        ]
        params = dict(generic[0][1]) if generic else {}
    if params:
        params.setdefault("symbol", coin)
        params.setdefault("pair", f"{coin}/USDT")
    return params

@dataclass
class StrategyInfo:
    """策略元数据。"""

    name: str
    path: Path
    labels: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class StrategyManager:
    """扫描、解析和加载策略文件。"""

    def __init__(self, strategy_dir: Path):
        self.strategy_dir = strategy_dir
        self.strategies: List[StrategyInfo] = []

    def scan(self) -> List[StrategyInfo]:
        self.strategies = []
        if not self.strategy_dir.exists():
            return self.strategies
        for path in sorted(self.strategy_dir.glob("*.py")):
            source = 读取文本(path)
            labels = self._parse_labels(source)
            params = self._calibrate_default_params(path.stem, self._parse_params(source))
            self.strategies.append(StrategyInfo(path.stem, path, labels, params))
        self.strategies.sort(key=self._strategy_sort_key)
        return self.strategies

    def _parse_labels(self, source: str) -> Dict[str, str]:
        labels: Dict[str, str] = {}
        for line in source.splitlines()[:100]:
            text = line.strip()
            if not text.startswith("#"):
                continue
            text = text.lstrip("#").strip()
            if not text:
                continue
            if "：" in text:
                key, value = text.split("：", 1)
            elif ":" in text:
                key, value = text.split(":", 1)
            else:
                parts = text.split(None, 1)
                if len(parts) != 2:
                    continue
                key, value = parts
            labels[key.strip()] = value.strip()
        return labels

    def _parse_params(self, source: str) -> Dict[str, Dict[str, Any]]:
        """解析策略源码和注释里的 PARAMS_* 参数包。"""
        params: Dict[str, Dict[str, Any]] = {}
        try:
            source = source.lstrip("\ufeff")
            tree = ast.parse(source)
            module_vars: Dict[str, Any] = {}
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        if isinstance(node.value, ast.Dict):
                            value = {}
                            for key_node, value_node in zip(node.value.keys, node.value.values):
                                try:
                                    if isinstance(value_node, ast.Starred) and isinstance(value_node.value, ast.Name):
                                        ref = module_vars.get(value_node.value.id, {})
                                        if isinstance(ref, dict):
                                            value.update(ref)
                                    else:
                                        key = ast.literal_eval(key_node)
                                        value[key] = ast.literal_eval(value_node)
                                except Exception:
                                    continue
                        else:
                            continue
                    module_vars[target.id] = value
                    if target.id.upper() == "SYMBOL_PARAMS" and isinstance(value, dict):
                        for asset, asset_params in value.items():
                            if isinstance(asset_params, dict):
                                params[f"PARAMS_{str(asset).upper()}"] = dict(asset_params)
                        continue
                    if isinstance(value, dict) and value and "PARAM" in target.id.upper():
                        params[target.id] = dict(value)
            for match in re.finditer(r"#\s*(PARAMS_[A-Z0-9_]+)\s*=\s*(\{[^\n]+\})", source):
                key = match.group(1)
                if key in params:
                    continue
                try:
                    value = ast.literal_eval(match.group(2))
                    if isinstance(value, dict) and value:
                        params[key] = value
                except Exception:
                    pass
        except Exception:
            pass
        return params or {"PARAMS_DEFAULT": {}}

    def _calibrate_default_params(self, strategy_name: str, params: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        calibrated = {key: dict(value) for key, value in (params or {}).items() if isinstance(value, dict)} or {"PARAMS_DEFAULT": {}}
        if strategy_name == "SMC_??????_ETH":
            for values in calibrated.values():
                risk_value = values.get("risk_percent")
                if isinstance(risk_value, (int, float)) and risk_value > 0.2:
                    values["risk_percent"] = float(risk_value) / 100.0
        def ensure_param(param_key: str, updates: Dict[str, Any]) -> None:
            base = dict(next(iter(calibrated.values()), {}))
            base.update(updates)
            calibrated[param_key] = base
        def update_matching(match: str, updates: Dict[str, Any]) -> bool:
            touched = False
            for param_key, values in calibrated.items():
                if match in param_key:
                    values.update(updates)
                    touched = True
            return touched
        if strategy_name == "BB挤压突破":
            if not update_matching("BTC", {"squeeze_pct": 8, "expansion_mult": 1.5}):
                ensure_param("PARAMS_BTC", {"squeeze_pct": 8, "expansion_mult": 1.5})
        elif strategy_name == "BOS移动止损增强版":
            if not update_matching("BTC", {"n_swings": 1}):
                ensure_param("PARAMS_BOS_BTC", {"n_swings": 1})
        elif strategy_name == "摆动点区间反转":
            for values in calibrated.values():
                values.update({"rr_ratio": 2.0, "adx_threshold": 20})
        elif strategy_name == "趋势衰竭反转":
            if not update_matching("BTC", {"wick_ratio": 0.3}):
                ensure_param("PARAMS_BTC", {"wick_ratio": 0.3})
        return calibrated

    def _strategy_sort_key(self, item: StrategyInfo) -> Tuple[int, str]:
        order = ["SMC", "趋势回调", "BOS", "趋势衰竭", "摆动点", "BB", "成交量", FUNDING_REVERSAL_STRATEGY_NAME, CROSS_SECTION_MOMENTUM_STRATEGY_NAME]
        for idx, key in enumerate(order):
            if key in item.name:
                return idx, item.name
        return 99, item.name

    def load_module(self, path: Path):
        module_name = f"strategy_{hashlib.md5(str(path).encode('utf-8')).hexdigest()}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载策略文件：{path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class DataCacheManager:
    """管理本地K线缓存。"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def scan(self) -> List[Tuple[str, Path]]:
        items: List[Tuple[str, Path]] = []
        if not self.data_dir.exists():
            return items
        for path in sorted(self.data_dir.rglob("*.csv")):
            items.append((path.stem.replace("_", " "), path))
        return items

    def load(self, path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        if "datetime" in df.columns and "timestamp" not in df.columns:
            df = df.rename(columns={"datetime": "timestamp"})
        if "time" in df.columns and "timestamp" not in df.columns:
            df = df.rename(columns={"time": "timestamp"})
        if "timestamp" not in df.columns:
            df = df.rename(columns={df.columns[0]: "timestamp"})
        if pd.api.types.is_numeric_dtype(df["timestamp"]):
            sample = float(df["timestamp"].dropna().iloc[0]) if len(df["timestamp"].dropna()) else 0
            unit = "ms" if sample > 10_000_000_000 else "s"
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit=unit, errors="coerce")
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        lower_map = {col.lower(): col for col in df.columns}
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns and col in lower_map:
                df = df.rename(columns={lower_map[col]: col})
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return df.dropna(subset=["timestamp", "open", "high", "low", "close"]).reset_index(drop=True)


class MetricCard(QFrame):
    """指标卡片控件。"""

    def __init__(self, title: str, value: str = "-", color: str = COLOR_TEXT):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("mutedLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.value_label.setStyleSheet(f"color: {color};")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, color: str = COLOR_TEXT) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {color};")



def _标准化周期(timeframe: str) -> str:
    text = str(timeframe or "1h").lower()
    mapping = {"h1": "1h", "1h": "1h", "60m": "1h", "h4": "4h", "4h": "4h", "m15": "15m", "15m": "15m"}
    return mapping.get(text, text)


def 读取本地缓存K线(symbol: str, timeframe: str) -> pd.DataFrame:
    cache = DataCacheManager(DATA_CACHE_DIR)
    tf = _标准化周期(timeframe)
    coin = symbol.replace("/", "_").lower()
    candidates = []
    for _, path in cache.scan():
        stem = path.stem.lower()
        if coin in stem and (tf in stem or (tf == "1h" and "h1" in stem)):
            candidates.append(path)
    if not candidates:
        for _, path in cache.scan():
            if coin in path.stem.lower():
                candidates.append(path)
    if not candidates:
        raise RuntimeError(f"本地缓存中未找到 {symbol} {timeframe} 数据。")
    df = cache.load(candidates[0])
    if tf == "4h" and len(df):
        df = df.set_index("timestamp").resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    return df


def _记录真实收盘锚点(symbol: str, df: pd.DataFrame) -> None:
    """记录最近一次真实行情收盘价，供网络失败时的模拟行情锚定。"""
    if df is None or df.empty or "close" not in df:
        return
    close_series = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close_series.empty:
        return
    last_close = float(close_series.iloc[-1])
    if math.isfinite(last_close) and last_close > 0:
        _LAST_REAL_CLOSE[str(symbol).upper()] = last_close


def _周期转时间差(timeframe: str) -> pd.Timedelta:
    """将常见K线周期转为时间间隔，模拟数据生成时使用。"""
    text = _标准化周期(timeframe)
    match = re.fullmatch(r"(\d+)([mhdw])", text)
    if not match:
        return pd.Timedelta(hours=1)
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return pd.Timedelta(minutes=value)
    if unit == "h":
        return pd.Timedelta(hours=value)
    if unit == "d":
        return pd.Timedelta(days=value)
    return pd.Timedelta(weeks=value)


def _模拟价格锚点(symbol: str) -> float:
    """优先使用真实收盘价；没有真实锚点时才使用接近市场量级的保守默认值。"""
    key = str(symbol).upper()
    if key in _LAST_REAL_CLOSE:
        return _LAST_REAL_CLOSE[key]
    base = key.split("/")[0].split("-")[0]
    return _DEFAULT_PRICE_ANCHORS.get(base, 100.0)


def _generate_simulated_ohlcv(symbol: str, timeframe: str, limit: int = 200, annual_vol: float = 1.0) -> pd.DataFrame:
    """生成有真实价格锚点的模拟OHLCV，年化波动率限制在150%以内。"""
    bars = max(30, int(limit or 200))
    step = _周期转时间差(timeframe)
    end_ts = pd.Timestamp.now().floor("min")
    start_ts = end_ts - step * (bars - 1)
    timestamps = pd.date_range(start=start_ts, periods=bars, freq=step)

    annual_vol = min(abs(float(annual_vol or 1.0)), 1.5)
    daily_vol = annual_vol / math.sqrt(365)
    rng = np.random.default_rng()
    price = max(_模拟价格锚点(symbol), 1e-8)
    rows = []
    for ts in timestamps:
        open_price = price
        pct_change = float(rng.normal(0.0, daily_vol))
        pct_change = float(np.clip(pct_change, -0.95, 0.95))
        close_price = max(open_price * (1.0 + pct_change), 1e-8)
        wick = abs(float(rng.normal(0.0, daily_vol / 2)))
        high_price = max(open_price, close_price) * (1.0 + wick)
        low_price = max(min(open_price, close_price) * (1.0 - wick), 1e-8)
        volume = float(rng.lognormal(mean=7.0, sigma=0.6))
        rows.append([ts, open_price, high_price, low_price, close_price, volume])
        price = close_price
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def 获取市场数据(symbol: str, timeframe: str, preferred_exchange: str, payload: Dict[str, Any], allow_local: bool = True) -> Tuple[pd.DataFrame, str, str]:
    """获取真实K线：优先交易所公共API，失败时按统一网络候选重试。"""
    timeframe_value = _标准化周期(timeframe)
    base_payload = dict(payload or {})
    last_exc: Optional[Exception] = None
    exchange_order = [preferred_exchange] + [item for item in EXCHANGE_FALLBACKS if item != preferred_exchange]
    for exchange_id in exchange_order:
        trial_base = dict(base_payload)
        trial_base["exchange"] = exchange_id
        for mode, trial in network_payload_candidates(trial_base):
            try:
                exchange = 创建交易所实例(exchange_id, trial, include_keys=False)
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe_value, limit=200)
                df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().reset_index(drop=True)
                _记录真实收盘锚点(symbol, df)
                return df, f"{exchange_id} public API/{mode}", exchange_id
            except Exception as exc:
                last_exc = exc
    if allow_local:
        try:
            df = 读取本地缓存K线(symbol, timeframe_value)
            _记录真实收盘锚点(symbol, df)
            return df, "data_cache", "local"
        except Exception as exc:
            last_exc = exc
    df = _generate_simulated_ohlcv(symbol, timeframe_value, limit=200, annual_vol=1.0)
    return df, f"simulated(anchor={_模拟价格锚点(symbol):.8g})", "simulated"



def get_market_data(symbol: str, timeframe: str, preferred_exchange: str, payload: Dict[str, Any], allow_local: bool = True) -> Tuple[pd.DataFrame, str, str]:
    return globals()["\u83b7\u53d6\u5e02\u573a\u6570\u636e"](symbol, timeframe, preferred_exchange, payload, allow_local=allow_local)

def 计算重放段绩效(result: Dict[str, Any], capital: float) -> Dict[str, Any]:
    raw_equity = np.asarray(result.get("equity", [1.0]), dtype=float)
    if raw_equity.size == 0:
        raw_equity = np.asarray([1.0])
    raw_equity = np.nan_to_num(raw_equity, nan=1.0, posinf=1.0, neginf=1.0)
    first = raw_equity[0] if abs(raw_equity[0]) > 1e-12 else 1.0
    curve = np.maximum(capital * (raw_equity / first), 0.0001)
    peak = np.maximum.accumulate(curve)
    drawdown = curve / peak - 1.0
    return {"curve": curve.tolist(), "final_equity": float(curve[-1]), "return_pct": float(curve[-1] / capital - 1.0) if capital else 0.0, "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0, "trades": result.get("trades", [])}



class AIDecisionEngine:
    """AI决策引擎：DeepSeek建议 + 本地技术指标兜底。"""

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config

    def _calc_market_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """计算ADX、ATR、EMA位置、布林带宽度和成交量比。"""
        frame = df.tail(260).copy()
        close = frame["close"].astype(float)
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        volume = frame.get("volume", pd.Series([0] * len(frame))).astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
        atr = float(atr_series.iloc[-1]) if len(atr_series.dropna()) else float(tr.mean() or 0)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr_s = atr_series.replace(0, np.nan).reset_index(drop=True)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr_s
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr_s
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = float(dx.rolling(14).mean().iloc[-1]) if len(dx.dropna()) else 0.0
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=min(200, len(close)), adjust=False).mean().iloc[-1])
        last = float(close.iloc[-1])
        if last >= ema20 >= ema50 >= ema200:
            pos = "多头排列"
        elif last <= ema20 <= ema50 <= ema200:
            pos = "空头排列"
        else:
            pos = "均线缠绕"
        vol_ratio = float(volume.tail(5).mean() / max(volume.tail(30).mean(), 1e-9)) if len(volume) >= 30 else 1.0
        bb_mid = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else close.mean()
        bb_std = close.rolling(20).std().iloc[-1] if len(close) >= 20 else close.std()
        bb_width = float((4 * bb_std) / bb_mid) if bb_mid else 0.0
        change = float(close.pct_change(12).iloc[-1]) if len(close) >= 13 else 0.0
        return {"adx": adx, "atr": atr, "volume_ratio": vol_ratio, "ema20": ema20, "ema50": ema50, "ema200": ema200, "price": last, "price_position": pos, "bb_width": bb_width, "change": change}

    def _local_state_from_indicators(self, indicators: Dict[str, Any]) -> Tuple[str, float, str]:
        """DeepSeek不可用时的本地判断：先判高波动，再用ADX+EMA判断趋势。"""
        adx = float(indicators.get("adx", 0) or 0)
        pos = str(indicators.get("price_position", ""))
        bb_width = float(indicators.get("bb_width", 0) or 0)
        vol_ratio = float(indicators.get("volume_ratio", 1) or 1)
        if bb_width > 0.08 or vol_ratio > 2.2:
            return "HIGH_VOLATILITY", 0.45, "本地兜底：布林带宽度或成交量比显著放大。"
        if adx > 25 and "多头" in pos:
            return "TRENDING_UP", 0.4, "本地兜底：ADX>25且EMA处于多头排列。"
        if adx > 25 and "空头" in pos:
            return "TRENDING_DOWN", 0.4, "本地兜底：ADX>25且EMA处于空头排列。"
        return "RANGING", 0.4, "本地兜底：趋势强度不足，按RANGING处理。"

    def _deepseek_market_state(self, indicators: Dict[str, Any], symbol: str, timeframe: str, fallback: Tuple[str, float, str]) -> Tuple[str, float, str, str]:
        """调用DeepSeek判断市场状态，失败必须降级本地规则。"""
        if OpenAI is None:
            return fallback[0], fallback[1], "⚠️ 未安装openai SDK，已降级为本地规则: " + fallback[2], "fallback"
        secure = self.config.load_secure() if self.config else {}
        ai = secure.get("deepseek", {})
        api_key = ai.get("api_key", "")
        if not api_key:
            return fallback[0], fallback[1], "⚠️ 未配置DeepSeek，已降级为本地规则: " + fallback[2], "fallback"
        prompt = (
            "请只返回JSON，不要Markdown。字段：market_state, confidence, reason。"
            "market_state只能是 TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY, 趋势末端。\n"
            f"交易对={symbol}, 周期={timeframe}, 特征={safe_json_dumps(indicators, ensure_ascii=False)}"
        )
        try:
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=DEEPSEEK_TIMEOUT_SECONDS, max_retries=0, http_client=deepseek_http_client_with_proxy())
            response = client.chat.completions.create(
                model=ai.get("model", "deepseek-v4-flash"),
                messages=[{"role": "system", "content": "你是量化市场状态分类器。AI只能给建议，不能修改风控。"}, {"role": "user", "content": prompt}],
                max_tokens=220,
            )
            text = extract_deepseek_message_text(response)
            if not str(text or "").strip():
                return fallback[0], fallback[1], "⚠️ AI返回空，已降级为本地规则: " + fallback[2], "fallback"
            state, confidence, reason, ok = parse_ai_market_response(text, fallback[0], fallback[1])
            if not ok:
                return state, confidence, "⚠️ AI返回格式异常，已降级为本地规则: " + fallback[2], "fallback"
            return state, confidence, reason, "deepseek"
        except Exception as exc:
            return fallback[0], fallback[1], f"⚠️ AI超时或网络异常，已降级为本地规则: {fallback[2]}；原因：{exc}", "fallback"

    def make_decision(self, df: pd.DataFrame, strategies: List[StrategyInfo], default_strategy: Optional[StrategyInfo], default_params: Dict[str, Any], symbol: str, timeframe: str, source: str, use_deepseek: bool = False, previous_state: str = "") -> Dict[str, Any]:
        indicators = self._calc_market_indicators(df)
        fallback = self._local_state_from_indicators(indicators)
        if use_deepseek:
            state, confidence, reason, analysis_source = self._deepseek_market_state(indicators, symbol, timeframe, fallback)
        else:
            state, confidence, reason = fallback
            analysis_source = "local"
            if previous_state and 规范化市场状态(previous_state) != 规范化市场状态(state):
                reason = f"辅助确认：市场状态从 {previous_state} 衰减/切换到 {state}。" + reason
        candidates = [item for item in strategies if 策略覆盖交易对(item, symbol)] or strategies
        strategy = self._choose_strategy({"state": state}, candidates, default_strategy, symbol)
        active_strategies = 选择自动交易激活策略(candidates, state, strategy.name if strategy else "")
        params = 选择策略参数包(strategy.params, symbol) if strategy and strategy.params else {}
        if not params:
            params = dict(default_params)
        direction = "买入" if state == "TRENDING_UP" else ("卖出" if state == "TRENDING_DOWN" else "观望")
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "symbol": symbol, "timeframe": timeframe, "market_state": state, "confidence": confidence, "strategy": strategy.name if strategy else "-", "strategy_path": str(strategy.path) if strategy else "", "active_strategies": active_strategies, "params": params, "direction": direction, "suggested_leverage": 1, "reason": reason, "analysis_source": analysis_source, "indicators": indicators}

    def _choose_strategy(self, market: Dict[str, Any], strategies: List[StrategyInfo], default_strategy: Optional[StrategyInfo], symbol: str = "") -> Optional[StrategyInfo]:
        state = 规范化市场状态(str(market.get("state", "")))
        candidates = [item for item in strategies if 策略覆盖交易对(item, symbol)] or strategies
        preferred = 选择自动交易激活策略(candidates, state, default_strategy.name if default_strategy else "")
        for name in preferred:
            for item in candidates:
                if item.name == name:
                    return item
        return candidates[0] if candidates else default_strategy

class BacktestWorker(QThread):
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, strategy_path: Path, data_path: Path, params: Dict[str, Any], start_date: QDate, end_date: QDate, capital: float, leverage: float, fee_rate: float = BACKTEST_FEE_RATE):
        super().__init__()
        self.strategy_path = strategy_path
        self.data_path = data_path
        self.params = params
        self.start_date = start_date
        self.end_date = end_date
        self.capital = capital
        self.leverage = leverage
        self.fee_rate = fee_rate

    def run(self) -> None:
        try:
            df = DataCacheManager(DATA_CACHE_DIR).load(self.data_path)
            start_ts = pd.Timestamp(self.start_date.toPyDate())
            end_ts = pd.Timestamp(self.end_date.toPyDate()) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            if df["timestamp"].dt.tz is not None:
                start_ts = start_ts.tz_localize("UTC")
                end_ts = end_ts.tz_localize("UTC")
            df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)
            if len(df) < 30:
                raise RuntimeError("回测数据不足。")
            module = StrategyManager(STRATEGY_DIR).load_module(self.strategy_path)
            raw = module.strategy_logic(df, None, self.params)
            self.finished_ok.emit(self._normalize_result(raw, df, self.leverage, self.fee_rate))
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _entry_close_from_idx(self, item: Dict[str, Any], df: pd.DataFrame) -> Tuple[float, bool]:
        """用 entry_idx 对应的当前K线收盘价校正入场价，避免历史/未来K线价格泄露。"""
        if "entry_idx" not in item or "close" not in df:
            return 0.0, False
        try:
            entry_idx = int(item.get("entry_idx"))
        except Exception:
            return 0.0, False
        if entry_idx < 0 or entry_idx >= len(df):
            return 0.0, False
        entry_close = float(pd.to_numeric(pd.Series([df["close"].iloc[entry_idx]]), errors="coerce").iloc[0])
        if math.isfinite(entry_close) and entry_close > 0:
            return entry_close, True
        return 0.0, False

    def _normalize_result(self, raw: Dict[str, Any], df: pd.DataFrame, leverage: float, fee_rate: float) -> Dict[str, Any]:
        equity = np.asarray(raw.get("equity", [1.0]), dtype=float)
        if len(equity) == 0:
            equity = np.asarray([1.0])
        first = equity[0] if abs(equity[0]) > 1e-12 else 1.0
        equity = np.maximum(self.capital * (equity / first), 0.0001)
        peak = np.maximum.accumulate(equity)
        drawdown = equity / peak - 1.0
        trades = []
        for item in raw.get("trades", []) or []:
            if isinstance(item, dict):
                entry_price = float(item.get("entry_price", item.get("price", 0)) or 0)
                corrected_entry, corrected = self._entry_close_from_idx(item, df)
                if corrected:
                    entry_price = corrected_entry
                exit_price = float(item.get("exit_price", item.get("price", 0)) or 0)
                raw_pct = item.get("pnl_pct", None)
                if corrected and entry_price and exit_price:
                    # 入场价被校正后必须重算收益率，不能沿用策略层可能带偏的 pnl_pct。
                    raw_pct = None
                if raw_pct is None:
                    raw_pnl = float(item.get("pnl", 0) or 0)
                    if entry_price and exit_price:
                        side_text = str(item.get("direction", item.get("side", item.get("type", "")))).lower()
                        if "short" in side_text or "?" in side_text:
                            raw_pct = (entry_price - exit_price) / entry_price
                        else:
                            raw_pct = (exit_price - entry_price) / entry_price
                    else:
                        raw_pct = raw_pnl / max(self.capital, 1e-9)
                raw_pct = float(raw_pct or 0)
                if abs(raw_pct) > 1:
                    raw_pct = raw_pct / 100.0
                trades.append({"direction": item.get("direction", item.get("side", item.get("type", "-"))), "entry_time": str(item.get("entry_time", item.get("time", item.get("entry_idx", "-")))), "exit_time": str(item.get("exit_time", item.get("exit_idx", "-"))), "entry_price": entry_price, "exit_price": exit_price, "pnl_pct": raw_pct, "reason": item.get("reason", item.get("exit_reason", "-"))})
        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]
        pf = sum(t["pnl_pct"] for t in wins) / abs(sum(t["pnl_pct"] for t in losses)) if losses and abs(sum(t["pnl_pct"] for t in losses)) > 1e-9 else sum(t["pnl_pct"] for t in wins)
        metrics = {"total_return": float(equity[-1] / equity[0] - 1.0) if len(equity) > 1 and equity[0] else 0.0, "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0, "trade_count": len(trades), "win_rate": len(wins) / len(trades) if trades else 0.0, "profit_factor": float(pf), "final_equity": float(equity[-1])}
        return {"equity": equity.tolist(), "drawdown": drawdown.tolist(), "trades": trades, "metrics": metrics, "attribution": {}}


class HistoricalReplayWorker(QThread):
    """历史行情重放线程，批量推进以保证速度调节有效。"""

    progress = pyqtSignal(dict)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, config: ConfigManager, data_path: Path, baseline_strategy_path: Path, start_date: QDate, end_date: QDate, speed: int = 1, batch_size: int = 24):
        super().__init__()
        self.config = config
        self.data_path = data_path
        self.baseline_strategy_path = baseline_strategy_path
        self.start_date = start_date
        self.end_date = end_date
        self.speed = max(1, int(speed))
        self.batch_size = max(1, int(batch_size))
        self.running = True
        self.paused = False

    def stop(self) -> None:
        self.running = False

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def run(self) -> None:
        try:
            data_manager = DataCacheManager(DATA_CACHE_DIR)
            strategy_manager = StrategyManager(STRATEGY_DIR)
            strategies = strategy_manager.scan()
            if not strategies:
                raise RuntimeError("未扫描到可用于重放的策略文件。")
            symbol_hint = self.data_path.stem
            compatible_strategies = [item for item in strategies if 策略覆盖交易对(item, symbol_hint)]
            if not compatible_strategies:
                raise RuntimeError("当前数据标的没有匹配的策略，请检查策略文件头部标的标签。")
            selected = next((item for item in compatible_strategies if item.path == self.baseline_strategy_path), None)
            baseline_strategy = selected or compatible_strategies[0]
            baseline_params = 选择策略参数包(baseline_strategy.params, symbol_hint)
            if not baseline_params:
                raise RuntimeError(f"策略 {baseline_strategy.name} 没有适配 {识别标的文本(symbol_hint)} 的参数包，已停止重放以避免假结果。")
            strategies = compatible_strategies
            df = data_manager.load(self.data_path)
            start_ts = pd.Timestamp(self.start_date.toPyDate())
            end_ts = pd.Timestamp(self.end_date.toPyDate()) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            if df["timestamp"].dt.tz is not None:
                start_ts = start_ts.tz_localize("UTC")
                end_ts = end_ts.tz_localize("UTC")
            df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)
            if len(df) < 120:
                raise RuntimeError("可用于重放的数据不足，请放宽时间范围。")
            warmup = min(max(80, self.batch_size * 4), len(df) // 2)
            engine = AIDecisionEngine(self.config)
            module_cache: Dict[Path, Any] = {baseline_strategy.path: strategy_manager.load_module(baseline_strategy.path)}
            current_ai_capital = 10000.0
            current_static_capital = 10000.0
            ai_curve = [current_ai_capital]
            static_curve = [current_static_capital]
            timeline: List[Dict[str, Any]] = []
            strategy_stats: Dict[str, Dict[str, float]] = {}
            switch_count = correct_switch = wrong_switch = 0
            avoided_drawdown = missed_profit = 0.0
            prev_strategy_path = baseline_strategy.path
            step = max(1, self.batch_size)
            sleep_ms = 0 if self.speed >= 10 else max(1, int(40 / self.speed))
            starts = list(range(warmup, len(df), step))
            total_steps = max(1, len(starts))
            if self.speed >= 100:
                emit_every = 20
            elif self.speed >= 50:
                emit_every = 10
            elif self.speed >= 10:
                emit_every = 4
            elif self.speed >= 5:
                emit_every = 2
            else:
                emit_every = 1
            for idx, start in enumerate(starts, 1):
                if not self.running:
                    break
                while self.paused and self.running:
                    self.msleep(80)
                end = min(len(df), start + step)
                context_df = df.iloc[:start].reset_index(drop=True)
                segment_df = df.iloc[max(0, end - 320):end].reset_index(drop=True)
                if len(segment_df) < 80:
                    continue
                decision = engine.make_decision(context_df, strategies, baseline_strategy, baseline_params, self.data_path.stem, "1h", "历史行情重放")
                ai_strategy_path = Path(decision.get("strategy_path") or baseline_strategy.path)
                ai_strategy = next((item for item in strategies if item.path == ai_strategy_path), baseline_strategy)
                ai_params = dict(decision.get("params") or 选择策略参数包(ai_strategy.params, self.data_path.stem) or baseline_params)
                if ai_strategy.path not in module_cache:
                    module_cache[ai_strategy.path] = strategy_manager.load_module(ai_strategy.path)
                try:
                    static_result = module_cache[baseline_strategy.path].strategy_logic(segment_df, None, baseline_params)
                except Exception as exc:
                    static_result = {"equity": [current_static_capital, current_static_capital], "trades": []}
                    decision["reason"] = f"静态策略本步跳过：{exc}；" + str(decision.get("reason", ""))
                try:
                    ai_result = module_cache[ai_strategy.path].strategy_logic(segment_df, None, ai_params)
                except Exception as exc:
                    ai_result = {"equity": [current_ai_capital, current_ai_capital], "trades": []}
                    decision["reason"] = f"AI策略本步跳过：{exc}；" + str(decision.get("reason", ""))
                static_perf = 计算重放段绩效(static_result, current_static_capital)
                ai_perf = 计算重放段绩效(ai_result, current_ai_capital)
                static_before = current_static_capital
                ai_before = current_ai_capital
                current_static_capital = static_perf["final_equity"]
                current_ai_capital = ai_perf["final_equity"]
                static_curve.append(current_static_capital)
                ai_curve.append(current_ai_capital)
                switched = ai_strategy.path != prev_strategy_path
                switch_note = ""
                if switched:
                    switch_count += 1
                    if ai_perf["return_pct"] >= static_perf["return_pct"]:
                        correct_switch += 1
                        switch_note = "正确切换"
                    else:
                        wrong_switch += 1
                        switch_note = "错误切换"
                    avoided_drawdown += max(0.0, abs(static_perf["max_drawdown"]) - abs(ai_perf["max_drawdown"])) * max(ai_before, 1.0)
                    missed_profit += max(0.0, static_perf["return_pct"] - ai_perf["return_pct"]) * max(static_before, 1.0)
                direction = str(decision.get("direction", "观望"))
                action = "平仓" if switched and "趋势衰竭反转" in ai_strategy.name else ("买" if direction in ("买入", "做多") else ("卖" if direction in ("卖出", "做空") else "观望"))
                segment_end_time = str(segment_df["timestamp"].iloc[-1])[:19]
                step_pnl = current_ai_capital - ai_before
                stat = strategy_stats.setdefault(ai_strategy.name, {"count": 0, "pnl": 0.0, "wins": 0, "trades": 0})
                stat["count"] += 1
                stat["pnl"] += float(step_pnl)
                stat["wins"] += 1 if step_pnl > 0 else 0
                stat["trades"] += len(ai_perf.get("trades", []) or [])
                timeline.append({"time": segment_end_time, "state": decision.get("market_state", "-"), "strategy": ai_strategy.name, "switched": switched, "note": switch_note, "action": action, "pnl": step_pnl, "decision": str(decision.get("reason", ""))[:220], "ai_equity": current_ai_capital, "static_equity": current_static_capital})
                if idx % emit_every == 0 or idx == total_steps:
                    self.progress.emit({"progress": max(1, min(100, int(idx / total_steps * 100))), "time": segment_end_time, "state": decision.get("market_state", "-"), "strategy": ai_strategy.name, "switched": switched, "switch_note": switch_note, "ai_equity": current_ai_capital, "static_equity": current_static_capital, "ai_curve": list(ai_curve), "static_curve": list(static_curve), "timeline": list(timeline), "switch_count": switch_count, "correct_switch": correct_switch, "wrong_switch": wrong_switch, "dynamic_final": current_ai_capital, "static_final": current_static_capital, "switch_ratio": float(correct_switch / switch_count) if switch_count else 0.0})
                prev_strategy_path = ai_strategy.path
                if sleep_ms:
                    self.msleep(sleep_ms)
            report = {"timeline": timeline, "ai_curve": ai_curve, "static_curve": static_curve, "strategy_stats": strategy_stats, "switch_count": switch_count, "correct_switch": correct_switch, "wrong_switch": wrong_switch, "switch_ratio": float(correct_switch / switch_count) if switch_count else 0.0, "avoided_drawdown": float(avoided_drawdown), "missed_profit": float(missed_profit), "dynamic_final": float(current_ai_capital), "static_final": float(current_static_capital), "start_time": str(df["timestamp"].iloc[warmup])[:19], "end_time": str(df["timestamp"].iloc[-1])[:19]}
            self.finished_ok.emit(report)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ApiTestWorker(QThread):
    """API连接测试线程。"""

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, task: str, payload: Dict[str, Any]):
        super().__init__()
        self.task = task
        self.payload = payload

    def run(self) -> None:
        try:
            if self.task == "exchange":
                self._test_exchange()
            elif self.task == "deepseek":
                self._test_deepseek()
        except Exception as exc:
            self.failed.emit(str(exc) if self.task == "exchange" else str(exc))

    def _exchange_attempts(self) -> List[Tuple[str, Dict[str, Any]]]:
        """使用统一网络候选队列测试交易所连通性。"""
        return network_payload_candidates(self.payload)

    def _test_exchange_once(self, mode: str, payload: Dict[str, Any]) -> str:
        """执行单次连接测试，模拟盘优先验证私有余额接口。"""
        exchange_id = payload.get("exchange", "okx")
        has_keys = bool(payload.get("api_key") and payload.get("secret"))
        sandbox_mode = bool(payload.get("sandbox_mode"))
        hostname = str(payload.get("okx_hostname", "") or "")
        host_note = f"，域名 {hostname}" if hostname else ""
        if sandbox_mode and not has_keys:
            raise RuntimeError("模拟盘连接需要 API Key 和 Secret。")
        if sandbox_mode and has_keys:
            private_exchange = 创建私有接口交易所(exchange_id, payload)
            if exchange_id == "okx" and hasattr(private_exchange, "privateGetAccountBalance"):
                balance = 读取OKX模拟盘余额(private_exchange)
                return f"✅ 已连接（{mode}）：模拟盘API验证成功{host_note}，OKX余额明细 {解析OKX余额条目(balance)} 个。"
            balance = private_exchange.fetch_balance()
            currencies = [key for key, value in balance.get("total", {}).items() if value]
            return f"✅ 已连接（{mode}）：{exchange_id} 模拟盘私有接口可用{host_note}，非零币种 {len(currencies)} 个。"
        public_exchange = 创建交易所实例(exchange_id, payload, include_keys=False)
        ticker = public_exchange.fetch_ticker("BTC/USDT")
        last_price = ticker.get("last") or ticker.get("close") or "-"
        if has_keys:
            try:
                private_exchange = 创建私有接口交易所(exchange_id, payload)
                balance = private_exchange.fetch_balance()
                currencies = [key for key, value in balance.get("total", {}).items() if value]
                return f"✅ 已连接（{mode}）：Public API正常{host_note}，BTC/USDT={last_price}；私钥验证成功，非零币种 {len(currencies)} 个。"
            except Exception as private_exc:
                return f"✅ 已连接（{mode}）：Public API正常{host_note}，BTC/USDT={last_price}；私有余额探测失败，原因：{分类连接错误(private_exc)}"
        return f"✅ 已连接（{mode}）：Public API正常{host_note}，BTC/USDT={last_price}；未填写私钥，已跳过私钥验证。"

    def _test_exchange(self) -> None:
        errors: List[str] = []
        direct_timeout = False
        for mode, trial in self._exchange_attempts():
            try:
                self.finished_ok.emit(self._test_exchange_once(mode, trial))
                return
            except Exception as exc:
                detail = 分类连接错误(exc)
                if mode.startswith("直连") and ("超时" in detail or "timeout" in str(exc).lower() or "timed out" in str(exc).lower()):
                    direct_timeout = True
                host = trial.get("okx_hostname", "")
                label = mode + (f"/{host}" if host else "")
                errors.append(f"- {label}：{detail}")
        if not 用户已配置代理(self.payload):
            errors.append("- 代理：未启用用户代理，已跳过第三级代理测试。")
        dns_hint = "\n如持续超时，可尝试将DNS改为 8.8.8.8 或 1.1.1.1。" if direct_timeout else ""
        raise RuntimeError("连接失败，可尝试配置代理后重试。" + dns_hint + "\n\n尝试记录：\n" + "\n".join(errors))

    def _test_deepseek(self) -> None:
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK，请先安装 requirements.txt。")
        client = OpenAI(api_key=self.payload.get("api_key", ""), base_url="https://api.deepseek.com", timeout=DEEPSEEK_TIMEOUT_SECONDS, max_retries=0, http_client=deepseek_http_client_with_proxy())
        response = client.chat.completions.create(model=self.payload.get("model", "deepseek-v4-flash"), messages=[{"role": "user", "content": "请回复：连接正常"}], max_tokens=16)
        text = response.choices[0].message.content if response.choices else "连接正常"
        self.finished_ok.emit(f"DeepSeek连接成功：{text}")

class LiveRefreshWorker(QThread):
    """实盘监控刷新线程。"""

    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, exchange_config: Dict[str, Any]):
        super().__init__()
        self.exchange_config = exchange_config

    def run(self) -> None:
        """读取余额和持仓，与交易所连接测试共用网络候选队列。"""
        errors: List[str] = []
        last_exchange = None
        exchange_id = self.exchange_config.get("exchange", "okx")
        for mode, trial in network_payload_candidates(self.exchange_config):
            try:
                exchange = 创建私有接口交易所(exchange_id, trial)
                last_exchange = exchange
                if exchange_id == "okx" and trial.get("sandbox_mode") and hasattr(exchange, "privateGetAccountBalance"):
                    raw_balance = 读取OKX模拟盘余额(exchange)
                    balance = self._parse_okx_balance(raw_balance)
                else:
                    balance = exchange.fetch_balance()
                positions = []
                if exchange_id == "okx" and trial.get("sandbox_mode") and hasattr(exchange, "privateGetAccountPositions"):
                    try:
                        raw_positions = exchange.privateGetAccountPositions({})
                        positions = self._parse_okx_positions(raw_positions)
                    except Exception:
                        positions = []
                elif exchange.has.get("fetchPositions"):
                    try:
                        positions = exchange.fetch_positions()
                    except Exception:
                        positions = []
                self.finished_ok.emit({"balance": balance, "positions": positions, "network_mode": mode})
                return
            except Exception as exc:
                errors.append(f"{mode}: {分类连接错误(exc)}")
                last_exchange = locals().get("exchange", last_exchange)
        warning = "\n".join(errors[-6:]) if errors else "????"
        self.finished_ok.emit({"balance": {"total": {}, "free": {}, "used": {}}, "positions": [], "warning": warning + "\n\n" + exchange_debug_info(RuntimeError(warning), last_exchange, self.exchange_config)})

    def _parse_okx_positions(self, raw_positions: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in (raw_positions.get("data", []) if isinstance(raw_positions, dict) else []):
            if not isinstance(item, dict):
                continue
            size = float(item.get("pos", item.get("availPos", 0)) or 0)
            if abs(size) <= 0:
                continue
            rows.append({
                "symbol": str(item.get("instId", "-")).replace("-SWAP", "/USDT:USDT"),
                "side": item.get("posSide") or ("long" if size > 0 else "short"),
                "contracts": abs(size),
                "entryPrice": float(item.get("avgPx", 0) or 0),
                "markPrice": float(item.get("markPx", item.get("last", 0)) or 0),
                "unrealizedPnl": float(item.get("upl", 0) or 0),
                "leverage": item.get("lever", "-"),
            })
        return rows

    def _parse_okx_balance(self, raw_balance: Any) -> Dict[str, Dict[str, float]]:
        """把OKX原始余额响应转换成界面表格需要的CCXT近似结构。"""
        total: Dict[str, float] = {}
        free: Dict[str, float] = {}
        used: Dict[str, float] = {}
        for account in (raw_balance.get("data", []) if isinstance(raw_balance, dict) else []):
            for item in account.get("details", []) if isinstance(account, dict) else []:
                cur = str(item.get("ccy", "")).upper()
                if not cur:
                    continue
                total[cur] = float(item.get("eq", item.get("cashBal", 0)) or 0)
                free[cur] = float(item.get("availBal", item.get("availEq", 0)) or 0)
                used[cur] = max(0.0, total[cur] - free[cur])
        return {"total": total, "free": free, "used": used}


class AutoTradeWorker(QThread):
    """Simulation auto-trading worker. Never places real orders."""

    decision_ready = pyqtSignal(dict)
    order_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, config: ConfigManager, exchange_id: str, symbol: str, interval_seconds: int = 60):
        super().__init__()
        self.config = config
        self.exchange_id = exchange_id or "okx"
        self.symbol = symbol
        self.interval_seconds = interval_seconds
        self.running = True
        self.open_directional_leverages: List[float] = []
        self.open_nondirectional_leverages: List[float] = []
        self.last_active_strategies: List[str] = []
        self.last_switch_time: Optional[datetime] = None
        self.last_main_analysis_at: Optional[datetime] = None
        self.last_aux_analysis_at: Optional[datetime] = None
        self.last_market_decision: Optional[Dict[str, Any]] = None
        self.last_cross_section_date: str = ""
        self.last_cross_section_result: Optional[Dict[str, Any]] = None

    def stop(self) -> None:
        """Stop the loop."""
        self.running = False

    def run(self) -> None:
        while self.running:
            try:
                payload = self._execute_once()
                if payload:
                    self.decision_ready.emit(payload["decision"])
                    self.order_ready.emit(payload["order"])
            except Exception as exc:
                self.failed.emit(str(exc))
            for _ in range(60):
                if not self.running:
                    break
                self.msleep(1000)

    def _execute_once(self) -> Dict[str, Any]:
        """Execute one simulated auto-trading cycle."""
        api_config = self.config.load_api_config()
        strategy_manager = StrategyManager(STRATEGY_DIR)
        strategies = strategy_manager.scan()
        directional_universe = [
            item for item in strategies
            if CROSS_SECTION_MOMENTUM_STRATEGY_NAME not in item.name and FUNDING_REVERSAL_STRATEGY_NAME not in item.name
        ]
        now = datetime.now()
        today_key = now.strftime("%Y-%m-%d")
        cross_section_strategy = 查找横截面动量策略(strategies)
        if self.last_cross_section_date != today_key or not self.last_cross_section_result:
            self.last_cross_section_result = 评估横截面动量选币(
                strategy_manager, cross_section_strategy, self.exchange_id, api_config, directional_universe
            )
            self.last_cross_section_date = today_key
        cross_section = dict(self.last_cross_section_result or {})
        selected_symbol = cross_section.get("target_symbol") or self.symbol
        df, market_source, used_exchange = 获取市场数据(selected_symbol, "1h", self.exchange_id, api_config, allow_local=True)

        funding_strategy = 查找资金费率反转策略(strategies, selected_symbol)
        funding_signal = 评估资金费率反转信号(df, funding_strategy, selected_symbol)
        default_strategy = strategies[0] if strategies else None
        default_params = 选择策略参数包(default_strategy.params, selected_symbol) if default_strategy and default_strategy.params else {}
        engine = AIDecisionEngine(self.config)
        need_main = self.last_main_analysis_at is None or (now - self.last_main_analysis_at).total_seconds() >= AI_MAIN_ANALYSIS_INTERVAL_SECONDS
        need_aux = self.last_aux_analysis_at is None or (now - self.last_aux_analysis_at).total_seconds() >= AI_AUX_CONFIRM_INTERVAL_SECONDS
        if self.last_market_decision and self.last_market_decision.get("symbol") != selected_symbol:
            need_main = True
            need_aux = True
        if not need_main and not need_aux and self.last_market_decision:
            decision = dict(self.last_market_decision)
            decision["time"] = now.strftime("%Y-%m-%d %H:%M:%S")
            decision["reason"] = "沿用上一轮市场状态，等待下一次主分析/辅助确认。" + str(decision.get("reason", ""))
            decision["analysis_source"] = "cached"
        else:
            decision = engine.make_decision(
                df, strategies, default_strategy, default_params, selected_symbol, "1h", "自动交易",
                use_deepseek=need_main,
                previous_state=(self.last_market_decision or {}).get("market_state", ""),
            )
            if need_main:
                self.last_main_analysis_at = now
            if need_aux or need_main:
                self.last_aux_analysis_at = now
            self.last_market_decision = dict(decision)
        next_main = (self.last_main_analysis_at or now) + timedelta(seconds=AI_MAIN_ANALYSIS_INTERVAL_SECONDS)
        decision["next_main_analysis_time"] = next_main.strftime("%H:%M:%S")
        decision["next_main_analysis_seconds"] = max(0, int((next_main - now).total_seconds()))

        decision.setdefault("params", {})
        direction_blocked_by_cross_section = bool(cross_section.get("cash_only"))
        closed_cross_section_leverage = 0.0
        if direction_blocked_by_cross_section and self.open_directional_leverages:
            closed_cross_section_leverage = sum(self.open_directional_leverages)
            self.open_directional_leverages.clear()
        decision["cross_section_momentum"] = {
            "enabled": bool(cross_section.get("enabled")),
            "status": cross_section.get("status", "未加载"),
            "target_symbol": selected_symbol if not direction_blocked_by_cross_section else "",
            "weights": cross_section.get("weights", {}),
            "cash_only": direction_blocked_by_cross_section,
            "params": cross_section.get("params", {}),
            "reason": cross_section.get("reason", ""),
            "closed_directional_leverage": closed_cross_section_leverage,
        }
        decision["symbol"] = selected_symbol
        raw_active_strategies = 选择自动交易激活策略(strategies, decision.get("market_state", ""), decision.get("strategy", ""))
        active_strategies = raw_active_strategies
        market_state = 规范化市场状态(decision.get("market_state", ""))
        now = datetime.now()
        reversal_override = market_state == "TREND_END" or any("趋势衰竭反转" in item for item in raw_active_strategies)
        closed_trend_leverage = 0.0
        if reversal_override and self.open_directional_leverages:
            # 趋势衰竭反转是硬编码风控优先级最高的信号，触发时先平掉已有顺势模拟仓位。
            closed_trend_leverage = sum(self.open_directional_leverages)
            self.open_directional_leverages.clear()
        # 策略切换冷却期硬编码为12小时；趋势衰竭反转信号可无视冷却期立即切换。
        if (
            self.last_active_strategies
            and raw_active_strategies
            and raw_active_strategies != self.last_active_strategies
            and not reversal_override
            and self.last_switch_time
            and now - self.last_switch_time < timedelta(hours=12)
        ):
            active_strategies = self.last_active_strategies
            left = timedelta(hours=12) - (now - self.last_switch_time)
            decision["switch_status"] = f"策略切换冷却中，剩余约{max(0, int(left.total_seconds() // 3600))}小时，沿用上一组策略"
        else:
            if raw_active_strategies != self.last_active_strategies:
                self.last_active_strategies = list(raw_active_strategies)
                self.last_switch_time = now
            decision["switch_status"] = "趋势衰竭触发，已平掉顺势仓位并立即切换" if reversal_override else "允许切换/保持"
        if direction_blocked_by_cross_section:
            active_strategies = []
            decision["switch_status"] = f"横截面崩盘检测全现金，已平掉方向性仓位 {closed_cross_section_leverage:.1f}x，暂停开仓"
            decision["direction"] = "观望"
            decision["reason"] = f"横截面动量选币全现金：{cross_section.get('reason', '')}；" + str(decision.get("reason", ""))
        risk_percent = 0.0 if direction_blocked_by_cross_section else calculate_risk_percent(active_strategies, 规范化市场状态(decision.get("market_state", "")))
        # 自动交易硬编码总杠杆上限为3x，AI建议只能被截断，不能突破。
        leverage = min(AUTO_TRADE_MAX_TOTAL_LEVERAGE, max(1.0, float(decision.get("suggested_leverage", 1) or 1)))
        decision["params"]["leverage"] = leverage
        decision["params"]["risk_percent"] = risk_percent
        decision["active_strategies"] = active_strategies
        decision["directional_strategies"] = list(active_strategies)
        decision["nondirectional_strategies"] = [FUNDING_REVERSAL_STRATEGY_NAME] if funding_signal.get("enabled") else []
        price = float(df["close"].iloc[-1])
        direction = decision.get("direction", "观望")
        risk_amount = 10000.0 * risk_percent
        quantity = risk_amount * leverage / price if price > 0 else 0.0
        order_id = f"SIM-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        risk_result = "通过"
        status = "模拟成交"
        current_total_leverage = sum(self.open_directional_leverages) + sum(self.open_nondirectional_leverages)
        if direction == "观望" or quantity <= 0:
            risk_result = "拒绝：方向为观望或数量无效"
            status = "风控拒绝"
        elif current_total_leverage + leverage > AUTO_TRADE_MAX_TOTAL_LEVERAGE:
            risk_result = "总杠杆超限，已拒绝"
            status = "风控拒绝"
            quantity = 0.0
        if status == "模拟成交":
            self.open_directional_leverages.append(leverage)

        funding_order_id = ""
        funding_status = str(funding_signal.get("status", "无信号"))
        funding_risk_result = "无信号"
        funding_leverage = float(funding_signal.get("leverage", 1.0) or 1.0)
        funding_risk_percent = float(funding_signal.get("risk_percent", calculate_risk_percent([FUNDING_REVERSAL_STRATEGY_NAME], "ALL")) or 0.0)
        funding_quantity = 0.0
        if funding_signal.get("enabled") and self.open_nondirectional_leverages:
            funding_status = "运行中"
            funding_risk_result = "已有仓位，保持独立运行"
        elif funding_signal.get("has_signal"):
            funding_order_id = f"SIM-FUND-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            funding_quantity = 10000.0 * funding_risk_percent * funding_leverage / price if price > 0 else 0.0
            total_before_funding = sum(self.open_directional_leverages) + sum(self.open_nondirectional_leverages)
            if funding_quantity <= 0:
                funding_status = "风控拒绝"
                funding_risk_result = "拒绝：资金费率数量无效"
            elif total_before_funding + funding_leverage > AUTO_TRADE_MAX_TOTAL_LEVERAGE:
                funding_status = "风控拒绝"
                funding_risk_result = "总杠杆超限，已拒绝"
                funding_quantity = 0.0
            else:
                funding_status = "运行中"
                funding_risk_result = "通过"
                self.open_nondirectional_leverages.append(funding_leverage)
                funding_remark = (
                    f"数据源={market_source}；非方向性={FUNDING_REVERSAL_STRATEGY_NAME}；"
                    f"风险={funding_risk_percent * 100:.2f}%；"
                    f"总杠杆={sum(self.open_directional_leverages) + sum(self.open_nondirectional_leverages):.1f}x；"
                    f"{funding_signal.get('reason', '')}"
                )
                funding_order_row = [
                    funding_order_id,
                    decision["time"],
                    used_exchange,
                    selected_symbol,
                    funding_signal.get("direction", "资金费率套利"),
                    "市价",
                    f"{price:.6f}",
                    f"{funding_quantity:.8f}",
                    f"{funding_leverage:.0f}x",
                    "模拟成交",
                    FUNDING_REVERSAL_STRATEGY_NAME,
                    funding_remark,
                ]
                funding_trade_row = [decision["time"], used_exchange, selected_symbol, funding_signal.get("direction", "资金费率套利"), "模拟市价", f"{price:.6f}", f"{funding_quantity:.8f}", "模拟成交", funding_remark]
                self.config.append_sim_order(funding_order_row)
                self.config.append_trade_log(funding_trade_row)

        projected_total_leverage = sum(self.open_directional_leverages) + sum(self.open_nondirectional_leverages)
        decision["funding_reversal"] = {
            "strategy": funding_signal.get("strategy", FUNDING_REVERSAL_STRATEGY_NAME),
            "status": funding_status,
            "has_signal": bool(funding_signal.get("has_signal")),
            "risk_percent": funding_risk_percent,
            "leverage": funding_leverage,
            "risk_result": funding_risk_result,
            "order_id": funding_order_id,
            "reason": funding_signal.get("reason", ""),
        }
        decision["total_leverage"] = projected_total_leverage
        remark = (
            f"数据源={market_source}；激活策略={'+'.join(active_strategies) or '-'}；"
            f"选币={cross_section.get('status', '-')}/{selected_symbol if not direction_blocked_by_cross_section else 'USDT现金'}；"
            f"单策略风险={risk_percent * 100:.2f}%；总杠杆={projected_total_leverage:.1f}x；"
            f"非方向性={FUNDING_REVERSAL_STRATEGY_NAME}[{funding_status}]；"
            f"切换状态={decision.get('switch_status', '-')}; 平仓杠杆={closed_trend_leverage:.1f}x; "
            f"{decision.get('market_state')}：{str(decision.get('reason', ''))[:160]}"
        )
        order_row = [
            order_id,
            decision["time"],
            used_exchange,
            selected_symbol,
            direction,
            "市价",
            f"{price:.6f}",
            f"{quantity:.8f}",
            f"{leverage:.0f}x",
            status,
            "AI自动交易",
            remark,
        ]
        trade_row = [decision["time"], used_exchange, selected_symbol, direction, "模拟市价", f"{price:.6f}", f"{quantity:.8f}", status, remark]
        self.config.append_sim_order(order_row)
        self.config.append_trade_log(trade_row)
        decision["risk_result"] = risk_result
        decision["order_id"] = order_id
        decision["status"] = status
        decision["data_source"] = market_source
        decision["summary"] = (
            f"{selected_symbol} {decision['market_state']} {direction} {leverage:.0f}x {status} / "
            f"风险{risk_percent * 100:.2f}% / 策略{'+'.join(active_strategies) or '-'} / "
            f"非方向性{FUNDING_REVERSAL_STRATEGY_NAME}[{funding_status}]"
        )
        self.config.append_ai_decision_bundle(decision, "", order_id, status)
        return {"decision": decision, "order": order_row}


class StrategyBacktestPage(QWidget):
    """策略回测页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.strategy_manager = StrategyManager(STRATEGY_DIR)
        self.data_manager = DataCacheManager(DATA_CACHE_DIR)
        self.strategy_items: List[StrategyInfo] = []
        self.data_items: List[Tuple[str, Path]] = []
        self.worker: Optional[BacktestWorker] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.ai_review_text = ""
        self.ai_review_index = 0
        self.ai_review_timer = QTimer(self)
        self.ai_review_timer.timeout.connect(self._ai_review_step)
        self._build_ui()
        self.reload_all()

    def _build_ui(self) -> None:
        """构建回测页面。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 12)
        title = QLabel("策略回测")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        splitter.addWidget(left)

        form_group = QGroupBox("回测参数")
        form = QFormLayout(form_group)
        self.strategy_combo = QComboBox()
        self.data_combo = QComboBox()
        self.start_date = QDateEdit()
        self.end_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.end_date.setCalendarPopup(True)
        self.capital_input = QLineEdit("10000")
        self.leverage_input = QLineEdit("1")
        form.addRow("策略", self.strategy_combo)
        form.addRow("交易对/周期", self.data_combo)
        form.addRow("开始日期", self.start_date)
        form.addRow("结束日期", self.end_date)
        form.addRow("初始本金", self.capital_input)
        form.addRow("杠杆倍数", self.leverage_input)
        left_layout.addWidget(form_group)

        self.strategy_detail = QTextEdit()
        self.strategy_detail.setReadOnly(True)
        self.strategy_detail.setFixedHeight(86)
        detail_group = QGroupBox("策略详情")
        detail_layout = QVBoxLayout(detail_group)
        detail_layout.addWidget(self.strategy_detail)
        left_layout.addWidget(detail_group)

        self.param_text = QPlainTextEdit()
        self.param_text.setPlaceholderText("策略参数JSON")
        self.param_text.setFixedHeight(150)
        param_group = QGroupBox("策略参数")
        param_layout = QVBoxLayout(param_group)
        param_layout.addWidget(self.param_text)
        left_layout.addWidget(param_group)

        self.strategy_table = QTableWidget(0, 4)
        self.strategy_table.setHorizontalHeaderLabels(["策略", "行情", "频率", "标的"])
        设置表格样式(self.strategy_table)
        self.strategy_table.setMaximumHeight(170)
        left_layout.addWidget(self.strategy_table, 0)

        button_row = QHBoxLayout()
        self.reload_button = 创建按钮("刷新策略")
        self.run_button = 创建按钮("开始回测", True)
        self.ai_optimize_button = 创建按钮("AI自动优化")
        button_row.addWidget(self.reload_button)
        button_row.addWidget(self.run_button)
        button_row.addWidget(self.ai_optimize_button)
        left_layout.addStretch(1)
        left_layout.addLayout(button_row)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 0, 0, 0)
        splitter.addWidget(right)
        splitter.setSizes([440, 1000])

        metric_row = QHBoxLayout()
        self.cards = {
            "total_return": MetricCard("总收益", "-"),
            "max_drawdown": MetricCard("最大回撤", "-"),
            "trade_count": MetricCard("交易笔数", "-"),
            "win_rate": MetricCard("胜率", "-"),
            "profit_factor": MetricCard("盈亏因子", "-"),
            "final_equity": MetricCard("期末权益", "-"),
        }
        for card in self.cards.values():
            metric_row.addWidget(card)
        right_layout.addLayout(metric_row)

        attribution_row = QHBoxLayout()
        self.view_combo = QComboBox()
        self.view_combo.addItems(["回测曲线", "AI贡献分析"])
        self.ai_excess_card = MetricCard("AI超额收益", "-")
        self.ai_ratio_card = MetricCard("AI收益占比", "-")
        self.ai_summary_card = MetricCard("AI建议摘要", "-")
        attribution_row.addWidget(QLabel("视图"))
        attribution_row.addWidget(self.view_combo)
        attribution_row.addWidget(self.ai_excess_card)
        attribution_row.addWidget(self.ai_ratio_card)
        attribution_row.addWidget(self.ai_summary_card, 2)
        right_layout.addLayout(attribution_row)

        chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self.equity_plot = pg.PlotWidget()
        self.drawdown_plot = pg.PlotWidget()
        self._prepare_plot(self.equity_plot, "资金曲线")
        self._prepare_plot(self.drawdown_plot, "回撤曲线")
        chart_splitter.addWidget(self.equity_plot)
        chart_splitter.addWidget(self.drawdown_plot)
        chart_splitter.setSizes([420, 180])
        right_layout.addWidget(chart_splitter, 2)

        self.trade_table = QTableWidget(0, 7)
        self.trade_table.setHorizontalHeaderLabels(["方向", "入场时间", "出场时间", "入场价", "出场价", "收益", "原因"])
        设置表格样式(self.trade_table)
        self.trade_table.setMinimumHeight(170)
        right_layout.addWidget(self.trade_table, 1)

        review_row = QHBoxLayout()
        self.ai_read_button = 创建按钮("AI解读", True)
        review_row.addWidget(self.ai_read_button)
        review_row.addStretch()
        right_layout.addLayout(review_row)
        self.ai_review_output = QTextEdit()
        self.ai_review_output.setReadOnly(True)
        self.ai_review_output.setMaximumHeight(120)
        right_layout.addWidget(self.ai_review_output)

        self.reload_button.clicked.connect(self.reload_all)
        self.run_button.clicked.connect(self.run_backtest)
        self.ai_optimize_button.clicked.connect(self.run_ai_auto_optimize)
        self.ai_read_button.clicked.connect(self.run_ai_result_review)
        self.strategy_combo.currentIndexChanged.connect(self.update_params)
        self.data_combo.currentIndexChanged.connect(self.update_date_range)
        self.view_combo.currentIndexChanged.connect(self.replot_current_result)

    def _prepare_plot(self, plot: pg.PlotWidget, title: str) -> None:
        """设置图表深色风格。"""
        plot.setBackground(COLOR_PANEL)
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setTitle(title, color=COLOR_TEXT, size="12pt")
        plot.getAxis("left").setTextPen(COLOR_MUTED)
        plot.getAxis("bottom").setTextPen(COLOR_MUTED)

    def reload_all(self) -> None:
        """刷新策略和缓存数据。"""
        self.strategy_items = self.strategy_manager.scan()
        self.data_items = self.data_manager.scan()
        self.strategy_combo.clear()
        self.data_combo.clear()
        for item in self.strategy_items:
            market = 获取策略标签(item, "适用行情", "適用行情")
            self.strategy_combo.addItem(f"{item.name} | {market}", item)
        for label, path in self.data_items:
            self.data_combo.addItem(label, path)
        self._fill_strategy_table()
        self.update_params()
        self.update_strategy_detail()
        self.update_date_range()
        self.status_changed.emit(f"已扫描 {len(self.strategy_items)} 个策略，{len(self.data_items)} 个缓存文件。")

    def _fill_strategy_table(self) -> None:
        """填充策略列表。"""
        self.strategy_table.setRowCount(len(self.strategy_items))
        for row, info in enumerate(self.strategy_items):
            values = [
                info.name,
                获取策略标签(info, "适用行情", "適用行情"),
                获取策略标签(info, "交易频率", "交易頻率"),
                获取策略标签(info, "标的", "适用标的", "標的限制", "标的限制"),
            ]
            for col, value in enumerate(values):
                self.strategy_table.setItem(row, col, QTableWidgetItem(str(value)))

    def update_params(self) -> None:
        """根据当前策略显示默认参数。"""
        info = self.strategy_combo.currentData()
        if not isinstance(info, StrategyInfo):
            self.param_text.setPlainText("{}")
            return
        symbol_hint = self.data_combo.currentData().stem if hasattr(self, "data_combo") and isinstance(self.data_combo.currentData(), Path) else info.name
        default_params = 选择策略参数包(info.params, symbol_hint)
        if not 策略覆盖交易对(info, symbol_hint):
            self.param_text.setPlainText("{}")
            self.status_changed.emit(f"当前策略不覆盖 {识别标的文本(symbol_hint)}，请选择匹配的数据源或策略。")
        else:
            self.param_text.setPlainText(safe_json_dumps(default_params, ensure_ascii=False, indent=2))
        self.update_strategy_detail()

    def update_strategy_detail(self) -> None:
        """展示当前策略头部标签详情。"""
        info = self.strategy_combo.currentData()
        if not isinstance(info, StrategyInfo):
            self.strategy_detail.setPlainText("")
            return
        lines = [
            f"策略文件：{info.name}",
            f"适用行情：{获取策略标签(info, '适用行情', '適用行情')}",
            f"不适行情：{获取策略标签(info, '不适行情', '不適行情', '不适用行情')}",
            f"核心逻辑：{获取策略标签(info, '核心逻辑', '核心邏輯')}",
            f"适用标的：{获取策略标签(info, '标的', '适用标的', '標的限制', '标的限制')}",
        ]
        self.strategy_detail.setPlainText("\n".join(lines))

    def update_date_range(self) -> None:
        """根据缓存数据设置日期范围。"""
        path = self.data_combo.currentData()
        if not isinstance(path, Path):
            today = QDate.currentDate()
            self.start_date.setDate(today.addMonths(-3))
            self.end_date.setDate(today)
            return
        try:
            df = self.data_manager.load(path)
            start = df["timestamp"].min().date()
            end = df["timestamp"].max().date()
            self.start_date.setDate(QDate(start.year, start.month, start.day))
            self.end_date.setDate(QDate(end.year, end.month, end.day))
        except Exception:
            pass

    def run_backtest(self) -> None:
        """启动回测线程。"""
        info = self.strategy_combo.currentData()
        data_path = self.data_combo.currentData()
        if not isinstance(info, StrategyInfo) or not isinstance(data_path, Path):
            QMessageBox.warning(self, "提示", "请先选择策略和缓存数据。")
            return
        try:
            params = json.loads(self.param_text.toPlainText() or "{}")
            if not 策略覆盖交易对(info, data_path.stem):
                QMessageBox.warning(self, "标的不匹配", f"策略 {info.name} 的标的标签不覆盖 {识别标的文本(data_path.stem)}，请切换策略或数据源。")
                return
            if not params:
                QMessageBox.warning(self, "参数缺失", f"策略 {info.name} 没有适配 {识别标的文本(data_path.stem)} 的默认参数包，已停止回测以避免假结果。")
                return
            capital = float(self.capital_input.text())
            leverage = float(self.leverage_input.text())
            if leverage > MAX_LEVERAGE:
                leverage = MAX_LEVERAGE
                self.leverage_input.setText(str(int(MAX_LEVERAGE)))
                self.status_changed.emit("杠杆超过20x，已自动截断为20x。")
        except Exception as exc:
            QMessageBox.warning(self, "参数错误", f"请检查本金、杠杆和参数JSON：{exc}")
            return
        self.run_button.setEnabled(False)
        self.status_changed.emit("回测运行中...")
        self.worker = BacktestWorker(info.path, data_path, params, self.start_date.date(), self.end_date.date(), capital, leverage)
        self.worker.finished_ok.connect(self.on_backtest_ok)
        self.worker.failed.connect(self.on_backtest_failed)
        self.worker.start()

    def on_backtest_ok(self, result: Dict[str, Any]) -> None:
        """展示回测结果。"""
        self.run_button.setEnabled(True)
        self.last_result = result
        metrics = result["metrics"]
        self.cards["total_return"].set_value(f"{metrics['total_return'] * 100:.2f}%", COLOR_GREEN if metrics["total_return"] >= 0 else COLOR_RED)
        self.cards["max_drawdown"].set_value(f"{metrics['max_drawdown'] * 100:.2f}%", COLOR_RED)
        self.cards["trade_count"].set_value(str(metrics["trade_count"]), COLOR_TEXT)
        self.cards["win_rate"].set_value(f"{metrics['win_rate'] * 100:.1f}%", COLOR_GREEN if metrics["win_rate"] >= 0.5 else COLOR_AMBER)
        self.cards["profit_factor"].set_value(f"{metrics['profit_factor']:.2f}", COLOR_GREEN if metrics["profit_factor"] >= 1 else COLOR_RED)
        self.cards["final_equity"].set_value(格式化数字(metrics["final_equity"]), COLOR_TEXT)
        self._fill_attribution_cards(result)
        self._plot_result(result)
        self._fill_trade_table(result["trades"])
        self.status_changed.emit("回测完成。")

    def on_backtest_failed(self, message: str) -> None:
        """展示回测错误。"""
        self.run_button.setEnabled(True)
        self.status_changed.emit("回测失败。")
        QMessageBox.critical(self, "回测失败", message[-3000:])

    def _plot_result(self, result: Dict[str, Any]) -> None:
        """绘制资金曲线和回撤曲线。"""
        if self.view_combo.currentText() == "AI贡献分析" and result.get("attribution"):
            self._plot_attribution(result["attribution"])
            return
        equity = np.asarray(result["equity"], dtype=float)
        drawdown = np.asarray(result["drawdown"], dtype=float) * 100
        x = np.arange(len(equity))
        self.equity_plot.clear()
        self.drawdown_plot.clear()
        self.equity_plot.setTitle("资金曲线", color=COLOR_TEXT, size="12pt")
        self.drawdown_plot.setTitle("回撤曲线", color=COLOR_TEXT, size="12pt")
        self.equity_plot.plot(x, equity, pen=pg.mkPen(COLOR_GREEN, width=2))
        self.drawdown_plot.plot(x, drawdown, pen=pg.mkPen(COLOR_RED, width=2), fillLevel=0, brush=pg.mkBrush(246, 70, 93, 45))

    def _plot_attribution(self, attribution: Dict[str, Any]) -> None:
        """绘制β、β+α和α三条AI贡献分析曲线。"""
        beta = np.asarray(attribution.get("beta", []), dtype=float)
        beta_alpha = np.asarray(attribution.get("beta_alpha", []), dtype=float)
        alpha = np.asarray(attribution.get("alpha", []), dtype=float)
        x = np.arange(len(beta))
        self.equity_plot.clear()
        self.drawdown_plot.clear()
        self.equity_plot.setTitle("AI贡献分析：β / β+α", color=COLOR_TEXT, size="12pt")
        self.drawdown_plot.setTitle("AI超额收益α", color=COLOR_TEXT, size="12pt")
        self.equity_plot.plot(x, beta, pen=pg.mkPen(COLOR_AMBER, width=2), name="β 静态策略收益")
        self.equity_plot.plot(x, beta_alpha, pen=pg.mkPen(COLOR_GREEN, width=2), name="β+α AI调参收益")
        self.drawdown_plot.plot(x, alpha, pen=pg.mkPen(COLOR_BLUE, width=2), fillLevel=0, brush=pg.mkBrush(59, 130, 246, 45), name="α 超额收益")

    def _fill_attribution_cards(self, result: Dict[str, Any]) -> None:
        """更新AI贡献分析指标卡。"""
        attribution = result.get("attribution") or {}
        if not attribution:
            self.ai_excess_card.set_value("-")
            self.ai_ratio_card.set_value("-")
            self.ai_summary_card.set_value("-")
            return
        excess = float(attribution.get("ai_excess", 0.0))
        ratio = float(attribution.get("ai_ratio", 0.0))
        decision = attribution.get("decision", {})
        params = decision.get("params", {})
        summary = f"{decision.get('strategy', '-')} / {decision.get('market_state', '-')} / {params.get('leverage', 1)}x"
        self.ai_excess_card.set_value(格式化数字(excess), COLOR_GREEN if excess >= 0 else COLOR_RED)
        self.ai_ratio_card.set_value(f"{ratio * 100:.2f}%", COLOR_GREEN if ratio >= 0 else COLOR_RED)
        self.ai_summary_card.set_value(summary, COLOR_TEXT)

    def replot_current_result(self) -> None:
        """切换视图时重绘当前回测结果。"""
        if self.last_result:
            self._plot_result(self.last_result)

    def _fill_trade_table(self, trades: List[Dict[str, Any]]) -> None:
        """填充交易明细。"""
        self.trade_table.setRowCount(len(trades))
        for row, trade in enumerate(trades):
            values = [
                trade["direction"],
                trade["entry_time"],
                trade["exit_time"],
                格式化数字(trade["entry_price"], 4),
                格式化数字(trade["exit_price"], 4),
                f"{trade['pnl_pct'] * 100:.2f}%",
                str(trade["reason"]),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 5:
                    item.setForeground(QColor(COLOR_GREEN if trade["pnl_pct"] >= 0 else COLOR_RED))
                self.trade_table.setItem(row, col, item)

    def _score_ai_optimize_result(self, normalized: Dict[str, Any], target: str) -> float:
        """按用户选择的目标给AI优化结果打分。"""
        metrics = normalized.get("metrics", {})
        equity = pd.Series(normalized.get("equity", []), dtype="float64")
        returns = equity.pct_change().dropna()
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 2 and returns.std() else 0.0
        if "回撤" in target:
            return -abs(float(metrics.get("max_drawdown", 0.0)))
        return sharpe

    def run_ai_auto_optimize(self) -> None:
        """在回测页执行AI自动优化：最多20轮，连续5轮无提升停止。"""
        info = self.strategy_combo.currentData()
        data_path = self.data_combo.currentData()
        if not isinstance(info, StrategyInfo) or not isinstance(data_path, Path):
            QMessageBox.warning(self, "提示", "请先选择策略和数据。")
            return
        try:
            params = json.loads(self.param_text.toPlainText() or "{}")
            capital = float(self.capital_input.text())
            leverage = min(MAX_LEVERAGE, max(1.0, float(self.leverage_input.text())))
            df = self.data_manager.load(data_path)
            start_ts = pd.Timestamp(self.start_date.date().toPyDate())
            end_ts = pd.Timestamp(self.end_date.date().toPyDate()) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            if df["timestamp"].dt.tz is not None:
                start_ts = start_ts.tz_localize("UTC")
                end_ts = end_ts.tz_localize("UTC")
            df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)
            worker = BacktestWorker(info.path, data_path, params, self.start_date.date(), self.end_date.date(), capital, leverage)
            strategy_manager = StrategyManager(STRATEGY_DIR)
            module = strategy_manager.load_module(info.path)
            best_params = dict(params)
            try:
                best_result = worker._normalize_result(module.strategy_logic(df, None, best_params), df, leverage, BACKTEST_FEE_RATE)
            except Exception:
                best_result = worker._normalize_result(module.strategy_logic(df, None, self._sanitize_strategy_params(best_params)), df, leverage, BACKTEST_FEE_RATE)
            target, ok = QMessageBox.question(self, "AI自动优化", "优化目标使用夏普比率？选择 No 则使用最大回撤。") == QMessageBox.StandardButton.Yes, True
            target_name = "夏普比率" if target else "最大回撤"
            best_score = self._score_ai_optimize_result(best_result, target_name)
            no_improve = 0
            lines = [f"初始评分({target_name})：{best_score:.4f}"]
            for i in range(1, 21):
                candidate = self._next_backtest_params(best_params, i)
                candidate = self._limit_param_range(candidate, params, 0.3)
                try:
                    result = worker._normalize_result(module.strategy_logic(df, None, candidate), df, leverage, BACKTEST_FEE_RATE)
                except Exception as exc:
                    lines.append(f"第{i:02d}轮失败：{exc}")
                    no_improve += 1
                    self.ai_review_output.setPlainText("\n".join(lines))
                    if no_improve >= 5:
                        lines.append("优化已收敛：连续5次无提升。")
                        break
                    continue
                score = self._score_ai_optimize_result(result, target_name)
                improved = score > best_score
                if improved:
                    best_score = score
                    best_params = dict(candidate)
                    best_result = result
                    no_improve = 0
                else:
                    no_improve += 1
                lines.append(f"第{i:02d}轮 score={score:.4f} {'接受' if improved else '拒绝'} 参数={safe_json_dumps(candidate, ensure_ascii=False)}")
                self.ai_review_output.setPlainText("\n".join(lines))
                QApplication.processEvents()
                if no_improve >= 5:
                    lines.append("优化已收敛：连续5次无提升。")
                    break
            self.param_text.setPlainText(safe_json_dumps(best_params, ensure_ascii=False, indent=2))
            self.last_result = best_result
            self.on_backtest_ok(best_result)
            self.ai_review_output.setPlainText("\n".join(lines))
        except Exception as exc:
            QMessageBox.critical(self, "AI自动优化失败", traceback.format_exc()[-3000:])

    def _next_backtest_params(self, params: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        """生成下一轮参数候选；AI不可用时仍可本地迭代。"""
        candidate = dict(params)
        factor = 1.0 + (0.08 if iteration % 2 else -0.06)
        for key, value in candidate.items():
            if isinstance(value, (int, float)) and key not in ("initial_capital", "leverage"):
                if isinstance(value, bool):
                    continue
                if isinstance(value, int) and not isinstance(value, bool):
                    candidate[key] = max(1, int(round(float(value) * factor)))
                else:
                    candidate[key] = round(float(value) * factor, 6)
                break
        return candidate

    def _sanitize_strategy_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """保持策略参数原有类型，避免整数参数被误改成浮点。"""
        cleaned = dict(params or {})
        for key, value in list(cleaned.items()):
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                cleaned[key] = int(value)
            elif isinstance(value, float):
                cleaned[key] = float(value)
        return cleaned

    def _limit_param_range(self, candidate: Dict[str, Any], base: Dict[str, Any], pct: float) -> Dict[str, Any]:
        """限制参数只在原参数±30%内变化。"""
        limited = dict(candidate)
        for key, base_value in base.items():
            if isinstance(base_value, (int, float)) and isinstance(limited.get(key), (int, float)):
                low = float(base_value) * (1 - pct)
                high = float(base_value) * (1 + pct)
                if low > high:
                    low, high = high, low
                value = min(high, max(low, float(limited[key])))
                if isinstance(base_value, int) and not isinstance(base_value, bool):
                    limited[key] = max(1, int(round(value)))
                else:
                    limited[key] = round(value, 6)
        return self._sanitize_strategy_params(limited)

    def run_ai_result_review(self) -> None:
        """把核心指标和最近10笔交易发给DeepSeek，并用打字机效果展示。"""
        if not self.last_result:
            QMessageBox.warning(self, "提示", "请先完成一次回测。")
            return
        metrics = self.last_result.get("metrics", {})
        trades = self.last_result.get("trades", [])[-10:]
        prompt = f"核心指标：{safe_json_dumps(metrics, ensure_ascii=False)}\n最近10笔交易：{safe_json_dumps(trades, ensure_ascii=False)}\n请用中文解释策略表现、主要风险和下一步优化建议。"
        try:
            secure = self.config.load_secure()
            ai = secure.get("deepseek", {})
            api_key = ai.get("api_key", "")
            if not api_key or OpenAI is None:
                raise RuntimeError("未配置DeepSeek或openai SDK不可用")
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=DEEPSEEK_TIMEOUT_SECONDS, max_retries=0, http_client=deepseek_http_client_with_proxy())
            response = client.chat.completions.create(
                model=ai.get("model", "deepseek-v4-flash"),
                messages=[{"role": "system", "content": "你是量化交易复盘助手，AI只能建议不能修改风控。"}, {"role": "user", "content": prompt}],
                max_tokens=700,
            )
            text = extract_deepseek_message_text(response)
        except Exception as exc:
            text = f"DeepSeek不可用，使用本地复盘：总收益{float(metrics.get('total_return', 0))*100:.2f}%，最大回撤{float(metrics.get('max_drawdown', 0))*100:.2f}%，最近交易{len(trades)}笔。建议检查亏损交易的入场环境、止损距离和策略适用行情。\n原因：{exc}"
        self.ai_review_text = text
        self.ai_review_index = 0
        self.ai_review_output.clear()
        self.ai_review_timer.start(18)

    def _ai_review_step(self) -> None:
        """回测页AI解读打字机效果。"""
        if self.ai_review_index >= len(self.ai_review_text):
            self.ai_review_timer.stop()
            return
        self.ai_review_output.insertPlainText(self.ai_review_text[self.ai_review_index])
        self.ai_review_index += 1


class LiveMonitorPage(QWidget):
    """实盘监控页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.worker: Optional[LiveRefreshWorker] = None
        self.auto_trade_worker: Optional[AutoTradeWorker] = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._build_ui()

    def _build_ui(self) -> None:
        """构建实盘监控界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title_row = QHBoxLayout()
        title = QLabel("实盘监控")
        title.setObjectName("pageTitle")
        self.refresh_button = 创建按钮("立即刷新", True)
        self.auto_check = QCheckBox("自动刷新 30秒")
        self.trade_symbol_combo = QComboBox()
        self.trade_symbol_combo.addItems(list(CROSS_SECTION_SYMBOLS))
        self.auto_trade_check = QCheckBox("🤖 自动交易")
        self.auto_trade_status = QLabel("自动交易未开启")
        self.auto_trade_status.setObjectName("mutedLabel")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(QLabel("交易对"))
        title_row.addWidget(self.trade_symbol_combo)
        title_row.addWidget(self.auto_trade_check)
        title_row.addWidget(self.auto_trade_status)
        title_row.addWidget(self.auto_check)
        title_row.addWidget(self.refresh_button)
        layout.addLayout(title_row)

        self.ai_decision_label = QLabel("最近AI决策：-")
        self.ai_decision_label.setObjectName("mutedLabel")
        layout.addWidget(self.ai_decision_label)

        metric_row = QHBoxLayout()
        self.total_card = MetricCard("账户权益", "-")
        self.free_card = MetricCard("可用余额", "-")
        self.pnl_card = MetricCard("持仓浮盈亏", "-")
        self.position_card = MetricCard("持仓数量", "-")
        for card in [self.total_card, self.free_card, self.pnl_card, self.position_card]:
            metric_row.addWidget(card)
        layout.addLayout(metric_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.balance_table = QTableWidget(0, 4)
        self.balance_table.setHorizontalHeaderLabels(["币种", "总额", "可用", "冻结"])
        设置表格样式(self.balance_table)
        self.position_table = QTableWidget(0, 7)
        self.position_table.setHorizontalHeaderLabels(["交易对", "方向", "数量", "开仓价", "标记价", "未实现盈亏", "杠杆"])
        设置表格样式(self.position_table)
        splitter.addWidget(self.balance_table)
        splitter.addWidget(self.position_table)
        splitter.setSizes([300, 360])
        layout.addWidget(splitter, 1)

        # ========== 策略协同状态面板（页面底部，涩谷风） ==========
        # 硬编码映射表在文件顶部常量区，AI不可修改。
        coord_group = QGroupBox("📊 策略协同状态")
        coord_group.setStyleSheet(f"""
            QGroupBox {{
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
                margin-top: 14px;
                padding: 16px 14px 12px 14px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {COLOR_PANEL_2}, stop:1 {COLOR_PANEL});
                font-size: 13px;
                font-weight: 700;
                color: {COLOR_TEXT};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 9px;
                color: #e94560;
            }}
        """)
        coord_layout = QVBoxLayout(coord_group)
        coord_layout.setSpacing(6)

        # 第一行：当前行情 + 置信度 + 下次分析
        self.coord_market_label = QLabel("当前行情: -")
        self.coord_market_label.setStyleSheet(f"color: {COLOR_BLUE}; font-weight: 700; font-size: 14px;")
        coord_layout.addWidget(self.coord_market_label)

        self.coord_next_label = QLabel("下次分析: -")
        self.coord_next_label.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 11px;")
        coord_layout.addWidget(self.coord_next_label)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {COLOR_BORDER};")
        coord_layout.addWidget(sep)

        # 激活策略列表（每个策略一行，显示独立风险%）
        self.coord_strategy_label = QLabel("激活策略: -")
        self.coord_strategy_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 13px;")
        self.coord_strategy_label.setWordWrap(True)
        coord_layout.addWidget(self.coord_strategy_label)

        # 仓位与杠杆
        self.coord_risk_label = QLabel("总风险: -")
        self.coord_risk_label.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 13px;")
        coord_layout.addWidget(self.coord_risk_label)

        # 状态灯（最后一行）
        self.coord_status_label = QLabel("🟢 正常运行")
        self.coord_status_label.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: 700; font-size: 14px;")
        coord_layout.addWidget(self.coord_status_label)

        # 横截面动量选币状态
        self.coord_cross_section_label = QLabel(f"⏳ {CROSS_SECTION_MOMENTUM_STRATEGY_NAME}: 每周调仓 [未加载]")
        self.coord_cross_section_label.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 12px;")
        coord_layout.addWidget(self.coord_cross_section_label)

        # 资金费率反转状态
        self.coord_funding_label = QLabel(f"⏳ {FUNDING_REVERSAL_STRATEGY_NAME}: 基座策略 [无信号]")
        self.coord_funding_label.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 12px;")
        coord_layout.addWidget(self.coord_funding_label)

        layout.addWidget(coord_group)

        self.refresh_button.clicked.connect(self.refresh)
        self.auto_check.stateChanged.connect(self.toggle_timer)
        self.auto_trade_check.stateChanged.connect(self.toggle_auto_trade)

    def toggle_timer(self) -> None:
        """切换自动刷新。"""
        if self.auto_check.isChecked():
            self.timer.start(30000)
            self.refresh()
        else:
            self.timer.stop()

    def refresh(self) -> None:
        exchange_config = self.config.load_api_config() or self.config.load_secure().get("exchange", {})
        if not exchange_config.get("api_key") or not exchange_config.get("secret"):
            self.connection_status_changed.emit(False, "未配置交易所API")
            self.status_changed.emit("请先在 AI设置 页面配置交易所API。")
            return
        self.refresh_button.setEnabled(False)
        self.status_changed.emit("正在刷新实盘账户...")
        self.worker = LiveRefreshWorker(exchange_config)
        self.worker.finished_ok.connect(self.on_refresh_ok)
        self.worker.failed.connect(self.on_refresh_failed)
        self.worker.start()

    def toggle_auto_trade(self) -> None:
        """开启或停止模拟盘自动交易。"""
        if self.auto_trade_check.isChecked():
            api_config = self.config.load_api_config() or self.config.load_secure().get("exchange", {})
            exchange_id = api_config.get("exchange", "okx")
            symbol = self.trade_symbol_combo.currentText()
            self.auto_trade_status.setText("自动交易运行中")
            self.auto_trade_status.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: 700;")
            self.auto_trade_worker = AutoTradeWorker(self.config, exchange_id, symbol, 60)
            self.auto_trade_worker.decision_ready.connect(self.on_auto_trade_decision)
            self.auto_trade_worker.order_ready.connect(self.on_auto_trade_order)
            self.auto_trade_worker.failed.connect(self.on_auto_trade_failed)
            self.auto_trade_worker.start()
            self.status_changed.emit("模拟盘自动交易已开启。")
        else:
            if self.auto_trade_worker:
                self.auto_trade_worker.stop()
                self.auto_trade_worker = None
            self.auto_trade_status.setText("自动交易未开启")
            self.auto_trade_status.setStyleSheet("")
            self.coord_status_label.setText("状态灯: ⚪ 未运行")
            self.coord_cross_section_label.setText(f"选币权重: {CROSS_SECTION_MOMENTUM_STRATEGY_NAME} [未加载]")
            self.coord_cross_section_label.setStyleSheet(f"color: {COLOR_MUTED}; font-weight: 700;")
            self.coord_funding_label.setText(f"非方向性: {FUNDING_REVERSAL_STRATEGY_NAME} [无信号]")
            self.coord_funding_label.setStyleSheet(f"color: {COLOR_MUTED}; font-weight: 700;")
            self.coord_next_label.setText("???????: -")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_MUTED}; font-weight: 700;")
            self.status_changed.emit("模拟盘自动交易已停止。")

    def on_auto_trade_decision(self, decision: Dict[str, Any]) -> None:
        """展示最近一次AI自动交易决策，并更新策略协同面板。"""
        summary = decision.get("summary") or f"{decision.get('symbol')} {decision.get('market_state')} {decision.get('strategy')}"
        self.ai_decision_label.setText(f"最近AI决策：{summary}")
        active = decision.get("active_strategies", []) or []
        params = decision.get("params", {}) or {}
        confidence = decision.get("confidence", decision.get("???", "-"))
        try:
            confidence_text = f"{float(confidence) * 100:.0f}%"
        except Exception:
            confidence_text = str(confidence)
        market_state_raw = decision.get("market_state", "-")
        norm_state = 规范化市场状态(market_state_raw)
        next_time = decision.get("next_main_analysis_time", "-")
        left_seconds = int(decision.get("next_main_analysis_seconds", 0) or 0)
        left_text = f"{left_seconds // 3600:02d}:{(left_seconds % 3600) // 60:02d}:{left_seconds % 60:02d}"
        source = decision.get("analysis_source", "-")

        # 第一行：当前行情 + 置信度
        self.coord_market_label.setText(f"当前行情: {norm_state}    置信度: {confidence_text}")
        # 第二行：下次分析时间
        self.coord_next_label.setText(f"下次分析: {next_time}  剩余 {left_text}  来源: {source}")

        # 构建每个策略的明细行
        strategy_lines = []
        total_risk = 0.0
        total_leverage = float(decision.get("total_leverage", 0) or 0)

        # 方向性策略（已在active中）
        for s in active:
            if s in INDEPENDENT_STRATEGIES:
                risk = 0.015  # 趋势衰竭反转 1.5%
            elif len(active) == 2:
                risk = 0.005  # 双策略共振各0.5%
            elif len(active) >= 3:
                risk = 0.0033  # 三策略各0.33%
            elif "BB挤压" in s or "成交量异动" in s:
                risk = 0.010
            else:
                risk = 0.010  # 默认1%
            strategy_lines.append(f"  ✅ {s}    单策略风险 {risk*100:.2f}%")
            total_risk += risk

        # INDEPENDENT_STRATEGIES 未激活时显示等待
        for s in INDEPENDENT_STRATEGIES:
            if not any(s in a for a in active):
                strategy_lines.append(f"  ⏳ {s}    等待独立信号触发")

        # BASE_STRATEGIES (资金费率反转)
        for s in BASE_STRATEGIES:
            funding = decision.get("funding_reversal", {}) or {}
            if funding.get("enabled") or funding.get("has_signal"):
                strategy_lines.append(f"  ✅ {s}    基座策略 1.00% (非方向性)")
                total_risk += 0.01
            else:
                strategy_lines.append(f"  ⏳ {s}    基座策略 [尚无信号]")

        # WEEKLY_STRATEGIES (横截面动量选币)
        for s in WEEKLY_STRATEGIES:
            cross = decision.get("cross_section_momentum", {}) or {}
            cs_status = str(cross.get("status") or "未加载")
            if cs_status == "运行中":
                strategy_lines.append(f"  ✅ {s}    每周调仓 [运行中]")
            else:
                strategy_lines.append(f"  ⏳ {s}    每周调仓 [{cs_status}]")

        self.coord_strategy_label.setText("\n".join(strategy_lines) if strategy_lines else "激活策略: -")

        # 总风险与总杠杆
        if active:
            self.coord_risk_label.setText(
                f"总风险: {total_risk*100:.2f}%    总杠杆: ≤{max(total_leverage, 1.0):.1f}x"
            )
        else:
            self.coord_risk_label.setText("总风险: 0.00%    总杠杆: -")

        # 横截面动量状态
        cross_section = decision.get("cross_section_momentum", {}) or {}
        cs_status = str(cross_section.get("status") or "未加载")
        weights = cross_section.get("weights", {}) or {}
        top_weights = sorted(
            [(str(k), float(v or 0)) for k, v in weights.items() if float(v or 0) > 0],
            key=lambda item: item[1], reverse=True
        )[:3]
        weight_text = " / ".join([f"{k.replace('/USDT', '')} {v * 100:.0f}%" for k, v in top_weights]) or "USDT现金"
        weight_status = f"[{cs_status}] {weight_text}" if cs_status != "未加载" else f"[{cs_status}]"
        self.coord_cross_section_label.setText(f"{'✅' if cs_status == '运行中' else '⏳'} {CROSS_SECTION_MOMENTUM_STRATEGY_NAME}: 每周调仓 {weight_status}")
        self.coord_cross_section_label.setStyleSheet(
            f"color: {COLOR_RED if cs_status == '全现金' else (COLOR_GREEN if cs_status == '运行中' else COLOR_MUTED)}; font-size: 12px; font-weight: 700;"
        )

        # 资金费率反转状态
        funding = decision.get("funding_reversal", {}) or {}
        funding_status = str(funding.get("status") or "无信号")
        funding_emoji = "✅" if funding_status == "运行中" or funding.get("has_signal") else "⏳"
        self.coord_funding_label.setText(f"{funding_emoji} {FUNDING_REVERSAL_STRATEGY_NAME}: 基座策略 [{funding_status}]")
        self.coord_funding_label.setStyleSheet(
            f"color: {COLOR_GREEN if funding_status == '运行中' else (COLOR_RED if '拒绝' in funding_status else COLOR_MUTED)}; font-size: 12px; font-weight: 700;"
        )

        # 状态灯（含 ⚡ 高波动熔断）
        high_vol_meltdown = norm_state == "HIGH_VOLATILITY" and bool(decision.get("high_vol_熔断"))
        if high_vol_meltdown:
            self.coord_status_label.setText("⚡ 高波动熔断 — 暂停全部方向性策略")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: 700; font-size: 14px;")
        elif bool(cross_section.get("cash_only")):
            self.coord_status_label.setText("🔴 横截面崩盘避险 — 持有现金")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: 700; font-size: 14px;")
        elif any("趋势衰竭反转" in item for item in active):
            self.coord_status_label.setText("🔴 趋势衰竭触发平仓 — 顺势仓位已平")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: 700; font-size: 14px;")
        elif len(active) >= 2:
            self.coord_status_label.setText("🟡 共振加仓中 — 注意风险控制")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_AMBER}; font-weight: 700; font-size: 14px;")
        elif len(active) == 1:
            self.coord_status_label.setText("🟢 正常运行")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: 700; font-size: 14px;")
        else:
            self.coord_status_label.setText("⚪ 无激活策略 — 等待市场信号")
            self.coord_status_label.setStyleSheet(f"color: {COLOR_MUTED}; font-weight: 700; font-size: 14px;")

    def on_auto_trade_order(self, order_row: List[Any]) -> None:
        """收到模拟订单后刷新状态。"""
        self.status_changed.emit(f"模拟订单已记录：{order_row[0]} / {order_row[9]}")

    def on_auto_trade_failed(self, message: str) -> None:
        """展示自动交易失败信息，但不自动关闭开关。"""
        self.status_changed.emit(f"自动交易执行失败：{message}")

    def on_refresh_ok(self, payload: Dict[str, Any]) -> None:
        """刷新实盘数据显示。"""
        self.refresh_button.setEnabled(True)
        balance = payload.get("balance", {})
        positions = payload.get("positions", [])
        self._fill_balance(balance)
        self._fill_positions(positions)
        warning = payload.get("warning", "")
        has_balance = bool((balance.get("total", {}) or {}))
        has_positions = bool(positions)
        if warning:
            message = f"?????????{warning}"
            connected = False
        elif not has_balance and not has_positions:
            connected = True
        else:
            message = f"????????{datetime.now().strftime('%H:%M:%S')}"
            connected = True
        self.connection_status_changed.emit(connected, message)
        self.status_changed.emit(message)

    def on_refresh_failed(self, message: str) -> None:
        """展示刷新失败状态。"""
        self.refresh_button.setEnabled(True)
        clean = 分类连接错误(RuntimeError(message))
        self.connection_status_changed.emit(False, clean)
        self.status_changed.emit(f"实盘刷新失败：{clean}")

    def _fill_balance(self, balance: Dict[str, Any]) -> None:
        """填充余额表格。"""
        total = balance.get("total", {})
        free = balance.get("free", {})
        used = balance.get("used", {})
        currencies = [cur for cur, value in total.items() if value]
        if not currencies:
            self.balance_table.setRowCount(1)
            for col, value in enumerate(["?????", "0", "0", "0"]):
                self.balance_table.setItem(0, col, QTableWidgetItem(value))
            self.total_card.set_value("0.00", COLOR_TEXT)
            self.free_card.set_value("0.00", COLOR_TEXT)
            return
        self.balance_table.setRowCount(len(currencies))
        account_total = 0.0
        account_free = 0.0
        for row, cur in enumerate(currencies):
            total_value = total.get(cur, 0) or 0
            free_value = free.get(cur, 0) or 0
            used_value = used.get(cur, 0) or 0
            if cur in ("USDT", "USDC", "USD"):
                account_total += float(total_value)
                account_free += float(free_value)
            values = [cur, 格式化数字(total_value, 6), 格式化数字(free_value, 6), 格式化数字(used_value, 6)]
            for col, value in enumerate(values):
                self.balance_table.setItem(row, col, QTableWidgetItem(value))
        self.total_card.set_value(格式化数字(account_total), COLOR_TEXT)
        self.free_card.set_value(格式化数字(account_free), COLOR_TEXT)

    def _fill_positions(self, positions: List[Dict[str, Any]]) -> None:
        """填充持仓表格。"""
        active = []
        for pos in positions:
            contracts = pos.get("contracts", pos.get("contractSize", pos.get("size", 0))) or 0
            try:
                if abs(float(contracts)) > 0:
                    active.append(pos)
            except Exception:
                pass
        if not active:
            self.position_table.setRowCount(1)
            for col, value in enumerate(["???", "-", "0", "-", "-", "0", "-"]):
                self.position_table.setItem(0, col, QTableWidgetItem(value))
            self.pnl_card.set_value("0.00", COLOR_TEXT)
            self.position_card.set_value("0", COLOR_TEXT)
            return
        self.position_table.setRowCount(len(active))
        total_pnl = 0.0
        for row, pos in enumerate(active):
            pnl = float(pos.get("unrealizedPnl", 0) or 0)
            total_pnl += pnl
            values = [
                pos.get("symbol", "-"),
                pos.get("side", "-"),
                格式化数字(pos.get("contracts", pos.get("size", 0)), 6),
                格式化数字(pos.get("entryPrice", 0), 4),
                格式化数字(pos.get("markPrice", 0), 4),
                格式化数字(pnl, 2),
                str(pos.get("leverage", "-")),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 5:
                    item.setForeground(QColor(COLOR_GREEN if pnl >= 0 else COLOR_RED))
                self.position_table.setItem(row, col, item)
        self.pnl_card.set_value(格式化数字(total_pnl), COLOR_GREEN if total_pnl >= 0 else COLOR_RED)
        self.position_card.set_value(str(len(active)), COLOR_TEXT)



class AIMarketAnalysisWorker(QThread):
    """AI市场分析后台线程，防止DeepSeek超时时卡住界面。"""

    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, config: ConfigManager, symbol: str, timeframe: str, model: str, api_key: str, persona: str):
        super().__init__()
        self.config = config
        self.symbol = symbol
        self.timeframe = timeframe
        self.model = model
        self.api_key = api_key
        self.persona = persona

    def run(self) -> None:
        try:
            helper = AIAssistantPage.__new__(AIAssistantPage)
            helper.config = self.config
            helper.data_manager = DataCacheManager(DATA_CACHE_DIR)
            frames = helper._load_multi_timeframe_data(self.symbol)
            features = {tf: helper._calc_ai_market_features(frame) for tf, frame in frames.items()}
            local_state = helper._local_market_state(features)
            feature_lines = []
            for tf_name in ("15m", "1h", "4h"):
                values = features.get(tf_name)
                if not values:
                    continue
                feature_lines.append(
                    f"{tf_name}: ADX={values['adx']:.2f}, ATR={values['atr']:.2f}, 成交量比={values['volume_ratio']:.2f}, "
                    f"价格={values['price']:.4f}, EMA20={values['ema20']:.4f}, EMA50={values['ema50']:.4f}, EMA200={values['ema200']:.4f}, "
                    f"位置={values['price_position']}, 布林带宽度={values['bb_width']:.4f}, 涨跌幅={values['change'] * 100:.2f}%"
                )
            local_text = "\n".join(feature_lines)
            prompt = (
                "\u8bf7\u53ea\u8fd4\u56deJSON\uff0c\u4e0d\u8981Markdown\u3002\u5b57\u6bb5\uff1amarket_state, confidence, reason\u3002"
                "market_state\u53ea\u80fd\u662f TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY, \u8d8b\u52bf\u672b\u7aef\u3002\n"
                f"\u6807\u7684={self.symbol}\n\u591a\u5468\u671f\u7279\u5f81:\n{local_text}"
            )
            source = "fallback"
            try:
                if OpenAI is None:
                    raise RuntimeError("未安装openai SDK")
                if not self.api_key:
                    raise RuntimeError("未配置DeepSeek API Key")
                ai_text, used_model = deepseek_chat_text(
                    self.api_key,
                    self.model or "deepseek-chat",
                    [{"role": "system", "content": self.persona}, {"role": "user", "content": prompt}],
                    max_tokens=520,
                )
                state, confidence, reason, ok = parse_ai_market_response(ai_text, local_state[0], local_state[1])
                if ok:
                    source = "deepseek"
                    status_line = f"✅ 当前市场: {state} (置信度{confidence * 100:.0f}%)"
                else:
                    reason = reason or local_state[2]
                    status_line = f"⚠️ AI返回格式异常，已降级为本地规则: {state}"
            except Exception as exc:
                state, confidence, reason = local_state
                reason = f"\u26a0\ufe0f AI\u8d85\u65f6\u6216\u4e0d\u53ef\u7528\uff0c\u5df2\u964d\u7ea7\u4e3a\u672c\u5730\u89c4\u5219: {reason}\n\u539f\u56e0\uff1a{exc}"
                status_line = f"⚠️ AI超时，已降级为本地规则: {state}"
            self.finished_ok.emit({
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "state": state,
                "confidence": confidence,
                "reason": reason,
                "features": features,
                "local_text": local_text,
                "status_line": status_line,
                "source": source,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ===== AI 流式输出工作线程 =====
class AIStreamingWorker(QThread):
    """AI流式输出工作线程。通过openai stream=True接收chunk，逐块emit到主线程更新UI。
    
    使用方式（主线程）：
        worker = AIStreamingWorker(api_key, model, messages)
        worker.chunk_received.connect(lambda text: output.insertPlainText(text))
        worker.finished_ok.connect(lambda full: on_complete(full))
        worker.failed.connect(lambda err: on_error(err))
        worker.start()
    """

    chunk_received = pyqtSignal(str)    # 每收到一个内容chunk，主线程实时插入到文本区
    finished_ok = pyqtSignal(str)       # 完成时返回完整文本
    failed = pyqtSignal(str)            # 失败时返回错误信息

    def __init__(self, api_key: str, model: str, messages: list, max_tokens: int = 700):
        """初始化流式工作线程。
        
        Args:
            api_key: DeepSeek API密钥
            model: 模型名称（如 deepseek-chat）
            messages: OpenAI格式的消息列表
            max_tokens: 最大输出token数
        """
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.max_tokens = max_tokens

    def run(self) -> None:
        """启动流式请求，逐块emit到主线程。"""
        try:
            if OpenAI is None:
                raise RuntimeError("未安装 openai SDK")
            if not self.api_key:
                raise RuntimeError("未配置DeepSeek API Key")
            # 构造带代理的httpx client，避免国内直连DNS劫持
            client = OpenAI(
                api_key=self.api_key,
                base_url=DEEPSEEK_CONFIG['base_url'],
                timeout=DEEPSEEK_CONFIG['timeout'],
                max_retries=0,
                http_client=deepseek_http_client_with_proxy(),
            )
            # 核心：stream=True 启用流式输出
            response = client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                max_tokens=self.max_tokens,
                stream=True,
            )
            full_text = ""
            for chunk in response:
                # DeepSeek流式chunk中delta.content可能为None
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', None)
                if content:
                    full_text += content
                    self.chunk_received.emit(content)  # 实时emit到主线程
            self.finished_ok.emit(full_text)
        except Exception as e:
            self.failed.emit(str(e))


class AIAssistantPage(QWidget):
    """AI助手页面，集中提供市场分析、策略匹配、参数优化和复盘分析。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.strategy_manager = StrategyManager(STRATEGY_DIR)
        self.data_manager = DataCacheManager(DATA_CACHE_DIR)
        self.last_market_result: Dict[str, Any] = {}
        self.replay_worker: Optional[HistoricalReplayWorker] = None
        self.replay_history: List[Dict[str, Any]] = []
        self.replay_report: Dict[str, Any] = {}
        self.typewriter_text = ""
        self.typewriter_index = 0
        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.timeout.connect(self._typewriter_step)
        self.market_analysis_worker: Optional[AIMarketAnalysisWorker] = None
        self.streaming_worker: Optional[AIStreamingWorker] = None  # 真正的流式输出工作线程
        self.persona_defaults = {
            "市场分析师": "你是专业加密货币市场分析师，输出市场状态、置信度和简洁理由。",
            "策略匹配师": "你是量化策略匹配师，根据市场状态和策略标签推荐最合适的策略。",
            "参数优化师": "你是参数优化师，谨慎建议下一轮参数，避免过拟合。",
            "复盘教练": "你是交易复盘教练，客观分析策略优缺点和改进动作。",
        }
        self._build_ui()
        self._load_ai_settings()
        self._reload_strategy_choices()

    def _build_ui(self) -> None:
        """构建AI助手主界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title = QLabel("AI助手")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_market_tab(), "市场行情分析")
        self.tabs.addTab(self._build_match_tab(), "策略匹配")
        self.tabs.addTab(self._build_optimize_tab(), "参数优化")
        self.tabs.addTab(self._build_review_tab(), "复盘分析")
        self.tabs.addTab(self._build_replay_tab(), "历史行情重放")
        self.tabs.addTab(self._build_settings_tab(), "AI设置")
        layout.addWidget(self.tabs, 1)

    def _build_market_tab(self) -> QWidget:
        """构建市场分析子页面。"""
        page = QWidget()
        page.setObjectName("aiTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        form = QHBoxLayout()
        self.market_symbol_combo = QComboBox()
        self.market_symbol_combo.addItems(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        self.market_tf_combo = QComboBox()
        self.market_tf_combo.addItems(["H1", "H4", "M15"])
        self.market_button = 创建按钮("开始分析", True)
        form.addWidget(QLabel("交易对"))
        form.addWidget(self.market_symbol_combo)
        form.addWidget(QLabel("周期"))
        form.addWidget(self.market_tf_combo)
        form.addWidget(self.market_button)
        form.addStretch()
        layout.addLayout(form)
        card_row = QHBoxLayout()
        self.market_state_card = MetricCard("市场状态", "-")
        self.market_conf_card = MetricCard("置信度", "-")
        self.market_tf_card = MetricCard("分析周期", "15m/1h/4h")
        self.market_data_card = MetricCard("数据源", "data_cache")
        for card in [self.market_state_card, self.market_conf_card, self.market_tf_card, self.market_data_card]:
            card_row.addWidget(card)
        layout.addLayout(card_row)
        self.market_result = QTextEdit()
        self.market_result.setReadOnly(True)
        layout.addWidget(self.market_result, 1)
        self.market_button.clicked.connect(self.run_market_analysis)
        return page

    def _build_match_tab(self) -> QWidget:
        """构建策略匹配子页面。"""
        page = QWidget()
        page.setObjectName("aiTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        row = QHBoxLayout()
        self.match_button = 创建按钮("推荐策略", True)
        row.addWidget(QLabel("基于最近一次市场行情分析结果"))
        row.addWidget(self.match_button)
        row.addStretch()
        layout.addLayout(row)
        self.match_result = QTextEdit()
        self.match_result.setReadOnly(True)
        layout.addWidget(self.match_result, 1)
        self.match_button.clicked.connect(self.run_strategy_match)
        return page

    def _build_optimize_tab(self) -> QWidget:
        """构建参数优化子页面。"""
        page = QWidget()
        page.setObjectName("aiTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        row = QHBoxLayout()
        self.optimize_strategy_combo = QComboBox()
        self.optimize_target_combo = QComboBox()
        self.optimize_target_combo.addItems(["夏普比率", "年化收益", "最大回撤", "盈亏比"])
        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(1, 100)
        self.iter_spin.setValue(20)
        self.optimize_button = 创建按钮("开始优化", True)
        row.addWidget(QLabel("策略"))
        row.addWidget(self.optimize_strategy_combo, 2)
        row.addWidget(QLabel("优化目标"))
        row.addWidget(self.optimize_target_combo)
        row.addWidget(QLabel("最大迭代"))
        row.addWidget(self.iter_spin)
        row.addWidget(self.optimize_button)
        layout.addLayout(row)
        self.optimize_table = QTableWidget(0, 5)
        self.optimize_table.setHorizontalHeaderLabels(["迭代", "参数", "总收益", "最大回撤", "评分"])
        设置表格样式(self.optimize_table)
        layout.addWidget(self.optimize_table, 1)
        self.optimize_button.clicked.connect(self.run_parameter_optimization)
        return page

    def _build_review_tab(self) -> QWidget:
        """构建复盘分析子页面。"""
        page = QWidget()
        page.setObjectName("aiTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        row = QHBoxLayout()
        self.review_button = 创建按钮("开始复盘", True)
        row.addWidget(QLabel("读取最近交易记录并输出复盘建议"))
        row.addWidget(self.review_button)
        row.addStretch()
        layout.addLayout(row)
        self.review_output = QTextEdit()
        self.review_output.setReadOnly(True)
        layout.addWidget(self.review_output, 1)
        self.review_button.clicked.connect(self.run_review_analysis)
        return page

    def _prepare_plot(self, plot: pg.PlotWidget, title: str) -> None:
        """设置AI助手内图表的深色主题样式。"""
        plot.setBackground(COLOR_PANEL)
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setTitle(title, color=COLOR_TEXT, size="12pt")
        plot.getAxis("left").setTextPen(COLOR_MUTED)
        plot.getAxis("bottom").setTextPen(COLOR_MUTED)

    def _build_replay_tab(self) -> QWidget:
        """构建历史行情重放子页面。"""
        page = QWidget()
        page.setObjectName("aiTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        control_row = QHBoxLayout()
        self.replay_symbol_combo = QComboBox()
        self.replay_symbol_combo.addItems(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        self.replay_source_combo = QComboBox()
        self.replay_strategy_combo = QComboBox()
        self.replay_start_date = QDateEdit()
        self.replay_end_date = QDateEdit()
        self.replay_speed_combo = QComboBox()
        self.replay_batch_spin = QSpinBox()
        self.replay_start_button = 创建按钮("开始重放", True)
        self.replay_pause_button = 创建按钮("暂停")
        self.replay_stop_button = 创建按钮("停止重放")
        self.replay_pause_button.setEnabled(False)
        self.replay_stop_button.setEnabled(False)

        self.replay_start_date.setCalendarPopup(True)
        self.replay_end_date.setCalendarPopup(True)
        self.replay_start_date.setDisplayFormat("yyyy-MM-dd")
        self.replay_end_date.setDisplayFormat("yyyy-MM-dd")
        self.replay_speed_combo.addItems(["1x", "5x", "10x", "50x", "100x"])
        self.replay_batch_spin.setRange(12, 720)
        self.replay_batch_spin.setValue(96)

        control_row.addWidget(QLabel("标的"))
        control_row.addWidget(self.replay_symbol_combo)
        control_row.addWidget(QLabel("数据源"))
        control_row.addWidget(self.replay_source_combo, 2)
        control_row.addWidget(QLabel("策略"))
        control_row.addWidget(self.replay_strategy_combo, 2)
        control_row.addWidget(QLabel("开始"))
        control_row.addWidget(self.replay_start_date)
        control_row.addWidget(QLabel("结束"))
        control_row.addWidget(self.replay_end_date)
        control_row.addWidget(QLabel("速度"))
        control_row.addWidget(self.replay_speed_combo)
        control_row.addWidget(QLabel("批量"))
        control_row.addWidget(self.replay_batch_spin)
        control_row.addWidget(self.replay_start_button)
        control_row.addWidget(self.replay_pause_button)
        control_row.addWidget(self.replay_stop_button)
        layout.addLayout(control_row)

        progress_row = QHBoxLayout()
        self.replay_progress = QProgressBar()
        self.replay_progress.setRange(0, 100)
        self.replay_status_label = QLabel("请选择数据源和策略后开始重放。")
        self.replay_status_label.setObjectName("mutedLabel")
        progress_row.addWidget(self.replay_progress, 3)
        progress_row.addWidget(self.replay_status_label, 2)
        layout.addLayout(progress_row)

        metric_row = QHBoxLayout()
        self.replay_ai_final_card = MetricCard("AI动态收益", "-")
        self.replay_static_final_card = MetricCard("静态收益", "-")
        self.replay_switch_card = MetricCard("切换次数", "-")
        self.replay_quality_card = MetricCard("切换正确率", "-")
        self.replay_avoid_card = MetricCard("避免回撤", "-")
        self.replay_miss_card = MetricCard("错失利润", "-")
        for card in [
            self.replay_ai_final_card,
            self.replay_static_final_card,
            self.replay_switch_card,
            self.replay_quality_card,
            self.replay_avoid_card,
            self.replay_miss_card,
        ]:
            metric_row.addWidget(card)
        layout.addLayout(metric_row)

        chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self.replay_equity_plot = pg.PlotWidget()
        self.replay_state_plot = pg.PlotWidget()
        self._prepare_plot(self.replay_equity_plot, "重放绩效对比")
        self._prepare_plot(self.replay_state_plot, "市场状态与切换标记")
        chart_splitter.addWidget(self.replay_equity_plot)
        chart_splitter.addWidget(self.replay_state_plot)
        chart_splitter.setSizes([320, 220])
        layout.addWidget(chart_splitter, 2)

        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.replay_table = QTableWidget(0, 7)
        self.replay_table.setHorizontalHeaderLabels(["时间", "市场状态", "激活策略", "动作", "盈亏", "AI权益", "静态权益"])
        设置表格样式(self.replay_table)
        self.replay_report_output = QTextEdit()
        self.replay_report_output.setReadOnly(True)
        bottom_splitter.addWidget(self.replay_table)
        bottom_splitter.addWidget(self.replay_report_output)
        bottom_splitter.setSizes([760, 380])
        layout.addWidget(bottom_splitter, 2)

        self.replay_source_combo.currentIndexChanged.connect(self._update_replay_date_range)
        self.replay_symbol_combo.currentIndexChanged.connect(self._reload_replay_sources)
        self.replay_start_button.clicked.connect(self.start_replay_test)
        self.replay_pause_button.clicked.connect(self.pause_replay_test)
        self.replay_stop_button.clicked.connect(self.stop_replay_test)
        self._reload_replay_sources()
        return page

    def _reload_replay_sources(self) -> None:
        """刷新重放数据源，优先列出H1缓存。"""
        if not hasattr(self, "replay_source_combo"):
            return
        current = self.replay_source_combo.currentData() if self.replay_source_combo.count() else None
        self.replay_source_combo.blockSignals(True)
        self.replay_source_combo.clear()
        items = self.data_manager.scan()
        coin_key = self.replay_symbol_combo.currentText().replace("/", "_") if hasattr(self, "replay_symbol_combo") else ""
        if coin_key:
            items = [item for item in items if coin_key.lower() in item[1].stem.lower()]
        h1_items = [item for item in items if any(tag in item[1].stem.lower() for tag in ("1h", "h1", "60m"))]
        if not h1_items:
            h1_items = items
        for label, path in h1_items:
            self.replay_source_combo.addItem(label, path)
        if current is not None:
            idx = self.replay_source_combo.findData(current)
            if idx >= 0:
                self.replay_source_combo.setCurrentIndex(idx)
        self.replay_source_combo.blockSignals(False)
        if hasattr(self, "replay_strategy_combo") and self.replay_strategy_combo.count() == 0:
            self._reload_strategy_choices()
        if hasattr(self, "replay_status_label"):
            if self.replay_source_combo.count() == 0:
                self.replay_status_label.setText("?????K????????????????")
            elif hasattr(self, "replay_strategy_combo") and self.replay_strategy_combo.count() == 0:
                self.replay_status_label.setText("??????????? strategies ???")
            else:
                self.replay_status_label.setText("???????")
        self._update_replay_date_range()

    def _update_replay_date_range(self) -> None:
        """根据所选缓存自动回填重放日期范围。"""
        path = self.replay_source_combo.currentData() if hasattr(self, "replay_source_combo") else None
        if not isinstance(path, Path):
            today = QDate.currentDate()
            self.replay_start_date.setDate(today.addMonths(-3))
            self.replay_end_date.setDate(today)
            return
        try:
            df = self.data_manager.load(path)
            if "datetime" in df.columns and "timestamp" not in df.columns:
                df = df.rename(columns={"datetime": "timestamp"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp"])
            start = df["timestamp"].min().date()
            end = df["timestamp"].max().date()
            self.replay_start_date.setDate(QDate(start.year, start.month, start.day))
            self.replay_end_date.setDate(QDate(end.year, end.month, end.day))
        except Exception:
            today = QDate.currentDate()
            self.replay_start_date.setDate(today.addMonths(-3))
            self.replay_end_date.setDate(today)

    def start_replay_test(self) -> None:
        """启动历史行情重放。"""
        data_path = self.replay_source_combo.currentData()
        strategy = self.replay_strategy_combo.currentData()
        if not isinstance(data_path, Path) or not isinstance(strategy, StrategyInfo):
            QMessageBox.warning(self, "提示", "请选择可用的数据源和策略后再开始重放。")
            return
        if self.replay_worker is not None:
            self.replay_worker.stop()
            self.replay_worker = None
        speed_text = self.replay_speed_combo.currentText().strip().replace("x", "") or "1"
        batch_size = self.replay_batch_spin.value()
        self.replay_history = []
        self.replay_report = {}
        self.replay_progress.setValue(0)
        self.replay_start_button.setEnabled(False)
        self.replay_pause_button.setEnabled(True)
        self.replay_pause_button.setText("暂停")
        self.replay_stop_button.setEnabled(True)
        self.replay_status_label.setText("历史行情重放运行中...")
        self.replay_worker = HistoricalReplayWorker(
            self.config,
            data_path,
            strategy.path,
            self.replay_start_date.date(),
            self.replay_end_date.date(),
            int(speed_text),
            batch_size,
        )
        self.replay_worker.progress.connect(self._on_replay_progress)
        self.replay_worker.finished_ok.connect(self._on_replay_finished)
        self.replay_worker.failed.connect(self._on_replay_failed)
        self.replay_worker.start()
        self.status_changed.emit("历史行情重放已启动。")

    def pause_replay_test(self) -> None:
        """暂停或继续历史行情重放。"""
        if self.replay_worker is None:
            return
        paused = self.replay_pause_button.text() == "暂停"
        self.replay_worker.set_paused(paused)
        self.replay_pause_button.setText("继续" if paused else "暂停")
        self.replay_status_label.setText("重放已暂停" if paused else "历史行情重放运行中...")

    def stop_replay_test(self) -> None:
        """停止历史行情重放。"""
        if self.replay_worker is not None:
            self.replay_worker.stop()
        self.replay_start_button.setEnabled(True)
        self.replay_pause_button.setEnabled(False)
        self.replay_stop_button.setEnabled(False)
        self.replay_status_label.setText("已停止")
        self.status_changed.emit("历史行情重放已停止。")

    def _on_replay_progress(self, payload: Dict[str, Any]) -> None:
        """刷新重放中的进度、图表和表格。"""
        self.replay_history = payload.get("timeline", self.replay_history)
        self.replay_progress.setValue(int(payload.get("progress", 0)))
        self.replay_status_label.setText(
            f"{payload.get('time', '-') } | {payload.get('state', '-') } | {payload.get('strategy', '-') }"
            + (f" | {payload.get('switch_note', '')}" if payload.get("switch_note") else "")
        )
        self._update_replay_cards(payload)
        self._render_replay_chart(payload.get("ai_curve", []), payload.get("static_curve", []), self.replay_history)
        self._fill_replay_table(self.replay_history)

    def _on_replay_finished(self, report: Dict[str, Any]) -> None:
        """重放完成后刷新最终报告。"""
        self.replay_worker = None
        self.replay_start_button.setEnabled(True)
        self.replay_pause_button.setEnabled(False)
        self.replay_stop_button.setEnabled(False)
        self.replay_progress.setValue(100)
        self.replay_report = report
        self.replay_history = report.get("timeline", [])
        self._update_replay_cards(report)
        self._render_replay_chart(report.get("ai_curve", []), report.get("static_curve", []), self.replay_history)
        self._fill_replay_table(self.replay_history)
        self._render_replay_report(report)
        self.replay_status_label.setText(f"重放完成：{report.get('start_time', '-') } ~ {report.get('end_time', '-') }")
        self.status_changed.emit("历史行情重放完成。")

    def _on_replay_failed(self, message: str) -> None:
        """显示重放失败原因。"""
        self.replay_worker = None
        self.replay_start_button.setEnabled(True)
        self.replay_pause_button.setEnabled(False)
        self.replay_stop_button.setEnabled(False)
        self.replay_status_label.setText("重放失败")
        QMessageBox.critical(self, "历史行情重放失败", message[-3000:])
        self.status_changed.emit("历史行情重放失败。")

    def _update_replay_cards(self, report: Dict[str, Any]) -> None:
        """刷新重放关键指标卡。"""
        ai_final = float(report.get("dynamic_final", 0.0) or 0.0)
        static_final = float(report.get("static_final", 0.0) or 0.0)
        switch_count = int(report.get("switch_count", 0) or 0)
        correct_switch = int(report.get("correct_switch", 0) or 0)
        wrong_switch = int(report.get("wrong_switch", 0) or 0)
        ratio = float(report.get("switch_ratio", 0.0) or 0.0)
        avoided = float(report.get("avoided_drawdown", 0.0) or 0.0)
        missed = float(report.get("missed_profit", 0.0) or 0.0)
        ai_final = float(report.get("dynamic_final", 0.0) or 0.0)
        static_final = float(report.get("static_final", 0.0) or 0.0)
        total_return = (ai_final / 10000.0 - 1.0) * 100 if ai_final else 0.0
        win_rate = float(report.get("correct_switch", 0) / report.get("switch_count", 1)) if report.get("switch_count", 0) else 0.0
        curve = np.asarray(report.get("ai_curve", []) or [], dtype=float)
        if curve.size:
            peak = np.maximum.accumulate(curve)
            max_dd = float((curve / np.maximum(peak, 1e-9) - 1.0).min())
        else:
            max_dd = 0.0
        self.replay_ai_final_card.set_value(格式化数字(ai_final), COLOR_GREEN if ai_final >= static_final else COLOR_RED)
        self.replay_static_final_card.set_value(格式化数字(static_final), COLOR_TEXT)
        self.replay_switch_card.set_value(str(switch_count), COLOR_TEXT)
        self.replay_quality_card.set_value(f"{ratio * 100:.1f}%", COLOR_GREEN if ratio >= 0.5 else COLOR_AMBER)
        self.replay_avoid_card.set_value(格式化数字(avoided), COLOR_GREEN)
        self.replay_miss_card.set_value(格式化数字(missed), COLOR_RED if missed > 0 else COLOR_TEXT)
        self.replay_report_output.setPlainText(
            f"总收益：{total_return:.2f}%\n胜率：{win_rate * 100:.1f}%\n最大回撤：{max_dd * 100:.2f}%\nAI决策次数：{switch_count}\nAI决策准确率：{ratio * 100:.1f}%"
        )

    def _render_replay_chart(self, ai_curve: List[float], static_curve: List[float], timeline: List[Dict[str, Any]]) -> None:
        """绘制重放绩效曲线和市场状态轨迹。"""
        if not ai_curve or not static_curve:
            return
        self.replay_equity_plot.clear()
        self.replay_state_plot.clear()
        self.replay_equity_plot.addLegend()
        self.replay_state_plot.addLegend()
        x = np.arange(min(len(ai_curve), len(static_curve)))
        ai_values = np.asarray(ai_curve[: len(x)], dtype=float)
        static_values = np.asarray(static_curve[: len(x)], dtype=float)
        self.replay_equity_plot.plot(x, static_values, pen=pg.mkPen(COLOR_AMBER, width=2), name="静态策略")
        self.replay_equity_plot.plot(x, ai_values, pen=pg.mkPen(COLOR_GREEN, width=2), name="AI动态")
        times = ["起点"] + [item.get("time", "") for item in timeline]
        if len(times) >= 2:
            step = max(1, len(times) // 8)
            ticks = [(idx, times[idx][5:16] if len(times[idx]) >= 16 else times[idx]) for idx in range(0, len(times), step)]
            if ticks and ticks[-1][0] != len(times) - 1:
                ticks.append((len(times) - 1, times[-1][5:16] if len(times[-1]) >= 16 else times[-1]))
            self.replay_equity_plot.getAxis("bottom").setTicks([ticks])
            self.replay_state_plot.getAxis("bottom").setTicks([ticks])
        state_codes = [0] + [self._replay_state_code(item.get("state", "")) for item in timeline]
        state_x = np.arange(len(state_codes))
        self.replay_state_plot.plot(state_x, state_codes, pen=pg.mkPen(COLOR_BLUE, width=2), name="市场状态")
        switch_x = [idx + 1 for idx, item in enumerate(timeline) if item.get("switched")]
        if switch_x:
            switch_y = [state_codes[min(idx, len(state_codes) - 1)] for idx in switch_x]
            scatter = pg.ScatterPlotItem(x=switch_x, y=switch_y, size=10, brush=pg.mkBrush(COLOR_RED), pen=pg.mkPen(COLOR_RED), symbol="t")
            self.replay_state_plot.addItem(scatter)

    def _fill_replay_table(self, timeline: List[Dict[str, Any]]) -> None:
        """刷新重放明细表。"""
        self.replay_table.setRowCount(len(timeline))
        for row, item in enumerate(timeline):
            values = [
                item.get("time", ""),
                item.get("state", ""),
                item.get("strategy", ""),
                item.get("action", "观望"),
                格式化数字(item.get("pnl", 0.0)),
                格式化数字(item.get("ai_equity", 0.0)),
                格式化数字(item.get("static_equity", 0.0)),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if col == 3:
                    if value in ("买", "卖", "平仓"):
                        cell.setForeground(QColor(COLOR_AMBER))
                    else:
                        cell.setForeground(QColor(COLOR_MUTED))
                if col == 4:
                    try:
                        cell.setForeground(QColor(COLOR_GREEN if float(str(value).replace(',', '')) >= 0 else COLOR_RED))
                    except Exception:
                        pass
                self.replay_table.setItem(row, col, cell)

    def _render_replay_report(self, report: Dict[str, Any]) -> None:
        """生成历史行情重放的文本报告。"""
        ai_final = float(report.get("dynamic_final", 0.0) or 0.0)
        static_final = float(report.get("static_final", 0.0) or 0.0)
        alpha = ai_final - static_final
        lines = [
            f"重放区间：{report.get('start_time', '-')} ~ {report.get('end_time', '-')}",
            f"切换次数：{report.get('switch_count', 0)}",
            f"正确切换：{report.get('correct_switch', 0)}",
            f"错误切换：{report.get('wrong_switch', 0)}",
            f"切换正确率：{float(report.get('switch_ratio', 0.0) or 0.0) * 100:.1f}%",
            f"AI动态收益：{格式化数字(ai_final)}",
            f"静态策略收益：{格式化数字(static_final)}",
            f"AI超额收益：{格式化数字(alpha)}",
            f"因切换避免回撤：{格式化数字(report.get('avoided_drawdown', 0.0))}",
            f"因切换错失利润：{格式化数字(report.get('missed_profit', 0.0))}",
            "",
            "",
        ]
        lines.append("\u5404\u7b56\u7565\u6548\u679c\uff1a")
        stats = report.get("strategy_stats", {}) or {}
        if stats:
            for name, stat in sorted(stats.items(), key=lambda item: float(item[1].get("count", 0)), reverse=True):
                count = int(stat.get("count", 0) or 0)
                pnl = float(stat.get("pnl", 0.0) or 0.0)
                wins = int(stat.get("wins", 0) or 0)
                trades = int(stat.get("trades", 0) or 0)
                win_rate = wins / count * 100 if count else 0.0
                avg_pnl = pnl / count if count else 0.0
                pnl_text = f"{pnl:,.2f}"
                avg_text = f"{avg_pnl:,.2f}"
                lines.append(f"- {name}\uff1a\u6fc0\u6d3b{count}\u6b21\uff0c\u7d2f\u8ba1\u76c8\u4e8f{pnl_text}\uff0c\u5e73\u5747\u5355\u6b65{avg_text}\uff0c\u80dc\u7387{win_rate:.1f}%\uff0c\u7b56\u7565\u4ea4\u6613\u6570{trades}")
        else:
            lines.append("- \u6682\u65e0\u7b56\u7565\u7edf\u8ba1\u3002")
        lines.extend(["", "\u6700\u8fd15\u6b21AI\u5224\u65ad\uff1a"])
        for item in report.get("timeline", [])[-5:]:
            decision_text = str(item.get("decision", "")).replace("\n", " ")
            lines.append(f"- {item.get('time', '-')} | {item.get('state', '-')} | {item.get('strategy', '-')} | {item.get('note') or '???'} | {decision_text}")
        self.replay_report_output.setPlainText("\n".join(lines))

    def _replay_state_code(self, state: str) -> float:
        """把市场状态转换成图表数值。"""
        mapping = {
            "TRENDING_UP": 3,
            "TRENDING_DOWN": -3,
            "RANGING": 0,
            "HIGH_VOLATILITY": 2,
            "上升趋势": 3,
            "下降趋势": -3,
            "区间震荡": 0,
            "高波动震荡": 2,
            "弱势震荡": -1,
        }
        return float(mapping.get(state, 0))

    def _build_settings_tab(self) -> QWidget:
        """构建AI设置子页面。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.ai_key_input = QLineEdit()
        self.ai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_model_combo = QComboBox()
        # 新增 deepseek-chat（快速分析/复盘）和 deepseek-reasoner（深度推理/参数优化）
        self.ai_model_combo.addItems(["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-pro"])
        self.persona_combo = QComboBox()
        self.persona_combo.addItems(list(self.persona_defaults.keys()))
        self.persona_text = QTextEdit()
        # 打字机速度配置（仅用于非流式文本显示场景）
        self.typewriter_spin = QSpinBox()
        self.typewriter_spin.setRange(5, 100)
        self.typewriter_spin.setValue(30)
        self.typewriter_spin.setSuffix(" ms/字")
        self.save_ai_settings_button = 创建按钮("保存AI设置", True)
        form.addRow("DeepSeek API Key", self.ai_key_input)
        form.addRow("模型", self.ai_model_combo)
        form.addRow("打字机速度", self.typewriter_spin)
        form.addRow("提示词人设", self.persona_combo)
        form.addRow("人设内容", self.persona_text)
        form.addRow(self.save_ai_settings_button)
        layout.addLayout(form)
        layout.addStretch()
        self.persona_combo.currentTextChanged.connect(self._show_persona)
        self.save_ai_settings_button.clicked.connect(self.save_ai_settings)
        return page

    def _load_ai_settings(self) -> None:
        """读取DeepSeek Key、模型、打字机速度和提示词人设。"""
        secure = self.config.load_secure()
        ai = secure.get("deepseek", {})
        self.ai_key_input.setText(ai.get("api_key", ""))
        # 模型：优先用户设置，默认使用 DEEPSEEK_CONFIG['chat_model']
        model = ai.get("model", DEEPSEEK_CONFIG['chat_model'])
        known_models = ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-pro"]
        if model not in known_models:
            model = DEEPSEEK_CONFIG['chat_model']
        self.ai_model_combo.setCurrentIndex(max(self.ai_model_combo.findText(model), 0))
        # 打字机速度（ms/字），默认为30ms
        typewriter_speed = ai.get("typewriter_speed", 30)
        if hasattr(self, "typewriter_spin"):
            self.typewriter_spin.setValue(typewriter_speed)
        personas = secure.get("ai_personas", self.persona_defaults)
        self.persona_defaults.update(personas)
        self._show_persona(self.persona_combo.currentText())

    def _show_persona(self, name: str) -> None:
        """展示当前提示词人设。"""
        self.persona_text.setPlainText(self.persona_defaults.get(name, ""))

    def save_ai_settings(self) -> None:
        """保存AI助手设置到本地加密配置，含key、模型、打字机速度和人设。"""
        self.persona_defaults[self.persona_combo.currentText()] = self.persona_text.toPlainText()
        secure = self.config.load_secure()
        secure["deepseek"] = {
            "api_key": self.ai_key_input.text().strip(),
            "model": self.ai_model_combo.currentText(),
            "typewriter_speed": self.typewriter_spin.value() if hasattr(self, "typewriter_spin") else 30,
        }
        secure["ai_personas"] = self.persona_defaults
        self.config.save_secure(secure)
        self.status_changed.emit("AI助手设置已保存。")

    def _reload_strategy_choices(self) -> None:
        """刷新回测、优化和重放页面的策略下拉框。"""
        strategies = self.strategy_manager.scan()
        optimize_current = self.optimize_strategy_combo.currentData() if self.optimize_strategy_combo.count() else None
        self.optimize_strategy_combo.blockSignals(True)
        self.optimize_strategy_combo.clear()
        for info in strategies:
            label = f"{info.name} | {获取策略标签(info, '适用行情', '適用行情', '行情风格')}"
            self.optimize_strategy_combo.addItem(label, info)
        if optimize_current is not None:
            idx = self.optimize_strategy_combo.findData(optimize_current)
            if idx >= 0:
                self.optimize_strategy_combo.setCurrentIndex(idx)
        self.optimize_strategy_combo.blockSignals(False)
        if hasattr(self, "replay_strategy_combo"):
            current = self.replay_strategy_combo.currentData() if self.replay_strategy_combo.count() else None
            self.replay_strategy_combo.blockSignals(True)
            self.replay_strategy_combo.clear()
            for info in strategies:
                label = f"{info.name} | {获取策略标签(info, '适用行情', '適用行情', '行情风格')}"
                self.replay_strategy_combo.addItem(label, info)
            if current is not None:
                idx = self.replay_strategy_combo.findData(current)
                if idx >= 0:
                    self.replay_strategy_combo.setCurrentIndex(idx)
            self.replay_strategy_combo.blockSignals(False)

    def _load_market_df(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """从data_cache读取指定交易对和周期，H4由H1重采样生成。"""
        base = symbol.replace("/", "_")
        candidates = []
        if timeframe == "M15":
            candidates = [f"{base}_15m.csv", f"{base}_M15.csv"]
        elif timeframe == "H1":
            candidates = [f"{base}_1h.csv", f"{base}_H1.csv"]
        else:
            candidates = [f"{base}_1h.csv", f"{base}_H1.csv"]
        for name in candidates:
            path = DATA_CACHE_DIR / name
            if path.exists():
                try:
                    df = self.data_manager.load(path)
                except Exception:
                    # 兼容部分缓存使用datetime字段的旧格式。
                    df = pd.read_csv(path)
                    if "datetime" in df.columns and "timestamp" not in df.columns:
                        df = df.rename(columns={"datetime": "timestamp"})
                    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"]).reset_index(drop=True)
                if timeframe == "H4":
                    df = df.set_index("timestamp").resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
                return df
        raise RuntimeError(f"未找到 {symbol} {timeframe} 的本地缓存数据。")

    def _load_multi_timeframe_data(self, symbol: str) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        api_config = self.config.load_api_config() or self.config.load_secure().get("exchange", {})
        preferred_exchange = str(api_config.get("exchange", "okx") or "okx")
        for tf in ("15m", "1h", "4h"):
            try:
                df, _, _ = get_market_data(symbol, tf, preferred_exchange, api_config, allow_local=True)
                frames[tf] = df
            except Exception:
                try:
                    fallback_tf = {"15m": "M15", "1h": "H1", "4h": "H4"}.get(tf, tf)
                    frames[tf] = self._load_market_df(symbol, fallback_tf)
                except Exception:
                    continue
        if not frames:
            raise RuntimeError(f"??? {symbol} ???AI???K????")
        return frames

    def _calc_market_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """计算ADX、ATR、均线位置、成交量变化等市场指标。"""
        data = df.tail(160).copy()
        high = data["high"].astype(float)
        low = data["low"].astype(float)
        close = data["close"].astype(float)
        volume = data["volume"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / tr.rolling(14).mean().replace(0, np.nan).reset_index(drop=True)
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / tr.rolling(14).mean().replace(0, np.nan).reset_index(drop=True)
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = dx.rolling(14).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        volume_change = volume.tail(10).mean() / volume.tail(30).mean() - 1 if volume.tail(30).mean() else 0
        change = close.iloc[-1] / close.iloc[-24] - 1 if len(close) >= 24 and close.iloc[-24] else 0
        state = "RANGING"
        if atr / close.iloc[-1] > 0.035:
            state = "HIGH_VOLATILITY"
        elif close.iloc[-1] > ma20 > ma60 and adx > 20:
            state = "TRENDING_UP"
        elif close.iloc[-1] < ma20 < ma60 and adx > 20:
            state = "TRENDING_DOWN"
        confidence = min(0.95, max(0.35, (float(adx) if not np.isnan(adx) else 10) / 60))
        return {"adx": float(adx), "atr": float(atr), "ma20": float(ma20), "ma60": float(ma60), "volume_change": float(volume_change), "change": float(change), "state": state, "confidence": confidence}

    def _calc_ai_market_features(self, df: pd.DataFrame) -> Dict[str, Any]:
        """计算AI市场分析需要的完整技术特征。"""
        data = df.tail(260).copy()
        high = data["high"].astype(float)
        low = data["low"].astype(float)
        close = data["close"].astype(float)
        volume = data["volume"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr_base = atr_series.replace(0, np.nan).reset_index(drop=True)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr_base
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr_base
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = float(dx.rolling(14).mean().iloc[-1]) if len(dx.dropna()) else 0.0
        last = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close) >= 200 else float(close.ewm(span=min(120, len(close)), adjust=False).mean().iloc[-1])
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = ma20 + 2 * std20
        bb_lower = ma20 - 2 * std20
        bb_width = float(((bb_upper - bb_lower) / ma20.replace(0, np.nan)).iloc[-1]) if len(ma20.dropna()) else 0.0
        vol_base = float(volume.tail(30).mean()) if len(volume) >= 30 else float(volume.mean())
        volume_ratio = float(volume.tail(5).mean() / vol_base) if vol_base else 1.0
        change = float(close.iloc[-1] / close.iloc[-24] - 1) if len(close) >= 24 and close.iloc[-24] else 0.0
        atr = float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else 0.0
        price_position = "EMA20上方" if last >= ema20 else "EMA20下方"
        if last >= ema20 >= ema50 >= ema200:
            price_position = "多头排列上方"
        elif last <= ema20 <= ema50 <= ema200:
            price_position = "空头排列下方"
        return {
            "adx": adx,
            "atr": atr,
            "volume_ratio": volume_ratio,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "price": last,
            "price_position": price_position,
            "bb_width": bb_width,
            "change": change,
        }

    def _local_market_state(self, features: Dict[str, Dict[str, Any]]) -> Tuple[str, float, str]:
        """DeepSeek不可用时的本地兜底：ADX>25且价格位置确认趋势，否则震荡。"""
        one_hour = features.get("1h") or features.get("h1") or next(iter(features.values()))
        adx = float(one_hour.get("adx", 0.0) or 0.0)
        position = str(one_hour.get("price_position", ""))
        bb_width = float(one_hour.get("bb_width", 0.0) or 0.0)
        volume_ratio = float(one_hour.get("volume_ratio", 1.0) or 1.0)
        if bb_width > 0.08 or volume_ratio > 2.2:
            return "HIGH_VOLATILITY", 0.45, "本地兜底：布林带宽或成交量比显著放大。"
        if adx > 25 and "多头" in position:
            return "TRENDING_UP", 0.4, "本地兜底：ADX>25且价格处于多头排列。"
        if adx > 25 and "空头" in position:
            return "TRENDING_DOWN", 0.4, "本地兜底：ADX>25且价格处于空头排列。"
        return "RANGING", 0.4, "本地兜底：趋势强度不足，按RANGING处理。"

    def _parse_ai_state(self, text: str, fallback: Tuple[str, float, str]) -> Tuple[str, float, str]:
        """从DeepSeek文本中提取市场状态和置信度，提取失败则使用本地兜底。"""
        allowed = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "趋势末端"]
        state = next((item for item in allowed if item in text), fallback[0])
        confidence = fallback[1]
        match = re.search(r"(?:置信度|confidence)\D*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
        if match:
            confidence = float(match.group(1))
            if confidence > 1:
                confidence = confidence / 100.0
            confidence = max(0.0, min(1.0, confidence))
        return state, confidence, text.strip() or fallback[2]

    def _calc_multi_timeframe_features(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """从当前缓存派生多周期特征，DeepSeek不可用时也能形成稳定判断。"""
        features: Dict[str, Dict[str, Any]] = {}
        frames = {"当前周期": df}
        if "timestamp" in df.columns:
            indexed = df.copy()
            indexed["timestamp"] = pd.to_datetime(indexed["timestamp"], errors="coerce")
            indexed = indexed.dropna(subset=["timestamp"]).set_index("timestamp")
            for label, rule in (("4H", "4h"), ("1D", "1D")):
                resampled = indexed.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
                if len(resampled) >= 40:
                    frames[label] = resampled
        for label, frame in frames.items():
            try:
                features[label] = self._calc_market_indicators(frame)
            except Exception:
                continue
        return features

    def _deepseek_text(self, persona: str, prompt: str, max_tokens: int = 360) -> str:
        """调用DeepSeek生成文本，失败时抛出异常给调用方兜底。"""
        if OpenAI is None:
            raise RuntimeError("未安装 openai SDK")
        secure = self.config.load_secure()
        ai = secure.get("deepseek", {})
        api_key = self.ai_key_input.text().strip() or ai.get("api_key", "")
        if not api_key:
            raise RuntimeError("未配置DeepSeek API Key")
        model = self.ai_model_combo.currentText() or ai.get("model", "deepseek-v4-flash")
        text, used_model = deepseek_chat_text(
            api_key,
            model,
            [{"role": "system", "content": self.persona_defaults.get(persona, "")}, {"role": "user", "content": prompt}],
            max_tokens=max(max_tokens, 520),
        )
        return text

    def run_market_analysis(self) -> None:
        """启动后台市场分析，避免DeepSeek或本地取数卡住界面。"""
        if self.market_analysis_worker and self.market_analysis_worker.isRunning():
            self.status_changed.emit("AI市场分析正在进行，请稍候。")
            return
        symbol = self.market_symbol_combo.currentText()
        timeframe = {"m15": "15m", "h1": "1h", "h4": "4h"}.get(self.market_tf_combo.currentText().lower(), "1h")
        api_key = self.ai_key_input.text().strip() if hasattr(self, "ai_key_input") else ""
        if not api_key:
            api_key = self.config.load_secure().get("deepseek", {}).get("api_key", "")
        model = self.ai_model_combo.currentText() if hasattr(self, "ai_model_combo") else self.config.load_secure().get("deepseek", {}).get("model", "deepseek-v4-flash")
        persona = self.persona_defaults.get("市场分析师", "")
        self.market_button.setEnabled(False)
        self.market_result.setPlainText("\U0001f504 AI\u5206\u6790\u4e2d...\n\u6b63\u5728\u8bfb\u53d6data_cache\u5e76\u8ba1\u7b9715m/1h/4h\u7279\u5f81\uff0cDeepSeek\u6700\u591a\u7b49\u5f8560\u79d2\u3002")
        self.status_changed.emit("AI市场分析已启动。")
        self.market_analysis_worker = AIMarketAnalysisWorker(self.config, symbol, timeframe, model, api_key, persona)
        self.market_analysis_worker.finished_ok.connect(self._on_market_analysis_ok)
        self.market_analysis_worker.failed.connect(self._on_market_analysis_failed)
        self.market_analysis_worker.start()

    def _on_market_analysis_ok(self, payload: Dict[str, Any]) -> None:
        """市场分析完成后刷新界面。"""
        self.market_button.setEnabled(True)
        state = payload.get("state", "RANGING")
        confidence = float(payload.get("confidence", 0.4) or 0.4)
        features = payload.get("features", {})
        reason = payload.get("reason", "")
        local_text = payload.get("local_text", "")
        status_line = payload.get("status_line", "")
        self.last_market_result = {
            "symbol": payload.get("symbol", ""),
            "timeframe": payload.get("timeframe", ""),
            "indicators": {"state": state, "confidence": confidence, "features": features},
            "multi_features": features,
            "text": reason,
        }
        self.market_state_card.set_value(state, COLOR_GREEN if state in ("TRENDING_UP", "TRENDING_DOWN") else COLOR_AMBER)
        self.market_conf_card.set_value(f"{confidence * 100:.0f}%", COLOR_GREEN if confidence >= 0.6 else COLOR_AMBER)
        self.market_tf_card.set_value("15m / 1h / 4h")
        self.market_data_card.set_value("data_cache")
        self.market_result.setPlainText(f"{status_line}\n\n\u672c\u5730\u7279\u5f81\n{local_text}\n\nAI/\u672c\u5730\u5224\u65ad\n{reason}")
        self.status_changed.emit("AI市场分析完成。")
        self.market_analysis_worker = None

    def _on_market_analysis_failed(self, message: str) -> None:
        """市场分析线程失败时不让界面卡在分析中。"""
        self.market_button.setEnabled(True)
        self.market_result.setPlainText("\u26a0\ufe0f \u5e02\u573a\u5206\u6790\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5data_cache\u662f\u5426\u6709\u5bf9\u5e94\u6807\u7684K\u7ebf\u3002\n\n" + message[-3000:])
        self.status_changed.emit("AI市场分析失败。")
        self.market_analysis_worker = None

    def run_strategy_match(self) -> None:
        """根据市场状态推荐策略。"""
        strategies = self.strategy_manager.scan()
        if not strategies:
            self.match_result.setPlainText("未扫描到策略文件。")
            return
        state = self.last_market_result.get("indicators", {}).get("state", "RANGING")
        readable_state = {"TRENDING_UP": "上升趋势", "TRENDING_DOWN": "下降趋势", "RANGING": "区间震荡", "HIGH_VOLATILITY": "高波动震荡"}.get(state, state)
        engine = AIDecisionEngine(self.config)
        symbol = self.last_market_result.get("symbol", "BTC/USDT")
        chosen = engine._choose_strategy({"state": readable_state}, strategies, strategies[0], symbol)
        if state == "趋势末端":
            chosen = next((s for s in strategies if "趋势衰竭反转" in s.name), chosen)
        filtered = [s for s in strategies if 策略覆盖交易对(s, symbol)]
        strategy_text = "\n".join([f"{s.name}: {s.labels}" for s in filtered])
        chosen_labels = chosen.labels if chosen else {}
        terminal_note = "趋势末端：无视冷却期，强制平掉顺势仓位，只激活趋势衰竭反转策略。\n" if state == "趋势末端" else ""
        local = (
            f"推荐策略：{chosen.name if chosen else '-'}\n"
            f"推荐理由：市场状态为{state}，交易对为{symbol}，已先按策略头部标签和标的过滤，"
            f"再按适用行情/不适行情打分。\n"
            f"{terminal_note}"
            f"命中标签：{safe_json_dumps(chosen_labels, ensure_ascii=False)}"
        )
        try:
            ai_text = self._deepseek_text("策略匹配师", f"市场状态：{state}\n交易对：{symbol}\n已过滤策略库：\n{strategy_text}\n请推荐一个最匹配策略并说明理由。")
            ai_text = f"{local}\n\nDeepSeek补充：\n{ai_text}"
        except Exception as exc:
            ai_text = f"{local}\nDeepSeek不可用：{exc}"
        self.match_result.setPlainText(ai_text)
        self.status_changed.emit("策略匹配完成。")

    def _score_backtest(self, result: Dict[str, Any], target: str) -> Tuple[float, Dict[str, float]]:
        """按目标把回测结果转换成评分。"""
        equity = np.asarray(result.get("equity", [1.0]), dtype=float)
        returns = pd.Series(equity).pct_change().dropna()
        total_return = float(equity[-1] / equity[0] - 1) if len(equity) > 1 and equity[0] else 0.0
        max_drawdown = float((equity / np.maximum.accumulate(equity) - 1).min()) if len(equity) else 0.0
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 2 and returns.std() else 0.0
        trades = result.get("trades", [])
        wins = [float(t.get("pnl_pct", t.get("pnl", 0))) for t in trades if float(t.get("pnl_pct", t.get("pnl", 0))) > 0]
        losses = [float(t.get("pnl_pct", t.get("pnl", 0))) for t in trades if float(t.get("pnl_pct", t.get("pnl", 0))) <= 0]
        profit_factor = sum(wins) / abs(sum(losses)) if losses and abs(sum(losses)) > 1e-9 else sum(wins)
        score_map = {"夏普比率": sharpe, "年化收益": total_return, "最大回撤": -abs(max_drawdown), "盈亏比": profit_factor}
        return float(score_map.get(target, total_return)), {"return": total_return, "drawdown": max_drawdown, "sharpe": sharpe, "pf": float(profit_factor)}

    def run_parameter_optimization(self) -> None:
        """执行轻量迭代参数优化，连续5次无提升自动停止。"""
        info = self.optimize_strategy_combo.currentData()
        if not isinstance(info, StrategyInfo):
            QMessageBox.warning(self, "提示", "请先选择策略。")
            return
        try:
            df = self._load_market_df("BTC/USDT", "H1")
            module = self.strategy_manager.load_module(info.path)
            params = 选择策略参数包(info.params, "BTC/USDT")
            best_score = -float("inf")
            best_params: Dict[str, Any] = dict(params)
            no_improve = 0
            self.optimize_table.setRowCount(0)
            stop_reason = "达到最大迭代次数"
            for i in range(1, self.iter_spin.value() + 1):
                result = module.strategy_logic(df, None, params)
                score, metrics = self._score_backtest(result, self.optimize_target_combo.currentText())
                row = self.optimize_table.rowCount()
                self.optimize_table.insertRow(row)
                values = [str(i), safe_json_dumps(params, ensure_ascii=False), f"{metrics['return'] * 100:.2f}%", f"{metrics['drawdown'] * 100:.2f}%", f"{score:.4f}"]
                for col, value in enumerate(values):
                    self.optimize_table.setItem(row, col, QTableWidgetItem(value))
                QApplication.processEvents()
                if score > best_score:
                    best_score = score
                    best_params = dict(params)
                    no_improve = 0
                else:
                    no_improve += 1
                if no_improve >= 5:
                    stop_reason = "连续5轮无提升，判定收敛"
                    break
                params = self._next_params(params, i)
            row = self.optimize_table.rowCount()
            self.optimize_table.insertRow(row)
            summary_values = ["结论", safe_json_dumps(best_params, ensure_ascii=False), "-", "-", f"最佳评分 {best_score:.4f}；{stop_reason}"]
            for col, value in enumerate(summary_values):
                self.optimize_table.setItem(row, col, QTableWidgetItem(value))
            self.status_changed.emit(f"参数优化完成：{stop_reason}。")
        except Exception as exc:
            QMessageBox.critical(self, "参数优化失败", str(exc))

    def _next_params(self, params: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        """本地生成下一轮参数建议，DeepSeek不可用时也能推进优化。"""
        new_params = dict(params)
        factor = 1.0 + (0.03 if iteration % 2 else -0.02)
        for key, value in list(new_params.items()):
            if isinstance(value, (int, float)) and key not in ("initial_capital", "leverage"):
                new_params[key] = round(float(value) * factor, 6)
                break
        return new_params

    def run_review_analysis(self) -> None:
        """读取最近交易记录并做复盘分析——使用AIStreamingWorker实现真正的流式输出。"""
        rows = self.config.load_logs()[-50:]  # 最多取最近50条交易记录
        if not rows:
            self.review_output.setPlainText("暂无交易记录，无法进行AI复盘分析。")
            return
        text = "\n".join([",".join(row) for row in rows])
        # 追加策略行情匹配度统计
        match_stats = self._build_review_match_stats()
        prompt = (
            f"你是交易复盘教练，请客观分析以下最近交易记录(\u6700\u591a50\u6761)\u7684\u7b56\u7565\u4f18\u7f3a\u70b9\u548c\u6539\u8fdb\u5efa\u8bae\u3002\n"
            f"\u8bf7\u6309\u4ee5\u4e0b\u7ed3\u6784\u8f93\u51fa\uff1a\n"
            f"1. \u4f18\u70b9\u603b\u7ed3\n"
            f"2. \u7f3a\u70b9\u5206\u6790\n"
            f"3. \u6539\u8fdb\u5efa\u8bae\n"
            f"4. \u7b56\u7565\u9002\u7528\u884c\u60c5\u5339\u914d\u5ea6\u7edf\u8ba1\n"
            f"\u4ea4\u6613\u8bb0\u5f55\uff1a\n{text}\n"
            f"\u7b56\u7565\u5339\u914d\u7edf\u8ba1\uff1a\n{match_stats}"
        )
        api_key = self._get_api_key()
        model = self._get_model()
        if not api_key:
            self._start_typewriter(f"\u672a\u914d\u7f6eDeepSeek API Key\uff0c\u7ed9\u51fa\u672c\u5730\u590d\u76d8\uff1a\u6700\u8fd1\u5171\u6709 {len(rows)} \u6761\u4ea4\u6613\u8bb0\u5f55\u3002\u5efa\u8bae\u68c0\u67e5\u4e8f\u635f\u4ea4\u6613\u7684\u5165\u573a\u6761\u4ef6\u3001\u6b62\u635f\u6267\u884c\u548c\u884c\u60c5\u72b6\u6001\u5339\u914d\u5ea6\u3002")
            return
        # 使用 AIStreamingWorker 实现真正的流式输出
        self.review_output.clear()
        self.review_button.setEnabled(False)
        self.streaming_worker = AIStreamingWorker(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": self.persona_defaults.get("\u590d\u76d8\u6559\u7ec3", "")},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
        )
        self.streaming_worker.chunk_received.connect(self._on_review_chunk)
        self.streaming_worker.finished_ok.connect(self._on_review_complete)
        self.streaming_worker.failed.connect(self._on_review_failed)
        self.streaming_worker.start()
        self.status_changed.emit("AI\u590d\u76d8\u5206\u6790\u5df2\u542f\u52a8\uff08\u6d41\u5f0f\u8f93\u51fa\uff09\u3002")

    def _get_api_key(self) -> str:
        """获取当前配置的DeepSeek API Key。"""
        key = self.ai_key_input.text().strip() if hasattr(self, "ai_key_input") else ""
        return key or self.config.load_secure().get("deepseek", {}).get("api_key", "")

    def _get_model(self) -> str:
        """获取当前选择的模型，默认用 chat_model。"""
        if hasattr(self, "ai_model_combo") and self.ai_model_combo.currentText():
            return self.ai_model_combo.currentText()
        return DEEPSEEK_CONFIG['chat_model']

    def _build_review_match_stats(self) -> str:
        """统计策略适用行情匹配度。"""
        try:
            if not AI_LOG_FILE.exists():
                return "\u6682\u65e0\u5386\u53f2\u51b3\u7b56\u65e5\u5fd7\u3002"
            with AI_LOG_FILE.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if len(rows) <= 1:
                return "\u6682\u65e0\u5386\u53f2\u51b3\u7b56\u65e5\u5fd7\u3002"
            recent = rows[-50:]
            return f"\u6700\u8fd1 {len(recent)} \u6761AI\u51b3\u7b56\u8bb0\u5f55\uff0c\u5305\u542b\u5e02\u573a\u72b6\u6001\u3001\u7b56\u7565\u9009\u62e9\u548c\u7ed3\u679c\u3002"
        except Exception:
            return "\u65e0\u6cd5\u8bfb\u53d6\u51b3\u7b56\u65e5\u5fd7\u3002"

    def _on_review_chunk(self, chunk: str) -> None:
        """流式输出：每收到一个chunk立即追加到UI。"""
        cursor = self.review_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.review_output.setTextCursor(cursor)
        self.review_output.insertPlainText(chunk)

    def _on_review_complete(self, full_text: str) -> None:
        """流式复盘完成。"""
        self.review_button.setEnabled(True)
        self.status_changed.emit("AI复盘分析完成。")

    def _on_review_failed(self, error: str) -> None:
        """流式复盘失败，降级为本地分析。"""
        self.review_button.setEnabled(True)
        rows = self.config.load_logs()[-30:]
        fallback = f"DeepSeek不可用，使用本地复盘：最近共有 {len(rows)} 条交易记录。建议检查亏损交易的入场条件、止损执行和行情状态匹配度。\n原因：{error}"
        self._start_typewriter(fallback)
        self.status_changed.emit("AI复盘失败，已降级为本地分析。")

    def _start_typewriter(self, text: str) -> None:
        """启动打字机效果。速度从AI设置读取，默认为30ms/字。"""
        self.typewriter_text = text
        self.typewriter_index = 0
        self.review_output.clear()
        # 使用配置项的打字机速度
        speed = 30
        if hasattr(self, "typewriter_spin"):
            speed = self.typewriter_spin.value()
        self.typewriter_timer.start(speed)

    def _typewriter_step(self) -> None:
        """逐字输出文字。速度由 typewriter_timer 的 interval 控制。"""
        if self.typewriter_index >= len(self.typewriter_text):
            self.typewriter_timer.stop()
            return
        self.review_output.insertPlainText(self.typewriter_text[self.typewriter_index])
        self.typewriter_index += 1


class HistoricalReplayPage(AIAssistantPage):
    """左侧独立历史重放页面，复用AI助手中的重放能力。"""

    def __init__(self, config: ConfigManager):
        QWidget.__init__(self)
        self.config = config
        self.strategy_manager = StrategyManager(STRATEGY_DIR)
        self.data_manager = DataCacheManager(DATA_CACHE_DIR)
        self.last_market_result: Dict[str, Any] = {}
        self.replay_worker: Optional[HistoricalReplayWorker] = None
        self.replay_history: List[Dict[str, Any]] = []
        self.replay_report: Dict[str, Any] = {}
        self.typewriter_text = ""
        self.typewriter_index = 0
        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.timeout.connect(self._typewriter_step)
        self.persona_defaults = {
            "市场分析师": "你是专业加密货币市场分析师，输出市场状态、置信度和简洁理由。",
            "策略匹配师": "你是量化策略匹配师，根据市场状态和策略标签推荐最合适的策略。",
            "参数优化师": "你是参数优化师，谨慎建议下一轮参数，避免过拟合。",
            "复盘教练": "你是交易复盘教练，客观分析策略优缺点和改进动作。",
        }
        self._build_ui()
        self._reload_strategy_choices()
        self._reload_replay_sources()

    def _build_ui(self) -> None:
        """只展示历史重放，避免用户在AI助手多标签中找不到入口。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title = QLabel("历史重放")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addWidget(self._build_replay_tab(), 1)

    def _reload_strategy_choices(self) -> None:
        """独立历史重放页只刷新重放策略列表。"""
        if not hasattr(self, "replay_strategy_combo"):
            return
        current = self.replay_strategy_combo.currentData() if self.replay_strategy_combo.count() else None
        self.replay_strategy_combo.blockSignals(True)
        self.replay_strategy_combo.clear()
        for info in self.strategy_manager.scan():
            label = f"{info.name} | {获取策略标签(info, '适用行情', '適用行情', '行情风格')}"
            self.replay_strategy_combo.addItem(label, info)
        if current is not None:
            idx = self.replay_strategy_combo.findData(current)
            if idx >= 0:
                self.replay_strategy_combo.setCurrentIndex(idx)
        self.replay_strategy_combo.blockSignals(False)


class SettingsPage(QWidget):
    """交易所API和AI设置页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.worker: Optional[ApiTestWorker] = None
        self.pending_exchange_payload: Optional[Dict[str, Any]] = None
        self.pending_save_exchange = False
        self.current_test_task = ""
        self._build_ui()
        self.load_config()

    def _build_ui(self) -> None:
        """构建设置界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title = QLabel("AI设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        exchange_group = QGroupBox("交易所API Key管理")
        exchange_form = QFormLayout(exchange_group)
        self.exchange_combo = QComboBox()
        for label, value in [("OKX", "okx"), ("Binance", "binance"), ("Bybit", "bybit"), ("Bitget", "bitget"), ("Gate.io", "gateio")]:
            self.exchange_combo.addItem(label, value)
        self.trade_env_combo = QComboBox()
        self.trade_env_combo.addItems(["实盘", "模拟"])
        self.api_key_input = QLineEdit()
        self.secret_input = QLineEdit()
        self.password_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        exchange_form.addRow("交易所", self.exchange_combo)
        exchange_form.addRow("交易环境", self.trade_env_combo)
        exchange_form.addRow("API Key", self.api_key_input)
        exchange_form.addRow("Secret", self.secret_input)
        exchange_form.addRow("Passphrase", self.password_input)
        self.proxy_group = QGroupBox("代理配置（可选，直连失败时再启用）")
        self.proxy_group.setCheckable(True)
        self.proxy_group.setChecked(False)
        proxy_layout = QVBoxLayout(self.proxy_group)
        self.proxy_body = QWidget()
        proxy_form = QFormLayout(self.proxy_body)
        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(["不使用代理", "HTTP", "SOCKS5"])
        self.proxy_host_input = QLineEdit()
        self.proxy_host_input.setPlaceholderText("例如 127.0.0.1")
        self.proxy_port_input = QLineEdit()
        self.proxy_port_input.setPlaceholderText("例如 7890")
        self.proxy_user_input = QLineEdit()
        self.proxy_pass_input = QLineEdit()
        self.proxy_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        proxy_form.addRow("代理类型", self.proxy_type_combo)
        proxy_form.addRow("代理地址", self.proxy_host_input)
        proxy_form.addRow("代理端口", self.proxy_port_input)
        proxy_form.addRow("代理用户名", self.proxy_user_input)
        proxy_form.addRow("代理密码", self.proxy_pass_input)
        proxy_layout.addWidget(self.proxy_body)
        self.proxy_body.setVisible(False)
        self.proxy_group.toggled.connect(self.proxy_body.setVisible)
        exchange_form.addRow(self.proxy_group)
        exchange_buttons = QHBoxLayout()
        self.test_exchange_button = 创建按钮("测试连接")
        self.save_exchange_button = 创建按钮("保存并测试连接", True)
        exchange_buttons.addWidget(self.test_exchange_button)
        exchange_buttons.addWidget(self.save_exchange_button)
        exchange_form.addRow(exchange_buttons)
        layout.addWidget(exchange_group)

        ai_group = QGroupBox("DeepSeek 设置")
        ai_form = QFormLayout(ai_group)
        self.deepseek_key_input = QLineEdit()
        self.deepseek_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_combo = QComboBox()
        self.model_combo.addItems(["deepseek-v4-flash", "deepseek-v4-pro"])
        ai_form.addRow("API Key", self.deepseek_key_input)
        ai_form.addRow("模型", self.model_combo)
        ai_buttons = QHBoxLayout()
        self.test_ai_button = 创建按钮("测试DeepSeek")
        self.save_ai_button = 创建按钮("加密保存", True)
        ai_buttons.addWidget(self.test_ai_button)
        ai_buttons.addWidget(self.save_ai_button)
        ai_form.addRow(ai_buttons)
        layout.addWidget(ai_group)

        hint = QLabel("交易所API会写入 config/api.json；DeepSeek配置继续写入本地加密配置。")
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)
        layout.addStretch()

        self.test_exchange_button.clicked.connect(self.test_exchange)
        self.save_exchange_button.clicked.connect(self.save_exchange)
        self.test_ai_button.clicked.connect(self.test_deepseek)
        self.save_ai_button.clicked.connect(self.save_deepseek)

    def load_config(self) -> None:
        """自动加载已保存的加密配置。"""
        secure = self.config.load_secure()
        exchange = self.config.load_api_config() or secure.get("exchange", {})
        ai = secure.get("deepseek", {})
        index = self.exchange_combo.findData(exchange.get("exchange", "okx"))
        self.exchange_combo.setCurrentIndex(max(index, 0))
        env = "模拟" if exchange.get("sandbox_mode") else "实盘"
        env_index = self.trade_env_combo.findText(env)
        self.trade_env_combo.setCurrentIndex(max(env_index, 0))
        self.api_key_input.setText(exchange.get("api_key", ""))
        self.secret_input.setText(exchange.get("secret", ""))
        self.password_input.setText(exchange.get("password", ""))
        proxy = exchange.get("proxy", {})
        proxy_type = proxy.get("type", "不使用代理")
        proxy_index = self.proxy_type_combo.findText(proxy_type)
        self.proxy_type_combo.setCurrentIndex(max(proxy_index, 0))
        self.proxy_host_input.setText(proxy.get("host", ""))
        self.proxy_port_input.setText(str(proxy.get("port", "")))
        self.proxy_user_input.setText(proxy.get("username", ""))
        self.proxy_pass_input.setText(proxy.get("password", ""))
        proxy_enabled = 用户已配置代理(exchange)
        self.proxy_group.setChecked(proxy_enabled)
        self.proxy_body.setVisible(proxy_enabled)
        self.deepseek_key_input.setText(ai.get("api_key", ""))
        saved_model = ai.get("model", "deepseek-v4-flash")
        if saved_model not in ("deepseek-v4-flash", "deepseek-v4-pro"):
            saved_model = "deepseek-v4-flash"
        model_index = self.model_combo.findText(saved_model)
        self.model_combo.setCurrentIndex(max(model_index, 0))

    def _exchange_payload(self) -> Dict[str, Any]:
        """读取交易所表单配置；代理为可选项，未展开时不会被用于连接。"""
        proxy_type = self.proxy_type_combo.currentText()
        proxy_enabled = bool(self.proxy_group.isChecked() and "不使用" not in proxy_type and self.proxy_host_input.text().strip() and self.proxy_port_input.text().strip())
        return {
            "exchange": self.exchange_combo.currentData(),
            "sandbox_mode": self.trade_env_combo.currentText() == "模拟",
            "api_key": self.api_key_input.text().strip(),
            "secret": self.secret_input.text().strip(),
            "password": self.password_input.text().strip(),
            "proxy": {
                "enabled": proxy_enabled,
                "type": proxy_type if proxy_enabled else "不使用代理",
                "host": self.proxy_host_input.text().strip(),
                "port": self.proxy_port_input.text().strip(),
                "username": self.proxy_user_input.text().strip(),
                "password": self.proxy_pass_input.text().strip(),
            },
        }

    def _deepseek_payload(self) -> Dict[str, Any]:
        """读取DeepSeek表单配置。"""
        return {
            "api_key": self.deepseek_key_input.text().strip(),
            "model": self.model_combo.currentText(),
        }

    def save_exchange(self) -> None:
        """保存前先测试交易所public API和私钥。"""
        payload = self._exchange_payload()
        self.pending_exchange_payload = payload
        self.pending_save_exchange = True
        self.current_test_task = "exchange"
        self.save_exchange_button.setEnabled(False)
        self.test_exchange_button.setEnabled(False)
        self.status_changed.emit("正在测试交易所连接，成功后保存到 config/api.json...")
        self.worker = ApiTestWorker("exchange", payload)
        self.worker.finished_ok.connect(self._test_ok)
        self.worker.failed.connect(self._test_failed)
        self.worker.start()

    def save_deepseek(self) -> None:
        """加密保存DeepSeek配置。"""
        secure = self.config.load_secure()
        secure["deepseek"] = self._deepseek_payload()
        self.config.save_secure(secure)
        self.status_changed.emit("DeepSeek配置已加密保存。")

    def test_exchange(self) -> None:
        """测试交易所连接。"""
        payload = self._exchange_payload()
        self.pending_save_exchange = False
        self.pending_exchange_payload = payload
        self.current_test_task = "exchange"
        self.test_exchange_button.setEnabled(False)
        self.status_changed.emit("正在测试交易所连接...")
        self.worker = ApiTestWorker("exchange", payload)
        self.worker.finished_ok.connect(self._test_ok)
        self.worker.failed.connect(self._test_failed)
        self.worker.start()

    def test_deepseek(self) -> None:
        """测试DeepSeek连接。"""
        self.current_test_task = "deepseek"
        payload = self._deepseek_payload()
        if not payload["api_key"]:
            QMessageBox.warning(self, "提示", "请先填写 DeepSeek API Key。")
            return
        self.current_test_task = "deepseek"
        self.test_ai_button.setEnabled(False)
        self.status_changed.emit("正在测试DeepSeek连接...")
        self.worker = ApiTestWorker("deepseek", payload)
        self.worker.finished_ok.connect(self._test_ok)
        self.worker.failed.connect(self._test_failed)
        self.worker.start()

    def _test_ok(self, message: str) -> None:
        """???????"""
        self.test_exchange_button.setEnabled(True)
        self.save_exchange_button.setEnabled(True)
        self.test_ai_button.setEnabled(True)
        task = self.current_test_task
        if self.pending_save_exchange and self.pending_exchange_payload:
            self.config.save_api_config(self.pending_exchange_payload)
            secure = self.config.load_secure()
            secure["exchange"] = self.pending_exchange_payload
            self.config.save_secure(secure)
            message = f"{message}\n交易所API已保存到 config/api.json；代理仅在你启用可选代理区时保存和使用。"
        self.pending_save_exchange = False
        self.status_changed.emit(message)
        if task == "exchange":
            self.connection_status_changed.emit(True, message)
        self.current_test_task = ""
        QMessageBox.information(self, "连接成功", message)

    def _test_failed(self, message: str) -> None:
        """???????"""
        self.test_exchange_button.setEnabled(True)
        self.save_exchange_button.setEnabled(True)
        self.test_ai_button.setEnabled(True)
        task = self.current_test_task
        self.pending_save_exchange = False
        self.status_changed.emit(f"连接失败：{message}")
        if task == "exchange":
            self.connection_status_changed.emit(False, message)
        self.current_test_task = ""
        QMessageBox.critical(self, "连接失败", message)


class LogsPage(QWidget):
    """交易日志页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.rows: List[List[str]] = []
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        """构建交易日志界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title_row = QHBoxLayout()
        title = QLabel("交易日志")
        title.setObjectName("pageTitle")
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("按交易对、方向、状态筛选")
        self.reload_button = 创建按钮("刷新")
        self.export_button = 创建按钮("导出", True)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.filter_input, 2)
        title_row.addWidget(self.reload_button)
        title_row.addWidget(self.export_button)
        layout.addLayout(title_row)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["时间", "交易所", "交易对", "方向", "类型", "价格", "数量", "状态", "备注"])
        设置表格样式(self.table)
        layout.addWidget(self.table, 1)

        self.filter_input.textChanged.connect(self.apply_filter)
        self.reload_button.clicked.connect(self.reload)
        self.export_button.clicked.connect(self.export)

    def reload(self) -> None:
        """重新加载日志。"""
        self.rows = self.config.load_logs()
        self.apply_filter()
        self.status_changed.emit(f"已加载 {len(self.rows)} 条交易日志。")

    def apply_filter(self) -> None:
        """按关键字筛选日志。"""
        keyword = self.filter_input.text().strip().lower()
        rows = [row for row in self.rows if not keyword or keyword in " ".join(row).lower()]
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(9):
                item = QTableWidgetItem(row[c] if c < len(row) else "")
                if c == 7:
                    text = item.text()
                    if "成" in text or "filled" in text.lower():
                        item.setForeground(QColor(COLOR_GREEN))
                    elif "拒" in text or "fail" in text.lower() or "撤" in text:
                        item.setForeground(QColor(COLOR_RED))
                self.table.setItem(r, c, item)

    def export(self) -> None:
        """导出筛选后的日志。"""
        path, _ = QFileDialog.getSaveFileName(self, "导出交易日志", str(ROOT_DIR / "交易日志.csv"), "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
            writer.writerow(headers)
            for row in range(self.table.rowCount()):
                writer.writerow([self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())])
        self.status_changed.emit(f"交易日志已导出：{path}")


class AIDecisionLogsPage(QWidget):
    """AI决策日志页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.rows: List[List[str]] = []
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        """构建AI决策日志界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title_row = QHBoxLayout()
        title = QLabel("AI决策日志")
        title.setObjectName("pageTitle")
        self.time_filter = QLineEdit()
        self.time_filter.setPlaceholderText("按时间筛选")
        self.type_filter = QLineEdit()
        self.type_filter.setPlaceholderText("按类型筛选")
        self.strategy_filter = QLineEdit()
        self.strategy_filter.setPlaceholderText("按策略筛选")
        self.reload_button = 创建按钮("刷新", True)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.time_filter)
        title_row.addWidget(self.type_filter)
        title_row.addWidget(self.strategy_filter)
        title_row.addWidget(self.reload_button)
        layout.addLayout(title_row)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(["时间", "类型", "交易对", "周期", "市场状态", "策略", "参数JSON", "决策理由", "风控结果", "订单ID", "后续盈亏", "状态"])
        设置表格样式(self.table)
        layout.addWidget(self.table, 1)

        self.time_filter.textChanged.connect(self.apply_filter)
        self.type_filter.textChanged.connect(self.apply_filter)
        self.strategy_filter.textChanged.connect(self.apply_filter)
        self.reload_button.clicked.connect(self.reload)

    def reload(self) -> None:
        """重新加载AI决策日志。"""
        self.rows = self.config.load_ai_decision_logs()
        self.apply_filter()
        self.status_changed.emit(f"已加载 {len(self.rows)} 条AI决策日志。")

    def apply_filter(self) -> None:
        """按时间、类型、策略筛选AI决策日志。"""
        time_key = self.time_filter.text().strip().lower()
        type_key = self.type_filter.text().strip().lower()
        strategy_key = self.strategy_filter.text().strip().lower()
        rows = []
        for row in self.rows:
            time_value = row[0].lower() if len(row) > 0 else ""
            type_value = row[1].lower() if len(row) > 1 else ""
            strategy_value = row[5].lower() if len(row) > 5 else ""
            if time_key and time_key not in time_value:
                continue
            if type_key and type_key not in type_value:
                continue
            if strategy_key and strategy_key not in strategy_value:
                continue
            rows.append(row)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(12):
                item = QTableWidgetItem(row[c] if c < len(row) else "")
                if c == 8:
                    text = item.text()
                    if "通过" in text or "调整" in text:
                        item.setForeground(QColor(COLOR_GREEN))
                    elif "拒绝" in text or "失败" in text:
                        item.setForeground(QColor(COLOR_RED))
                if c == 10:
                    text = item.text().replace("%", "")
                    try:
                        value = float(text)
                        item.setForeground(QColor(COLOR_GREEN if value >= 0 else COLOR_RED))
                    except Exception:
                        pass
                self.table.setItem(r, c, item)


class RiskPage(QWidget):
    """风控面板页面。"""

    status_changed = pyqtSignal(str)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """构建风控页面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 12)
        title_row = QHBoxLayout()
        title = QLabel("风控面板")
        title.setObjectName("pageTitle")
        self.refresh_button = 创建按钮("刷新规则", True)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.refresh_button)
        layout.addLayout(title_row)

        card_row = QHBoxLayout()
        self.risk_score = MetricCard("风险评分", "低", COLOR_GREEN)
        self.rule_count = MetricCard("启用规则", "6")
        self.block_count = MetricCard("拦截次数", "0")
        self.alert_count = MetricCard("预警次数", "0")
        for card in [self.risk_score, self.rule_count, self.block_count, self.alert_count]:
            card_row.addWidget(card)
        layout.addLayout(card_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["规则", "状态", "阈值", "当前值", "说明"])
        设置表格样式(self.table)
        layout.addWidget(self.table, 1)
        self.refresh_button.clicked.connect(self.refresh)

    def refresh(self) -> None:
        """刷新风控规则状态。"""
        rules = [
            ("单笔风险", "正常", "≤ 1.00%", "0.00%", "按止损距离限制单笔潜在亏损"),
            ("日内最大亏损", "正常", "≤ 3.00%", "0.00%", "触发后停止新开仓"),
            ("最大杠杆", "正常", "≤ 20x", "1x", "避免过度放大波动"),
            ("自动交易仓位分配", "正常", "总杠杆 ≤ 3x", "硬编码启用", "资金费率反转 → 独立 1.0% | 双策略共振 → 各 0.5% | 趋势衰竭反转 → 1.5% | BB挤压突破 → 1.0%"),
            ("横截面动量选币", "正常", "不占风险预算", "权重门控", "输出多币种权重；全现金时平掉方向性仓位并暂停方向性开仓"),
            ("最大持仓数", "正常", "≤ 3", "0", "控制相关性和保证金占用"),
            ("API状态", "待验证" if not self.config.load_secure().get("exchange", {}).get("api_key") else "正常", "已配置", "已配置" if self.config.load_secure().get("exchange", {}).get("api_key") else "未配置", "交易前必须通过私有接口验证"),
        ]
        self.table.setRowCount(len(rules))
        warning = 0
        for row, rule in enumerate(rules):
            for col, value in enumerate(rule):
                item = QTableWidgetItem(value)
                if col == 1:
                    if value == "正常":
                        item.setForeground(QColor(COLOR_GREEN))
                    elif value == "待验证":
                        item.setForeground(QColor(COLOR_AMBER))
                        warning += 1
                    else:
                        item.setForeground(QColor(COLOR_RED))
                self.table.setItem(row, col, item)
        self.risk_score.set_value("中" if warning else "低", COLOR_AMBER if warning else COLOR_GREEN)
        self.alert_count.set_value(str(warning), COLOR_AMBER if warning else COLOR_TEXT)
        self.status_changed.emit("风控规则已刷新。")


# ===== 三线程架构（UI防冻）=====
class OKXThread(QThread):
    """OKX WebSocket行情+REST订单线程。
    通过pyqtSignal向主线程推送行情数据和订单状态。"""
    ticker_updated = pyqtSignal(dict)
    order_updated = pyqtSignal(dict)
    connection_status = pyqtSignal(bool, str)

    def __init__(self, api_config: dict, symbol: str = "BTC/USDT"):
        super().__init__()
        self.api_config = api_config
        self.symbol = symbol
        self._running = False

    def run(self) -> None:
        """主循环：连接交易所，订阅行情，处理订单。"""
        self._running = True
        self.exec()

    def stop(self) -> None:
        """安全停止线程。"""
        self._running = False
        self.quit()
        self.wait(3000)


class AIThread(QThread):
    """DeepSeek API调用线程。
    异步执行AI分析任务，通过信号回传结果，避免阻塞UI。"""
    analysis_done = pyqtSignal(str)
    analysis_error = pyqtSignal(str)
    progress_update = pyqtSignal(str)

    def __init__(self, api_key: str = "", model: str = "deepseek-chat"):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self._running = False

    def run(self) -> None:
        """主循环：等待任务队列。"""
        self._running = True
        self.exec()

    def stop(self) -> None:
        """安全停止线程。"""
        self._running = False
        self.quit()
        self.wait(3000)


class StrategyThread(QThread):
    """策略门控+风控+下单线程。
    用QMutex保护共享状态，所有下单操作串行化。"""
    signal_generated = pyqtSignal(dict)
    order_placed = pyqtSignal(dict)
    risk_warning = pyqtSignal(str)
    status_update = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._mutex = QMutex()
        self._running = False

    def run(self) -> None:
        """主循环：门控检查→风控检查→下单。"""
        self._running = True
        self.exec()

    def stop(self) -> None:
        """安全停止线程。"""
        self._running = False
        self.quit()
        self.wait(3000)


class MainWindow(QMainWindow):
    """主窗口 — MUJI极简风格，6标签页布局。"""

    def __init__(self):
        super().__init__()
        self.config = ConfigManager(CONFIG_DIR)
        self.setWindowTitle("加密货币量化交易平台 V4")
        self.resize(1480, 900)
        self._build_ui()
        self._apply_style()
        self._build_status_bar()
        self.statusBar().showMessage("就绪")

    def _build_ui(self) -> None:
        """构建主框架：QTabWidget 6标签页 + 底部状态栏。"""
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        # 顶部标题栏
        title_bar = QHBoxLayout()
        app_title = QLabel("加密货币量化交易平台 V4")
        app_title.setObjectName("pageTitle")
        title_bar.addWidget(app_title)
        title_bar.addStretch()
        root.addLayout(title_bar)

        # 6标签页QTabWidget
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        # Tab 1: 策略回测
        self.backtest_page = StrategyBacktestPage(self.config)
        self.backtest_page.status_changed.connect(self.statusBar().showMessage)
        if hasattr(self.backtest_page, "connection_status_changed"):
            self.backtest_page.connection_status_changed.connect(self._update_connection_status)
        self.tabs.addTab(self.backtest_page, "📊 策略回测")

        # Tab 2: 实盘监控
        self.live_page = LiveMonitorPage(self.config)
        self.live_page.status_changed.connect(self.statusBar().showMessage)
        if hasattr(self.live_page, "connection_status_changed"):
            self.live_page.connection_status_changed.connect(self._update_connection_status)
        self.tabs.addTab(self.live_page, "📡 实盘监控")

        # Tab 3: AI助手
        self.ai_page = AIAssistantPage(self.config)
        self.ai_page.status_changed.connect(self.statusBar().showMessage)
        if hasattr(self.ai_page, "connection_status_changed"):
            self.ai_page.connection_status_changed.connect(self._update_connection_status)
        self.tabs.addTab(self.ai_page, "🤖 AI助手")

        # Tab 4: 历史重放
        self.replay_page = HistoricalReplayPage(self.config)
        self.replay_page.status_changed.connect(self.statusBar().showMessage)
        if hasattr(self.replay_page, "connection_status_changed"):
            self.replay_page.connection_status_changed.connect(self._update_connection_status)
        self.tabs.addTab(self.replay_page, "📜 历史重放")

        # Tab 5: 策略管理（策略卡片网格）
        self.strategy_mgmt_page = self._build_strategy_management_page()
        self.tabs.addTab(self.strategy_mgmt_page, "🧠 策略管理")

        # Tab 6: 系统设置（合并设置+风控+日志）
        self.settings_page = self._build_system_settings_page()
        self.tabs.addTab(self.settings_page, "⚙️ 系统设置")

        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_strategy_management_page(self) -> QWidget:
        """构建策略管理页：策略卡片网格 + 行情-策略映射表。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("策略超市")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        refresh_btn = QPushButton("刷新策略列表")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addStretch()
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 策略卡片网格
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.strategy_cards_container = QWidget()
        self.strategy_cards_grid = QGridLayout(self.strategy_cards_container)
        self.strategy_cards_grid.setSpacing(10)
        scroll.setWidget(self.strategy_cards_container)
        layout.addWidget(scroll, 2)

        # 行情-策略映射表
        map_group = QGroupBox("行情-策略映射")
        map_layout = QVBoxLayout(map_group)
        self.market_strategy_table = QTableWidget(0, 3)
        self.market_strategy_table.setHorizontalHeaderLabels(["市场状态", "触发条件", "推荐策略"])
        设置表格样式(self.market_strategy_table)
        map_layout.addWidget(self.market_strategy_table)
        layout.addWidget(map_group, 1)

        self._populate_strategy_cards()
        self._populate_market_strategy_map()
        refresh_btn.clicked.connect(self._populate_strategy_cards)
        return page

    def _populate_strategy_cards(self) -> None:
        """从strategy_loader加载策略列表，渲染卡片网格。"""
        while self.strategy_cards_grid.count():
            item = self.strategy_cards_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            manager = StrategyManager(STRATEGY_DIR)
            strategies = manager.list_strategies()
        except Exception:
            strategies = []
        row = col = 0
        for info in strategies:
            card = self._create_strategy_card(info)
            self.strategy_cards_grid.addWidget(card, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

    def _create_strategy_card(self, info) -> QFrame:
        """创建单个策略卡片。"""
        card = QFrame()
        card.setObjectName("metricCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        name_label = QLabel(getattr(info, 'name', '未命名策略'))
        name_label.setStyleSheet(f"font-weight: 700; font-size: 14px; color: {COLORS['accent']};")
        layout.addWidget(name_label)

        tag_layout = QHBoxLayout()
        for tag_text in (getattr(info, 'tags', '') or '').split(','):
            tag_text = tag_text.strip()
            if tag_text:
                tag = QLabel(tag_text)
                tag.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text_secondary']}; padding: 2px 8px; border-radius: 4px; font-size: 10px;")
                tag_layout.addWidget(tag)
        tag_layout.addStretch()
        layout.addLayout(tag_layout)

        quality = getattr(info, 'quality_score', 3) or 3
        stars = "★" * min(5, int(quality)) + "☆" * max(0, 5 - int(quality))
        star_label = QLabel(stars)
        star_label.setStyleSheet(f"color: {COLORS['warning']}; font-size: 12px;")
        layout.addWidget(star_label)

        params = getattr(info, 'params', {}) or {}
        param_text = ', '.join(f"{k}={v}" for k, v in list(params.items())[:4])
        if param_text:
            param_label = QLabel(param_text)
            param_label.setObjectName("mutedLabel")
            param_label.setWordWrap(True)
            layout.addWidget(param_label)

        return card

    def _populate_market_strategy_map(self) -> None:
        """填充行情-策略映射表。"""
        self.market_strategy_table.setRowCount(0)
        for state, info in MARKET_STRATEGY_MAP.items():
            row = self.market_strategy_table.rowCount()
            self.market_strategy_table.insertRow(row)
            self.market_strategy_table.setItem(row, 0, QTableWidgetItem(state))
            self.market_strategy_table.setItem(row, 1, QTableWidgetItem(info.get("trigger", "")))
            self.market_strategy_table.setItem(row, 2, QTableWidgetItem(", ".join(info.get("strategies", []))))

    def _build_system_settings_page(self) -> QWidget:
        """构建系统设置页：交易所连接 + 风控视图 + 日志管理。"""
        page = QWidget()
        layout_main = QVBoxLayout(page)
        layout_main.setContentsMargins(14, 14, 14, 14)
        layout_main.setSpacing(12)
        title = QLabel("系统设置")
        title.setObjectName("pageTitle")
        layout_main.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)

        self.exchange_settings = SettingsPage(self.config)
        self.exchange_settings.status_changed.connect(self.statusBar().showMessage)
        if hasattr(self.exchange_settings, "connection_status_changed"):
            self.exchange_settings.connection_status_changed.connect(self._update_connection_status)
        layout.addWidget(self.exchange_settings)

        self.risk_page = RiskPage(self.config)
        self.risk_page.status_changed.connect(self.statusBar().showMessage)
        layout.addWidget(self.risk_page)

        log_section = QGroupBox("日志管理")
        log_row = QHBoxLayout()
        export_btn = QPushButton("导出日志")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn = QPushButton("清空日志")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        log_row.addWidget(export_btn)
        log_row.addWidget(clear_btn)
        log_row.addStretch()
        log_section.setLayout(log_row)

        def _export_logs():
            path, _ = QFileDialog.getSaveFileName(page, "导出日志", str(CONFIG_DIR / "trade_logs_export.csv"), "CSV (*.csv)")
            if path:
                try:
                    import shutil
                    shutil.copy(LOG_FILE, path)
                    QMessageBox.information(page, "导出成功", f"日志已导出到 {path}")
                except Exception as e:
                    QMessageBox.warning(page, "导出失败", str(e))

        def _clear_logs():
            reply = QMessageBox.question(page, "确认清空", "确定清空所有交易日志？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    LOG_FILE.write_text("")
                    QMessageBox.information(page, "已清空", "交易日志已清空")
                except Exception as e:
                    QMessageBox.warning(page, "清空失败", str(e))

        export_btn.clicked.connect(_export_logs)
        clear_btn.clicked.connect(_clear_logs)
        layout.addWidget(log_section)
        layout.addStretch()
        scroll.setWidget(inner)
        layout_main.addWidget(scroll, 1)
        return page

    def _build_status_bar(self) -> None:
        """构建底部状态栏：连接状态 | 当前策略 | 最后交易 | 保证金率。"""
        self._status_connection = QLabel("🟢 OKX已连接")
        self._status_strategy = QLabel("当前策略: --")
        self._status_last_trade = QLabel("最后交易: --")
        self._status_margin = QLabel("保证金率: --")
        for widget in [self._status_connection, self._status_strategy, self._status_last_trade, self._status_margin]:
            widget.setStyleSheet(f"color: {COLORS['text_secondary']}; padding: 0 12px;")
            self.statusBar().addPermanentWidget(widget)

    def _update_connection_status(self, connected: bool, message: str = "") -> None:
        """更新底部连接状态显示。"""
        if connected:
            mode = ""
            for candidate in ("直连", "备用域名", "代理"):
                if candidate in message:
                    mode = candidate
                    break
            self._status_connection.setText(f"🟢 OKX已连接（{mode}）" if mode else "🟢 OKX已连接")
        else:
            self._status_connection.setText("🔴 未连接")
        if message:
            self._status_connection.setToolTip(message)

    def closeEvent(self, event) -> None:
        """关闭窗口时停止所有线程。"""
        pages = [
            getattr(self, "backtest_page", None),
            getattr(self, "live_page", None),
            getattr(self, "ai_page", None),
            getattr(self, "replay_page", None),
        ]
        for page in pages:
            if page:
                worker = getattr(page, "auto_trade_worker", None)
                if worker:
                    worker.stop()
        super().closeEvent(event)

    def _apply_style(self) -> None:
        """应用MUJI极简浅色主题。"""
        pg.setConfigOptions(antialias=True, background=COLORS["bg"])
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {COLORS["bg"]};
                color: {COLORS["text_primary"]};
                font-family: "Microsoft YaHei", "Segoe UI";
                font-size: 10pt;
            }}
            QTabWidget#mainTabs::pane {{
                background: {COLORS["card"]};
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
                top: -1px;
            }}
            QTabBar::tab {{
                background: {COLORS["bg"]};
                color: {COLORS["text_secondary"]};
                border: 1px solid {COLOR_BORDER};
                padding: 10px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 10pt;
            }}
            QTabBar::tab:selected {{
                background: {COLORS["card"]};
                color: {COLORS["accent"]};
                border-bottom-color: {COLORS["card"]};
                font-weight: 700;
            }}
            QTabBar::tab:hover {{
                color: {COLORS["accent"]};
            }}
            QLabel#pageTitle {{
                font-size: 18pt;
                font-weight: 700;
                color: {COLORS["text_primary"]};
                padding-bottom: 8px;
            }}
            QLabel#mutedLabel {{
                color: {COLORS["text_secondary"]};
                font-size: 10pt;
            }}
            QLabel#metricValue {{
                font-size: 18pt;
                font-weight: 700;
            }}
            QFrame#metricCard, QGroupBox {{
                background: {COLORS["card"]};
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
            }}
            QGroupBox {{
                margin-top: 12px;
                padding: 14px 8px 8px 8px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {COLORS["text_primary"]};
            }}
            QLineEdit, QComboBox, QDateEdit, QPlainTextEdit, QTextEdit {{
                background: {COLORS["card"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {COLORS["accent"]};
            }}
            QPushButton {{
                background: {COLORS["card"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-size: 10pt;
            }}
            QPushButton:hover {{
                border-color: {COLORS["accent"]};
                background: {COLORS["bg"]};
            }}
            QPushButton[class="primary"] {{
                background: {COLORS["accent"]};
                border-color: {COLORS["accent"]};
                color: white;
            }}
            QPushButton[class="primary"]:hover {{
                background: #6b8a68;
                border-color: #6b8a68;
            }}
            QPushButton:disabled {{
                color: {COLORS["text_secondary"]};
                background: {COLORS["bg"]};
            }}
            QTableWidget {{
                background: {COLORS["card"]};
                alternate-background-color: {COLORS["bg"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background: {COLORS["bg"]};
                color: {COLORS["text_primary"]};
                border: none;
                padding: 6px;
                font-weight: 700;
            }}
            QTableWidget::item {{
                padding: 4px;
                border: none;
            }}
            QTableWidget::item:selected {{
                background: rgba(125, 157, 122, 0.20);
            }}
            QSplitter::handle {{
                background: {COLORS["bg"]};
            }}
            QStatusBar {{
                background: {COLORS["card"]};
                color: {COLORS["text_secondary"]};
                border-top: 1px solid {COLOR_BORDER};
            }}
            QCheckBox {{
                color: {COLORS["text_primary"]};
                spacing: 6px;
            }}
            QComboBox::drop-down {{
                border: none;
                padding-right: 6px;
            }}
            QComboBox QAbstractItemView {{
                background: {COLORS["card"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
                selection-background-color: {COLORS["accent"]};
            }}
            QScrollBar:vertical {{
                background: {COLORS["bg"]};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLOR_BORDER};
                min-height: 30px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {COLORS["text_secondary"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QProgressBar {{
                border: 1px solid {COLOR_BORDER};
                border-radius: 4px;
                text-align: center;
                background: {COLORS["bg"]};
            }}
            QProgressBar::chunk {{
                background: {COLORS["accent"]};
                border-radius: 3px;
            }}
            QWidget#aiTabPage {{
                background: {COLORS["card"]};
                color: {COLORS["text_primary"]};
            }}
            QWidget#aiTabPage QLabel {{
                color: {COLORS["text_primary"]};
                font-weight: 600;
            }}
            QWidget#aiTabPage QLabel#mutedLabel {{
                color: {COLORS["text_secondary"]};
                font-weight: 500;
            }}
            QWidget#aiTabPage QTextEdit,
            QWidget#aiTabPage QPlainTextEdit,
            QWidget#aiTabPage QTableWidget {{
                background: {COLORS["bg"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
            }}
            QWidget#aiTabPage QComboBox,
            QWidget#aiTabPage QDateEdit,
            QWidget#aiTabPage QSpinBox,
            QWidget#aiTabPage QLineEdit {{
                background: {COLORS["bg"]};
                color: {COLORS["text_primary"]};
                border: 1px solid {COLOR_BORDER};
            }}
        """)


def main() -> None:
    """应用入口。"""
    app = QApplication(sys.argv)
    app.setApplicationName("加密货币量化交易平台 V4")
    app.setFont(QFont("Microsoft YaHei", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()












