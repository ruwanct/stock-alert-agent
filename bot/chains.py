"""
LangChain pieces — LLM, prompts (LCEL chains), structured output.
"""

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from bot import config
from bot.schemas import DailyDigest, MarketAlert


def get_llm() -> ChatAnthropic:
    return ChatAnthropic(model=config.MODEL, max_tokens=4000, timeout=90, max_retries=2)


# NOTE: ChatPromptTemplate uses {slot} syntax — any literal brace must be
# doubled ({{ }}). The templates below contain no literal braces.
ANALYSIS_SYSTEM = """You are a senior stock market analyst writing a Telegram/Discord alert from multi-source data.
You also have MEMORY of your past calls and past alerts — use it.

## RULES
- Cross-reference news: if multiple sources report the same story, mark the signal HIGH confidence.
- TOP 3 gainers and TOP 3 losers in the movers lists (only tickers present in the Prices section; use the exact prices shown).
- 3 BUY signals with entry prices, 1-2 SELL signals with entry prices (entry price = the current price shown). WATCH signals have no entry price.
- Every signal needs reasoning citing the news source(s) in `sources`.
- Sector heat map: up / down / flat. Include defense/military if relevant.
- Keep text fields short: reasons under 120 characters, bottom line 2-3 sentences. No markdown characters (no asterisks or underscores).

## MEMORY RULES
- Reference past calls when relevant (e.g. "NVDA BUY from Monday is +3.8%").
- If a signal repeats an open call from the track record, keep the same ticker and action so it counts as reaffirmed, and say why it still holds.
- Learn from losses: put what the track record taught you in `lessons` (empty string if nothing to learn).
- Do NOT repeat a losing call without new justification; if you do, state the new catalyst.
- List continuing multi-day stories in `continuing_stories` (e.g. "defense rally, day 4") using the recent and weekly digests.
- Never invent past calls: only use the memory provided below."""

ANALYSIS_HUMAN = """## Coverage
{coverage}

## Prices (sorted by performance)
{prices}

## News
{news}

## Track record (open + recent closed signals)
{track_record}

## Recent alert digests
{recent_digests}

## Weekly digests (older history)
{weekly_digests}

## Ticker notes
{ticker_notes}

## Validation feedback from your previous attempt
{validation_feedback}"""

DIGEST_SYSTEM = "You compress stock alerts into a compact memory digest for future runs."
DIGEST_HUMAN = "Summarize this alert in 2-3 sentences and 3-5 short theme tags (e.g. 'defense rally', 'AI capex').\n\n{alert}"

WEEKLY_SYSTEM = "You compress several stock-alert digests from one week into one short memory summary."
WEEKLY_HUMAN = "Write a summary of at most 3 sentences covering the key themes, big moves and calls.\n\n{text}"


def build_analysis_chain(llm=None):
    """prompt | llm.with_structured_output(MarketAlert) → MarketAlert"""
    llm = llm or get_llm()
    prompt = ChatPromptTemplate.from_messages([("system", ANALYSIS_SYSTEM), ("human", ANALYSIS_HUMAN)])
    return prompt | llm.with_structured_output(MarketAlert)


def build_digest_chain(llm=None):
    """prompt | llm.with_structured_output(DailyDigest) → DailyDigest"""
    llm = llm or get_llm()
    prompt = ChatPromptTemplate.from_messages([("system", DIGEST_SYSTEM), ("human", DIGEST_HUMAN)])
    return prompt | llm.with_structured_output(DailyDigest)


def build_weekly_chain(llm=None):
    """prompt | llm | StrOutputParser → str"""
    llm = llm or get_llm()
    prompt = ChatPromptTemplate.from_messages([("system", WEEKLY_SYSTEM), ("human", WEEKLY_HUMAN)])
    return prompt | llm | StrOutputParser()
