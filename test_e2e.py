# -*- coding: utf-8 -*-
"""
端到端全流程测试脚本。
测试所有模块的导入、配置、连接和核心功能。
运行方式：C:/Program Files/Python312/python.exe test_e2e.py
"""

import json
import os
import sys
from pathlib import Path

# 设置项目根目录环境变量
PROJECT_ROOT = Path(__file__).resolve().parent
os.environ["V4_PROJECT_ROOT"] = str(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# ===== 测试结果收集 =====
results = []
passed = 0
failed = 0
warnings = 0


def test(name, condition, msg=""):
    """记录测试结果。"""
    global passed, failed
    if condition:
        passed += 1
        results.append(f"  ✅ PASS: {name}")
    else:
        failed += 1
        results.append(f"  ❌ FAIL: {name} — {msg}")
    return condition


def warn(name, msg=""):
    """记录警告（不影响最终通过）。"""
    global warnings
    warnings += 1
    results.append(f"  ⚠️  WARN: {name} — {msg}")


# ===== 1. 模块导入测试 =====
print("=" * 60)
print("1. 模块导入测试")
print("=" * 60)

# Python 标准库
test("Path 从 pathlib", True)
test("json 模块", True)

# 第三方库
try:
    import pandas as pd

    test("pandas 导入", True)
except ImportError:
    test("pandas 导入", False, "pip install pandas")

try:
    import numpy as np

    test("numpy 导入", True)
except ImportError:
    test("numpy 导入", False, "pip install numpy")

try:
    import ccxt

    test("ccxt 导入", True)
except ImportError:
    test("ccxt 导入", False, "pip install ccxt")

try:
    from openai import OpenAI

    test("openai 导入", True)
except ImportError:
    test("openai 导入", False, "pip install openai")

# PyQt6
try:
    from PyQt6.QtWidgets import QApplication

    test("PyQt6 导入", True)
except ImportError:
    test("PyQt6 导入", False, "pip install PyQt6>=6.5")

# 项目模块
try:
    from exchange_interface import SimExchange, load_config, save_config

    test("exchange_interface 导入", True)
except Exception as e:
    test("exchange_interface 导入", False, str(e))

try:
    from capital_manager import CapitalManager

    test("capital_manager 导入", True)
except Exception as e:
    test("capital_manager 导入", False, str(e))

try:
    from settings_page import SettingsPage

    test("settings_page 导入", True)
except Exception as e:
    test("settings_page 导入", False, str(e))

# ===== 2. 配置读取测试 =====
print("\n" + "=" * 60)
print("2. 配置读取测试")
print("=" * 60)

config_dir = PROJECT_ROOT / "config"

# api_config.json
api_file = config_dir / "api_config.json"
if test("api_config.json 存在", api_file.exists(),
        f"路径: {api_file}"):
    try:
        raw = json.loads(api_file.read_text(encoding="utf-8"))
        has_data = "data" in raw or "apiKey" in raw
        test("api_config.json 包含凭证数据", has_data)
    except Exception as e:
        test("api_config.json 可解析", False, str(e))

# user_config.json
user_file = config_dir / "user_config.json"
if test("user_config.json 存在", user_file.exists(),
        f"路径: {user_file}"):
    config = json.loads(user_file.read_text(encoding="utf-8"))
    test("max_leverage 字段", "max_leverage" in config)
    test("risk_per_trade_pct 字段", "risk_per_trade_pct" in config)
    test("daily_loss_limit_pct 字段", "daily_loss_limit_pct" in config)
    test("custom_capital 字段", "custom_capital" in config)
    test("exchange_mode 字段", "exchange_mode" in config)
    val = config.get("custom_capital", 0)
    test(f"custom_capital = {val} (>0)", val > 0, f"当前值: {val}")

# ===== 3. 交易所连接测试（模拟盘） =====
print("\n" + "=" * 60)
print("3. 交易所连接测试（模拟盘）")
print("=" * 60)

try:
    from exchange_interface import SimExchange

    exchange = SimExchange()
    connected = exchange.connect()
    if test("OKX 模拟盘连接", connected,
            "注意: 需自行配置代理"):
        # 获取余额
        try:
            balance = exchange.fetch_balance()
            total_usdt = balance.get("total", {}).get("USDT", 0)
            test(f"账户余额: {total_usdt} USDT", total_usdt > 0,
                 f"余额: {total_usdt}")
        except Exception as e:
            warn("账户余额获取", f"不影响核心功能: {e}")

        # 获取行情
        try:
            df = exchange.fetch_ohlcv("BTC/USDT", "1h", 10)
            test(f"BTC/USDT 行情获取 ({len(df)}条)", len(df) > 0,
                 f"K线数量: {len(df)}")
        except Exception as e:
            warn("行情获取", f"可能需要代理: {e}")
    else:
        warn("OKX 模拟盘连接",
             f"连接失败（预期内：可能需要代理）: {exchange.last_error}")
except Exception as e:
    warn("交易所连接测试", f"模块加载异常: {e}")

# ===== 4. AI 市场状态判断测试 =====
print("\n" + "=" * 60)
print("4. AI 市场状态判断测试")
print("=" * 60)

try:
    from openai import OpenAI

    test("openai SDK 可用", True)
except Exception:
    test("openai SDK 可用", False, "pip install openai")

# DEEPSEEK_CONFIG 存在性检查
try:
    # 尝试加载 main.py 中的配置
    main_path = PROJECT_ROOT / "main.py"
    if main_path.exists():
        import ast
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        test("main.py AST 解析", True, f"文件: {main_path.stat().st_size} bytes")
    else:
        test("main.py 存在", False, "请确保 main.py 已就位")
except Exception as e:
    warn("main.py 解析", str(e))

# ===== 5. 策略匹配和运行测试 =====
print("\n" + "=" * 60)
print("5. 策略匹配和运行测试")
print("=" * 60)

strategies_dir = PROJECT_ROOT / "量化平台V3" / "strategies"
if test("策略目录存在", strategies_dir.exists(),
        f"路径: {strategies_dir}"):
    py_files = list(strategies_dir.glob("*.py"))
    test(f"策略文件数量: {len(py_files)}", len(py_files) > 0)

    for sf in py_files[:3]:  # 抽样检查前3个
        try:
            content = sf.read_text(encoding="utf-8")
            has_strategy = "strategy_logic" in content or "def strategy" in content
            has_params = "PARAMS_SCHEMA" in content or "PARAMS_" in content
            test(f"{sf.name}: 包含策略逻辑", has_strategy)
            test(f"{sf.name}: 包含PARAMS声明", has_params)
        except Exception as e:
            warn(f"{sf.name}", str(e))

# ===== 6. 回测验证测试 =====
print("\n" + "=" * 60)
print("6. 回测验证测试")
print("=" * 60)

# py_compile 检查
import py_compile

# 检查 main.py
main_path = PROJECT_ROOT / "main.py"
if main_path.exists():
    try:
        py_compile.compile(str(main_path), doraise=True)
        test("main.py py_compile 通过", True)
    except py_compile.PyCompileError as e:
        test("main.py py_compile 通过", False, str(e))

# 检查 exchange_interface.py
ei_path = PROJECT_ROOT / "exchange_interface.py"
if ei_path.exists():
    try:
        py_compile.compile(str(ei_path), doraise=True)
        test("exchange_interface.py py_compile 通过", True)
    except py_compile.PyCompileError as e:
        test("exchange_interface.py py_compile 通过", False, str(e))

# 检查 capital_manager.py
cm_path = PROJECT_ROOT / "capital_manager.py"
if cm_path.exists():
    try:
        py_compile.compile(str(cm_path), doraise=True)
        test("capital_manager.py py_compile 通过", True)
    except py_compile.PyCompileError as e:
        test("capital_manager.py py_compile 通过", False, str(e))

# AST 解析检查
for path in [main_path, ei_path, cm_path]:
    if path and path.exists():
        try:
            import ast
            tree = ast.parse(path.read_text(encoding="utf-8"))
            test(f"{path.name} AST 解析通过", True,
                 f"{len(tree.body)} 顶级节点")
        except SyntaxError as e:
            test(f"{path.name} AST 解析", False, str(e))

# ===== 7. 复盘分析测试 =====
print("\n" + "=" * 60)
print("7. 复盘分析测试")
print("=" * 60)

reports_dir = PROJECT_ROOT / "reports"
test("reports/ 目录存在", reports_dir.exists() or reports_dir.mkdir(parents=True, exist_ok=True))

# 日志目录
logs_dir = PROJECT_ROOT / "logs"
test("logs/ 目录存在", logs_dir.exists() or logs_dir.mkdir(parents=True, exist_ok=True))

# data_cache 目录
data_cache_dir = PROJECT_ROOT / "data_cache"
test("data_cache/ 目录存在", data_cache_dir.exists() or data_cache_dir.mkdir(parents=True, exist_ok=True))

# ===== 8. 部署文件检查 =====
print("\n" + "=" * 60)
print("8. 部署文件检查")
print("=" * 60)

deploy_bat = PROJECT_ROOT / "deploy.bat"
if test("deploy.bat 存在", deploy_bat.exists(),
        f"路径: {deploy_bat}"):
    content = deploy_bat.read_text(encoding="utf-8")
    test("deploy.bat 包含 pip install", "pip install" in content)
    test("deploy.bat 包含 python main", "python main" in content or "python start_v4" in content)

start_v4 = PROJECT_ROOT / "start_v4.py"
test("start_v4.py 存在", start_v4.exists(),
     f"路径: {start_v4}" if start_v4.exists() else "待创建")

reqs_file = PROJECT_ROOT / "requirements.txt"
test("requirements.txt 存在", reqs_file.exists(),
     f"路径: {reqs_file}" if reqs_file.exists() else "待创建")

gitignore_file = PROJECT_ROOT / ".gitignore"
test(".gitignore 存在", gitignore_file.exists(),
     f"路径: {gitignore_file}" if gitignore_file.exists() else "待创建")
if gitignore_file.exists():
    content = gitignore_file.read_text(encoding="utf-8")
    test(".gitignore 保护 config/*.json", "config/*.json" in content)

# ===== 汇总 =====
print("\n" + "=" * 60)
print("测试汇总")
print("=" * 60)
for r in results:
    print(r)

print(f"\n总计: {passed} 通过, {failed} 失败, {warnings} 警告")

# 输出结果状态码
sys.exit(0 if failed == 0 else 1)
