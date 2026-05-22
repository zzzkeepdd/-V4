# -*- coding: utf-8 -*-
"""策略扫描、标签解析和参数包加载。"""

import ast
import re
from pathlib import Path


_COINS = ("BTC", "ETH", "SOL")
_EXCLUDED_FILENAMES = {"成交量异动.py", "SMC_结构转换突破_ETH.py"}
_GENERIC_PACK_KEYWORDS = (
    ("DEFAULT", 70),
    ("RECOMMENDED", 65),
    ("STANDARD", 60),
    ("HIGH_QUALITY", 55),
    ("HIGH_FREQ", 50),
    ("STRICT", 45),
    ("LOOSE", 40),
    ("BEST", 35),
)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="ignore") as fh:
        return fh.read()


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _collect_values(text: str, labels: tuple[str, ...]) -> str:
    values = []
    for label in labels:
        value = _first_match(
            rf"^\s*(?:#\s*)?(?:{label})(?:[（(][^）)]*[）)])?\s*[:：]\s*(.+)$",
            text,
        )
        if value:
            values.append(value)
    return " ".join(values)


def _parse_coins(text: str) -> list[str]:
    coins = []
    for token in re.findall(r"\b(BTC|ETH|SOL)\b", text.upper()):
        if token not in coins:
            coins.append(token)
    return coins


def _parse_quality_score(source: str, filename: str) -> float:
    scores = [float(match.group(1)) for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)/5", source)]
    if scores:
        return max(scores)
    return 5.0


