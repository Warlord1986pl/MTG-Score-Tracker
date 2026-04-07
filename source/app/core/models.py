from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

EventType = str
LeagueStatus = Literal["active", "completed"]
MatchScore = Literal["2-0", "2-1", "1-2", "0-2"]
MatchResult = Literal["win", "loss"]
PlayDraw = Literal["Play", "Draw"]
HandType = str
DrawType = str
GameResult = Literal["Win", "Loss"]


@dataclass(slots=True)
class LeagueCreateInput:
    event_type: EventType
    format_name: str
    deck_name: str
    deck_archetype: str
    moxfield_url: str = ""
    date_yyyy_mm_dd: str = ""
    changes: str = ""
    goal: str = ""
    concerns: str = ""
    notes: str = ""
    deck_list_name: str = ""
    deck_list_content: str = ""
    deck_list_source: str = ""
    tournament_structure: dict | None = None  # {type, players, rounds, has_top_8}


@dataclass(slots=True)
class LeagueMeta:
    league_id: str
    created_at: str
    date: str
    event_type: EventType
    format: str
    deck_name: str
    deck_archetype: str
    deck_list_name: str
    moxfield_url: str
    status: LeagueStatus
    matches_count: int
    wins: int
    losses: int
    tournament_structure: dict | None = None  # {type, players, rounds, has_top_8}


@dataclass(slots=True)
class MatchCreateInput:
    opponent_deck: str
    opponent_archetype: str
    score: MatchScore
    games: list["GameInput"] = field(default_factory=list)
    sideboard_notes: str = ""
    key_moments: str = ""
    observations: str = ""


@dataclass(slots=True)
class GameInput:
    game_no: int
    play_draw: PlayDraw
    hand_type: HandType
    hand_sequence: list[HandType]
    mulligan_suggested: bool
    mulligan_count: int
    opening_hand_size: int
    draw_type: DrawType
    result: GameResult


@dataclass(slots=True)
class MatchMeta:
    match_id: str
    opponent_deck: str
    opponent_archetype: str
    score: MatchScore
    match_result: MatchResult
    had_mulligan: bool
    heavy_mulligan: bool
    created_at: str


@dataclass(slots=True)
class GlobalStats:
    total_matches: int = 0
    total_wins: int = 0
    total_losses: int = 0
    winrate: float = 0.0
    winrate_by_archetype: dict[str, float] = field(default_factory=dict)
    mulligan_rate: float = 0.0
    mana_screw_rate: float = 0.0
    mana_flood_rate: float = 0.0
    updated_at: str | None = None


@dataclass(slots=True)
class LeagueCreateResult:
    league_path: str
    league_id: str
    meta: LeagueMeta


@dataclass(slots=True)
class MatchCreateResult:
    match_path: str
    match_id: str
    meta: MatchMeta


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
