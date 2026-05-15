"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PriceRange(BaseModel):
    """Inclusive price band in the instrument's quote currency."""

    low: float
    high: float


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.

    Field order follows the logical sequence the LLM should reason through:
    direction → conviction → narrative → entry → exit → hold conditions.
    """

    rating: PortfolioRating = Field(
        description="The final position rating: Buy / Overweight / Hold / Underweight / Sell.",
    )
    confidence: int = Field(
        ge=1, le=100,
        description=(
            "Conviction level 1–100. Set this before filling sizing fields — "
            "it should inform position size and stop width. "
            "80+ = strong signal; below 50 = genuinely conflicted evidence."
        ),
    )
    executive_summary: str = Field(
        description=(
            "2–4 sentence action plan. Reference the entry/exit levels, "
            "sizing, and the key catalyst that could invalidate the thesis."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analyst debate. "
            "Incorporate prior memory lessons if present in the prompt context."
        ),
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description=(
            "Recommended holding period e.g. '3–6 months'. "
            "Required for Buy / Overweight / Underweight / Sell. Omit for Hold."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description=(
            "Primary take-profit price in the instrument's quote currency. "
            "Required for Buy / Overweight / Sell. Omit for Hold."
        ),
    )
    price_target_2: Optional[float] = Field(
        default=None,
        description=(
            "Stretch (secondary) take-profit target. "
            "Optional for Buy / Overweight / Sell."
        ),
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description=(
            "Hard stop price to exit and cut the loss. "
            "For longs (Buy / Overweight): set below entry. "
            "For shorts (Sell): set above entry. "
            "Required for Buy / Overweight / Sell. Omit for Hold."
        ),
    )
    trailing_stop_pct: Optional[float] = Field(
        default=None,
        ge=0, le=100,
        description=(
            "Trailing stop as a percentage from the running peak (longs) "
            "or trough (shorts), e.g. 8.0 for 8%. "
            "Optional for Buy / Overweight / Sell."
        ),
    )
    entry_range: Optional[PriceRange] = Field(
        default=None,
        description=(
            "Ideal buy zone (low / high prices). "
            "Required for Buy / Overweight. Omit otherwise."
        ),
    )
    position_size_pct: Optional[float] = Field(
        default=None,
        ge=0, le=100,
        description=(
            "Recommended portfolio allocation as a percentage. "
            "Scale with confidence: high confidence → full size, "
            "low confidence → half size. "
            "Required for Buy / Overweight. Omit for Hold / Sell."
        ),
    )
    short_entry_range: Optional[PriceRange] = Field(
        default=None,
        description=(
            "Ideal short-entry zone (low / high prices). "
            "Required for Sell when initiating a short. Omit otherwise."
        ),
    )
    short_position_size_pct: Optional[float] = Field(
        default=None,
        ge=0, le=100,
        description=(
            "Short position size as a portfolio percentage. "
            "Required for Sell when initiating a short. Omit otherwise."
        ),
    )
    review_trigger: Optional[str] = Field(
        default=None,
        description=(
            "Specific, observable condition that would prompt a rating change, "
            "e.g. 'earnings miss > 10%' or 'price breaks $180 support'. "
            "Required for Hold. Omit otherwise."
        ),
    )
    re_evaluate_after: Optional[str] = Field(
        default=None,
        description=(
            "Date or catalyst after which to re-assess, "
            "e.g. 'Q2 2024 earnings release' or '2024-07-15'. "
            "Required for Hold. Omit otherwise."
        ),
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown.
    ``**Rating**`` is always the first line so ``parse_rating`` can find it
    without scanning the full document.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Confidence**: {decision.confidence}/100",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.price_target_2 is not None:
        parts.extend(["", f"**Price Target 2**: {decision.price_target_2}"])
    if decision.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {decision.stop_loss}"])
    if decision.trailing_stop_pct is not None:
        parts.extend(["", f"**Trailing Stop**: {decision.trailing_stop_pct}%"])
    if decision.entry_range is not None:
        parts.extend(["", f"**Entry Range**: {decision.entry_range.low}–{decision.entry_range.high}"])
    if decision.position_size_pct is not None:
        parts.extend(["", f"**Position Size**: {decision.position_size_pct}%"])
    if decision.short_entry_range is not None:
        parts.extend(["", f"**Short Entry Range**: {decision.short_entry_range.low}–{decision.short_entry_range.high}"])
    if decision.short_position_size_pct is not None:
        parts.extend(["", f"**Short Position Size**: {decision.short_position_size_pct}%"])
    if decision.review_trigger:
        parts.extend(["", f"**Review Trigger**: {decision.review_trigger}"])
    if decision.re_evaluate_after:
        parts.extend(["", f"**Re-evaluate After**: {decision.re_evaluate_after}"])
    return "\n".join(parts)