def _literal_eval_node(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                merged = _literal_eval_node(value_node, env)
                if isinstance(merged, dict):
                    result.update(merged)
                continue
            result[_literal_eval_node(key_node, env)] = _literal_eval_node(value_node, env)
        return result
    if isinstance(node, ast.List):
        return [_literal_eval_node(item, env) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal_eval_node(item, env) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_literal_eval_node(item, env) for item in node.elts}
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise KeyError(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal_eval_node(node.operand, env)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return +_literal_eval_node(node.operand, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_eval_node(node.left, env) + _literal_eval_node(node.right, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        result = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                merged = _literal_eval_node(keyword.value, env)
                if isinstance(merged, dict):
                    result.update(merged)
            else:
                result[keyword.arg] = _literal_eval_node(keyword.value, env)
        return result
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def _parse_param_assignments(source: str) -> dict:
    env = {}
    parsed = {}

    def store_assignment(line: str) -> None:
        try:
            module = ast.parse(line)
        except SyntaxError:
            return
        if len(module.body) != 1 or not isinstance(module.body[0], ast.Assign):
            return
        node = module.body[0]
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        name = node.targets[0].id
        if not name.startswith("PARAMS_"):
            return
        try:
            value = _literal_eval_node(node.value, env)
        except Exception:
            return
        env[name] = value
        parsed[name] = value

    for raw_line in source.splitlines():
        stripped = raw_line.lstrip()
        if not stripped.startswith("#"):
            continue
        content = stripped[1:].lstrip()
        if content.startswith("PARAMS_") and "=" in content:
            store_assignment(content)

    try:
        module = ast.parse(source)
    except SyntaxError:
        return parsed

    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "SYMBOL_PARAMS":
                try:
                    value = _literal_eval_node(node.value, env)
                    if isinstance(value, dict):
                        for asset, asset_params in value.items():
                            if isinstance(asset_params, dict):
                                key = f"PARAMS_{str(asset).upper()}"
                                env[key] = dict(asset_params)
                                parsed[key] = dict(asset_params)
                except Exception:
                    pass
                continue
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if not name.startswith("PARAMS_"):
            continue
        try:
            value = _literal_eval_node(node.value, env)
        except Exception:
            continue
        env[name] = value
        parsed[name] = value
    return parsed


def _pack_rank(name: str, coin: str, order_index: int) -> tuple[int, int]:
    upper = name.upper()
    score = 0
    if upper == f"PARAMS_{coin}":
        score += 1000
    if re.search(rf"(?:^|_){coin}(?:_|$)", upper):
        score += 500
    for keyword, bonus in _GENERIC_PACK_KEYWORDS:
        if keyword in upper:
            score += bonus
    return score, -order_index


def _choose_pack_name(packs: dict, order_map: dict, coin: str) -> str:
    ranked = []
    for name, value in packs.items():
        if not isinstance(value, dict) or not value:
            continue
        ranked.append((_pack_rank(name, coin, order_map[name]), name))
    if not ranked:
        return ""
    ranked.sort(reverse=True)
    return ranked[0][1]


def _is_verified_pack(name: str, coin: str) -> bool:
    upper = name.upper()
    return bool(
        upper == f"PARAMS_{coin}"
        or re.search(rf"(?:^|_){coin}(?:_|$)", upper)
    )


def _normalize_tags(source: str) -> dict:
    suitable_market = _first_match(
        r"^\s*(?:#\s*)?(?:适用行情)\s*[:：]\s*(.+)$",
        source,
    )
    unsuitable_market = _first_match(
        r"^\s*(?:#\s*)?(?:不适行情|不适用行情)\s*[:：]\s*(.+)$",
        source,
    )
    frequency = _first_match(
        r"^\s*(?:#\s*)?(?:交易频率)\s*[:：]\s*(.+)$",
        source,
    )
    coins_text = _collect_values(source, ("标的限制", "标的"))
    core_logic = _first_match(
        r"^\s*(?:#\s*)?(?:核心逻辑)\s*[:：]\s*(.+)$",
        source,
    )
    known_risks = _first_match(
        r"(?ms)^\s*(?:#\s*)?(?:已知风险|已知局限)(?:[（(][^）)]*[）)])?\s*[:：]?\s*(.+?)(?=^\s*(?:#\s*)?(?:适用行情|不适行情|不适用行情|交易频率|标的限制|标的|核心逻辑|参数|预置参数包|PARAMS_|import\b|def\b|class\b|\"\"\"|''')|\Z)",
        source,
    )

    coins = _parse_coins(coins_text)
    if not coins:
        coins = [coin for coin in _COINS if coin in source.upper()]
    return {
        "suitable_market": suitable_market,
        "unsuitable_market": unsuitable_market,
        "frequency": frequency,
        "coins": coins,
        "core_logic": core_logic,
        "known_risks": known_risks,
    }


def _build_strategy_record(path: Path, source: str) -> dict | None:
    if path.name in _EXCLUDED_FILENAMES:
        return None

    quality_score = _parse_quality_score(source, path.name)
    if quality_score < 4:
        return None

    tags = _normalize_tags(source)
    params = _parse_param_assignments(source)
    if not params:
        return None

    order_map = {name: index for index, name in enumerate(params)}
    selected_params = {}
    params_quality = {}
    for coin in _COINS:
        pack_name = _choose_pack_name(params, order_map, coin)
        if not pack_name:
            continue
        selected_params[coin] = dict(params[pack_name])
        params_quality[coin] = "verified" if _is_verified_pack(pack_name, coin) else "fallback"

    if "BTC" not in selected_params or not selected_params["BTC"]:
        btc_fallback = next((name for name, value in params.items() if isinstance(value, dict) and value), "")
        if not btc_fallback:
            return None
        selected_params["BTC"] = dict(params[btc_fallback])
        params_quality["BTC"] = "fallback"

    for coin in _COINS:
        if coin not in selected_params or not selected_params[coin]:
            fallback_name = _choose_pack_name(params, order_map, "BTC")
            if not fallback_name:
                fallback_name = next((name for name, value in params.items() if isinstance(value, dict) and value), "")
            if not fallback_name:
                return None
            selected_params[coin] = dict(params[fallback_name])
            params_quality[coin] = "fallback"

    return {
        "name": path.stem,
        "file_path": str(path),
        "quality_score": quality_score,
        "tags": tags,
        "params_packs": {coin: dict(selected_params[coin]) for coin in _COINS},
        "params_quality": {coin: params_quality.get(coin, "fallback") for coin in _COINS},
        "known_risks": tags["known_risks"],
        "best_params": {coin: dict(selected_params[coin]) for coin in _COINS},
    }


def load_strategies(strategy_dir: str) -> list[dict]:
    """扫描策略目录并返回标准化策略列表。"""
    base = Path(strategy_dir)
    if not base.exists():
        return []

    strategies = []
    for path in sorted(base.glob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = _read_text(path)
        record = _build_strategy_record(path, source)
        if record is None:
            continue
        strategies.append(record)

    strategies.sort(key=lambda item: (-float(item["quality_score"]), item["name"]))
    return strategies


__all__ = ["load_strategies"]
