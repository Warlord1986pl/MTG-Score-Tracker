from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import AnalyticsConfig, AnalyticsService, FileStorageService, LeagueCreateInput, MatchCreateInput


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.storage = FileStorageService(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_analysis_generates_core_exports(self) -> None:
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
            MatchCreateInput(opponent_deck="Murktide", opponent_archetype="Tempo", score="2-1"),
        )
        self.storage.create_match(
            league.league_path,
            MatchCreateInput(opponent_deck="Burn", opponent_archetype="Aggro", score="0-2"),
        )

        service = AnalyticsService(self.storage)
        output_dir = self.repo_root / "analysis_out"
        result = service.run_analysis(
            output_dir,
            AnalyticsConfig(time_granularity="week", include_charts=False, projection_rounds=5),
        )

        self.assertEqual(result["summary"]["matches"], 2)
        self.assertTrue((output_dir / "analysis.json").exists())
        self.assertTrue((output_dir / "analysis.md").exists())
        self.assertTrue((output_dir / "overall.csv").exists())
        self.assertTrue((output_dir / "record_projection.csv").exists())

        payload = json.loads((output_dir / "analysis.json").read_text(encoding="utf-8"))
        self.assertIn("overall", payload)
        self.assertIn("trends", payload)
        self.assertIn("record_projection", payload)
        self.assertEqual(len(payload["record_projection"]), 6)  # 0-5 through 5-0

    def test_run_analysis_respects_date_range(self) -> None:
        old_league = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
                date_yyyy_mm_dd="2026-03-15",
            )
        )
        self.storage.create_match(
            old_league.league_path,
            MatchCreateInput(opponent_deck="Deck Old", opponent_archetype="Control", score="2-1"),
        )

        new_league = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
                date_yyyy_mm_dd="2026-04-01",
            )
        )
        self.storage.create_match(
            new_league.league_path,
            MatchCreateInput(opponent_deck="Deck New", opponent_archetype="Combo", score="2-1"),
        )

        service = AnalyticsService(self.storage)
        output_dir = self.repo_root / "analysis_out_date_range"
        result = service.run_analysis(
            output_dir,
            AnalyticsConfig(
                time_granularity="week",
                include_charts=False,
                date_from="2026-04-01",
                date_to="2026-04-30",
            ),
        )

        self.assertEqual(result["summary"]["matches"], 1)
        payload = json.loads((output_dir / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["filters"]["date_from"], "2026-04-01")
        self.assertEqual(payload["filters"]["date_to"], "2026-04-30")

    def test_compare_events_returns_deltas(self) -> None:
        league_event = self.storage.create_league(
            LeagueCreateInput(
                event_type="League",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
                date_yyyy_mm_dd="2026-04-01",
            )
        )
        self.storage.create_match(
            league_event.league_path,
            MatchCreateInput(opponent_deck="A", opponent_archetype="Control", score="2-1"),
        )

        challenge_event = self.storage.create_league(
            LeagueCreateInput(
                event_type="Challenge",
                format_name="Modern",
                deck_name="Zoo",
                deck_archetype="Aggro",
                date_yyyy_mm_dd="2026-04-02",
            )
        )
        self.storage.create_match(
            challenge_event.league_path,
            MatchCreateInput(opponent_deck="B", opponent_archetype="Combo", score="0-2"),
        )

        service = AnalyticsService(self.storage)
        result = service.compare_events("League", "Challenge", "2026-04-01", "2026-04-30")

        self.assertEqual(result["summary_a"]["matches"], 1)
        self.assertEqual(result["summary_b"]["matches"], 1)
        self.assertAlmostEqual(float(result["summary_a"]["winrate_pct"]), 100.0, places=1)
        self.assertAlmostEqual(float(result["summary_b"]["winrate_pct"]), 0.0, places=1)
        self.assertAlmostEqual(float(result["delta"]["winrate_pp"]), 100.0, places=1)


if __name__ == "__main__":
    unittest.main()
