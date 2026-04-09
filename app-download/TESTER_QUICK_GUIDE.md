# MTG Score Tracker – Tester Quick Guide

## Purpose

`MTG Score Tracker` is a desktop app for recording MTGO league and tournament results in a structured way. It helps the user track match outcomes, mulligans, draw quality, matchup notes, and overall performance over time.

---

## Main Functions

### 1. Tracker tab
This is the main working area.

#### New League
Use this section to create a new league or tournament entry:
- choose the **event type** and **format**
- enter your **deck name** and **archetype**
- optionally add a **Moxfield link**
- import a **decklist** from `.txt` or MTGO `.dek`
- define tournament structure such as **Swiss / Single Elimination**, number of rounds, and Top 8
- add context notes such as:
  - deck changes
  - goals for the event
  - concerns
  - general notes

#### Add Match
Use this section to log each match:
- opponent deck and archetype
- final match score (`2-0`, `2-1`, `1-2`, `0-2`)
- per-game information:
  - play/draw
  - hand quality
  - mulligan count
  - opening hand size
  - draw quality
  - game result
- matchup notes:
  - sideboard plan
  - key moments
  - observations

The app automatically updates the active league record and global stats.

---

### 2. Loaded League tab
This tab is used to review and edit existing data.

You can:
- open a previously created league
- review the decklist and event summary
- edit league context notes
- review saved matches
- update sideboard notes, key moments, and observations

---

### 3. Statistics tab
This tab generates performance summaries from saved data.

It can:
- build a summary of matches, wins, losses, and win rate
- filter by date range and event type
- generate charts and tables
- export results to Markdown / CSV / Excel
- compare two event types against each other

---

### 4. League Browser and data recall
The league browser allows testers to:
- load an existing league
- continue the last active league
- preview a league before loading it
- filter active vs completed leagues
- sort leagues by date or win rate

---

### 5. Deck memory and autocomplete
The app stores deck names and archetypes locally.

This means:
- previously used deck names appear in the dropdown list
- typing in the deck field should show autocomplete suggestions
- new decks/archetypes can be added and reused later
- the `Manage Decks` window lets the user edit the stored list

---

## Suggested Test Flow

A simple testing flow could be:

1. Create a new league.
2. Import a decklist.
3. Add 2–3 matches with different results.
4. Reopen the league and confirm the notes are still there.
5. Check whether deck autocomplete works in both deck fields.
6. Generate a statistics report and confirm the charts/tables open correctly.
7. End the league and verify that the status and history update correctly.

---

## Useful Shortcuts

- `Ctrl+Enter` → add match
- `Alt+W` → set match score to `2-0`
- `Alt+Q` → set match score to `2-1`
- `Alt+E` → set match score to `1-2`
- `Alt+L` → set match score to `0-2`

---

## What to Report During Testing

If something looks wrong, please report:
- what you clicked
- what you expected to happen
- what actually happened
- whether the issue is reproducible
- screenshots or error popups if available

Helpful examples:
- missing deck names in the dropdown
- incorrect saving/loading of league data
- broken charts or export files
- crashes when reopening the app
- UI elements not updating correctly

---

## Data & Privacy

All data is stored locally on the machine. The app does not require an internet connection for normal tracking and statistics workflows.
