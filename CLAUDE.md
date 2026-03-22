# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -e .
# or
pip install -r requirements.txt
```

**Run the interactive CLI:**
```bash
python -m cli.main
```

**Run a quick analysis via Python API:**
```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
ta = TradingAgentsGraph(debug=True, config=config)
final_state, decision = ta.propagate("NVDA", "2024-01-15")
```

**Environment setup:** Copy `.env.example` to `.env` and populate the relevant API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, etc.).

## Architecture

TradingAgents implements a **multi-agent trading firm** using LangGraph state machines. Agents collaborate through structured debate rounds to produce BUY/HOLD/SELL decisions.

### Agent Pipeline

```
Analyst Team → Researcher Debate → Trader → Risk Debate → Final Decision
```

1. **Analyst Team** (`agents/analysts/`): Four specialized agents run in parallel to gather data — market (technical), sentiment (social media), news (macro), and fundamentals (financial). Each agent uses tools from `agents/utils/` to fetch and format data.

2. **Researcher Debate** (`agents/researchers/`): Bull and bear researchers debate based on analyst reports. The Research Manager judges the debate and produces an investment recommendation. `max_debate_rounds` in config controls iteration count.

3. **Trader** (`agents/trader/`): Synthesizes all research into a concrete trading plan.

4. **Risk Debate** (`agents/risk_mgmt/`): Aggressive, conservative, and neutral debaters challenge the trading plan. The Risk Manager judges; the Portfolio Manager gives final approval. `max_risk_discuss_rounds` controls iteration.

### Core Orchestration

- **`tradingagents/graph/trading_graph.py`** — `TradingAgentsGraph` is the top-level entry point. It initializes LLMs, wires memory, and exposes `.propagate(ticker, date)` and `.reflect_and_remember(returns)`.
- **`tradingagents/graph/setup.py`** — `GraphSetup` constructs the LangGraph workflow, connecting agents, tool nodes, and conditional routing edges.
- **`tradingagents/graph/conditional_logic.py`** — Routes debate rounds based on iteration count vs. configured max rounds.
- **`tradingagents/graph/signal_processing.py`** — Extracts the final trade decision from LLM output.
- **`tradingagents/graph/reflection.py`** — Updates agent memories based on trade outcomes (for learning).

### State Management

Agent state flows through the LangGraph graph as `TypedDict` objects defined in `agents/utils/agent_states.py`:
- `AgentState` — main conversation state (extends `MessagesState`)
- `InvestDebateState` — tracks bull/bear debate messages and round count
- `RiskDebateState` — tracks risk team debate messages and round count

### Data Layer

**`tradingagents/dataflows/`** implements a pluggable data abstraction:
- `interface.py` — abstract contracts for data retrieval
- Concrete implementations: `y_finance.py` (default, free) and `alpha_vantage_*.py` (paid)
- Configured per data type via `DEFAULT_CONFIG["data_vendors"]`; individual tools can be overridden with `DEFAULT_CONFIG["tool_vendors"]`
- Optional Redis caching for data responses

### LLM Abstraction

**`tradingagents/llm_clients/`** wraps multiple providers behind a common interface:
- `base_client.py` — abstract base class
- `factory.py` — creates the appropriate client given `llm_provider` config
- Providers: `openai`, `anthropic`, `google`, `xai`, `openrouter`, `ollama`
- Config distinguishes `deep_think_llm` (complex reasoning, used by managers/trader) from `quick_think_llm` (fast responses, used by analysts)
- Provider-specific thinking settings: `openai_reasoning_effort`, `google_thinking_level`

### Configuration

All defaults live in `tradingagents/default_config.py`. Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `llm_provider` | `"openai"` | LLM backend |
| `deep_think_llm` | `"gpt-5.2"` | Model for managers/trader |
| `quick_think_llm` | `"gpt-5-mini"` | Model for analysts |
| `max_debate_rounds` | `1` | Bull/bear debate iterations |
| `max_risk_discuss_rounds` | `1` | Risk team debate iterations |
| `data_vendors` | yfinance for all | Per-category data source |

### Memory System

`agents/utils/memory.py` (`FinancialSituationMemory`) gives agents persistent memory of past decisions and outcomes. After a trade resolves, call `ta.reflect_and_remember(returns)` to update memories so future runs incorporate past performance.
