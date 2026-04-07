"""Entry point for PyInstaller bundle and direct launch."""
import sys
from pathlib import Path

# Ensure project root is on sys.path when running as bundled exe
if getattr(sys, 'frozen', False):
    root = Path(sys._MEIPASS)
    sys.path.insert(0, str(root))

from app.desktop.main import run
run()
