# 量化交易系统 V4 — 策略超市 (Strategy Supermarket)

加密货币量化交易桌面程序。核心理念：AI 根据实时行情和币种特征，自动选择策略 + 选择参数 + 持续调参，人只需要看一眼最终结果。

Cryptocurrency quantitative trading desktop app. Core philosophy: AI selects the right strategy + right parameters for current market conditions, and continuously auto-tunes them. Human only needs to glance at the final result.

## AI 决策流水线 (AI Decision Pipeline)

V4 的核心不是"回测平台"，而是"AI 交易员"。每一轮分析走三层流水线：

```
行情数据(OHLCV) → 技术特征提取 → AI判断行情状态 → AI匹配策略+参数包 → 生成交易信号
```

### 1. AI 选策略 — 行情状态 × 币种适配 (AI Strategy Selection)

AI 读取 26 维技术特征（ADX/ATR/BB/RSI/EMA/成交量/摆动点），先判断当前行情属于六种状态之一，再根据状态自动匹配策略：

| 行情状态 | AI 判断条件 | 自动激活策略 |
|---------|------------|------------|
| TRENDING_UP | ADX>25, 价格>EMA20, EMA多头排列 | 趋势回调入场（趋势追随） |
| TRENDING_DOWN | ADX>25, 价格<EMA20, EMA空头排列 | 趋势回调入场（趋势追随） |
| RANGING | ADX<20, 价格区间震荡 | 摆动点区间反转（均值回归） |
| HIGH_VOLATILITY | ATR/ATR_MA>1.5, BB带宽扩张 | 暂停交易 / 降级到保守参数 |
| TREND_EXHAUSTION | 连续20+根同向K线, RSI背离 | 趋势衰竭反转（逆势捕捉） |
| CHAOTIC | 信号冲突, 无法判断 | 不交易，等待明确信号 |

同时按币种过滤：BTC/ETH 优先，SOL 仅在特定策略下可用（趋势回调入场 SOL 胜率仅 26.7%，自动排除）。

降级链：DeepSeek Reasoner → DeepSeek Chat → 本地规则引擎。任一层失败自动降级，不中断交易。

### 2. AI 选参数 — 行情自适应 (AI Parameter Selection)

同一个策略在不同行情下用不同参数包。AI 根据 market_state × coin 返回最优参数组合：

- RANGING + BTC：摆动点区间反转，adx_threshold=20（更敏感），rr_ratio=2.0
- RANGING + ETH：同一策略，adx_threshold=25（更保守），rr_ratio=2.0
- TRENDING + BTC：趋势回调入场，fib=[0.5,0.618]，volume=ON
- TRENDING + ETH：同一策略，fib=[0.382,0.618]，volume=ON

参数包在策略文件中预定义为 PARAMS_BTC / PARAMS_ETH / PARAMS_SOL。AI 选择哪个参数包，而不是重新发明参数——保证可解释性。

### 3. AI 自动调参 — DeepSeek 智能优化 (AI Auto-Tuning)

不是网格搜索，不是 ±8% 盲调。每次回测后，AI 读取当前参数和回测结果，分析策略含义，选择 1-3 个参数在 ±15% 范围内智能调整：

```
输入: 策略代码(摆动点区间反转) + 当前参数(pivot_lb=4, adx_threshold=20) + 回测结果(WR 42%, Sharpe 0.8)
AI 分析: "WR 偏低，pivot_lb 也许需要更短的窗口来捕捉更细的摆动结构。ADX 阈值偏高可能过滤了太多震荡机会。"
AI 输出: pivot_lb→3, adx_threshold→18
```

LLM 不可用时降级为递减幅度的小扰动（0.10→0.02），避免大盲跳破坏收敛。

## Key Features

- AI 决策流水线：行情判断 → 策略匹配 → 参数选择 → 自动调参，四步全自动
- AI Decision Pipeline: market state analysis → strategy matching → parameter selection → auto-tuning, fully automated
- 策略回测 + AI 智能参数优化（DeepSeek，非网格搜索）
- Strategy backtesting with AI-powered parameter optimization (DeepSeek, not grid search)
- OKX 模拟盘实盘监控（demo endpoint）
- Live trading monitor with OKX simulated trading
- 三层 AI 降级链：Reasoner → Chat → 本地规则，永不中断
- Three-tier AI degradation: Reasoner → Chat → Local rules, never halts
- 风控面板：杠杆/日亏损上限/ATR熔断/复利回撤锁仓
- Risk control: leverage cap, daily loss circuit breaker, ATR fuse, compound drawdown lock
- 历史重放：用历史数据验证策略表现
- Historical replay for strategy validation
- 策略协同面板：多策略冲突裁决
- Strategy coordination panel: multi-strategy conflict resolution

## Included Strategies

### 1. Swing Reversal Range (摆动点区间反转) 

Market regime: RANGING (震荡市)

Core logic: Identifies swing highs/lows to form temporary support/resistance zones. When price approaches zone boundaries and a reversal candlestick confirms, enters for zone mean reversion profit. ADX < 25 required for entry (trend filter).

| Parameter | Range | Optimal |
|-----------|-------|---------|
| pivot_lb | 3-6 | 4 |
| approach_ratio | 0.10-0.30 | 0.20 |
| adx_threshold | 20-30 | 20 (BTC), 25 (ETH) |
| rr_ratio | 1.5-2.0 | 2.0 (BTC/ETH), 1.5 (SOL) |

Quality: Passed preliminary validation (WR >= 40%, RR >= 1.5, n >= 50 on BTC/ETH). Five-layer formal inspection pending.

Candlestick patterns: Hammer (bullish reversal), Shooting Star (bearish reversal), Engulfing patterns.

