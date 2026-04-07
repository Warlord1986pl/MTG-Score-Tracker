from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.models import utc_now_iso


class ModelUtilityTests(unittest.TestCase):
    def test_utc_now_iso_ends_with_z(self) -> None:
        value = utc_now_iso()
        self.assertTrue(value.endswith("Z"))
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
