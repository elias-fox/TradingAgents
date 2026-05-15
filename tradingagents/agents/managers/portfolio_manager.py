"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Two-call design:
1. Free-text narrative call — the LLM writes a rich, flowing decision in its
   own words.  Stored as ``final_trade_decision`` for CLI display, memory
   log, and saved reports.
2. Structured extraction call — the same narrative is fed back to the LLM
   with a ``PortfolioDecision`` schema attached so it extracts the key
   machine-readable fields (rating, confidence, price targets, etc.).
   Stored as ``portfolio_decision`` for downstream consumers.

Step 2 is best-effort: if the provider does not support structured output or
the extraction fails, ``portfolio_decision`` is ``None`` and the narrative
in ``final_trade_decision`` is still preserved.
"""

from __future__ import annotations

import logging

from tradingagents.agents.schemas import PortfolioDecision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import bind_structured

logger = logging.getLogger(__name__)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to a long position
- **Overweight**: Favorable outlook; gradually increase long exposure
- **Hold**: Maintain current position; no new entry or exit
- **Underweight**: Reduce exposure; trim the position
- **Sell**: Exit long or initiate a short position

For Buy / Overweight: provide entry_range, position_size_pct, stop_loss, and price_target.
For Sell: provide stop_loss and price_target; add short_entry_range and short_position_size_pct if initiating a short.
For Hold: provide review_trigger and re_evaluate_after.
Always provide confidence (1–100).

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        # Step 1: narrative — unconstrained free-text so the LLM writes a rich
        # flowing decision rather than terse field completions.
        response = llm.invoke(prompt)
        final_trade_decision = response.content

        # Step 2: structured extraction — feed the narrative back with the
        # schema attached to pull out machine-readable fields.
        portfolio_decision = None
        if structured_llm is not None:
            try:
                extraction_prompt = (
                    "Extract the structured investment decision fields from "
                    "this Portfolio Manager decision:\n\n"
                    + final_trade_decision
                )
                decision_obj = structured_llm.invoke(extraction_prompt)
                portfolio_decision = decision_obj.model_dump()
            except Exception as exc:
                logger.warning(
                    "Portfolio Manager structured extraction failed (%s); "
                    "portfolio_decision will be None",
                    exc,
                )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "portfolio_decision": portfolio_decision,
        }

    return portfolio_manager_node
