from __future__ import annotations

from pathlib import Path

from app.core import FileStorageService, LeagueCreateInput, MatchCreateInput


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    storage = FileStorageService(repo_root)

    league = storage.create_league(
        LeagueCreateInput(
            event_type="League",
            format_name="Modern",
            deck_name="Zoo",
            deck_archetype="Aggro-Midrange",
            moxfield_url="",
            changes="Mainboard: +2 graveyard hate",
            goal="Maintain positive record vs combo",
            concerns="Blue tempo sideboard games",
            notes="Focus on mulligan discipline",
        )
    )

    storage.create_match(
        league_path=league.league_path,
        payload=MatchCreateInput(
            opponent_deck="Murktide",
            opponent_archetype="Tempo",
            score="2-1",
            sideboard_notes="+3 removal, -2 slow threats",
            key_moments="Won race in game 3",
            observations="On draw felt behind without early threat",
        ),
    )

    print(f"Created league: {league.league_id}")
    print(f"Path: {league.league_path}")


if __name__ == "__main__":
    main()
