# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (editable — required before running tests):**
```bash
pip install -e .
```

**Run the interactive CLI:**
```bash
python -m cli.main
```

**Run tests:**
```bash
source .venv/bin/activate
pytest                               # all tests
pytest tests/test_signal_processing.py  # single file
pytest -k "test_render"              # by name pattern
pytest -m unit                       # markers: unit | integration | smoke
```

Tests import from the local source, not the installed package. If imports resolve to `.venv/lib/.../site-packages/tradingagents/`, re-run `pip install -e .` to register the editable install.

**Run a quick analysis via Python API:**
```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
ta = TradingAgentsGraph(debug=True, config=config)
final_state, decision = ta.propagate("NVDA", "2024-01-15")
```

**Environment setup:** Copy `.env.example` to `.env` and populate the relevant API keys. For Ollama/LM Studio, set `OLLAMA_BASE_URL=http://host:port/v1` (default: `localhost:1234/v1`). Do not use `OLLAMA_HOST` — the code reads `OLLAMA_BASE_URL`.

## Architecture

TradingAgents implements a **multi-agent trading firm** using LangGraph state machines. Agents collaborate through structured debate rounds to produce BUY/HOLD/SELL decisions.

### Agent Pipeline

```
Analyst Team → Researcher Debate → Trader → Risk Debate → Final Decision
```

1. **Analyst Team** (`agents/analysts/`): Four specialized agents run in parallel — market (technical), sentiment, news (macro), fundamentals (financial). The sentiment analyst (`sentiment_analyst.py`, formerly `social_media_analyst`) pre-fetches Yahoo Finance news, StockTwits cashtag posts, and Reddit (r/wallstreetbets, r/stocks, r/investing) before invoking the LLM — no tool-calling, all data is injected into the prompt from turn 0. The other three analysts use tool-calling via `agents/utils/`.
2. **Researcher Debate** (`agents/researchers/`): Bull and bear researchers debate; the Research Manager judges and produces an investment recommendation. `max_debate_rounds` controls iteration count.
3. **Trader** (`agents/trader/`): Synthesizes all research into a concrete trading plan.
4. **Risk Debate** (`agents/risk_mgmt/`): Aggressive, conservative, and neutral debaters challenge the trading plan. The Risk Manager judges; the Portfolio Manager gives final approval. `max_risk_discuss_rounds` controls iteration.

### Core Orchestration

- **`tradingagents/graph/trading_graph.py`** — `TradingAgentsGraph` is the top-level entry point. Exposes `.propagate(ticker, date)` which returns `(final_state, rating_string)`.
- **`tradingagents/graph/setup.py`** — `GraphSetup` constructs the LangGraph workflow, connecting agents, tool nodes, and conditional routing edges.
- **`tradingagents/graph/conditional_logic.py`** — Routes debate rounds based on iteration count vs. configured max rounds.
- **`tradingagents/graph/signal_processing.py`** — Extracts the final rating from rendered markdown (no LLM call needed); delegates to `parse_rating()` defined in `agents/utils/rating.py`.
- **`tradingagents/graph/checkpointer.py`** — LangGraph checkpoint support. Provides `get_checkpointer()` (context manager yielding a per-ticker `SqliteSaver`), `has_checkpoint()`, `checkpoint_step()`, `clear_checkpoint()`, and `clear_all_checkpoints()`. Each ticker gets its own SQLite DB under `~/.tradingagents/checkpoints/`.
- **`tradingagents/graph/reflection.py`** — Deferred reflection: after a trade resolves, `Reflector` is called with return metrics to write lessons back to the memory log.

### Structured Output Pipeline

The three decision-making agents (Research Manager, Trader, Portfolio Manager) use structured output via `invoke_structured_or_freetext()` in `agents/utils/structured.py`. The pattern:

