from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Allow importing the app package when tests are run from repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import FileStorageService, LeagueCreateInput, MatchCreateInput


class FileStorageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.storage = FileStorageService(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_creates_required_global_files(self) -> None:
        self.storage.bootstrap()

        stats_path = self.repo_root / "data" / "global" / "stats.json"
        history_path = self.repo_root / "data" / "global" / "history.md"
        decks_path = self.repo_root / "data" / "config" / "decks.json"
        settings_path = self.repo_root / "data" / "config" / "app_settings.json"

        self.assertTrue(stats_path.exists())
        self.assertTrue(history_path.exists())
        self.assertTrue(decks_path.exists())
        self.assertTrue(settings_path.exists())

        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        self.assertEqual(stats["total_matches"], 0)
        self.assertEqual(stats["total_wins"], 0)
        self.assertEqual(stats["total_losses"], 0)

    def test_create_match_updates_league_and_global_stats(self) -> None:
        league = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
            )
        )

        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Murktide",
                opponent_archetype="Tempo",
                score="2-1",
            ),
        )

        league_meta = self.storage.read_json(Path(league.league_path) / "meta.json")
        self.assertEqual(league_meta["matches_count"], 1)
        self.assertEqual(league_meta["wins"], 1)
        self.assertEqual(league_meta["losses"], 0)

        global_stats = self.storage.read_json(self.repo_root / "data" / "global" / "stats.json")
        self.assertEqual(global_stats["total_matches"], 1)
        self.assertEqual(global_stats["total_wins"], 1)
        self.assertEqual(global_stats["total_losses"], 0)

    def test_swiss_to_top8_requires_explicit_decision(self) -> None:
        league = self.storage.create_league(
            LeagueCreateInput(
                event_type="Challenge",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
                tournament_structure={
                    "type": "Swiss",
                    "players": 32,
                    "rounds": 1,
                    "has_top_8": True,
                },
            )
        )

        before = self.storage.can_add_match(league.league_path)
        self.assertTrue(before["allowed"])

        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Amulet Titan",
                opponent_archetype="Combo",
                score="2-1",
            ),
        )

        blocked = self.storage.can_add_match(league.league_path)
        self.assertFalse(blocked["allowed"])
        self.assertTrue(blocked["requires_top8_decision"])

        self.storage.set_top8_qualification(league.league_path, qualified=False)
        after_decline = self.storage.can_add_match(league.league_path)
        self.assertFalse(after_decline["allowed"])
        self.assertIn("completed", after_decline["reason"].lower())

    def test_match_markdown_is_parseable_for_games_and_notes(self) -> None:
        league = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
            )
        )

        result = self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Control Deck",
                opponent_archetype="Control",
                score="2-1",
                sideboard_notes="+2 Veil, -2 Bolt",
                key_moments="Won game 3 on topdeck",
                observations="Matchup close post-board",
            ),
        )

        match_path = Path(result.match_path)
        text = match_path.read_text(encoding="utf-8")
        parsed = self.storage._parse_match_markdown(text)

        self.assertEqual(parsed["score"], "2-1")
        self.assertEqual(parsed["opponent_archetype"], "Control")
        self.assertEqual(len(parsed["games"]), 3)
        self.assertEqual(parsed["sideboard_notes"], "+2 Veil, -2 Bolt")

    def test_global_stats_aggregates_by_archetype(self) -> None:
        league = self.storage.create_league(
            LeagueCreateInput(
                event_type="Challenge",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
            )
        )

        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Deck A",
                opponent_archetype="Control",
                score="2-0",
            ),
        )
        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Deck B",
                opponent_archetype="Control",
                score="0-2",
            ),
        )
        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Deck C",
                opponent_archetype="Combo",
                score="2-1",
            ),
        )

        stats = self.storage.read_json(self.repo_root / "data" / "global" / "stats.json")
        self.assertEqual(stats["total_matches"], 3)
        self.assertEqual(stats["total_wins"], 2)
        self.assertEqual(stats["total_losses"], 1)
        self.assertAlmostEqual(float(stats["winrate_by_archetype"]["Control"]), 50.0, places=1)
        self.assertAlmostEqual(float(stats["winrate_by_archetype"]["Combo"]), 100.0, places=1)

    def test_backup_and_restore_data_folder(self) -> None:
        league = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
            )
        )

        self.storage.create_match(
            league.league_path,
            MatchCreateInput(
                opponent_deck="Murktide",
                opponent_archetype="Tempo",
                score="2-1",
            ),
        )

        backup_zip = self.storage.create_data_backup()
        self.assertTrue(Path(backup_zip).exists())

        data_root = self.repo_root / "data"
        self.assertTrue((data_root / "global" / "stats.json").exists())
        # Simulate accidental data loss.
        shutil.rmtree(data_root / "leagues", ignore_errors=True)
        shutil.rmtree(data_root / "global", ignore_errors=True)
        shutil.rmtree(data_root / "config", ignore_errors=True)
        self.assertFalse((data_root / "leagues").exists())

        self.storage.restore_data_backup(backup_zip, overwrite=True)
        self.assertTrue((data_root / "leagues").exists())
        self.assertTrue((data_root / "global" / "stats.json").exists())


if __name__ == "__main__":
    unittest.main()
