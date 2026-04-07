from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .models import (
    GameInput,
    GlobalStats,
    LeagueCreateInput,
    LeagueCreateResult,
    LeagueMeta,
    MatchCreateInput,
    MatchCreateResult,
    MatchMeta,
    utc_now_iso,
)


class FileStorageService:
    """File-first storage service for the MVP.

    Responsibilities:
    - Bootstrap required data files/folders.
    - Generate stable IDs for leagues and matches.
    - Persist league and match files with atomic writes.
    """

    MATCH_ID_PREFIX = "M"

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)
        self.data_root = self.repo_root / "data"
        self.leagues_root = self.data_root / "leagues"
        self.global_root = self.data_root / "global"
        self.config_root = self.data_root / "config"

    def bootstrap(self) -> None:
        self.leagues_root.mkdir(parents=True, exist_ok=True)
        self.global_root.mkdir(parents=True, exist_ok=True)
        self.config_root.mkdir(parents=True, exist_ok=True)

        stats_path = self.global_root / "stats.json"
        history_path = self.global_root / "history.md"
        decks_path = self.config_root / "decks.json"
        app_settings_path = self.config_root / "app_settings.json"

        if not stats_path.exists():
            self.write_json_atomic(stats_path, asdict(GlobalStats()))

        if not history_path.exists():
            self.write_text_atomic(history_path, "# League History\n\n")

        if not decks_path.exists():
            self.write_json_atomic(decks_path, {})

        if not app_settings_path.exists():
            self.write_json_atomic(
                app_settings_path,
                {
                    "default_deck": "Zoo",
                    "default_format": "Modern",
                    "last_event_type": "League MTGO",
                    "active_league_path": "",
                    "last_game_defaults": {
                        "play_draw": "Play",
                        "hand_7": "Good",
                        "hand_6": "Good",
                        "hand_5": "Good",
                        "hand_4": "Good",
                        "hand_3": "Good",
                        "mulligan_suggested": False,
                        "mulligan_count": 0,
                        "opening_hand_size": 7,
                        "draw_type": "Normal",
                        "result": "Win",
                    },
                },
            )

    def get_app_settings(self) -> dict[str, Any]:
        self.bootstrap()
        settings_path = self.config_root / "app_settings.json"
        settings = self.read_json(settings_path)
        if not isinstance(settings, dict):
            return {}
        return settings

    def save_app_settings(self, settings: dict[str, Any]) -> None:
        existing = self.get_app_settings()
        merged = existing.copy()
        merged.update(settings)
        self.write_json_atomic(self.config_root / "app_settings.json", merged)

    @staticmethod
    def _clean_string_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []

        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in values:
            item = str(raw).strip()
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            cleaned.append(item)
        return cleaned

    def get_option_list(self, key: str, default_options: list[str]) -> list[str]:
        settings = self.get_app_settings()
        cleaned = self._clean_string_list(settings.get(key))
        return cleaned or list(default_options)

    def save_option_list(self, key: str, options: list[str]) -> None:
        settings = self.get_app_settings()
        settings[key] = self._clean_string_list(options)
        self.write_json_atomic(self.config_root / "app_settings.json", settings)

    def calculate_swiss_rounds(self, players: int) -> int:
        player_count = max(2, int(players or 0))
        if player_count <= 8:
            return 3
        if player_count <= 16:
            return 4
        if player_count <= 32:
            return 5
        if player_count <= 64:
            return 6
        if player_count <= 128:
            return 7
        if player_count <= 226:
            return 8
        if player_count <= 409:
            return 9

        import math
        return max(1, math.ceil(math.log2(player_count)))

    def calculate_max_matches(self, tournament_structure: dict | None) -> int | None:
        """Calculate max matches based on tournament structure."""
        if not tournament_structure:
            return None

        tournament_type = str(tournament_structure.get("type", "Swiss")).strip() or "Swiss"
        rounds = int(tournament_structure.get("rounds", 0) or 0)
        players = int(tournament_structure.get("players", 0) or 0)
        has_top_8 = bool(tournament_structure.get("has_top_8", False))

        if tournament_type == "Swiss":
            max_matches = rounds if rounds > 0 else self.calculate_swiss_rounds(players)
            if has_top_8:
                # Top 8 is typically 3 games (quarterfinals, semifinals, final)
                max_matches += 3
            return max_matches
        if tournament_type == "Single Elimination":
            import math

            rounds_needed = max(1, math.ceil(math.log2(max(2, players or 2))))
            return rounds_needed if not has_top_8 else rounds_needed + 1

        return None

    def get_tournament_state(self, league_path: str | Path) -> dict[str, Any]:
        league_dir = Path(league_path)
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing league metadata: {meta_path}")

        meta = self.read_json(meta_path)
        structure = meta.get("tournament_structure") if isinstance(meta.get("tournament_structure"), dict) else {}
        progress = meta.get("tournament_progress") if isinstance(meta.get("tournament_progress"), dict) else {}

        swiss_rounds = int(structure.get("rounds", 0) or 0)
        has_top_8 = bool(structure.get("has_top_8", False))
        phase = str(progress.get("phase", "swiss")).strip() or "swiss"
        swiss_played = int(progress.get("swiss_played", 0) or 0)
        top8_played = int(progress.get("top8_played", 0) or 0)
        qualified_top8_raw = progress.get("qualified_top8", None)
        qualified_top8 = qualified_top8_raw if isinstance(qualified_top8_raw, bool) else None
        eliminated = bool(progress.get("eliminated", False))

        if swiss_rounds <= 0:
            max_matches = meta.get("max_matches")
            max_matches_int = int(max_matches) if isinstance(max_matches, int) else None
            return {
                "phase": "none",
                "swiss_rounds": 0,
                "swiss_played": int(meta.get("matches_count", 0) or 0),
                "has_top_8": False,
                "qualified_top8": None,
                "top8_played": 0,
                "eliminated": False,
                "requires_top8_decision": False,
                "max_matches": max_matches_int,
            }

        requires_top8_decision = (
            has_top_8
            and swiss_played >= swiss_rounds
            and qualified_top8 is None
            and phase == "swiss"
        )

        if phase == "top8":
            max_matches = swiss_rounds + 3
        else:
            max_matches = swiss_rounds

        return {
            "phase": phase,
            "swiss_rounds": swiss_rounds,
            "swiss_played": swiss_played,
            "has_top_8": has_top_8,
            "qualified_top8": qualified_top8,
            "top8_played": top8_played,
            "eliminated": eliminated,
            "requires_top8_decision": requires_top8_decision,
            "max_matches": max_matches,
        }

    def set_top8_qualification(self, league_path: str | Path, qualified: bool) -> dict[str, Any]:
        league_dir = Path(league_path)
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing league metadata: {meta_path}")

        meta = self.read_json(meta_path)
        structure = meta.get("tournament_structure") if isinstance(meta.get("tournament_structure"), dict) else {}
        progress = meta.get("tournament_progress") if isinstance(meta.get("tournament_progress"), dict) else {}

        if not bool(structure.get("has_top_8", False)):
            return self.get_tournament_state(league_dir)

        progress["qualified_top8"] = bool(qualified)
        progress["phase"] = "top8" if qualified else "completed"
        progress.setdefault("swiss_played", int(progress.get("swiss_played", 0) or 0))
        progress.setdefault("top8_played", int(progress.get("top8_played", 0) or 0))
        progress.setdefault("eliminated", False)
        meta["tournament_progress"] = progress

        if qualified:
            swiss_rounds = int(structure.get("rounds", 0) or 0)
            meta["max_matches"] = swiss_rounds + 3 if swiss_rounds > 0 else meta.get("max_matches")
        else:
            meta["status"] = "completed"
            swiss_rounds = int(structure.get("rounds", 0) or 0)
            if swiss_rounds > 0:
                meta["max_matches"] = swiss_rounds

        self.write_json_atomic(meta_path, meta)
        return self.get_tournament_state(league_dir)

    def can_add_match(self, league_path: str | Path) -> dict[str, Any]:
        state = self.get_tournament_state(league_path)
        phase = state.get("phase")

        if phase == "completed" or state.get("eliminated"):
            return {
                "allowed": False,
                "reason": "Tournament is already completed.",
                "requires_top8_decision": False,
                "state": state,
            }

        if state.get("requires_top8_decision"):
            return {
                "allowed": False,
                "reason": "Swiss rounds are complete. Choose whether you made Top 8.",
                "requires_top8_decision": True,
                "state": state,
            }

        max_matches = state.get("max_matches")
        if isinstance(max_matches, int):
            swiss_played = int(state.get("swiss_played", 0) or 0)
            top8_played = int(state.get("top8_played", 0) or 0)
            total_played = swiss_played + top8_played
            if total_played >= max_matches:
                return {
                    "allowed": False,
                    "reason": f"Tournament match limit reached ({total_played}/{max_matches}).",
                    "requires_top8_decision": False,
                    "state": state,
                }

        return {
            "allowed": True,
            "reason": "",
            "requires_top8_decision": False,
            "state": state,
        }

    def set_active_league_path(self, league_path: str | Path) -> None:
        league_dir = Path(league_path)
        if not (league_dir / "meta.json").exists():
            raise FileNotFoundError(f"Invalid league path: {league_dir}")
        self.save_app_settings({"active_league_path": str(league_dir)})

    def get_active_league_path(self) -> str | None:
        settings = self.get_app_settings()
        path_value = str(settings.get("active_league_path", "")).strip()
        if not path_value:
            return None
        league_dir = Path(path_value)
        if not (league_dir / "meta.json").exists():
            return None
        return str(league_dir)

    def clear_active_league_path(self) -> None:
        self.save_app_settings({"active_league_path": ""})

    def list_leagues(self) -> list[dict[str, Any]]:
        leagues: list[dict[str, Any]] = []
        for meta_path in sorted(self.leagues_root.rglob("meta.json"), reverse=True):
            league_dir = meta_path.parent
            meta = self.read_json(meta_path)

            relative = league_dir.relative_to(self.leagues_root)
            parts = relative.parts
            month_key = parts[0] if len(parts) > 0 else ""
            event_key = parts[1] if len(parts) > 1 else str(meta.get("event_type", "")).strip()

            leagues.append(
                {
                    "path": str(league_dir),
                    "relative_path": str(relative),
                    "month": month_key,
                    "event_folder": event_key,
                    "league_id": str(meta.get("league_id", "")),
                    "date": str(meta.get("date", "")),
                    "event_type": str(meta.get("event_type", "")),
                    "deck_name": str(meta.get("deck_name", "")),
                    "deck_archetype": str(meta.get("deck_archetype", "")),
                    "status": str(meta.get("status", "active")),
                    "wins": int(meta.get("wins", 0)),
                    "losses": int(meta.get("losses", 0)),
                    "matches_count": int(meta.get("matches_count", 0)),
                }
            )

        return leagues

    def get_league_snapshot(self, league_path: str | Path) -> dict[str, Any]:
        league_dir = Path(league_path)
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing league metadata: {meta_path}")

        meta = self.read_json(meta_path)
        matches = self._load_match_summaries(league_dir)
        stats = self._compute_league_stats(matches)
        return {
            "meta": meta,
            "stats": stats,
            "matches": matches,
            "decklist": self._read_decklist_markdown(league_dir),
            "path": str(league_dir),
        }

    def update_match_notes(
        self,
        league_path: str | Path,
        match_id: str,
        sideboard_notes: str,
        key_moments: str,
        observations: str,
    ) -> None:
        league_dir = Path(league_path)
        matches_dir = league_dir / "matches"
        if not matches_dir.exists():
            raise FileNotFoundError(f"Missing matches directory: {matches_dir}")

        target_path: Path | None = None
        parsed_match: dict[str, Any] | None = None

        for match_path in sorted(matches_dir.glob("*.md"), key=self._match_sort_key):
            text = match_path.read_text(encoding="utf-8")
            parsed = self._parse_match_markdown(text)
            if str(parsed.get("match_id", "")).strip() == match_id.strip():
                target_path = match_path
                parsed_match = parsed
                break

        if target_path is None or parsed_match is None:
            raise FileNotFoundError(f"Match not found: {match_id}")

        games: list[GameInput] = []
        for g in parsed_match.get("games", []):
            hand_sequence = list(g.get("hand_sequence", []))
            if not hand_sequence:
                hand_sequence = [str(g.get("hand_type", "Good"))]

            games.append(
                GameInput(
                    game_no=int(g.get("game_no", 1)),
                    play_draw=str(g.get("play_draw", "Play")),
                    hand_type=str(g.get("hand_type", "Good")),
                    hand_sequence=[str(x) for x in hand_sequence],
                    mulligan_suggested=bool(g.get("mulligan_suggested", False)),
                    mulligan_count=int(g.get("mulligan_count", 0)),
                    opening_hand_size=int(g.get("opening_hand_size", 7)),
                    draw_type=str(g.get("draw_type", "Normal")),
                    result=str(g.get("result", "Loss")),
                )
            )

        payload = MatchCreateInput(
            opponent_deck=str(parsed_match.get("opponent_deck", "")),
            opponent_archetype=str(parsed_match.get("opponent_archetype", "")),
            score=str(parsed_match.get("score", "2-1")),
            games=games,
            sideboard_notes=sideboard_notes.strip(),
            key_moments=key_moments.strip(),
            observations=observations.strip(),
        )

        meta = MatchMeta(
            match_id=str(parsed_match.get("match_id", match_id)),
            opponent_deck=str(parsed_match.get("opponent_deck", "")),
            opponent_archetype=str(parsed_match.get("opponent_archetype", "")),
            score=str(parsed_match.get("score", "2-1")),
            match_result=str(parsed_match.get("match_result", "loss")),
            had_mulligan=bool(parsed_match.get("had_mulligan", False)),
            heavy_mulligan=bool(parsed_match.get("heavy_mulligan", False)),
            created_at=str(parsed_match.get("created_at", "")) or utc_now_iso(),
        )

        self.write_text_atomic(target_path, self._render_match_markdown(meta, payload))
        self.refresh_league_report(league_dir)
        self.refresh_global_stats()

    def create_league(self, payload: LeagueCreateInput) -> LeagueCreateResult:
        self.bootstrap()

        league_date = payload.date_yyyy_mm_dd or date.today().isoformat()
        month_key = league_date[:7]
        month_path = self.leagues_root / month_key
        month_path.mkdir(parents=True, exist_ok=True)

        event_folder = self._event_type_folder_name(payload.event_type)
        event_path = month_path / event_folder
        event_path.mkdir(parents=True, exist_ok=True)

        league_id = self._next_numeric_directory_id(event_path)
        league_path = event_path / league_id
        matches_path = league_path / "matches"

        matches_path.mkdir(parents=True, exist_ok=False)

        meta = LeagueMeta(
            league_id=league_id,
            created_at=utc_now_iso(),
            date=league_date,
            event_type=payload.event_type,
            format=payload.format_name,
            deck_name=payload.deck_name,
            deck_archetype=payload.deck_archetype,
            deck_list_name=payload.deck_list_name.strip(),
            moxfield_url=payload.moxfield_url,
            status="active",
            matches_count=0,
            wins=0,
            losses=0,
            tournament_structure=payload.tournament_structure,
        )

        meta_payload = asdict(meta)
        meta_payload["deck_context"] = {
            "changes": payload.changes.strip(),
            "goal": payload.goal.strip(),
            "concerns": payload.concerns.strip(),
            "notes": payload.notes.strip(),
        }
        meta_payload["deck_list_source"] = payload.deck_list_source.strip()
        structure = payload.tournament_structure if isinstance(payload.tournament_structure, dict) else {}
        swiss_rounds = int(structure.get("rounds", 0) or 0)
        has_top_8 = bool(structure.get("has_top_8", False))
        meta_payload["max_matches"] = swiss_rounds if swiss_rounds > 0 else self.calculate_max_matches(payload.tournament_structure)
        meta_payload["tournament_progress"] = {
            "phase": "swiss" if swiss_rounds > 0 else "none",
            "swiss_played": 0,
            "top8_played": 0,
            "qualified_top8": None if has_top_8 else False,
            "eliminated": False,
        }

        self.write_json_atomic(league_path / "meta.json", meta_payload)
        self.write_text_atomic(
            league_path / "league.md",
            self._render_league_markdown(meta=meta, payload=payload),
        )
        if payload.deck_list_content.strip():
            self.write_text_atomic(
                league_path / "decklist.md",
                self._render_decklist_markdown(
                    payload.deck_list_name.strip() or f"{payload.deck_name} list",
                    payload.deck_list_content,
                    payload.deck_list_source.strip(),
                ),
            )

        # Remember deck/archetype relation for future autocomplete.
        self.upsert_deck_memory(payload.deck_name, payload.deck_archetype)

        return LeagueCreateResult(
            league_path=str(league_path),
            league_id=league_id,
            meta=meta,
        )

    def create_match(
        self,
        league_path: str | Path,
        payload: MatchCreateInput,
    ) -> MatchCreateResult:
        league_dir = Path(league_path)
        matches_path = league_dir / "matches"
        matches_path.mkdir(parents=True, exist_ok=True)

        match_id = self._next_match_id(matches_path)
        opponent_slug = self._slugify_for_filename(payload.opponent_deck)
        match_file_path = matches_path / f"{match_id}_vs_{opponent_slug}.md"

        match_result = "win" if payload.score in {"2-0", "2-1"} else "loss"
        had_mulligan = any(game.mulligan_count > 0 for game in payload.games)
        heavy_mulligan = any(game.opening_hand_size <= 4 for game in payload.games)
        meta = MatchMeta(
            match_id=match_id,
            opponent_deck=payload.opponent_deck,
            opponent_archetype=payload.opponent_archetype,
            score=payload.score,
            match_result=match_result,
            had_mulligan=had_mulligan,
            heavy_mulligan=heavy_mulligan,
            created_at=utc_now_iso(),
        )

        self.write_text_atomic(match_file_path, self._render_match_markdown(meta, payload))
        self._update_league_meta_after_match(league_dir, meta.match_result)
        self._advance_tournament_progress_after_match(league_dir, meta.match_result)
        self.refresh_league_report(league_dir)
        self.refresh_global_stats()

        return MatchCreateResult(
            match_path=str(match_file_path),
            match_id=match_id,
            meta=meta,
        )

    def complete_league(self, league_path: str | Path) -> dict[str, Any]:
        league_dir = Path(league_path)
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing league metadata: {meta_path}")

        self.refresh_league_report(league_dir)
        self.refresh_global_stats()

        meta = self.read_json(meta_path)
        meta["status"] = "completed"
        self.write_json_atomic(meta_path, meta)

        summary = self._build_league_summary(league_dir)
        self._append_history_summary(summary)
        return summary

    def refresh_league_report(self, league_path: str | Path) -> None:
        league_dir = Path(league_path)
        meta_path = league_dir / "meta.json"
        league_md_path = league_dir / "league.md"
        if not meta_path.exists() or not league_md_path.exists():
            return

        meta = self.read_json(meta_path)
        context = self._read_league_context(meta, league_md_path)
        matches = self._load_match_summaries(league_dir)
        stats = self._compute_league_stats(matches)

        meta["matches_count"] = stats["matches_count"]
        meta["wins"] = stats["wins"]
        meta["losses"] = stats["losses"]
        self.write_json_atomic(meta_path, meta)

        report = self._render_league_report_from_data(
            meta=meta,
            context=context,
            stats=stats,
            matches=matches,
            decklist=self._read_decklist_markdown(league_dir),
        )
        self.write_text_atomic(league_md_path, report)

    def refresh_global_stats(self) -> None:
        self.bootstrap()

        total_matches = 0
        total_wins = 0
        total_losses = 0

        total_games = 0
        total_games_with_mulligan = 0
        total_games_mana_screw = 0
        total_games_mana_flood = 0

        archetype_results: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})

        for league_meta_path in sorted(self.leagues_root.rglob("meta.json")):
            league_dir = league_meta_path.parent
            if not league_dir.is_dir():
                continue

            matches = self._load_match_summaries(league_dir)
            for match in matches:
                total_matches += 1
                if match["match_result"] == "win":
                    total_wins += 1
                else:
                    total_losses += 1

                archetype = str(match.get("opponent_archetype", "Unknown")).strip() or "Unknown"
                archetype_results[archetype]["total"] += 1
                if match["match_result"] == "win":
                    archetype_results[archetype]["wins"] += 1

                for game in match["games"]:
                    total_games += 1
                    if int(game.get("mulligan_count", 0)) > 0:
                        total_games_with_mulligan += 1

                    draw_type = str(game.get("draw_type", "")).strip()
                    if draw_type == "Mana Screw":
                        total_games_mana_screw += 1
                    elif draw_type == "Mana Flood":
                        total_games_mana_flood += 1

        winrate_by_archetype: dict[str, float] = {}
        for archetype, values in sorted(archetype_results.items()):
            winrate_by_archetype[archetype] = self._percent(values["wins"], values["total"])

        stats_payload = asdict(
            GlobalStats(
                total_matches=total_matches,
                total_wins=total_wins,
                total_losses=total_losses,
                winrate=self._percent(total_wins, total_matches),
                winrate_by_archetype=winrate_by_archetype,
                mulligan_rate=self._percent(total_games_with_mulligan, total_games),
                mana_screw_rate=self._percent(total_games_mana_screw, total_games),
                mana_flood_rate=self._percent(total_games_mana_flood, total_games),
                updated_at=utc_now_iso(),
            )
        )
        self.write_json_atomic(self.global_root / "stats.json", stats_payload)

    def get_last_game_defaults(self) -> dict[str, Any]:
        settings_path = self.config_root / "app_settings.json"
        defaults = {
            "play_draw": "Play",
            "hand_type": "Good",
            "hand_7": "Good",
            "hand_6": "Good",
            "hand_5": "Good",
            "hand_4": "Good",
            "hand_3": "Good",
            "mulligan_suggested": False,
            "mulligan_count": 0,
            "opening_hand_size": 7,
            "draw_type": "Normal",
            "result": "Win",
        }
        if not settings_path.exists():
            return defaults

        settings = self.read_json(settings_path)
        stored = settings.get("last_game_defaults")
        if not isinstance(stored, dict):
            return defaults

        merged = defaults.copy()
        merged.update(stored)
        merged["mulligan_count"] = max(0, min(4, int(merged.get("mulligan_count", 0))))
        merged["opening_hand_size"] = max(3, 7 - merged["mulligan_count"])
        return merged

    def save_last_game_defaults(self, defaults: dict[str, Any]) -> None:
        settings_path = self.config_root / "app_settings.json"
        settings = {} if not settings_path.exists() else self.read_json(settings_path)
        settings["last_game_defaults"] = defaults
        self.write_json_atomic(settings_path, settings)

    def get_archetype_options(self, default_options: list[str]) -> list[str]:
        settings_path = self.config_root / "app_settings.json"
        if not settings_path.exists():
            return default_options

        settings = self.read_json(settings_path)
        stored = settings.get("archetype_options")
        if not isinstance(stored, list):
            return default_options

        seen: set[str] = set()
        merged: list[str] = []
        for item in default_options + [str(x) for x in stored]:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item.strip())
        return merged

    def add_archetype_option(self, archetype: str) -> None:
        archetype = archetype.strip()
        if not archetype:
            return

        settings_path = self.config_root / "app_settings.json"
        settings = {} if not settings_path.exists() else self.read_json(settings_path)
        stored = settings.get("archetype_options")
        options = [str(x).strip() for x in stored] if isinstance(stored, list) else []

        if archetype.lower() not in {x.lower() for x in options if x}:
            options.append(archetype)
            settings["archetype_options"] = options
            self.write_json_atomic(settings_path, settings)

    def get_deck_archetype(self, deck_name: str) -> str | None:
        decks_path = self.config_root / "decks.json"
        if not decks_path.exists():
            return None

        decks = self.read_json(decks_path)
        value = decks.get(deck_name)
        if isinstance(value, str) and value:
            return value
        return None

    def get_all_deck_memory(self) -> dict[str, str]:
        decks_path = self.config_root / "decks.json"
        if not decks_path.exists():
            return {}

        decks = self.read_json(decks_path)
        cleaned: dict[str, str] = {}
        for key, value in decks.items():
            if isinstance(key, str) and isinstance(value, str):
                deck = key.strip()
                archetype = value.strip()
                if deck and archetype:
                    cleaned[deck] = archetype
        return cleaned

    def set_deck_memory(self, deck_memory: dict[str, str]) -> None:
        cleaned: dict[str, str] = {}
        for deck, archetype in deck_memory.items():
            deck_name = str(deck).strip()
            archetype_name = str(archetype).strip()
            if not deck_name or not archetype_name:
                continue
            cleaned[deck_name] = archetype_name

        decks_path = self.config_root / "decks.json"
        self.write_json_atomic(decks_path, cleaned)

    def upsert_deck_memory(self, deck_name: str, archetype: str) -> None:
        decks_path = self.config_root / "decks.json"
        decks = {} if not decks_path.exists() else self.read_json(decks_path)
        decks[deck_name] = archetype
        self.write_json_atomic(decks_path, decks)

    def write_text_atomic(self, path: str | Path, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile("w", delete=False, dir=target.parent, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        tmp_path.replace(target)

    def write_json_atomic(self, path: str | Path, data: dict[str, Any]) -> None:
        self.write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def read_json(self, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def create_data_backup(self, destination_dir: str | Path | None = None) -> str:
        """Create a timestamped zip backup of the whole data folder."""
        self.bootstrap()
        dest_root = Path(destination_dir) if destination_dir else self.repo_root / "data" / "backups"
        dest_root.mkdir(parents=True, exist_ok=True)

        timestamp = utc_now_iso().replace(":", "-")
        archive_base = dest_root / f"data_backup_{timestamp}"
        archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=self.repo_root, base_dir="data")
        return archive_path

    def restore_data_backup(self, backup_zip_path: str | Path, overwrite: bool = True) -> None:
        """Restore data folder from a zip backup created by create_data_backup."""
        backup_path = Path(backup_zip_path)
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup archive not found: {backup_path}")

        restore_tmp = self.repo_root / ".tmp_restore_data"
        if restore_tmp.exists():
            shutil.rmtree(restore_tmp)
        restore_tmp.mkdir(parents=True, exist_ok=True)

        shutil.unpack_archive(str(backup_path), str(restore_tmp), "zip")
        restored_data = restore_tmp / "data"
        if not restored_data.exists() or not restored_data.is_dir():
            shutil.rmtree(restore_tmp, ignore_errors=True)
            raise ValueError("Invalid backup archive structure: missing top-level data folder")

        if self.data_root.exists() and overwrite:
            shutil.rmtree(self.data_root)
        if not self.data_root.exists():
            shutil.copytree(restored_data, self.data_root)
        else:
            # If not overwriting, merge while keeping existing files.
            for source_path in restored_data.rglob("*"):
                relative = source_path.relative_to(restored_data)
                target_path = self.data_root / relative
                if source_path.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                if not target_path.exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)

        shutil.rmtree(restore_tmp, ignore_errors=True)

    def _next_directory_id(self, base_path: Path, prefix: str) -> str:
        max_id = 0
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")

        for item in base_path.iterdir():
            if not item.is_dir():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            max_id = max(max_id, int(match.group(1)))

        return f"{prefix}{max_id + 1:03d}"

    def _next_numeric_directory_id(self, base_path: Path) -> str:
        max_id = 0
        pattern = re.compile(r"^(\d+)$")

        for item in base_path.iterdir():
            if not item.is_dir():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            max_id = max(max_id, int(match.group(1)))

        return f"{max_id + 1:03d}"

    def _next_match_id(self, matches_path: Path) -> str:
        max_id = 0
        legacy_pattern = re.compile(r"^match-(\d+)\.md$", re.IGNORECASE)
        new_pattern = re.compile(r"^M(\d+)(?:_vs_.+)?\.md$", re.IGNORECASE)

        for file_path in matches_path.glob("*.md"):
            legacy_match = legacy_pattern.match(file_path.name)
            if legacy_match:
                max_id = max(max_id, int(legacy_match.group(1)))
                continue

            new_match = new_pattern.match(file_path.name)
            if new_match:
                max_id = max(max_id, int(new_match.group(1)))

        return f"{self.MATCH_ID_PREFIX}{max_id + 1:03d}"

    def _slugify_for_filename(self, raw: str) -> str:
        slug = raw.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug or "unknown_deck"

    def _event_type_folder_name(self, event_type: str) -> str:
        return self._slugify_for_filename(event_type)

    def _update_league_meta_after_match(self, league_dir: Path, match_result: str) -> None:
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            return

        meta = self.read_json(meta_path)
        meta["matches_count"] = int(meta.get("matches_count", 0)) + 1

        if match_result == "win":
            meta["wins"] = int(meta.get("wins", 0)) + 1
        else:
            meta["losses"] = int(meta.get("losses", 0)) + 1

        self.write_json_atomic(meta_path, meta)

    def _advance_tournament_progress_after_match(self, league_dir: Path, match_result: str) -> None:
        meta_path = league_dir / "meta.json"
        if not meta_path.exists():
            return

        meta = self.read_json(meta_path)
        structure = meta.get("tournament_structure") if isinstance(meta.get("tournament_structure"), dict) else {}
        progress = meta.get("tournament_progress") if isinstance(meta.get("tournament_progress"), dict) else {}

        swiss_rounds = int(structure.get("rounds", 0) or 0)
        if swiss_rounds <= 0:
            return

        phase = str(progress.get("phase", "swiss")).strip() or "swiss"
        swiss_played = int(progress.get("swiss_played", 0) or 0)
        top8_played = int(progress.get("top8_played", 0) or 0)
        has_top_8 = bool(structure.get("has_top_8", False))
        qualified_top8_raw = progress.get("qualified_top8", None)
        qualified_top8 = qualified_top8_raw if isinstance(qualified_top8_raw, bool) else None

        if phase == "swiss":
            swiss_played += 1
            progress["swiss_played"] = swiss_played

            if swiss_played >= swiss_rounds:
                if has_top_8 and qualified_top8 is None:
                    # Wait for explicit user decision in UI.
                    progress["phase"] = "swiss"
                elif has_top_8 and qualified_top8 is True:
                    progress["phase"] = "top8"
                    meta["max_matches"] = swiss_rounds + 3
                else:
                    progress["phase"] = "completed"
                    meta["status"] = "completed"
                    meta["max_matches"] = swiss_rounds

        elif phase == "top8":
            top8_played += 1
            progress["top8_played"] = top8_played
            if match_result == "loss":
                progress["phase"] = "completed"
                progress["eliminated"] = True
                meta["status"] = "completed"
            elif top8_played >= 3:
                progress["phase"] = "completed"
                meta["status"] = "completed"

        meta["tournament_progress"] = progress
        self.write_json_atomic(meta_path, meta)

    def _render_league_markdown(self, meta: LeagueMeta, payload: LeagueCreateInput) -> str:
        context = {
            "changes": payload.changes.strip(),
            "goal": payload.goal.strip(),
            "concerns": payload.concerns.strip(),
            "notes": payload.notes.strip(),
        }
        return "\n".join(
            [
                f"# League Report - {meta.league_id}",
                "",
                "## Meta",
                "",
                f"- Date: {meta.date}",
                f"- Event Type: {meta.event_type}",
                f"- Format: {meta.format}",
                f"- Deck: {meta.deck_name}",
                f"- Archetype: {meta.deck_archetype}",
                f"- Deck List Name: {meta.deck_list_name}",
                f"- Moxfield: {meta.moxfield_url}",
                "",
                "## Deck Context",
                "",
                f"- Changes: {self._inline_text(context['changes'])}",
                f"- Goal: {self._inline_text(context['goal'])}",
                f"- Concerns: {self._inline_text(context['concerns'])}",
                f"- Notes: {self._inline_text(context['notes'])}",
                "",
                "## Deck List",
                "",
                "_No decklist attached._",
                "",
                "## Summary Stats",
                "",
                "- Record: 0-0",
                "- Winrate: 0.0%",
                "- Mulligan Rate: 0.0%",
                "- Mana Screw Rate: 0.0%",
                "- Mana Flood Rate: 0.0%",
                "",
                "## Matches",
                "",
                "_No matches logged yet._",
                "",
                "## Key Takeaways",
                "",
                "",
                "## Content Hooks",
                "",
                "",
            ]
        )

    def _render_league_report_from_data(
        self,
        meta: dict[str, Any],
        context: dict[str, str],
        stats: dict[str, Any],
        matches: list[dict[str, Any]],
        decklist: str,
    ) -> str:
        lines = [
            f"# League Report - {meta.get('league_id', 'unknown')}",
            "",
            "## Meta",
            "",
            f"- Date: {meta.get('date', '')}",
            f"- Event Type: {meta.get('event_type', '')}",
            f"- Format: {meta.get('format', '')}",
            f"- Deck: {meta.get('deck_name', '')}",
            f"- Archetype: {meta.get('deck_archetype', '')}",
            f"- Deck List Name: {meta.get('deck_list_name', '')}",
            f"- Moxfield: {meta.get('moxfield_url', '')}",
            f"- Status: {meta.get('status', 'active')}",
            "",
            "## Deck Context",
            "",
            f"- Changes: {self._inline_text(context.get('changes', ''))}",
            f"- Goal: {self._inline_text(context.get('goal', ''))}",
            f"- Concerns: {self._inline_text(context.get('concerns', ''))}",
            f"- Notes: {self._inline_text(context.get('notes', ''))}",
            "",
            "## Deck List",
            "",
        ]

        if decklist.strip():
            lines.extend(decklist.strip().splitlines())
        else:
            lines.append("_No decklist attached._")

        lines.extend(
            [
            "",
            "## Summary Stats",
            "",
            f"- Record: {stats['wins']}-{stats['losses']}",
            f"- Winrate: {stats['winrate']:.1f}%",
            f"- Match Winrate by Archetype: {self._render_kv_percent(stats['winrate_by_archetype'])}",
            f"- Mulligan Rate: {stats['mulligan_rate']:.1f}%",
            f"- Mana Screw Rate: {stats['mana_screw_rate']:.1f}%",
            f"- Mana Flood Rate: {stats['mana_flood_rate']:.1f}%",
            f"- Heavy Mulligan Matches: {stats['heavy_mulligan_matches']}",
            "",
            "## Matches",
            "",
            ]
        )

        if not matches:
            lines.extend(["_No matches logged yet._", ""])
        else:
            for match in matches:
                lines.extend(
                    [
                        f"### {match['match_id']} - {match['score']} ({match['match_result']})",
                        "",
                        f"- Opponent: {match['opponent_deck']} ({match['opponent_archetype']})",
                    ]
                )
                if match["had_mulligan"]:
                    lines.append("- Mulligan Flag: Yes")
                if match["heavy_mulligan"]:
                    lines.append("- Heavy Mulligan Flag: Yes")
                lines.append("- Games:")
                for game in match["games"]:
                    lines.append(
                        "  - "
                        f"G{game['game_no']}: {game['result']}, "
                        f"{game['play_draw']}, Hand {game['opening_hand_size']}, "
                        f"Mulls {game['mulligan_count']}, Draw {game['draw_type']}"
                    )
                lines.append("")

        lines.extend(["## Key Takeaways", "", "", "## Content Hooks", "", ""])
        return "\n".join(lines)

    def _render_match_markdown(self, meta: MatchMeta, payload: MatchCreateInput) -> str:
        games_count = 3 if payload.score in {"2-1", "1-2"} else 2
        games = payload.games[:games_count] if payload.games else self._default_games(games_count)

        lines = [
            f"# Match {meta.match_id}",
            "",
            "## Match Meta",
            "",
            f"- Opponent Deck: {meta.opponent_deck}",
            f"- Opponent Archetype: {meta.opponent_archetype}",
            f"- Score: {meta.score}",
            f"- Match Result: {meta.match_result}",
            f"- Mulligan Match: {'Yes' if meta.had_mulligan else 'No'}",
            f"- Heavy Mulligan Match: {'Yes' if meta.heavy_mulligan else 'No'}",
            "",
            "## Games",
            "",
        ]

        for game in games:
            hand_labels = ["7", "6", "5", "4", "3-"]
            hand_path = " -> ".join(
                [f"{hand_labels[idx]}:{hand}" for idx, hand in enumerate(game.hand_sequence)]
            )
            lines.extend(
                [
                    f"### Game {game.game_no}",
                    "",
                    f"- Play/Draw: {game.play_draw}",
                    f"- Kept Hand Type: {game.hand_type}",
                    f"- Hand Sequence: {hand_path}",
                    f"- Mulligan Suggested: {'Yes' if game.mulligan_suggested else 'No'}",
                    f"- Mulligan Count: {game.mulligan_count}",
                    f"- Opening Hand Size: {game.opening_hand_size}",
                    f"- Draw Type: {game.draw_type}",
                    f"- Result: {game.result}",
                    "",
                ]
            )

        lines.extend(
            [
                "## Notes",
                "",
                f"- Sideboarding: {payload.sideboard_notes}",
                f"- Key Moments: {payload.key_moments}",
                f"- Observations: {payload.observations}",
                "",
            ]
        )

        return "\n".join(lines)

    def _read_league_context(self, meta: dict[str, Any], league_md_path: Path) -> dict[str, str]:
        context = meta.get("deck_context")
        if isinstance(context, dict):
            return {
                "changes": str(context.get("changes", "")).strip(),
                "goal": str(context.get("goal", "")).strip(),
                "concerns": str(context.get("concerns", "")).strip(),
                "notes": str(context.get("notes", "")).strip(),
            }

        text = league_md_path.read_text(encoding="utf-8")
        return {
            "changes": self._extract_line_value(text, r"^- Changes:\s*(.*)$"),
            "goal": self._extract_line_value(text, r"^- Goal:\s*(.*)$"),
            "concerns": self._extract_line_value(text, r"^- Concerns:\s*(.*)$"),
            "notes": self._extract_line_value(text, r"^- Notes:\s*(.*)$"),
        }

    def _read_decklist_markdown(self, league_dir: Path) -> str:
        decklist_path = league_dir / "decklist.md"
        if not decklist_path.exists():
            return ""
        return decklist_path.read_text(encoding="utf-8")

    def _render_decklist_markdown(self, deck_list_name: str, raw_content: str, source: str) -> str:
        source_line = f"- Source: {source}" if source else "- Source: manual"
        normalized = self._normalize_decklist(raw_content)
        return "\n".join(
            [
                f"### {deck_list_name}",
                "",
                source_line,
                "",
                "#### Mainboard",
                "",
                *normalized["main"],
                "",
                "#### Sideboard",
                "",
                *normalized["side"],
                "",
            ]
        )

    def _normalize_decklist(self, raw_content: str) -> dict[str, list[str]]:
        lines = [line.rstrip() for line in raw_content.replace("\r\n", "\n").split("\n")]
        main: list[str] = []
        side: list[str] = []
        target = main
        switched = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if target is main and not switched and main:
                    target = side
                    switched = True
                continue

            lower = stripped.lower()
            if lower in {"sideboard", "// sideboard"}:
                target = side
                switched = True
                continue
            if lower.startswith("sb:"):
                target = side
                switched = True
                stripped = stripped[3:].strip()
                if not stripped:
                    continue

            target.append(stripped)

        if not main:
            main = ["_empty_"]
        if not side:
            side = ["_empty_"]

        return {"main": main, "side": side}

    def _load_match_summaries(self, league_dir: Path) -> list[dict[str, Any]]:
        matches_dir = league_dir / "matches"
        if not matches_dir.exists():
            return []

        matches: list[dict[str, Any]] = []
        for match_path in sorted(matches_dir.glob("*.md"), key=self._match_sort_key):
            text = match_path.read_text(encoding="utf-8")
            parsed = self._parse_match_markdown(text)
            parsed["source_path"] = str(match_path)
            matches.append(parsed)
        return matches

    def _match_sort_key(self, match_path: Path) -> tuple[int, str]:
        name = match_path.name
        legacy_match = re.match(r"^match-(\d+)\.md$", name, re.IGNORECASE)
        if legacy_match:
            return int(legacy_match.group(1)), name

        new_match = re.match(r"^M(\d+)(?:_vs_.+)?\.md$", name, re.IGNORECASE)
        if new_match:
            return int(new_match.group(1)), name

        return 10**9, name

    def _parse_match_markdown(self, text: str) -> dict[str, Any]:
        match_id = self._extract_line_value(text, r"^# Match\s+([^\n]+)$")
        opponent_deck = self._extract_line_value(text, r"^- Opponent Deck:\s*(.*)$")
        opponent_archetype = self._extract_line_value(text, r"^- Opponent Archetype:\s*(.*)$")
        score = self._extract_line_value(text, r"^- Score:\s*(.*)$")
        match_result = self._extract_line_value(text, r"^- Match Result:\s*(.*)$", default="loss")
        had_mulligan = self._extract_line_value(text, r"^- Mulligan Match:\s*(.*)$", default="No") == "Yes"
        heavy_mulligan = (
            self._extract_line_value(text, r"^- Heavy Mulligan Match:\s*(.*)$", default="No") == "Yes"
        )
        sideboard_notes = self._extract_line_value(text, r"^- Sideboarding:\s*(.*)$")
        key_moments = self._extract_line_value(text, r"^- Key Moments:\s*(.*)$")
        observations = self._extract_line_value(text, r"^- Observations:\s*(.*)$")

        games: list[dict[str, Any]] = []
        for game_match in re.finditer(r"^### Game\s+(\d+)\s*$([\s\S]*?)(?=^### Game\s+\d+\s*$|\Z)", text, re.MULTILINE):
            game_no = int(game_match.group(1))
            block = game_match.group(2)
            games.append(
                {
                    "game_no": game_no,
                    "play_draw": self._extract_line_value(block, r"^- Play/Draw:\s*(.*)$", default="Play"),
                    "hand_type": self._extract_line_value(block, r"^- Kept Hand Type:\s*(.*)$", default="Good"),
                    "hand_sequence": self._parse_hand_sequence(
                        self._extract_line_value(block, r"^- Hand Sequence:\s*(.*)$", default="")
                    ),
                    "mulligan_suggested": (
                        self._extract_line_value(block, r"^- Mulligan Suggested:\s*(.*)$", default="No") == "Yes"
                    ),
                    "draw_type": self._extract_line_value(block, r"^- Draw Type:\s*(.*)$", default="Normal"),
                    "result": self._extract_line_value(block, r"^- Result:\s*(.*)$", default="Loss"),
                    "mulligan_count": int(
                        self._extract_line_value(block, r"^- Mulligan Count:\s*(\d+)$", default="0")
                    ),
                    "opening_hand_size": int(
                        self._extract_line_value(block, r"^- Opening Hand Size:\s*(\d+)$", default="7")
                    ),
                }
            )

        return {
            "match_id": match_id,
            "opponent_deck": opponent_deck,
            "opponent_archetype": opponent_archetype,
            "score": score,
            "match_result": match_result,
            "had_mulligan": had_mulligan,
            "heavy_mulligan": heavy_mulligan,
            "sideboard_notes": sideboard_notes,
            "key_moments": key_moments,
            "observations": observations,
            "games": games,
        }

    def _parse_hand_sequence(self, raw: str) -> list[str]:
        if not raw.strip():
            return []

        sequence: list[str] = []
        for part in raw.split("->"):
            token = part.strip()
            if not token:
                continue
            if ":" in token:
                _, value = token.split(":", 1)
                value = value.strip()
                if value:
                    sequence.append(value)
            else:
                sequence.append(token)
        return sequence

    def _compute_league_stats(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        matches_count = len(matches)
        wins = sum(1 for m in matches if m["match_result"] == "win")
        losses = matches_count - wins

        total_games = sum(len(m["games"]) for m in matches)
        games_with_mulligan = sum(1 for m in matches for g in m["games"] if int(g["mulligan_count"]) > 0)
        games_mana_screw = sum(1 for m in matches for g in m["games"] if g["draw_type"] == "Mana Screw")
        games_mana_flood = sum(1 for m in matches for g in m["games"] if g["draw_type"] == "Mana Flood")
        heavy_mulligan_matches = sum(1 for m in matches if m["heavy_mulligan"])

        by_archetype: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})
        for match in matches:
            archetype = str(match.get("opponent_archetype", "Unknown")).strip() or "Unknown"
            by_archetype[archetype]["total"] += 1
            if match["match_result"] == "win":
                by_archetype[archetype]["wins"] += 1

        winrate_by_archetype = {
            archetype: self._percent(values["wins"], values["total"])
            for archetype, values in sorted(by_archetype.items())
        }

        return {
            "matches_count": matches_count,
            "wins": wins,
            "losses": losses,
            "winrate": self._percent(wins, matches_count),
            "winrate_by_archetype": winrate_by_archetype,
            "mulligan_rate": self._percent(games_with_mulligan, total_games),
            "mana_screw_rate": self._percent(games_mana_screw, total_games),
            "mana_flood_rate": self._percent(games_mana_flood, total_games),
            "heavy_mulligan_matches": heavy_mulligan_matches,
        }

    def _build_league_summary(self, league_dir: Path) -> dict[str, Any]:
        meta = self.read_json(league_dir / "meta.json")
        matches = self._load_match_summaries(league_dir)
        stats = self._compute_league_stats(matches)
        event_type = str(meta.get("event_type", ""))
        history_id = (
            f"{meta.get('date', '')}"
            f"-{self._event_type_folder_name(event_type)}"
            f"-{meta.get('league_id', 'unknown')}"
        )
        return {
            "league_id": meta.get("league_id", "unknown"),
            "date": meta.get("date", ""),
            "deck_name": meta.get("deck_name", ""),
            "deck_archetype": meta.get("deck_archetype", ""),
            "event_type": event_type,
            "history_id": history_id,
            "record": f"{stats['wins']}-{stats['losses']}",
            "winrate": stats["winrate"],
            "matches_count": stats["matches_count"],
        }

    def _append_history_summary(self, summary: dict[str, Any]) -> None:
        history_path = self.global_root / "history.md"
        if not history_path.exists():
            self.write_text_atomic(history_path, "# League History\n\n")

        content = history_path.read_text(encoding="utf-8")
        heading = f"## {summary['history_id']}"
        if heading in content:
            return

        lines = [
            heading,
            "",
            f"- Date: {summary['date']}",
            f"- Event Type: {summary['event_type']}",
            f"- Deck: {summary['deck_name']} ({summary['deck_archetype']})",
            f"- Record: {summary['record']}",
            f"- Winrate: {summary['winrate']:.1f}%",
            f"- Matches: {summary['matches_count']}",
            "",
        ]
        self.write_text_atomic(history_path, content + "\n".join(lines))

    def _extract_line_value(self, text: str, pattern: str, default: str = "") -> str:
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            return default
        return match.group(1).strip()

    def _inline_text(self, text: str) -> str:
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
        compact = " | ".join(part.strip() for part in normalized.split("\n") if part.strip())
        return compact or ""

    def _render_kv_percent(self, data: dict[str, float]) -> str:
        if not data:
            return "n/a"
        return ", ".join([f"{key}: {value:.1f}%" for key, value in sorted(data.items())])

    def _percent(self, part: int, whole: int) -> float:
        if whole <= 0:
            return 0.0
        return round((part / whole) * 100.0, 1)

    def _default_games(self, games_count: int) -> list[GameInput]:
        games: list[GameInput] = []
        for idx in range(1, games_count + 1):
            games.append(
                GameInput(
                    game_no=idx,
                    play_draw="Play",
                    hand_type="Good",
                    hand_sequence=["Good"],
                    mulligan_suggested=False,
                    mulligan_count=0,
                    opening_hand_size=7,
                    draw_type="Normal",
                    result="Win",
                )
            )
        return games