1. A Pydantic schema (defined in `agents/schemas.py`) is bound to the LLM via `with_structured_output`.
2. On success, the parsed object is passed to a render function (e.g. `render_pm_decision`) that converts it back to a markdown string with consistent `**Header**: value` section headers.
3. On failure (provider doesn't support structured output), the plain LLM response is used as-is.

This means the structured object is consumed inside the agent — `final_state["final_trade_decision"]` is always a markdown string, not the Pydantic object. Key schemas:

- `ResearchPlan` — Research Manager output; `recommendation` field uses the 5-tier `PortfolioRating` enum (Buy / Overweight / Hold / Underweight / Sell)
- `TraderProposal` — Trader output; `action` uses the 3-tier `TraderAction` enum (Buy / Hold / Sell)
- `PortfolioDecision` — Portfolio Manager output; includes `confidence`, `stop_loss`, `entry_range`, `price_target`, `review_trigger`, and other exit-strategy fields. `PriceRange` is a nested model with `low`/`high` floats.

### `propagate()` Return Value

```python
final_state, decision = ta.propagate("NVDA", "2024-01-15")
# decision: plain string — "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell"
# final_state: dict with keys including:
#   "final_trade_decision"   — rendered markdown of PortfolioDecision
#   "investment_plan"        — Research Manager's rendered markdown
#   "trader_investment_plan" — Trader's rendered markdown
#   "market_report", "sentiment_report", "news_report", "fundamentals_report"
#   "investment_debate_state", "risk_debate_state"  — full debate transcripts
```

### State Management

Agent state flows through LangGraph as `TypedDict` objects in `agents/utils/agent_states.py`:
- `AgentState` — main conversation state (extends `MessagesState`)
- `InvestDebateState` — tracks bull/bear debate messages and round count
- `RiskDebateState` — tracks risk team debate messages and round count

### Memory System

`agents/utils/memory.py` (`TradingMemoryLog`) gives the Portfolio Manager access to past decisions for the same ticker and cross-ticker lessons. Entries are stored as structured markdown in a flat file. The memory log lifecycle:

1. **At run start** — `_resolve_pending_entries(ticker)` looks up past decisions for that ticker and injects context into the PM prompt via `past_context`.
2. **At run end** — `store_decision()` appends a pending entry tagged with the rating.
3. **After outcome known** — `Reflector.reflect_on_final_decision()` writes lessons back to the log entry.

### Data Layer

**`tradingagents/dataflows/`** implements a pluggable data abstraction:
- `interface.py` — abstract contracts for data retrieval
- Concrete implementations: `y_finance.py` (default, free) and `alpha_vantage_*.py` (paid)
- `reddit.py` — fetches posts from r/wallstreetbets, r/stocks, r/investing for the sentiment analyst
- `stocktwits.py` — fetches cashtag messages with Bullish/Bearish sentiment tags for the sentiment analyst
- Configured per data type via `DEFAULT_CONFIG["data_vendors"]`; individual tools can be overridden with `DEFAULT_CONFIG["tool_vendors"]`

### LLM Abstraction

**`tradingagents/llm_clients/`** wraps multiple providers behind a common interface:
- `factory.py` — creates the appropriate client given `llm_provider` config
- `base_client.py` — `BaseLLMClient` ABC with `get_llm()`, `validate_model()`, `warn_if_unknown_model()`, and `normalize_content()` (flattens multi-block responses from Gemini/OpenAI Responses API to a plain string)
- `capabilities.py` — declarative per-model capability table (`ModelCapabilities`, `get_capabilities()`). Encodes which models reject `tool_choice`, require reasoning-content roundtrip, or lack JSON schema support. Clients consult this instead of hardcoded `if model_name` ladders.
- `api_key_env.py` — canonical `provider → env var` mapping (`PROVIDER_API_KEY_ENV`). Used by the CLI's interactive key-detection flow; add a row here when onboarding a new provider.
- `openai_client.py` — handles OpenAI, xAI, DeepSeek, Qwen, GLM, MiniMax, OpenRouter, and Ollama (all OpenAI-compatible)
- `anthropic_client.py`, `google_client.py`, `azure_client.py` — provider-specific clients
- `model_catalog.py` — shared model list used by both the CLI dropdown and validation
- Config distinguishes `deep_think_llm` (managers/trader) from `quick_think_llm` (analysts)
- Dual-region providers (Qwen, GLM, MiniMax) have separate `*-cn` variants with their own API keys and endpoints

### Configuration

All defaults live in `tradingagents/default_config.py`. Any key can be overridden at runtime via `TRADINGAGENTS_<KEY>` environment variables (e.g. `TRADINGAGENTS_LLM_PROVIDER=anthropic`).

| Key | Default | Description |
|-----|---------|-------------|
| `llm_provider` | `"openai"` | LLM backend (`openai`, `anthropic`, `google`, `azure`, `xai`, `deepseek`, `qwen`, `qwen-cn`, `glm`, `glm-cn`, `minimax`, `minimax-cn`, `openrouter`, `ollama`) |
| `deep_think_llm` | `"gpt-5.4"` | Model for managers/trader |
| `quick_think_llm` | `"gpt-5.4-mini"` | Model for analysts |
| `backend_url` | `None` | Provider endpoint override |
| `google_thinking_level` | `None` | Google thinking budget (`"high"`, `"minimal"`, etc.) |
| `openai_reasoning_effort` | `None` | OpenAI reasoning effort (`"low"`, `"medium"`, `"high"`) |
| `anthropic_effort` | `None` | Anthropic extended thinking effort (`"low"`, `"medium"`, `"high"`) |
| `max_debate_rounds` | `1` | Bull/bear debate iterations |
| `max_risk_discuss_rounds` | `1` | Risk team debate iterations |
| `max_recur_limit` | `100` | LangGraph recursion limit |
| `checkpoint_enabled` | `False` | Resume crashed runs via LangGraph SqliteSaver |
| `output_language` | `"English"` | Language for analyst reports and final decision |
| `news_article_limit` | `20` | Max articles per ticker for ticker-specific news |
| `global_news_article_limit` | `10` | Max articles for global/macro news |
| `global_news_lookback_days` | `7` | Macro news lookback window in days |
| `global_news_queries` | (5 queries) | Search queries used by the news analyst for macro headlines |
| `memory_log_max_entries` | `None` | Cap on resolved memory log entries; `None` disables rotation |
| `benchmark_ticker` | `None` | Override benchmark for alpha calculation (e.g. `"SPY"`); overrides `benchmark_map` |
| `benchmark_map` | (suffix→ticker dict) | Maps ticker exchange suffixes to benchmark indices (`.NS`→`^NSEI`, `.T`→`^N225`, etc.) |
| `data_vendors` | yfinance for all | Per-category data source |
| `tool_vendors` | `{}` | Per-tool overrides that take precedence over `data_vendors` |