### 2. Trend Pullback Entry (趋势回调入场)

Market regime: TRENDING (趋势市)

Core logic: After trend confirmed by consecutive swing points, waits for price to pull back to Fibonacci 0.5-0.618 zone. Enters on reversal candlestick + volume confirmation. TP = 2R from entry.

Grid search results (2025-05 ~ 2026-05):

| Symbol | Params | WR | Avg R | Return |
|--------|--------|-----|-------|--------|
| BTC | lb=5, ns=1, fib=[0.5,0.618], vol=ON | 53.8% | 0.62R | +26.5% |
| ETH | lb=5, ns=2, fib=[0.382,0.618], vol=ON | 60.0% | 0.80R | +55.4% |
| SOL | Not suitable | 26.7% | -0.20R | -18.2% |

Quality: Passed preliminary validation. Five-layer formal inspection pending.

SOL excluded — swing point noise too high for this strategy structure.

## Factor Analysis Framework

Both strategies share a common structural factor approach:

| Factor | Description | Usage |
|--------|-------------|-------|
| Swing Points | Local extrema via rolling window (parameter: lookback) | Zone boundary identification |
| ADX | Wilder's smoothed directional index | Market regime filter (trend vs range) |
| ATR | Average True Range | Volatility-based stop distance / entry filter |
| Volume MA | Rolling volume average | Reversal confirmation (volume > MA) |
| Fibonacci Levels | 0.382 / 0.5 / 0.618 retracement | Pullback depth measurement |
| Candlestick Structure | Wick/body ratio, engulfing detection | Signal quality gate |

The platform supports:
- IC (Information Coefficient) analysis
- ICIR (IC / IR ratio)
- Stratified backtesting
- Long/short return decomposition
- Sharpe ratio + max drawdown

## Five-Layer Quality Inspection (五重检验)

Formal quality inspection pipeline for strategies:

1. IC Test — signal-value correlation
2. ICIR — stability of signal quality
3. Stratified Backtest — performance by quantile
4. Long/Short Decomposition — directional asymmetry
5. Sharpe + Max Drawdown — risk-adjusted return

Both included strategies have passed preliminary validation and await formal inspection.

## Architecture

```
V4/
├── main.py                 # Main application (PyQt6, ~5600 lines)
├── strategy_loader.py      # Strategy file discovery and parameter parsing
├── ai_engine.py            # DeepSeek AI integration
├── market_engine.py        # Market data pipeline
├── risk_engine.py          # Risk control engine
├── data_layer.py           # Data caching layer
├── exchange_interface.py   # CCXT wrapper (OKX/Binance/Gate)
├── capital_manager.py      # Capital allocation & position sizing
├── scheduler.py            # Periodic task scheduler
├── settings_page.py        # Settings UI module
├── strategies/
│   ├── 摆动点区间反转.py       # Swing Reversal Range strategy
│   └── 趋势回调入场.py         # Trend Pullback Entry strategy
├── config/
│   └── user_config.json    # User preferences (public)
├── PROJECT_PLAN.md         # Full project specification
├── PROJECT_LESSONS.md      # Lessons learned & pitfalls
├── 踩坑经验手册.md          # Development pitfall handbook (Chinese)
└── specs/
    └── v4-strategy-supermarket-spec.md
```

## Tech Stack

- Python 3.11+, PyQt6, pyqtgraph
- CCXT 4.x (crypto exchange unified API)
- Pandas, NumPy
- OpenAI SDK → DeepSeek API
- DeepSeek chat (market analysis), DeepSeek reasoner (deep analysis/optimization)

## Setup

Requirements: Python 3.11+, Windows (primary) or Linux.

```bash
pip install PyQt6 pyqtgraph ccxt pandas numpy openai
```

Configure API keys in the application UI (Settings page):
- DeepSeek API key (for AI features)
- OKX API key/secret/passphrase (for simulated trading)

## Network Notes (China Users)

OKX API requires proxy for stable connectivity from mainland China. The platform supports three-tier fallback:

1. User-configured HTTP proxy
2. Alternate domains (aws.okx.com, okx.me)
3. Direct connection (last resort)

See 踩坑经验手册.md for detailed diagnostics and CCXT proxy configuration.

## Risk Controls (Hardcoded, Cannot Be Modified by AI)

| Guard | Condition | Action |
|-------|-----------|--------|
| Max leverage | > user setting (default 20x) | Reject order |
| Daily loss circuit breaker | > 5% | Pause all strategies |
| ATR volatility circuit breaker | H1 ATR > 20MA × 2 | Pause + yellow alert |
| Min position size | < 10 USDT | Skip |
| Compound drawdown lock | > 20% drawdown | Lock 80% capital |
| Compound strategy pause | > 40% drawdown | Manual intervention required |

## Design

MUJI minimalist design — restrained, functional, low saturation.
- Background: clean white/light gray cards
- Accent: muted tones, no neon
- Rounded card elements with soft shadows
- Emoji status indicators: 🟢 normal 🟡 resonance 🔴 close ⚡ circuit breaker

## Documentation

- PROJECT_PLAN.md — Full specification (18 sections, strategy library, risk rules, UI spec)
- PROJECT_LESSONS.md — Lessons from V3 development
- 踩坑经验手册.md — Comprehensive pitfall handbook (network, Streamlit, backtesting, Windows)

## License

MIT

## Development Notes

- Git version control is mandatory — V3 was lost once due to file overwrite
- Strategy files must declare PARAMS_XXX presets for AI optimization
- save_config uses merge mode, never overwrite
- CCXT exceptions use isinstance(), not string matching
- Backtest engine protects against signal reuse (last_exit_idx guard)
- Entry prices must use close[i], never historical close (no look-ahead bias)
