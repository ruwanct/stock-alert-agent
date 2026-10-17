"""
Pydantic schemas — the structured output Claude must return.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Mover(BaseModel):
    ticker: str
    change_pct: float = Field(description="Day change in percent, e.g. 3.2 or -1.8")
    price: float = Field(description="Current price in USD")
    reason: str = Field(description="Short reason for the move, citing news if possible")


class Signal(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL", "WATCH"]
    entry_price: Optional[float] = Field(
        default=None, description="Current price for BUY/SELL; null for WATCH"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str
    sources: list[str] = Field(default_factory=list, description="News sources backing the call")


class SectorHeat(BaseModel):
    sector: str
    direction: Literal["up", "down", "flat"]


class MarketAlert(BaseModel):
    pulse: str = Field(description="One-line market sentiment with direction")
    top_gainers: list[Mover] = Field(default_factory=list)
    top_losers: list[Mover] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    key_news: list[str] = Field(default_factory=list)
    sectors: list[SectorHeat] = Field(default_factory=list)
    continuing_stories: list[str] = Field(
        default_factory=list, description="Multi-day stories, e.g. 'defense rally day 4'"
    )
    lessons: str = Field(default="", description="What past performance taught; may be empty")
    bottom_line: str


class DailyDigest(BaseModel):
    summary: str = Field(description="2-3 sentence summary of this alert")
    themes: list[str] = Field(default_factory=list, description="3-5 short theme tags")
