# 量化交易平台 V4 (Quantitative Trading Platform V4)

PyQt6 desktop application for cryptocurrency quantitative trading — strategy backtesting, live monitoring, AI-assisted market analysis, and risk management. Built from V3 refactoring with enhanced multi-strategy coordination and MUJI minimalist design.

## Key Features

- Strategy backtesting with interactive parameter optimization (AI-powered via DeepSeek)
- Live trading monitor with OKX simulated trading (demo endpoint)
- AI market state analysis & strategy matching (DeepSeek chat/reasoner)
- Risk control panel with configurable leverage, daily loss limits, ATR circuit breakers
- Historical replay for strategy validation
- Strategy coordination panel for multi-strategy conflict resolution

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
