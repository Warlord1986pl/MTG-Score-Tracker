from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.card_tools import build_card_shortcuts, build_decklist_autocomplete_terms, extract_card_names_from_decklist


class CardToolTests(unittest.TestCase):
    def test_extract_card_names_from_decklist_preserves_main_and_sideboard_cards(self) -> None:
        decklist = """
### Zoo_OBM_v1.0

#### Mainboard

4 Orcish Bowmasters
4 Leyline of the Guildpact
2 Stubborn Denial

#### Sideboard

2 Wear / Tear
4 Consign to Memory
"""
        names = extract_card_names_from_decklist(decklist)

        self.assertIn("Orcish Bowmasters", names)
        self.assertIn("Leyline of the Guildpact", names)
        self.assertIn("Consign to Memory", names)

    def test_build_card_shortcuts_includes_deck_relevant_aliases(self) -> None:
        shortcuts = build_card_shortcuts(
            [
                "Orcish Bowmasters",
                "Leyline of the Guildpact",
                "Consign to Memory",
                "Teferi, Time Raveler",
            ]
        )

        self.assertEqual(shortcuts["OBM"], "Orcish Bowmasters")
        self.assertEqual(shortcuts["LOTG"], "Leyline of the Guildpact")
        self.assertEqual(shortcuts["CTM"], "Consign to Memory")
        self.assertEqual(shortcuts["T3F"], "Teferi, Time Raveler")

    def test_build_decklist_autocomplete_terms_prioritizes_current_deck_cards(self) -> None:
        decklist = """
4 Orcish Bowmasters
4 Ragavan, Nimble Pilferer

Sideboard
2 Damping Sphere
4 Consign to Memory
"""
        terms = build_decklist_autocomplete_terms(decklist)

        self.assertIn("Orcish Bowmasters", terms)
        self.assertIn("OBM", terms)
        self.assertIn("+2 Damping Sphere", terms)
        self.assertIn("-2 Ragavan, Nimble Pilferer", terms)
        self.assertIn("+4 Consign to Memory", terms)


if __name__ == "__main__":
    unittest.main()
