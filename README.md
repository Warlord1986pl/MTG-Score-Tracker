# MTG Score Tracker

Desktop app for tracking MTGO match results, leagues, mulligans, draw quality, and matchup notes.

## 🚀 Quick Start

### For end users
Open the `app-download/` folder and download the ready-to-run package or release ZIP.

Run:

- `MTGScoreTracker.exe`

> Keep `MTGScoreTracker.exe`, `_internal/`, and `data/` in the same folder. Do **not** copy only the `.exe` file.

### For testers
A short English tester cheat sheet is available here:

- [`app-download/TESTER_QUICK_GUIDE.md`](app-download/TESTER_QUICK_GUIDE.md)

### For development
The source code lives in `source/`.

```bash
cd source
pip install -r requirements.txt
python run.py
```

## ✨ Features

- Track leagues, matches, and match scores
- Save mulligan data, opening hand quality, and draw quality
- Store sideboard notes, key moments, and matchup observations
- Support `League MTGO`, `FNM/LGS Tournament`, Swiss, and single-elimination structures
- Configure dropdown values such as hand types, draw types, and event types
- Keep all data locally on your machine

## 📁 Repository Layout

| Folder / File | Purpose |
|---|---|
| `app-download/` | Portable app package and release-ready downloads for end users |
| `source/` | Source code, tests, build scripts, templates, and development data |
| `init_repo.bat` | Helper script for initializing the folder as a local git repository |

## 🔨 Build From Source

Requirements:

- Python 3.11+
- `pyinstaller`
- optional: Inno Setup 6 for the Windows installer

```bash
cd source
pip install pyinstaller
build\build.bat
```

Build outputs are placed in `app-download/`.

## 🔒 Data & Privacy

All data is stored locally:

- during development: under `source/data/`
- in the portable app: under `app-download/MTG-Score-Tracker/data/`

No internet connection is required for normal use, and no match data is sent anywhere by default.

## License

MIT
