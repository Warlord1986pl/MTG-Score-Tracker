from __future__ import annotations

import csv
import json
import shutil
import sys
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtCore import QEvent, Qt, QStringListModel

from app.core import AnalyticsConfig, AnalyticsService, FileStorageService, GameInput, LeagueCreateInput, MatchCreateInput
from app.core.card_tools import build_decklist_autocomplete_terms


DEFAULT_EVENT_TYPE_OPTIONS = [
    "League MTGO",
    "FNM/LGS Tournament",
    "Challenge",
    "Preliminary",
    "Qualifier",
    "Super Qualifier",
    "Showcase Challenge",
    "Showcase Qualifier",
    "MOCS",
    "Other",
]

DEFAULT_FORMAT_OPTIONS = [
    "Modern",
    "Legacy",
    "Pioneer",
    "Standard",
    "Pauper",
    "Vintage",
    "Commander",
    "Other",
]
DEFAULT_ARCHETYPE_OPTIONS = [
    "Aggro",
    "Midrange",
    "Control",
    "Combo",
    "Tempo",
    "Ramp",
    "Blink",
    "Graveyard",
    "Prison",
    "Other",
]
DEFAULT_HAND_TYPE_OPTIONS = [
    "Good",
    "No Lands",
    "Flood",
    "One Lander",
    "No Interaction",
    "No Threats",
    "Bad Mana",
    "LOTG/Scion",
]
DEFAULT_DRAW_TYPE_OPTIONS = ["Normal", "Mana Screw", "Mana Flood", "Perfect"]
DEFAULT_PLAYER_COUNT_OPTIONS = ["4", "8", "12", "16", "24", "32", "48", "64", "128"]
DEFAULT_AUTO_MULLIGAN_HAND_TYPES = ["No Lands", "Flood"]
HAND_TRACKED_SIZES = [7, 6, 5, 4, 3]
SCORE_TO_GAME_RESULTS = {
    "2-0": ["Win", "Win"],
    "0-2": ["Loss", "Loss"],
}
CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+)$")


class DeckAwareTextEdit(QTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.setPlaceholderText("Decklist-aware autocomplete available. Type a card name or shortcut, or press Ctrl+Space.")

    def set_completion_terms(self, terms: list[str]) -> None:
        self._completion_model.setStringList(list(terms))

    def _text_under_cursor(self) -> str:
        cursor = self.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        return cursor.selectedText().strip()

    def _insert_completion(self, completion: str) -> None:
        if not completion:
            return
        cursor = self.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        cursor.removeSelectedText()
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._completer.popup().isVisible() and event.key() in {Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Escape}:
            event.ignore()
            return

        force_popup = event.key() == Qt.Key_Space and bool(event.modifiers() & Qt.ControlModifier)
        if not force_popup:
            super().keyPressEvent(event)

        if self._completion_model.rowCount() == 0:
            self._completer.popup().hide()
            return

        prefix = self._text_under_cursor()
        if not force_popup and len(prefix) < 2:
            self._completer.popup().hide()
            return

        self._completer.setCompletionPrefix(prefix)
        popup = self._completer.popup()
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(320, popup.sizeHintForColumn(0) + 24))
        self._completer.complete(rect)


class DeckManagerDialog(QDialog):
    def __init__(self, deck_memory: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Decks")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_label = QLabel("Search")
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Filter by deck or archetype...")
        self.search_input.textChanged.connect(self._apply_filter)

        sort_btn = QPushButton("Sort A-Z")
        sort_btn.setToolTip("Sort entries alphabetically by deck name")
        sort_btn.clicked.connect(self._sort_rows)

        import_btn = QPushButton("Import CSV")
        import_btn.setToolTip("Import decks from CSV using deck and archetype columns")
        import_btn.clicked.connect(self._import_csv)

        export_btn = QPushButton("Export CSV")
        export_btn.setToolTip("Export the current deck list to CSV")
        export_btn.clicked.connect(self._export_csv)

        search_row.addWidget(search_label)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(sort_btn)
        search_row.addWidget(import_btn)
        search_row.addWidget(export_btn)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Deck", "Archetype"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        for deck, archetype in sorted(deck_memory.items()):
            self._append_row(deck, archetype)

        layout.addWidget(self.table)

        actions = QHBoxLayout()
        add_row_btn = QPushButton("Add Row")
        add_row_btn.clicked.connect(lambda: self._append_row("", ""))
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_selected_rows)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        actions.addWidget(add_row_btn)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        actions.addWidget(save_btn)
        actions.addWidget(cancel_btn)

        layout.addLayout(actions)

    def _append_row(self, deck: str, archetype: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(deck))
        self.table.setItem(row, 1, QTableWidgetItem(archetype))

    def _delete_selected_rows(self) -> None:
        selected_rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in selected_rows:
            self.table.removeRow(row)

    def _sort_rows(self) -> None:
        self.table.sortItems(0)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for row in range(self.table.rowCount()):
            deck_item = self.table.item(row, 0)
            archetype_item = self.table.item(row, 1)
            deck = deck_item.text().strip().lower() if deck_item else ""
            archetype = archetype_item.text().strip().lower() if archetype_item else ""
            is_visible = not query or query in deck or query in archetype
            self.table.setRowHidden(row, not is_visible)

    def _import_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Decks from CSV",
            str(Path.home()),
            "CSV Files (*.csv)",
        )
        if not file_path:
            return

        added = 0
        updated = 0
        skipped = 0
        with open(file_path, "r", encoding="utf-8", newline="") as csv_file:
            content = csv_file.read()

        try:
            dialect = csv.Sniffer().sniff(content[:2048], delimiters=",;")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if content.count(";") > content.count(",") else ","

        reader = csv.reader(content.splitlines(), delimiter=delimiter)
        rows = list(reader)

        # Supports both with and without header row.
        if rows and len(rows[0]) >= 2:
            maybe_header = [rows[0][0].strip().lower(), rows[0][1].strip().lower()]
            if maybe_header == ["deck", "archetype"]:
                rows = rows[1:]

        existing = self.get_deck_memory()[0]
        key_to_deck = {deck.lower(): deck for deck in existing}
        for row in rows:
            if len(row) < 2:
                skipped += 1
                continue
            deck = row[0].strip()
            archetype = row[1].strip()
            if not deck or not archetype:
                skipped += 1
                continue

            normalized = deck.lower()
            if normalized in key_to_deck:
                target_key = key_to_deck[normalized]
                if existing.get(target_key) != archetype:
                    updated += 1
                existing[target_key] = archetype
            else:
                existing[deck] = archetype
                key_to_deck[normalized] = deck
                added += 1

        self.table.setRowCount(0)
        for deck, archetype in sorted(existing.items()):
            self._append_row(deck, archetype)

        QMessageBox.information(
            self,
            "CSV Import",
            (
                f"Imported with delimiter '{delimiter}'.\n"
                f"Added: {added}\n"
                f"Updated: {updated}\n"
                f"Skipped: {skipped}"
            ),
        )
        self._apply_filter(self.search_input.text())

    def _export_csv(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Decks to CSV",
            str(Path.home() / "decks_export.csv"),
            "CSV Files (*.csv)",
        )
        if not file_path:
            return

        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        data, error_message = self.get_deck_memory()
        if error_message:
            QMessageBox.warning(self, "Cannot export", error_message)
            return

        with open(file_path, "w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["deck", "archetype"])
            for deck, archetype in sorted(data.items()):
                writer.writerow([deck, archetype])

        QMessageBox.information(self, "CSV Export", f"Exported {len(data)} rows.")

    def get_deck_memory(self) -> tuple[dict[str, str], str | None]:
        data: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            deck_item = self.table.item(row, 0)
            archetype_item = self.table.item(row, 1)
            deck = (deck_item.text() if deck_item else "").strip()
            archetype = (archetype_item.text() if archetype_item else "").strip()

            if not deck and not archetype:
                continue
            if not deck:
                return {}, "Deck name cannot be empty."
            if not archetype:
                return {}, f"Archetype cannot be empty for deck: {deck}"

            key = deck.lower()
            if key in {k.lower() for k in data.keys()}:
                return {}, f"Duplicate deck name detected: {deck}"

            data[deck] = archetype

        return data, None


class LeaguePreviewDialog(QDialog):
    def __init__(self, title: str, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 620)

        layout = QVBoxLayout(self)
        viewer = QTextEdit(self)
        viewer.setReadOnly(True)
        viewer.setPlainText(content)
        layout.addWidget(viewer)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)


class ConfigureListsDialog(QDialog):
    def __init__(
        self,
        current_options: dict[str, list[str]],
        default_options: dict[str, list[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Lists")
        self.resize(760, 680)
        self._default_options = default_options
        self._editors: dict[str, QTextEdit] = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Enter one value per line. Remove a line to hide it from dropdowns. "
            "Saved leagues and matches will not be modified."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        fields = [
            ("event_type_options", "Event Types", "Starter values for the event-type dropdown."),
            ("format_options", "Formats", "Starter values for the format dropdown."),
            ("hand_type_options", "Hand Types", "Categories used when evaluating opening hands."),
            ("draw_type_options", "Draw Types", "Categories used for draw-quality notes."),
            ("player_count_options", "Player Counts", "Suggested player-count values for tournaments."),
            (
                "auto_mulligan_hand_types",
                "Auto Mulligan Hand Types",
                "These hand types automatically tick the mulligan suggestion checkbox.",
            ),
        ]

        for key, label, tooltip in fields:
            editor = QTextEdit(self)
            editor.setMinimumHeight(72)
            editor.setPlainText("\n".join(current_options.get(key, [])))
            editor.setToolTip(tooltip)
            form.addRow(label, editor)
            self._editors[key] = editor

        layout.addLayout(form)

        button_row = QHBoxLayout()
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)

        button_row.addWidget(reset_btn)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

    @staticmethod
    def _clean_lines(text: str) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw_line in text.splitlines():
            item = raw_line.strip()
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            cleaned.append(item)
        return cleaned

    def _reset_defaults(self) -> None:
        for key, editor in self._editors.items():
            editor.setPlainText("\n".join(self._default_options.get(key, [])))

    def get_values(self) -> tuple[dict[str, list[str]], str | None]:
        values = {key: self._clean_lines(editor.toPlainText()) for key, editor in self._editors.items()}

        required_labels = {
            "event_type_options": "Event Types",
            "format_options": "Formats",
            "hand_type_options": "Hand Types",
            "draw_type_options": "Draw Types",
            "player_count_options": "Player Counts",
        }
        for key, label in required_labels.items():
            if not values.get(key):
                return {}, f"{label} cannot be empty."

        hand_lookup = {item.lower(): item for item in values["hand_type_options"]}
        filtered_auto = [
            hand_lookup[item.lower()]
            for item in values.get("auto_mulligan_hand_types", [])
            if item.lower() in hand_lookup
        ]
        if not filtered_auto:
            filtered_auto = [
                hand_lookup[item.lower()]
                for item in DEFAULT_AUTO_MULLIGAN_HAND_TYPES
                if item.lower() in hand_lookup
            ]
        values["auto_mulligan_hand_types"] = filtered_auto

        return values, None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MTG Score Tracker")
        self.resize(1100, 780)

        self.repo_root = self._resolve_repo_root()
        self._seed_portable_bundle_defaults()
        self.storage = FileStorageService(self.repo_root)
        self.storage.bootstrap()
        self.analytics = AnalyticsService(self.storage)
        self.event_type_options = self.storage.get_option_list("event_type_options", DEFAULT_EVENT_TYPE_OPTIONS)
        self.format_options = self.storage.get_option_list("format_options", DEFAULT_FORMAT_OPTIONS)
        self.hand_type_options = self.storage.get_option_list("hand_type_options", DEFAULT_HAND_TYPE_OPTIONS)
        self.draw_type_options = self.storage.get_option_list("draw_type_options", DEFAULT_DRAW_TYPE_OPTIONS)
        self.player_count_options = self.storage.get_option_list("player_count_options", DEFAULT_PLAYER_COUNT_OPTIONS)
        self.auto_mulligan_hand_types = set(
            self.storage.get_option_list("auto_mulligan_hand_types", DEFAULT_AUTO_MULLIGAN_HAND_TYPES)
        ) & set(self.hand_type_options)
        if not self.auto_mulligan_hand_types:
            self.auto_mulligan_hand_types = set(DEFAULT_AUTO_MULLIGAN_HAND_TYPES)
        self.archetype_options = self.storage.get_archetype_options(DEFAULT_ARCHETYPE_OPTIONS)

        self.current_league_path: str | None = None
        self.imported_decklist_path: str = ""
        self.imported_decklist_content: str = ""
        self.loaded_leagues_tabs: QTabWidget | None = None
        self.loaded_league_views: dict[str, dict[str, object]] = {}
        self._stats_chart_original = QPixmap()
        self.deck_memory: dict[str, str] = {}
        self.starter_decks_by_format: dict[str, list[str]] = {}
        self._deck_names_model = QStringListModel(self)
        self._deck_autocomplete_terms: list[str] = []

        self._build_ui()
        self._update_tournament_structure_state()
        self._setup_deck_autocomplete()
        self._load_deck_memory()
        self._load_game_defaults()
        self._refresh_text_autocomplete_terms()
        self._refresh_league_selector()
        self._restore_active_league()
        self.score.currentTextChanged.connect(self._on_score_changed)
        self._on_score_changed(self.score.currentText())

    def _setup_deck_autocomplete(self) -> None:
        for combo in (self.deck_name, self.opponent_deck):
            completer = QCompleter(self._deck_names_model, combo)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            combo.setCompleter(completer)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.textEdited.connect(lambda text, target=combo: self._show_deck_suggestions(target, text))

    def _show_deck_suggestions(self, combo: QComboBox, text: str) -> None:
        completer = combo.completer()
        if completer is None:
            return

        query = text.strip()
        if not query:
            completer.popup().hide()
            return

        completer.setCompletionPrefix(query)
        completer.complete()

    def _decklist_text_for_autocomplete(self) -> str:
        if self.imported_decklist_content.strip():
            return self.imported_decklist_content
        if self.current_league_path:
            try:
                snapshot = self.storage.get_league_snapshot(self.current_league_path)
                return str(snapshot.get("decklist", ""))
            except Exception:
                return ""
        return ""

    def _refresh_text_autocomplete_terms(self, decklist_text: str | None = None) -> None:
        source_text = decklist_text if decklist_text is not None else self._decklist_text_for_autocomplete()
        self._deck_autocomplete_terms = build_decklist_autocomplete_terms(source_text)
        for widget in (
            getattr(self, "changes", None),
            getattr(self, "goal", None),
            getattr(self, "concerns", None),
            getattr(self, "notes", None),
            getattr(self, "sideboard_notes", None),
            getattr(self, "key_moments", None),
            getattr(self, "observations", None),
        ):
            if isinstance(widget, DeckAwareTextEdit):
                widget.set_completion_terms(self._deck_autocomplete_terms)

    def _resolve_repo_root(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]

    def _seed_portable_bundle_defaults(self) -> None:
        if not getattr(sys, "frozen", False):
            return

        exe_dir = Path(sys.executable).resolve().parent
        bundled_root = exe_dir / "_internal"
        if not bundled_root.exists():
            return

        for folder_name in ("data", "templates"):
            source = bundled_root / folder_name
            target = exe_dir / folder_name
            if source.exists() and not target.exists():
                shutil.copytree(source, target)

        source_decks = bundled_root / "data" / "config" / "decks.json"
        target_decks = exe_dir / "data" / "config" / "decks.json"
        if source_decks.exists():
            try:
                bundled_data = json.loads(source_decks.read_text(encoding="utf-8"))
            except Exception:
                bundled_data = {}

            current_data: dict[str, str] = {}
            if target_decks.exists():
                try:
                    loaded_current = json.loads(target_decks.read_text(encoding="utf-8"))
                    if isinstance(loaded_current, dict):
                        current_data = loaded_current
                except Exception:
                    current_data = {}

            if isinstance(bundled_data, dict) and len(current_data) < len(bundled_data):
                merged = dict(bundled_data)
                merged.update(current_data)
                target_decks.parent.mkdir(parents=True, exist_ok=True)
                target_decks.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

        settings_path = exe_dir / "data" / "config" / "app_settings.json"
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                settings = {}

            if isinstance(settings, dict):
                current_active = str(settings.get("active_league_path", "")).strip()
                normalized_active = self._normalize_saved_league_path(current_active) or ""
                if normalized_active != current_active:
                    settings["active_league_path"] = normalized_active
                    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _normalize_event_type_label(value: str) -> str:
        return " ".join(str(value).strip().lower().split())

    def _is_mtgo_league_event(self, value: str | None = None) -> bool:
        normalized = self._normalize_event_type_label(value or self.event_type.currentText())
        return normalized in {"league", "league mtgo", "mtgo league"}

    def _is_fnm_lgs_event(self, value: str | None = None) -> bool:
        normalized = self._normalize_event_type_label(value or self.event_type.currentText())
        return normalized in {"fnm/lgs tournament", "fnm tournament", "lgs tournament", "fnm", "lgs"}

    def _default_list_config(self) -> dict[str, list[str]]:
        return {
            "event_type_options": list(DEFAULT_EVENT_TYPE_OPTIONS),
            "format_options": list(DEFAULT_FORMAT_OPTIONS),
            "hand_type_options": list(DEFAULT_HAND_TYPE_OPTIONS),
            "draw_type_options": list(DEFAULT_DRAW_TYPE_OPTIONS),
            "player_count_options": list(DEFAULT_PLAYER_COUNT_OPTIONS),
            "auto_mulligan_hand_types": list(DEFAULT_AUTO_MULLIGAN_HAND_TYPES),
        }

    def _current_list_config(self) -> dict[str, list[str]]:
        return {
            "event_type_options": list(self.event_type_options),
            "format_options": list(self.format_options),
            "hand_type_options": list(self.hand_type_options),
            "draw_type_options": list(self.draw_type_options),
            "player_count_options": list(self.player_count_options),
            "auto_mulligan_hand_types": sorted(self.auto_mulligan_hand_types),
        }

    def _reset_combo_items(self, combo: QComboBox, options: list[str], current_text: str | None = None) -> None:
        text = (current_text if current_text is not None else combo.currentText()).strip()
        was_blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItems(options)
        if text:
            combo.setCurrentText(text)
        combo.blockSignals(was_blocked)

    def _refresh_game_option_dropdowns(self) -> None:
        if not hasattr(self, "game_rows"):
            return

        for row in self.game_rows:
            for hand_input in row["hand_inputs"].values():
                self._reset_combo_items(hand_input, self.hand_type_options)
            self._reset_combo_items(row["draw_type"], self.draw_type_options)
            self._refresh_row_mulligan_state(row)

    def _refresh_configurable_dropdowns(self) -> None:
        if hasattr(self, "event_type"):
            self._reset_combo_items(self.event_type, self.event_type_options)
        if hasattr(self, "format_name"):
            self._reset_combo_items(self.format_name, self.format_options)
        if hasattr(self, "player_count"):
            self._reset_combo_items(self.player_count, self.player_count_options)
        self._refresh_game_option_dropdowns()
        if hasattr(self, "_refresh_deck_choices_for_format"):
            try:
                self._refresh_deck_choices_for_format(self.format_name.currentText())
            except Exception:
                pass
        if hasattr(self, "event_type"):
            self._update_tournament_structure_state()

    def _on_configure_lists(self) -> None:
        dialog = ConfigureListsDialog(self._current_list_config(), self._default_list_config(), self)
        if dialog.exec() != QDialog.Accepted:
            return

        values, error_message = dialog.get_values()
        if error_message:
            QMessageBox.warning(self, "Invalid configuration", error_message)
            return

        for key, options in values.items():
            self.storage.save_option_list(key, options)

        self.event_type_options = values["event_type_options"]
        self.format_options = values["format_options"]
        self.hand_type_options = values["hand_type_options"]
        self.draw_type_options = values["draw_type_options"]
        self.player_count_options = values["player_count_options"]
        self.auto_mulligan_hand_types = set(values["auto_mulligan_hand_types"])
        self._refresh_configurable_dropdowns()
        self._append_status("Updated configurable dropdown lists.")

    def _get_player_count_value(self, default: int = 32) -> int:
        try:
            return max(2, int(str(self.player_count.currentText()).strip()))
        except (TypeError, ValueError):
            return default

    def _calculate_recommended_rounds(self, tournament_type: str, player_count: int) -> int:
        calculated = self.storage.calculate_max_matches(
            {
                "type": tournament_type,
                "players": max(2, player_count),
                "rounds": 0,
                "has_top_8": False,
            }
        )
        return int(calculated or 1)

    def _update_tournament_structure_state(self) -> None:
        if not hasattr(self, "tournament_type"):
            return

        is_league_mtgo = self._is_mtgo_league_event()
        is_fnm_lgs = self._is_fnm_lgs_event()
        tournament_type = self.tournament_type.currentText().strip() or "Swiss"
        player_count = self._get_player_count_value()

        if is_league_mtgo:
            blocked = self.tournament_type.blockSignals(True)
            self.tournament_type.setCurrentText("Swiss")
            self.tournament_type.blockSignals(blocked)
            self.num_rounds.setValue(5)
            self.has_top_8.setChecked(False)
            self.tournament_type.setEnabled(False)
            self.player_count.setEnabled(False)
            self.num_rounds.setEnabled(False)
            self.has_top_8.setEnabled(False)
            self.num_rounds.setToolTip("League MTGO always uses 5 Swiss rounds.")
            return

        self.tournament_type.setEnabled(True)
        self.player_count.setEnabled(True)

        auto_rounds = is_fnm_lgs or tournament_type == "Single Elimination"
        if auto_rounds:
            self.num_rounds.setValue(self._calculate_recommended_rounds(tournament_type, player_count))
            self.num_rounds.setEnabled(False)
            if is_fnm_lgs:
                self.num_rounds.setToolTip("For FNM/LGS Tournament, rounds are calculated automatically from the player count.")
            else:
                self.num_rounds.setToolTip("Single-elimination rounds are calculated automatically from the player count.")
        else:
            self.num_rounds.setEnabled(True)
            self.num_rounds.setToolTip("Number of rounds in the tournament")

        if tournament_type == "Single Elimination":
            self.has_top_8.setChecked(False)
            self.has_top_8.setEnabled(False)
        else:
            self.has_top_8.setEnabled(True)

    def _on_event_type_changed(self, _value: str) -> None:
        self._update_tournament_structure_state()

    def _on_tournament_structure_changed(self, _value: str) -> None:
        self._update_tournament_structure_state()

    def _build_tournament_structure_payload(self) -> dict[str, object] | None:
        raw_players = str(self.player_count.currentText()).strip()
        try:
            player_count = max(2, int(raw_players))
        except (TypeError, ValueError):
            QMessageBox.warning(self, "Invalid Player Count", "Player count must be a whole number.")
            return None

        tournament_type = self.tournament_type.currentText().strip() or "Swiss"
        if self._is_mtgo_league_event():
            tournament_type = "Swiss"
            rounds = 5
            has_top_8 = False
        elif self._is_fnm_lgs_event() or tournament_type == "Single Elimination":
            rounds = self._calculate_recommended_rounds(tournament_type, player_count)
            has_top_8 = self.has_top_8.isChecked() if tournament_type == "Swiss" else False
        else:
            rounds = self.num_rounds.value()
            has_top_8 = self.has_top_8.isChecked() if tournament_type == "Swiss" else False

        return {
            "type": tournament_type,
            "players": player_count,
            "rounds": rounds,
            "has_top_8": has_top_8,
        }

    def _remap_into_repo_root(self, path_value: str | Path) -> Path:
        path = Path(path_value)
        lower_parts = [part.lower() for part in path.parts]
        if "data" in lower_parts:
            data_index = lower_parts.index("data")
            return self.repo_root.joinpath(*path.parts[data_index:])
        return path

    def _normalize_saved_league_path(self, league_path: str | None) -> str | None:
        raw = str(league_path or "").strip()
        if not raw:
            return None

        original = Path(raw)
        remapped = self._remap_into_repo_root(original)

        if getattr(sys, "frozen", False) and remapped != original and remapped.exists():
            return str(remapped)
        if original.exists():
            return str(original)
        if remapped != original and remapped.exists():
            return str(remapped)
        return None

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tabs = QTabWidget(root)

        tracker_tab = QWidget(tabs)
        tracker_layout = QVBoxLayout(tracker_tab)
        tracker_layout.setContentsMargins(0, 0, 0, 0)
        tracker_layout.setSpacing(10)
        tracker_layout.addWidget(self._build_league_box())
        tracker_layout.addWidget(self._build_match_box())

        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setPlaceholderText("Status and actions will appear here...")
        tracker_layout.addWidget(self.status)

        tabs.addTab(tracker_tab, "Tracker")
        tabs.addTab(self._build_loaded_league_tab(), "Loaded League")
        tabs.addTab(self._build_statistics_tab(), "Statistics")
        layout.addWidget(tabs)

        self.setCentralWidget(root)

    def _build_statistics_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        controls = QGroupBox("Statistics Analysis")
        form = QFormLayout(controls)

        self.stats_granularity = QComboBox()
        self.stats_granularity.addItems(["event", "day", "week", "month"])
        self.stats_granularity.setCurrentText("week")

        self.stats_event_filter = QLineEdit()
        self.stats_event_filter.setPlaceholderText("Optional: League,Challenge,Qualifier")

        self.stats_date_from = QLineEdit()
        self.stats_date_from.setPlaceholderText("DD MM YYYY")

        self.stats_date_to = QLineEdit()
        self.stats_date_to.setPlaceholderText("DD MM YYYY")

        self.stats_date_preset = QComboBox()
        self.stats_date_preset.addItems(
            [
                "Custom",
                "Last 7 Days",
                "Last 30 Days",
                "Last 90 Days",
                "Current Month",
                "Previous Month",
            ]
        )
        self.stats_date_preset.currentTextChanged.connect(self._on_stats_date_preset_changed)

        self.stats_projection_rounds = QSpinBox()
        self.stats_projection_rounds.setMinimum(1)
        self.stats_projection_rounds.setMaximum(20)
        self.stats_projection_rounds.setValue(5)

        self.stats_min_samples = QSpinBox()
        self.stats_min_samples.setMinimum(1)
        self.stats_min_samples.setMaximum(200)
        self.stats_min_samples.setValue(8)

        self.stats_include_charts = QCheckBox("Generate charts")
        self.stats_include_charts.setChecked(True)

        self.stats_output_name = QLineEdit()
        self.stats_output_name.setPlaceholderText("Optional folder name, e.g. april_full_analysis")

        form.addRow("Time Granularity", self.stats_granularity)
        form.addRow("Event Filter", self.stats_event_filter)
        form.addRow("Date From", self.stats_date_from)
        form.addRow("Date To", self.stats_date_to)
        form.addRow("Date Preset", self.stats_date_preset)
        form.addRow("Projection Rounds", self.stats_projection_rounds)
        form.addRow("Min Samples (Anomaly)", self.stats_min_samples)
        form.addRow("Charts", self.stats_include_charts)
        form.addRow("Output Folder", self.stats_output_name)

        self.stats_compare_event_a = QComboBox()
        self.stats_compare_event_b = QComboBox()
        compare_row = QWidget()
        compare_layout = QHBoxLayout(compare_row)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(6)
        compare_layout.addWidget(QLabel("A"))
        compare_layout.addWidget(self.stats_compare_event_a, 1)
        compare_layout.addWidget(QLabel("B"))
        compare_layout.addWidget(self.stats_compare_event_b, 1)
        self.stats_compare_btn = QPushButton("Compare Events")
        self.stats_compare_btn.clicked.connect(self._on_compare_events)
        compare_layout.addWidget(self.stats_compare_btn)
        form.addRow("Event vs Event", compare_row)

        actions = QHBoxLayout()
        generate_btn = QPushButton("Generate Analysis")
        generate_btn.clicked.connect(self._on_generate_statistics)
        actions.addWidget(generate_btn)
        actions.addStretch(1)
        form.addRow(actions)

        self.stats_results_tabs = QTabWidget()

        self.stats_preview = QTextEdit()
        self.stats_preview.setReadOnly(True)
        self.stats_preview.setPlaceholderText("Generated analysis summary will appear here.")
        self.stats_results_tabs.addTab(self.stats_preview, "Summary")

        charts_page = QWidget()
        charts_layout = QVBoxLayout(charts_page)
        charts_toolbar = QHBoxLayout()
        self.stats_chart_selector = QComboBox()
        self.stats_chart_selector.currentIndexChanged.connect(self._on_stats_chart_changed)
        charts_toolbar.addWidget(QLabel("Chart"))
        charts_toolbar.addWidget(self.stats_chart_selector, 1)
        charts_layout.addLayout(charts_toolbar)

        self.stats_chart_label = QLabel("No chart loaded")
        self.stats_chart_label.setAlignment(Qt.AlignCenter)
        self.stats_chart_label.setMinimumHeight(360)

        self.stats_chart_scroll = QScrollArea()
        self.stats_chart_scroll.setWidgetResizable(True)
        self.stats_chart_scroll.setWidget(self.stats_chart_label)
        self.stats_chart_scroll.viewport().installEventFilter(self)
        charts_layout.addWidget(self.stats_chart_scroll, 1)
        self.stats_results_tabs.addTab(charts_page, "Charts")

        tables_page = QWidget()
        tables_layout = QVBoxLayout(tables_page)
        tables_toolbar = QHBoxLayout()
        self.stats_table_selector = QComboBox()
        self.stats_table_selector.currentIndexChanged.connect(self._on_stats_table_changed)
        tables_toolbar.addWidget(QLabel("Table"))
        tables_toolbar.addWidget(self.stats_table_selector, 1)
        tables_layout.addLayout(tables_toolbar)

        self.stats_table = QTableWidget(0, 0)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tables_layout.addWidget(self.stats_table, 1)
        self.stats_results_tabs.addTab(tables_page, "Tables")

        self.stats_markdown_view = QTextEdit()
        self.stats_markdown_view.setReadOnly(True)
        self.stats_markdown_view.setPlaceholderText("Markdown report will be shown here.")
        self.stats_results_tabs.addTab(self.stats_markdown_view, "Markdown")

        self.stats_compare_view = QTextEdit()
        self.stats_compare_view.setReadOnly(True)
        self.stats_compare_view.setPlaceholderText("Event comparison will be shown here.")
        self.stats_results_tabs.addTab(self.stats_compare_view, "Compare")

        layout.addWidget(controls)
        layout.addWidget(self.stats_results_tabs, 1)
        self._refresh_stats_event_options([])
        return tab

    def _refresh_stats_event_options(self, leagues: list[dict[str, object]] | None = None) -> None:
        if not hasattr(self, "stats_compare_event_a") or not hasattr(self, "stats_compare_event_b"):
            return

        source = leagues if leagues is not None else self.storage.list_leagues()
        events = sorted({str(item.get("event_type", "")).strip() for item in source if str(item.get("event_type", "")).strip()})

        current_a = self.stats_compare_event_a.currentText().strip()
        current_b = self.stats_compare_event_b.currentText().strip()

        self.stats_compare_event_a.blockSignals(True)
        self.stats_compare_event_b.blockSignals(True)
        self.stats_compare_event_a.clear()
        self.stats_compare_event_b.clear()

        for event_name in events:
            self.stats_compare_event_a.addItem(event_name)
            self.stats_compare_event_b.addItem(event_name)

        self.stats_compare_event_a.blockSignals(False)
        self.stats_compare_event_b.blockSignals(False)

        if not events:
            self.stats_compare_btn.setEnabled(False)
            return

        self.stats_compare_btn.setEnabled(len(events) >= 2)

        idx_a = self.stats_compare_event_a.findText(current_a)
        idx_b = self.stats_compare_event_b.findText(current_b)
        self.stats_compare_event_a.setCurrentIndex(idx_a if idx_a >= 0 else 0)
        if idx_b >= 0:
            self.stats_compare_event_b.setCurrentIndex(idx_b)
        elif self.stats_compare_event_b.count() > 1:
            self.stats_compare_event_b.setCurrentIndex(1)
        else:
            self.stats_compare_event_b.setCurrentIndex(0)

    def _on_stats_date_preset_changed(self, preset: str) -> None:
        today = datetime.now().date()

        if preset == "Custom":
            return
        if preset == "Last 7 Days":
            start = today - timedelta(days=6)
            end = today
        elif preset == "Last 30 Days":
            start = today - timedelta(days=29)
            end = today
        elif preset == "Last 90 Days":
            start = today - timedelta(days=89)
            end = today
        elif preset == "Current Month":
            start = today.replace(day=1)
            end = today
        elif preset == "Previous Month":
            current_month_start = today.replace(day=1)
            end = current_month_start - timedelta(days=1)
            start = end.replace(day=1)
        else:
            return

        self.stats_date_from.setText(start.strftime("%d %m %Y"))
        self.stats_date_to.setText(end.strftime("%d %m %Y"))

    def _on_generate_statistics(self) -> None:
        raw_events = [x.strip() for x in self.stats_event_filter.text().split(",") if x.strip()]
        date_from_raw = self.stats_date_from.text().strip()
        date_to_raw = self.stats_date_to.text().strip()

        date_from_value = self._parse_ui_date(date_from_raw)
        date_to_value = self._parse_ui_date(date_to_raw)

        if date_from_raw and date_from_value is None:
            QMessageBox.warning(self, "Invalid Date", "Date From must be in DD MM YYYY format.")
            return
        if date_to_raw and date_to_value is None:
            QMessageBox.warning(self, "Invalid Date", "Date To must be in DD MM YYYY format.")
            return
        if date_from_value and date_to_value and date_from_value > date_to_value:
            QMessageBox.warning(self, "Invalid Range", "Date From cannot be later than Date To.")
            return

        date_from_iso = date_from_value.isoformat() if date_from_value else None
        date_to_iso = date_to_value.isoformat() if date_to_value else None

        config = AnalyticsConfig(
            time_granularity=self.stats_granularity.currentText().strip().lower(),
            event_types=raw_events or None,
            date_from=date_from_iso,
            date_to=date_to_iso,
            min_samples_for_anomaly=self.stats_min_samples.value(),
            projection_rounds=self.stats_projection_rounds.value(),
            include_charts=self.stats_include_charts.isChecked(),
        )

        custom_name = self.stats_output_name.text().strip()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = (
            self.repo_root / "data" / "analysis" / custom_name
            if custom_name
            else self.repo_root / "data" / "analysis" / f"analysis_{stamp}"
        )

        try:
            result = self.analytics.run_analysis(output_dir, config)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Statistics Failed", str(exc))
            return

        summary = result.get("summary", {})
        files = result.get("files", {})
        preview_lines = [
            "Statistics generation completed",
            "",
            f"Output directory: {result.get('output_dir', '')}",
            f"Date range: {date_from_raw or '-'} -> {date_to_raw or '-'}",
            f"Matches: {summary.get('matches', 0)}",
            f"Record: {summary.get('wins', 0)}-{summary.get('losses', 0)}",
            f"Winrate: {summary.get('winrate_pct', 0.0):.1f}%",
            f"95% CI: {summary.get('ci95_low', 0.0):.1f}% - {summary.get('ci95_high', 0.0):.1f}%",
            "",
            "Generated files:",
            f"- JSON: {files.get('json', '')}",
            f"- Markdown: {files.get('markdown', '')}",
            f"- Excel: {files.get('excel', '')}",
            f"- CSV folder: {files.get('csv_dir', '')}",
            f"- Charts folder: {files.get('charts_dir', '')}",
        ]
        self.stats_preview.setPlainText("\n".join(preview_lines))
        self._load_statistics_outputs(files)
        self._append_status(f"Generated statistics export in {result.get('output_dir', '')}")

    def _on_compare_events(self) -> None:
        event_a = self.stats_compare_event_a.currentText().strip()
        event_b = self.stats_compare_event_b.currentText().strip()
        if not event_a or not event_b:
            QMessageBox.warning(self, "Missing Event", "Select both events to compare.")
            return
        if event_a.lower() == event_b.lower():
            QMessageBox.warning(self, "Invalid Selection", "Event A and Event B must be different.")
            return

        date_from_raw = self.stats_date_from.text().strip()
        date_to_raw = self.stats_date_to.text().strip()
        date_from_value = self._parse_ui_date(date_from_raw)
        date_to_value = self._parse_ui_date(date_to_raw)

        if date_from_raw and date_from_value is None:
            QMessageBox.warning(self, "Invalid Date", "Date From must be in DD MM YYYY format.")
            return
        if date_to_raw and date_to_value is None:
            QMessageBox.warning(self, "Invalid Date", "Date To must be in DD MM YYYY format.")
            return
        if date_from_value and date_to_value and date_from_value > date_to_value:
            QMessageBox.warning(self, "Invalid Range", "Date From cannot be later than Date To.")
            return

        try:
            comparison = self.analytics.compare_events(
                event_a=event_a,
                event_b=event_b,
                date_from=date_from_value.isoformat() if date_from_value else None,
                date_to=date_to_value.isoformat() if date_to_value else None,
            )
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Compare Failed", str(exc))
            return

        a = comparison.get("summary_a", {})
        b = comparison.get("summary_b", {})
        d = comparison.get("delta", {})
        lines = [
            "Event vs Event Comparison",
            "",
            f"Date range: {date_from_raw or '-'} -> {date_to_raw or '-'}",
            "",
            f"A: {event_a}",
            f"- Matches: {a.get('matches', 0)}",
            f"- Record: {a.get('wins', 0)}-{a.get('losses', 0)}",
            f"- Winrate: {a.get('winrate_pct', 0.0):.1f}%",
            f"- 95% CI: {a.get('ci95_low', 0.0):.1f}% - {a.get('ci95_high', 0.0):.1f}%",
            "",
            f"B: {event_b}",
            f"- Matches: {b.get('matches', 0)}",
            f"- Record: {b.get('wins', 0)}-{b.get('losses', 0)}",
            f"- Winrate: {b.get('winrate_pct', 0.0):.1f}%",
            f"- 95% CI: {b.get('ci95_low', 0.0):.1f}% - {b.get('ci95_high', 0.0):.1f}%",
            "",
            "Delta (A - B)",
            f"- Matches: {d.get('matches', 0):+d}",
            f"- Winrate: {d.get('winrate_pp', 0.0):+.1f} pp",
            f"- Mulligan Rate: {d.get('mulligan_rate_pp', 0.0):+.1f} pp",
            f"- Mana Screw Rate: {d.get('mana_screw_rate_pp', 0.0):+.1f} pp",
            f"- Mana Flood Rate: {d.get('mana_flood_rate_pp', 0.0):+.1f} pp",
        ]
        self.stats_compare_view.setPlainText("\n".join(lines))
        self.stats_results_tabs.setCurrentWidget(self.stats_compare_view)
        self._append_status(f"Compared events: {event_a} vs {event_b}")

    def _parse_ui_date(self, value: str):
        raw = value.strip()
        if not raw:
            return None

        for fmt in ["%d %m %Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"]:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    def _load_statistics_outputs(self, files: dict[str, object]) -> None:
        markdown_path = str(files.get("markdown", "")).strip()
        csv_dir = str(files.get("csv_dir", "")).strip()
        charts_dir = str(files.get("charts_dir", "")).strip()

        if markdown_path and Path(markdown_path).exists():
            self.stats_markdown_view.setPlainText(Path(markdown_path).read_text(encoding="utf-8"))
        else:
            self.stats_markdown_view.setPlainText("Markdown report not found.")

        self.stats_table_selector.blockSignals(True)
        self.stats_table_selector.clear()
        csv_files: list[Path] = []
        if csv_dir and Path(csv_dir).exists():
            csv_files = sorted(Path(csv_dir).glob("*.csv"))
            for csv_file in csv_files:
                self.stats_table_selector.addItem(csv_file.name, str(csv_file))
        self.stats_table_selector.blockSignals(False)

        if csv_files:
            self.stats_table_selector.setCurrentIndex(0)
            self._load_csv_table(csv_files[0])
        else:
            self.stats_table.clear()
            self.stats_table.setRowCount(0)
            self.stats_table.setColumnCount(0)

        self.stats_chart_selector.blockSignals(True)
        self.stats_chart_selector.clear()
        chart_files: list[Path] = []
        if charts_dir and Path(charts_dir).exists():
            chart_files = sorted(Path(charts_dir).glob("*.png"))
            for chart_file in chart_files:
                self.stats_chart_selector.addItem(chart_file.name, str(chart_file))
        self.stats_chart_selector.blockSignals(False)

        if chart_files:
            self.stats_chart_selector.setCurrentIndex(0)
            self._load_chart_preview(chart_files[0])
        else:
            self._stats_chart_original = QPixmap()
            self.stats_chart_label.setPixmap(QPixmap())
            self.stats_chart_label.setText("No charts generated")

    def _on_stats_table_changed(self, _index: int) -> None:
        table_path = str(self.stats_table_selector.currentData() or "").strip()
        if not table_path:
            return
        path = Path(table_path)
        if path.exists():
            self._load_csv_table(path)

    def _on_stats_chart_changed(self, _index: int) -> None:
        chart_path = str(self.stats_chart_selector.currentData() or "").strip()
        if not chart_path:
            return
        path = Path(chart_path)
        if path.exists():
            self._load_chart_preview(path)

    def _load_csv_table(self, path: Path) -> None:
        try:
            rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        except Exception as exc:  # pragma: no cover
            self.stats_table.clear()
            self.stats_table.setRowCount(0)
            self.stats_table.setColumnCount(0)
            self.stats_preview.append(f"Failed to load table {path.name}: {exc}")
            return

        if not rows:
            self.stats_table.clear()
            self.stats_table.setRowCount(0)
            self.stats_table.setColumnCount(0)
            return

        header = rows[0]
        data_rows = rows[1:]
        self.stats_table.clear()
        self.stats_table.setColumnCount(len(header))
        self.stats_table.setRowCount(len(data_rows))
        self.stats_table.setHorizontalHeaderLabels(header)

        for r_idx, row in enumerate(data_rows):
            for c_idx in range(len(header)):
                value = row[c_idx] if c_idx < len(row) else ""
                self.stats_table.setItem(r_idx, c_idx, QTableWidgetItem(value))

        self.stats_table.resizeColumnsToContents()

    def _load_chart_preview(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._stats_chart_original = QPixmap()
            self.stats_chart_label.setPixmap(QPixmap())
            self.stats_chart_label.setText(f"Unable to load chart: {path.name}")
            return

        self._stats_chart_original = pixmap
        self._refresh_stats_chart_scaling()

    def _refresh_stats_chart_scaling(self) -> None:
        if self._stats_chart_original.isNull():
            return

        viewport_width = 0
        viewport_height = 0
        if hasattr(self, "stats_chart_scroll"):
            viewport = self.stats_chart_scroll.viewport()
            viewport_width = viewport.width()
            viewport_height = viewport.height()

        if viewport_width <= 0:
            viewport_width = max(320, self.stats_chart_label.width())
        if viewport_height <= 0:
            viewport_height = max(220, self.stats_chart_label.height())

        # Fit chart into visible viewport while preserving aspect ratio.
        target_w = max(320, viewport_width - 12)
        target_h = max(220, viewport_height - 12)
        scaled = self._stats_chart_original.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.stats_chart_label.setText("")
        self.stats_chart_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_stats_chart_scaling()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if hasattr(self, "stats_chart_scroll") and watched is self.stats_chart_scroll.viewport():
            if event.type() == QEvent.Resize:
                self._refresh_stats_chart_scaling()
        return super().eventFilter(watched, event)

    def _build_loaded_league_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        info = QLabel("Load one or more leagues, inspect decklist and match notes, edit fields, and save changes.")
        refresh_btn = QPushButton("Refresh Loaded")
        refresh_btn.clicked.connect(self._refresh_match_notes_tabs)

        toolbar.addWidget(info)
        toolbar.addStretch(1)
        toolbar.addWidget(refresh_btn)

        self.loaded_leagues_tabs = QTabWidget()
        self.loaded_leagues_tabs.setDocumentMode(True)
        self.loaded_leagues_tabs.setTabsClosable(True)
        self.loaded_leagues_tabs.tabCloseRequested.connect(self._on_close_loaded_league_tab)

        layout.addLayout(toolbar)
        layout.addWidget(self.loaded_leagues_tabs)
        return tab

    def _build_league_box(self) -> QGroupBox:
        box = QGroupBox("New League")
        grid = QGridLayout(box)

        form_left = QFormLayout()
        self.event_type = QComboBox()
        self.event_type.addItems(self.event_type_options)
        self.event_type.setEditable(True)
        self.event_type.setCurrentText("League MTGO")
        self.event_type.setToolTip("Event type from the starter list or your custom value")
        self.event_type.currentTextChanged.connect(self._on_event_type_changed)

        self.format_name = QComboBox()
        self.format_name.addItems(self.format_options)
        self.format_name.setEditable(True)
        self.format_name.setCurrentText("Modern")
        self.format_name.setToolTip("Event format from the starter list or your custom value")
        self.format_name.currentTextChanged.connect(self._on_format_changed)
        self.deck_name = QComboBox()
        self.deck_name.setEditable(True)
        self.deck_name.setCurrentText("Zoo")
        self.deck_name.setToolTip("Your deck name used in stats and generated Markdown files")
        self.deck_name.currentTextChanged.connect(self._on_my_deck_changed)
        self.add_my_deck_btn = QPushButton("Add Deck")
        self.add_my_deck_btn.setToolTip("Save this deck and archetype to deck memory")
        self.add_my_deck_btn.clicked.connect(self._on_add_my_deck)
        deck_row = QWidget()
        deck_row_layout = QHBoxLayout(deck_row)
        deck_row_layout.setContentsMargins(0, 0, 0, 0)
        deck_row_layout.setSpacing(6)
        deck_row_layout.addWidget(self.deck_name)
        deck_row_layout.addWidget(self.add_my_deck_btn)

        self.deck_archetype = QComboBox()
        self.deck_archetype.addItems(self.archetype_options)
        self.deck_archetype.setEditable(True)
        self.deck_archetype.setCurrentText("Midrange")
        self.deck_archetype.setToolTip("Deck archetype, for example Aggro, Midrange, Control, Combo")
        self.add_my_archetype_btn = QPushButton("Add Archetype")
        self.add_my_archetype_btn.setToolTip("Add a new archetype to the selectable list")
        self.add_my_archetype_btn.clicked.connect(self._on_add_my_archetype)
        deck_archetype_row = QWidget()
        deck_archetype_layout = QHBoxLayout(deck_archetype_row)
        deck_archetype_layout.setContentsMargins(0, 0, 0, 0)
        deck_archetype_layout.setSpacing(6)
        deck_archetype_layout.addWidget(self.deck_archetype)
        deck_archetype_layout.addWidget(self.add_my_archetype_btn)
        self.moxfield_url = QLineEdit()
        self.moxfield_url.setToolTip("Optional link to your Moxfield deck list")

        self.deck_list_name = QLineEdit()
        self.deck_list_name.setPlaceholderText("Classic_Zoo_V1.2")
        self.deck_list_name.setToolTip("Custom name of the exact list version used in this league")

        self.import_decklist_btn = QPushButton("Import Decklist")
        self.import_decklist_btn.setToolTip("Import decklist from .txt or MTGO .dek file")
        self.import_decklist_btn.clicked.connect(self._on_import_decklist)
        self.clear_decklist_btn = QPushButton("Clear")
        self.clear_decklist_btn.clicked.connect(self._on_clear_decklist)
        decklist_row = QWidget()
        decklist_row_layout = QHBoxLayout(decklist_row)
        decklist_row_layout.setContentsMargins(0, 0, 0, 0)
        decklist_row_layout.setSpacing(6)
        decklist_row_layout.addWidget(self.import_decklist_btn)
        decklist_row_layout.addWidget(self.clear_decklist_btn)
        self.decklist_import_label = QLabel("No file imported")
        decklist_row_layout.addWidget(self.decklist_import_label, 1)

        self.configure_lists_btn = QPushButton("Configure Lists")
        self.configure_lists_btn.setToolTip("Add, rename, or remove dropdown values without editing code")
        self.configure_lists_btn.clicked.connect(self._on_configure_lists)

        form_left.addRow("Event Type", self.event_type)
        form_left.addRow("Format", self.format_name)
        form_left.addRow("Deck", deck_row)
        form_left.addRow("Archetype", deck_archetype_row)
        form_left.addRow("Moxfield URL", self.moxfield_url)
        form_left.addRow("Deck List Name", self.deck_list_name)
        form_left.addRow("Decklist File", decklist_row)
        form_left.addRow("Configure", self.configure_lists_btn)

        # Tournament Structure
        self.tournament_type = QComboBox()
        self.tournament_type.addItems(["Swiss", "Single Elimination"])
        self.tournament_type.setToolTip("Tournament structure type")
        self.tournament_type.currentTextChanged.connect(self._on_tournament_structure_changed)

        self.player_count = QComboBox()
        self.player_count.addItems(self.player_count_options)
        self.player_count.setEditable(True)
        self.player_count.setCurrentText("32")
        self.player_count.setToolTip("Player count used to auto-calculate rounds for FNM/LGS Tournament and single elimination")
        self.player_count.currentTextChanged.connect(self._on_tournament_structure_changed)

        self.num_rounds = QSpinBox()
        self.num_rounds.setMinimum(1)
        self.num_rounds.setMaximum(20)
        self.num_rounds.setValue(5)
        self.num_rounds.setToolTip("Number of rounds in the tournament")

        self.has_top_8 = QCheckBox("Top 8")
        self.has_top_8.setToolTip("Check if tournament has a top 8 playoff")

        tournament_row = QWidget()
        tournament_layout = QHBoxLayout(tournament_row)
        tournament_layout.setContentsMargins(0, 0, 0, 0)
        tournament_layout.setSpacing(6)
        tournament_layout.addWidget(self.tournament_type)
        tournament_layout.addWidget(self.player_count)
        tournament_layout.addWidget(self.num_rounds)
        tournament_layout.addWidget(self.has_top_8)

        form_left.addRow("Tournament Structure", tournament_row)

        form_right = QFormLayout()
        self.changes = DeckAwareTextEdit()
        self.changes.setToolTip("What changed in your deck compared to the previous version")
        self.goal = DeckAwareTextEdit()
        self.goal.setToolTip("Your goal for this event, for example testing a card or reaching 4-1")
        self.concerns = DeckAwareTextEdit()
        self.concerns.setToolTip("Your concerns before the event, for example weak matchups")
        self.notes = DeckAwareTextEdit()
        self.notes.setToolTip("Any extra notes you want to remember")

        self.changes.setMaximumHeight(70)
        self.goal.setMaximumHeight(70)
        self.concerns.setMaximumHeight(70)
        self.notes.setMaximumHeight(70)

        form_right.addRow("Changes", self.changes)
        form_right.addRow("Goal", self.goal)
        form_right.addRow("Concerns", self.concerns)
        form_right.addRow("Notes", self.notes)

        grid.addLayout(form_left, 0, 0)
        grid.addLayout(form_right, 0, 1)

        actions = QHBoxLayout()
        self.create_league_btn = QPushButton("Create League")
        self.create_league_btn.setToolTip("Create a new league and generate folder, league.md, and meta.json")
        self.create_league_btn.clicked.connect(self._on_create_league)
        self.end_league_btn = QPushButton("End League")
        self.end_league_btn.setToolTip("Finalize active league, refresh report, update global stats, and append history")
        self.end_league_btn.clicked.connect(self._on_end_league)
        self.end_league_btn.setEnabled(False)
        self.manage_decks_btn = QPushButton("Manage Decks")
        self.manage_decks_btn.setToolTip("Open deck manager to edit deck and archetype memory")
        self.manage_decks_btn.clicked.connect(self._on_manage_decks)
        self.current_league_label = QLabel("Active league: none")

        actions.addWidget(self.create_league_btn)
        actions.addWidget(self.end_league_btn)
        actions.addWidget(self.manage_decks_btn)
        actions.addWidget(self.current_league_label)
        actions.addStretch(1)

        grid.addLayout(actions, 1, 0, 1, 2)

        picker_row = QHBoxLayout()
        self.league_selector = QComboBox()
        self.league_selector.setToolTip(
            "Load any existing league as active and continue logging across multiple days"
        )
        self.league_selector.setMinimumWidth(480)
        self.league_selector.currentIndexChanged.connect(self._on_league_selector_changed)

        self.refresh_leagues_btn = QPushButton("Refresh Leagues")
        self.refresh_leagues_btn.clicked.connect(self._refresh_league_selector)

        self.load_selected_btn = QPushButton("Load Selected")
        self.load_selected_btn.setToolTip("Set selected league as active")
        self.load_selected_btn.clicked.connect(self._on_load_selected_league)

        self.preview_league_btn = QPushButton("Preview Selected")
        self.preview_league_btn.setToolTip("Preview selected league summary and matches")
        self.preview_league_btn.clicked.connect(self._on_preview_selected_league)

        self.continue_active_btn = QPushButton("Continue Last Active")
        self.continue_active_btn.setToolTip("Re-load last active league from app settings")
        self.continue_active_btn.clicked.connect(self._restore_active_league)

        self.league_filter = QComboBox()
        self.league_filter.addItems(["All", "Active", "Completed"])
        self.league_filter.currentTextChanged.connect(self._refresh_league_selector)

        self.league_sort = QComboBox()
        self.league_sort.addItems(["Date (newest)", "Date (oldest)", "Winrate (high)", "Winrate (low)"])
        self.league_sort.currentTextChanged.connect(self._refresh_league_selector)

        picker_row.addWidget(QLabel("League Browser"))
        picker_row.addWidget(self.league_selector, 1)
        picker_row.addWidget(self.league_filter)
        picker_row.addWidget(self.league_sort)
        picker_row.addWidget(self.refresh_leagues_btn)
        picker_row.addWidget(self.continue_active_btn)
        picker_row.addWidget(self.load_selected_btn)
        picker_row.addWidget(self.preview_league_btn)
        grid.addLayout(picker_row, 2, 0, 1, 2)
        return box

    def _build_match_box(self) -> QGroupBox:
        box = QGroupBox("Add Match")
        grid = QGridLayout(box)

        form_left = QFormLayout()
        self.opponent_deck = QComboBox()
        self.opponent_deck.setEditable(True)
        self.opponent_deck.setToolTip("Opponent deck with autocomplete from deck memory")
        self.opponent_deck.currentTextChanged.connect(self._on_opponent_deck_changed)
        self.add_opponent_deck_btn = QPushButton("Add Deck")
        self.add_opponent_deck_btn.setToolTip("Save opponent deck and archetype to memory")
        self.add_opponent_deck_btn.clicked.connect(self._on_add_opponent_deck)
        opponent_deck_row = QWidget()
        opponent_deck_layout = QHBoxLayout(opponent_deck_row)
        opponent_deck_layout.setContentsMargins(0, 0, 0, 0)
        opponent_deck_layout.setSpacing(6)
        opponent_deck_layout.addWidget(self.opponent_deck)
        opponent_deck_layout.addWidget(self.add_opponent_deck_btn)

        self.opponent_archetype = QComboBox()
        self.opponent_archetype.addItems(self.archetype_options)
        self.opponent_archetype.setEditable(True)
        self.opponent_archetype.setToolTip("Opponent archetype, auto-filled for known decks")
        self.add_opponent_archetype_btn = QPushButton("Add Archetype")
        self.add_opponent_archetype_btn.setToolTip("Add a new archetype to the selectable list")
        self.add_opponent_archetype_btn.clicked.connect(self._on_add_opponent_archetype)
        opponent_archetype_row = QWidget()
        opponent_archetype_layout = QHBoxLayout(opponent_archetype_row)
        opponent_archetype_layout.setContentsMargins(0, 0, 0, 0)
        opponent_archetype_layout.setSpacing(6)
        opponent_archetype_layout.addWidget(self.opponent_archetype)
        opponent_archetype_layout.addWidget(self.add_opponent_archetype_btn)
        self.score = QComboBox()
        self.score.addItems(["2-0", "2-1", "1-2", "0-2"])
        self.score.setToolTip("Match score: 2-0 clean win, 2-1 close win, 1-2 close loss, 0-2 clean loss")

        form_left.addRow("Opponent Deck", opponent_deck_row)
        form_left.addRow("Opponent Archetype", opponent_archetype_row)
        form_left.addRow("Score", self.score)

        form_right = QFormLayout()
        self.sideboard_notes = DeckAwareTextEdit()
        self.sideboard_notes.setToolTip("Cards in and out for sideboarding in this matchup")
        self.key_moments = DeckAwareTextEdit()
        self.key_moments.setToolTip("Key moments of the match, mistakes, turning points, important cards")
        self.observations = DeckAwareTextEdit()
        self.observations.setToolTip("General matchup observations about what worked and what did not")

        self.sideboard_notes.setMaximumHeight(72)
        self.key_moments.setMaximumHeight(72)
        self.observations.setMaximumHeight(72)

        form_right.addRow("Sideboard Notes", self.sideboard_notes)
        form_right.addRow("Key Moments", self.key_moments)
        form_right.addRow("Observations", self.observations)

        grid.addLayout(form_left, 0, 0)
        grid.addLayout(form_right, 0, 1)

        games_box = QGroupBox("Fast Game Input")
        games_layout = QFormLayout(games_box)
        self.game_rows: list[dict[str, object]] = []
        for idx in range(1, 4):
            row = self._build_game_row(idx)
            games_layout.addRow(f"Game {idx}", row["container"])
            self.game_rows.append(row)

        grid.addWidget(games_box, 1, 0, 1, 2)

        actions = QHBoxLayout()
        self.add_match_btn = QPushButton("Add Match  [Ctrl+Enter]")
        self.add_match_btn.setToolTip("Save the match and update counters in the active league")
        self.add_match_btn.clicked.connect(self._on_add_match)
        self.add_match_btn.setEnabled(False)

        # Keyboard shortcuts for fast entry
        add_match_shortcut = QShortcut(QKeySequence("Ctrl+Return"), box)
        add_match_shortcut.activated.connect(self._on_add_match)
        # Alt+W/Q/E/L → score 2-0 / 2-1 / 1-2 / 0-2
        for key, idx in (("Alt+W", 0), ("Alt+Q", 1), ("Alt+E", 2), ("Alt+L", 3)):
            sc = QShortcut(QKeySequence(key), box)
            sc.activated.connect(lambda i=idx: self.score.setCurrentIndex(i))

        actions.addWidget(self.add_match_btn)
        actions.addStretch(1)
        grid.addLayout(actions, 2, 0, 1, 2)

        return box

    def _refresh_match_notes_tabs(self) -> None:
        if self.loaded_leagues_tabs is None:
            return

        if self.loaded_leagues_tabs.count() == 0:
            placeholder = QTextEdit()
            placeholder.setReadOnly(True)
            placeholder.setPlainText("No loaded leagues. Use League Browser -> Load Selected.")
            self.loaded_leagues_tabs.addTab(placeholder, "No League")
            return

        stale_paths: list[str] = []
        for league_path in list(self.loaded_league_views.keys()):
            try:
                snapshot = self.storage.get_league_snapshot(league_path)
            except Exception:
                stale_paths.append(league_path)
                continue

            view = self.loaded_league_views.get(league_path)
            if not isinstance(view, dict):
                continue

            summary_view = view.get("summary_view")
            decklist_view = view.get("decklist_view")
            if isinstance(summary_view, QTextEdit):
                summary_view.setPlainText(self._render_loaded_league_summary(snapshot))
            if isinstance(decklist_view, QTextEdit):
                decklist_view.setPlainText(self._render_loaded_league_decklist(snapshot))

            self._populate_match_tabs_for_league(league_path, snapshot)

        if stale_paths:
            for league_path in stale_paths:
                self._remove_loaded_league_tab(league_path)

    def _render_loaded_league_summary(self, snapshot: dict[str, object]) -> str:
        meta = snapshot.get("meta", {})
        stats = snapshot.get("stats", {})
        context = meta.get("deck_context", {}) if isinstance(meta, dict) else {}

        lines = [
            f"League: {meta.get('league_id', '')}",
            f"Date: {meta.get('date', '')}",
            f"Event: {meta.get('event_type', '')}",
            f"Deck: {meta.get('deck_name', '')} ({meta.get('deck_archetype', '')})",
            f"Deck List Name: {meta.get('deck_list_name', '')}",
            f"Status: {meta.get('status', '')}",
            "",
            f"Record: {stats.get('wins', 0)}-{stats.get('losses', 0)}",
            f"Winrate: {stats.get('winrate', 0.0):.1f}%",
            f"Mulligan Rate: {stats.get('mulligan_rate', 0.0):.1f}%",
            f"Mana Screw Rate: {stats.get('mana_screw_rate', 0.0):.1f}%",
            f"Mana Flood Rate: {stats.get('mana_flood_rate', 0.0):.1f}%",
            "",
            "League Notes:",
            str(context.get("notes", "")).strip() if isinstance(context, dict) else "",
        ]
        return "\n".join(lines)

    def _render_loaded_league_decklist(self, snapshot: dict[str, object]) -> str:
        meta = snapshot.get("meta", {})
        decklist_md = str(snapshot.get("decklist", "")).strip()

        lines = [
            f"League: {meta.get('league_id', '')}",
            f"Deck: {meta.get('deck_name', '')} ({meta.get('deck_archetype', '')})",
            f"Deck List Name: {meta.get('deck_list_name', '')}",
            "",
        ]
        if decklist_md:
            lines.append(decklist_md)
        else:
            lines.append("No decklist saved for this league.")
        return "\n".join(lines)

    def _build_loaded_league_view(self, league_path: str, snapshot: dict[str, object]) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        top = QHBoxLayout()
        
        # Left panel: summary + context
        left_panel = QVBoxLayout()
        summary_box = QGroupBox("League Summary")
        summary_layout = QVBoxLayout(summary_box)
        summary_view = QTextEdit()
        summary_view.setReadOnly(True)
        summary_view.setMaximumHeight(180)
        summary_view.setPlainText(self._render_loaded_league_summary(snapshot))
        summary_layout.addWidget(summary_view)
        left_panel.addWidget(summary_box, 1)
        
        context_box = QGroupBox("League Context")
        context_layout = QVBoxLayout(context_box)
        deck_terms = build_decklist_autocomplete_terms(str(snapshot.get("decklist", "")))
        
        meta = snapshot.get("meta", {})
        deck_context = meta.get("deck_context", {})
        changes = str(deck_context.get("changes", "")).strip()
        goal = str(deck_context.get("goal", "")).strip()
        concerns = str(deck_context.get("concerns", "")).strip()
        notes = str(deck_context.get("notes", "")).strip()
        
        changes_label = QLabel("Changes:")
        changes_edit = DeckAwareTextEdit()
        changes_edit.setPlainText(changes)
        changes_edit.setMaximumHeight(50)
        changes_edit.set_completion_terms(deck_terms)
        context_layout.addWidget(changes_label)
        context_layout.addWidget(changes_edit)
        
        goal_label = QLabel("Goal:")
        goal_edit = DeckAwareTextEdit()
        goal_edit.setPlainText(goal)
        goal_edit.setMaximumHeight(50)
        goal_edit.set_completion_terms(deck_terms)
        context_layout.addWidget(goal_label)
        context_layout.addWidget(goal_edit)
        
        concerns_label = QLabel("Concerns:")
        concerns_edit = DeckAwareTextEdit()
        concerns_edit.setPlainText(concerns)
        concerns_edit.setMaximumHeight(50)
        concerns_edit.set_completion_terms(deck_terms)
        context_layout.addWidget(concerns_label)
        context_layout.addWidget(concerns_edit)
        
        notes_label = QLabel("Notes:")
        notes_edit = DeckAwareTextEdit()
        notes_edit.setPlainText(notes)
        notes_edit.setMaximumHeight(50)
        notes_edit.set_completion_terms(deck_terms)
        context_layout.addWidget(notes_label)
        context_layout.addWidget(notes_edit)
        
        left_panel.addWidget(context_box, 1)
        
        left_container = QWidget()
        left_container.setLayout(left_panel)
        
        # Right panel: decklist
        decklist_box = QGroupBox("Decklist")
        decklist_layout = QVBoxLayout(decklist_box)
        decklist_view = QTextEdit()
        decklist_view.setReadOnly(True)
        decklist_view.setPlainText(self._render_loaded_league_decklist(snapshot))
        decklist_layout.addWidget(decklist_view)

        top.addWidget(left_container, 1)
        top.addWidget(decklist_box, 1)
        root.addLayout(top)

        match_tabs = QTabWidget()
        match_tabs.setDocumentMode(True)
        root.addWidget(match_tabs, 1)
        
        # Save context button
        save_context_btn = QPushButton("Save League Context")
        save_context_btn.clicked.connect(
            lambda: self._on_save_league_context(
                league_path, changes_edit, goal_edit, concerns_edit, notes_edit
            )
        )
        root.addWidget(save_context_btn)

        self.loaded_league_views[league_path] = {
            "summary_view": summary_view,
            "decklist_view": decklist_view,
            "context_fields": {
                "changes": changes_edit,
                "goal": goal_edit,
                "concerns": concerns_edit,
                "notes": notes_edit,
            },
            "match_tabs": match_tabs,
            "dirty": False,
            "matches": {},
        }

        self._populate_match_tabs_for_league(league_path, snapshot)
        return container

    def _populate_match_tabs_for_league(self, league_path: str, snapshot: dict[str, object]) -> None:
        view = self.loaded_league_views.get(league_path)
        if not isinstance(view, dict):
            return

        deck_terms = build_decklist_autocomplete_terms(str(snapshot.get("decklist", "")))

        match_tabs = view.get("match_tabs")
        if not isinstance(match_tabs, QTabWidget):
            return

        match_tabs.clear()
        view["matches"] = {}

        matches = list(snapshot.get("matches", []))
        if not matches:
            placeholder = QTextEdit()
            placeholder.setReadOnly(True)
            placeholder.setPlainText("No matches logged yet in this league.")
            match_tabs.addTab(placeholder, "No Matches")
            return

        for match in matches:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)

            header = QLabel(
                f"{match.get('match_id', '')} | {match.get('score', '')} ({match.get('match_result', '')}) "
                f"vs {match.get('opponent_deck', '')} [{match.get('opponent_archetype', '')}]"
            )
            tab_layout.addWidget(header)

            games = list(match.get("games", []))
            summary_box = QGroupBox("Match Hand and Mulligan Summary")
            summary_layout = QVBoxLayout(summary_box)

            keep_counts: dict[int, int] = {7: 0, 6: 0, 5: 0, 4: 0, 3: 0}
            total_mulls = 0
            mull_games = 0
            heavy_mull_games = 0
            game_wins = 0
            game_losses = 0
            details_lines: list[str] = []

            for game in games:
                game_no = int(game.get("game_no", 0))
                result = str(game.get("result", "")).strip()
                opening_size = int(game.get("opening_hand_size", 7))
                mull_count = int(game.get("mulligan_count", 0))
                draw_type = str(game.get("draw_type", "")).strip()
                hand_sequence = list(game.get("hand_sequence", []))

                bucket = opening_size if opening_size in {7, 6, 5, 4} else 3
                keep_counts[bucket] += 1
                total_mulls += mull_count
                if mull_count > 0:
                    mull_games += 1
                if opening_size <= 4:
                    heavy_mull_games += 1
                if result == "Win":
                    game_wins += 1
                elif result == "Loss":
                    game_losses += 1

                seq_text = " -> ".join(str(x) for x in hand_sequence if str(x).strip()) or "-"
                details_lines.append(
                    f"G{game_no}: keep {max(3, opening_size)} | mulls {mull_count} | result {result} | draw {draw_type} | seq {seq_text}"
                )

            keep_dist = (
                f"7:{keep_counts[7]}  6:{keep_counts[6]}  5:{keep_counts[5]}  "
                f"4:{keep_counts[4]}  3-:{keep_counts[3]}"
            )
            aggregate = (
                f"Games: {len(games)} | Game W-L: {game_wins}-{game_losses} | "
                f"Mulligan Games: {mull_games} | Total Mulls: {total_mulls} | "
                f"Heavy Mull Games (keep <=4): {heavy_mull_games}"
            )

            keep_label = QLabel(f"Keep hand distribution: {keep_dist}")
            aggregate_label = QLabel(aggregate)
            details = QTextEdit()
            details.setReadOnly(True)
            details.setMaximumHeight(120)
            details.setPlainText("\n".join(details_lines) if details_lines else "No game details available.")

            summary_layout.addWidget(keep_label)
            summary_layout.addWidget(aggregate_label)
            summary_layout.addWidget(details)
            tab_layout.addWidget(summary_box)

            form = QFormLayout()
            sideboard_edit = DeckAwareTextEdit()
            sideboard_edit.setMaximumHeight(90)
            sideboard_edit.set_completion_terms(deck_terms)
            sideboard_text = str(match.get("sideboard_notes", ""))
            sideboard_edit.setPlainText(sideboard_text)

            key_moments_edit = DeckAwareTextEdit()
            key_moments_edit.setMaximumHeight(90)
            key_moments_edit.set_completion_terms(deck_terms)
            key_moments_text = str(match.get("key_moments", ""))
            key_moments_edit.setPlainText(key_moments_text)

            observations_edit = DeckAwareTextEdit()
            observations_edit.setMaximumHeight(90)
            observations_edit.set_completion_terms(deck_terms)
            observations_text = str(match.get("observations", ""))
            observations_edit.setPlainText(observations_text)

            form.addRow("Sideboard Notes", sideboard_edit)
            form.addRow("Key Moments", key_moments_edit)
            form.addRow("Observations", observations_edit)
            tab_layout.addLayout(form)

            match_id = str(match.get("match_id", ""))
            sideboard_edit.textChanged.connect(lambda lp=league_path: self._mark_loaded_league_dirty(lp, True))
            key_moments_edit.textChanged.connect(lambda lp=league_path: self._mark_loaded_league_dirty(lp, True))
            observations_edit.textChanged.connect(lambda lp=league_path: self._mark_loaded_league_dirty(lp, True))

            save_btn = QPushButton("Save Changes")
            save_btn.clicked.connect(
                lambda _checked=False, lp=league_path, mid=match_id,
                sb=sideboard_edit, km=key_moments_edit, ob=observations_edit: self._on_save_match_notes(
                    lp,
                    mid,
                    sb.toPlainText(),
                    km.toPlainText(),
                    ob.toPlainText(),
                )
            )

            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(save_btn)
            tab_layout.addLayout(row)

            view["matches"][match_id] = {
                "sideboard_edit": sideboard_edit,
                "key_moments_edit": key_moments_edit,
                "observations_edit": observations_edit,
                "original": {
                    "sideboard_notes": sideboard_text,
                    "key_moments": key_moments_text,
                    "observations": observations_text,
                },
            }

            tab_title = match_id if match_id else "Match"
            match_tabs.addTab(tab, tab_title)

        self._mark_loaded_league_dirty(league_path, False)

    def _on_save_match_notes(
        self,
        league_path: str,
        match_id: str,
        sideboard_notes: str,
        key_moments: str,
        observations: str,
    ) -> None:
        if not league_path:
            QMessageBox.warning(self, "No active league", "Load an active league first.")
            return
        if not match_id.strip():
            QMessageBox.warning(self, "No match selected", "Cannot save notes for an unknown match.")
            return

        try:
            self.storage.update_match_notes(
                league_path,
                match_id,
                sideboard_notes,
                key_moments,
                observations,
            )
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Save Failed", str(exc))
            return

        self._append_status(f"Saved notes for {match_id}")
        self._refresh_single_loaded_league(league_path)

    def _on_save_league_context(
        self,
        league_path: str,
        changes_edit: QTextEdit,
        goal_edit: QTextEdit,
        concerns_edit: QTextEdit,
        notes_edit: QTextEdit,
    ) -> None:
        if not league_path:
            QMessageBox.warning(self, "No active league", "Load an active league first.")
            return

        try:
            league_dir = Path(league_path)
            meta_path = league_dir / "meta.json"
            if not meta_path.exists():
                raise FileNotFoundError(f"Missing league metadata: {meta_path}")

            meta = self.storage.read_json(meta_path)
            
            # Update context fields in deck_context subdictionary
            if "deck_context" not in meta:
                meta["deck_context"] = {}
            meta["deck_context"]["changes"] = changes_edit.toPlainText().strip()
            meta["deck_context"]["goal"] = goal_edit.toPlainText().strip()
            meta["deck_context"]["concerns"] = concerns_edit.toPlainText().strip()
            meta["deck_context"]["notes"] = notes_edit.toPlainText().strip()
            
            # Write back to file
            self.storage.write_json_atomic(meta_path, meta)
            
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Save Failed", str(exc))
            return

        self._append_status(f"Saved league context for {league_path}")
        self._refresh_single_loaded_league(league_path)

    def _refresh_single_loaded_league(self, league_path: str) -> None:
        if league_path not in self.loaded_league_views:
            return
        try:
            snapshot = self.storage.get_league_snapshot(league_path)
        except Exception:
            self._remove_loaded_league_tab(league_path)
            return

        view = self.loaded_league_views.get(league_path)
        if not isinstance(view, dict):
            return

        summary_view = view.get("summary_view")
        decklist_view = view.get("decklist_view")
        if isinstance(summary_view, QTextEdit):
            summary_view.setPlainText(self._render_loaded_league_summary(snapshot))
        if isinstance(decklist_view, QTextEdit):
            decklist_view.setPlainText(self._render_loaded_league_decklist(snapshot))

        self._populate_match_tabs_for_league(league_path, snapshot)

    def _mark_loaded_league_dirty(self, league_path: str, dirty: bool) -> None:
        view = self.loaded_league_views.get(league_path)
        if not isinstance(view, dict):
            return
        view["dirty"] = dirty

        if self.loaded_leagues_tabs is None:
            return

        for idx in range(self.loaded_leagues_tabs.count()):
            widget = self.loaded_leagues_tabs.widget(idx)
            if not isinstance(widget, QWidget):
                continue
            if str(widget.property("league_path") or "") != league_path:
                continue

            base = str(widget.property("base_title") or self.loaded_leagues_tabs.tabText(idx).rstrip(" *"))
            widget.setProperty("base_title", base)
            self.loaded_leagues_tabs.setTabText(idx, f"{base} *" if dirty else base)
            break

    def _load_or_focus_loaded_league(self, league_path: str) -> None:
        if self.loaded_leagues_tabs is None:
            return

        for idx in range(self.loaded_leagues_tabs.count()):
            widget = self.loaded_leagues_tabs.widget(idx)
            if not isinstance(widget, QWidget):
                continue
            if str(widget.property("league_path") or "") == league_path:
                self.loaded_leagues_tabs.setCurrentIndex(idx)
                return

        try:
            snapshot = self.storage.get_league_snapshot(league_path)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Load League Failed", str(exc))
            return

        if self.loaded_leagues_tabs.count() == 1:
            placeholder = self.loaded_leagues_tabs.widget(0)
            if isinstance(placeholder, QTextEdit) and self.loaded_leagues_tabs.tabText(0) == "No League":
                self.loaded_leagues_tabs.removeTab(0)

        league_widget = self._build_loaded_league_view(league_path, snapshot)
        league_widget.setProperty("league_path", league_path)
        title = f"{snapshot.get('meta', {}).get('event_type', '')} {snapshot.get('meta', {}).get('league_id', '')}".strip()
        if not title:
            title = Path(league_path).name
        
        matches_count = len(snapshot.get('matches', {}))
        meta = snapshot.get('meta', {})
        max_matches = meta.get('max_matches')
        title_base = f"{meta.get('event_type', '')} {meta.get('league_id', '')}".strip()
        if not title_base:
            title_base = Path(league_path).name
        
        if max_matches is not None:
            title = f"{title_base} [{matches_count}/{max_matches}]"
        else:
            title = f"{title_base} [{matches_count}]"
        
        league_widget.setProperty("base_title", title)
        index = self.loaded_leagues_tabs.addTab(league_widget, title)
        self.loaded_leagues_tabs.setCurrentIndex(index)

    def _save_all_loaded_league_changes(self, league_path: str) -> bool:
        view = self.loaded_league_views.get(league_path)
        if not isinstance(view, dict):
            return True

        matches = view.get("matches")
        if not isinstance(matches, dict):
            return True

        changed = False
        for match_id, bundle in matches.items():
            if not isinstance(bundle, dict):
                continue
            original = bundle.get("original", {})
            if not isinstance(original, dict):
                continue
            sideboard_edit = bundle.get("sideboard_edit")
            key_moments_edit = bundle.get("key_moments_edit")
            observations_edit = bundle.get("observations_edit")
            if not isinstance(sideboard_edit, QTextEdit) or not isinstance(key_moments_edit, QTextEdit) or not isinstance(observations_edit, QTextEdit):
                continue

            current_sideboard = sideboard_edit.toPlainText()
            current_key_moments = key_moments_edit.toPlainText()
            current_observations = observations_edit.toPlainText()
            if (
                current_sideboard == str(original.get("sideboard_notes", ""))
                and current_key_moments == str(original.get("key_moments", ""))
                and current_observations == str(original.get("observations", ""))
            ):
                continue

            try:
                self.storage.update_match_notes(
                    league_path,
                    str(match_id),
                    current_sideboard,
                    current_key_moments,
                    current_observations,
                )
            except Exception as exc:  # pragma: no cover
                QMessageBox.critical(self, "Save Failed", str(exc))
                return False
            changed = True

        if changed:
            self._append_status(f"Saved pending match-note changes for {league_path}")
        self._mark_loaded_league_dirty(league_path, False)
        return True

    def _remove_loaded_league_tab(self, league_path: str) -> None:
        if self.loaded_leagues_tabs is None:
            return
        for idx in range(self.loaded_leagues_tabs.count()):
            widget = self.loaded_leagues_tabs.widget(idx)
            if not isinstance(widget, QWidget):
                continue
            if str(widget.property("league_path") or "") != league_path:
                continue
            self.loaded_leagues_tabs.removeTab(idx)
            break

        if league_path in self.loaded_league_views:
            del self.loaded_league_views[league_path]

        if self.loaded_leagues_tabs.count() == 0:
            placeholder = QTextEdit()
            placeholder.setReadOnly(True)
            placeholder.setPlainText("No loaded leagues. Use League Browser -> Load Selected.")
            self.loaded_leagues_tabs.addTab(placeholder, "No League")

    def _on_close_loaded_league_tab(self, index: int) -> None:
        if self.loaded_leagues_tabs is None:
            return
        widget = self.loaded_leagues_tabs.widget(index)
        if not isinstance(widget, QWidget):
            return

        league_path = str(widget.property("league_path") or "")
        if not league_path:
            self.loaded_leagues_tabs.removeTab(index)
            return

        view = self.loaded_league_views.get(league_path, {})
        is_dirty = bool(view.get("dirty", False)) if isinstance(view, dict) else False
        if is_dirty:
            choice = QMessageBox.question(
                self,
                "Unsaved Changes",
                "This loaded league has unsaved match-note changes. Save before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if choice == QMessageBox.Cancel:
                return
            if choice == QMessageBox.Save and not self._save_all_loaded_league_changes(league_path):
                return

        self._remove_loaded_league_tab(league_path)

    def _build_game_row(self, game_no: int) -> dict[str, object]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        play_draw = QComboBox()
        play_draw.addItems(["Play", "Draw"])
        play_draw.setToolTip("Play means you start, Draw means you are on the draw")

        hand_inputs: dict[int, QComboBox] = {}
        for hand_size in HAND_TRACKED_SIZES:
            hand_input = QComboBox()
            hand_input.addItems(self.hand_type_options)
            hand_input.setToolTip(
                f"{hand_size}-card hand category. Use Configure Lists to add, rename, or remove values."
            )
            hand_input.currentTextChanged.connect(
                lambda _text, gno=game_no: self._on_hand_sequence_changed(gno)
            )
            hand_inputs[hand_size] = hand_input

        # Initially only the 7-card hand is active.
        for hand_size, hand_input in hand_inputs.items():
            hand_input.setEnabled(hand_size == 7)

        mulligan_suggested = QCheckBox("Suggest")
        mulligan_suggested.setToolTip(
            "Auto mulligan suggestion based on the hand types listed in Configure Lists"
        )

        mulligan_count = QSpinBox()
        mulligan_count.setRange(0, 4)
        mulligan_count.setValue(0)
        mulligan_count.setToolTip(
            "How many times you mulliganed (0 keeps 7, 1 keeps 6, 2 keeps 5, 3 keeps 4, 4 means 3 or fewer)"
        )

        opening_hand_label = QLabel("7 cards")
        opening_hand_label.setToolTip(
            "Opening hand size after mulligans (calculated automatically)"
        )

        draw_type = QComboBox()
        draw_type.addItems(self.draw_type_options)
        draw_type.setToolTip(
            "How your draws felt in this game. Use Configure Lists to customize the available labels."
        )

        result = QComboBox()
        result.addItems(["Win", "Loss"])
        result.setToolTip("Result of this individual game")

        mulligan_count.valueChanged.connect(
            lambda value, gno=game_no: self._on_mulligan_count_changed(gno, value)
        )

        layout.addWidget(QLabel("P/D"))
        layout.addWidget(play_draw)
        layout.addWidget(QLabel("H7"))
        layout.addWidget(hand_inputs[7])
        layout.addWidget(QLabel("H6"))
        layout.addWidget(hand_inputs[6])
        layout.addWidget(QLabel("H5"))
        layout.addWidget(hand_inputs[5])
        layout.addWidget(QLabel("H4"))
        layout.addWidget(hand_inputs[4])
        layout.addWidget(QLabel("H3-"))
        layout.addWidget(hand_inputs[3])
        layout.addWidget(mulligan_suggested)
        layout.addWidget(QLabel("Mulls"))
        layout.addWidget(mulligan_count)
        layout.addWidget(opening_hand_label)
        layout.addWidget(QLabel("Draw"))
        layout.addWidget(draw_type)
        layout.addWidget(QLabel("Result"))
        layout.addWidget(result)
        layout.addStretch(1)

        return {
            "container": container,
            "play_draw": play_draw,
            "hand_inputs": hand_inputs,
            "mulligan_suggested": mulligan_suggested,
            "mulligan_count": mulligan_count,
            "opening_hand_label": opening_hand_label,
            "draw_type": draw_type,
            "result": result,
        }

    def _load_deck_memory(self) -> None:
        self.deck_memory = self.storage.get_all_deck_memory()
        self.starter_decks_by_format = self._load_starter_decks_by_format()
        self._refresh_deck_choices_for_format()

    def _load_starter_decks_by_format(self) -> dict[str, list[str]]:
        path = self.storage.config_root / "starter_decks_by_format.json"
        if not path.exists():
            return {}

        try:
            raw = self.storage.read_json(path)
        except Exception:
            return {}

        out: dict[str, list[str]] = {}
        if not isinstance(raw, dict):
            return out

        for fmt, values in raw.items():
            fmt_name = str(fmt).strip()
            if not fmt_name or not isinstance(values, list):
                continue
            cleaned = sorted({str(v).strip() for v in values if str(v).strip()})
            if cleaned:
                out[fmt_name.lower()] = cleaned
        return out

    def _on_format_changed(self, _value: str) -> None:
        # Format selector drives deck dropdown filtering.
        if self.current_league_path:
            return
        self._refresh_deck_choices_for_format()

    def _refresh_deck_choices_for_format(self, preferred_format: str | None = None) -> None:
        current_my_deck = self.deck_name.currentText().strip()
        current_opponent_deck = self.opponent_deck.currentText().strip()

        self.deck_name.clear()
        self.opponent_deck.clear()
        self._deck_names_model.setStringList([])

        names = sorted(self.deck_memory.keys())
        if not names:
            return

        selected_format = (preferred_format or self.format_name.currentText() or "").strip().lower()
        allowed = {n.lower() for n in self.starter_decks_by_format.get(selected_format, [])}
        if allowed:
            filtered = [name for name in names if name.lower() in allowed]
            names = filtered if filtered else names

        self._deck_names_model.setStringList(names)
        self.deck_name.addItems(names)
        self.opponent_deck.addItems(names)

        if current_my_deck:
            self.deck_name.setCurrentText(current_my_deck)
        if current_opponent_deck:
            self.opponent_deck.setCurrentText(current_opponent_deck)

        self._on_my_deck_changed(self.deck_name.currentText())
        self._on_opponent_deck_changed(self.opponent_deck.currentText())

    def _add_archetype_to_combos(self, archetype: str) -> None:
        archetype = archetype.strip()
        if not archetype:
            return

        existing = [self.deck_archetype.itemText(i).strip().lower() for i in range(self.deck_archetype.count())]
        if archetype.lower() not in existing:
            self.deck_archetype.addItem(archetype)
            self.opponent_archetype.addItem(archetype)

        self.deck_archetype.setCurrentText(archetype)
        self.opponent_archetype.setCurrentText(archetype)

    def _reload_archetype_options(self) -> None:
        current_my = self.deck_archetype.currentText().strip()
        current_opponent = self.opponent_archetype.currentText().strip()

        self.archetype_options = self.storage.get_archetype_options(DEFAULT_ARCHETYPE_OPTIONS)

        self.deck_archetype.clear()
        self.opponent_archetype.clear()
        self.deck_archetype.addItems(self.archetype_options)
        self.opponent_archetype.addItems(self.archetype_options)

        if current_my:
            self.deck_archetype.setCurrentText(current_my)
        if current_opponent:
            self.opponent_archetype.setCurrentText(current_opponent)

    def _prompt_add_archetype(self, initial_value: str = "") -> None:
        archetype, ok = QInputDialog.getText(
            self,
            "Add Archetype",
            "Archetype name:",
            text=initial_value,
        )
        if not ok:
            return
        archetype = archetype.strip()
        if not archetype:
            QMessageBox.warning(self, "Missing data", "Archetype cannot be empty.")
            return

        self.storage.add_archetype_option(archetype)
        self._add_archetype_to_combos(archetype)
        self._append_status(f"Added archetype: {archetype}")

    def _on_add_my_archetype(self) -> None:
        self._prompt_add_archetype(self.deck_archetype.currentText().strip())

    def _on_add_opponent_archetype(self) -> None:
        self._prompt_add_archetype(self.opponent_archetype.currentText().strip())

    def _on_add_my_deck(self) -> None:
        deck_name = self.deck_name.currentText().strip()
        archetype = self.deck_archetype.currentText().strip()
        if not deck_name:
            QMessageBox.warning(self, "Missing data", "Deck name cannot be empty.")
            return
        if not archetype:
            QMessageBox.warning(self, "Missing data", "Archetype cannot be empty.")
            return

        self.storage.add_archetype_option(archetype)
        self.storage.upsert_deck_memory(deck_name, archetype)
        self._add_archetype_to_combos(archetype)
        self._load_deck_memory()
        self.deck_name.setCurrentText(deck_name)
        self.deck_archetype.setCurrentText(archetype)
        self._append_status(f"Saved deck: {deck_name} -> {archetype}")

    def _on_add_opponent_deck(self) -> None:
        deck_name = self.opponent_deck.currentText().strip()
        archetype = self.opponent_archetype.currentText().strip()
        if not deck_name:
            QMessageBox.warning(self, "Missing data", "Deck name cannot be empty.")
            return
        if not archetype:
            QMessageBox.warning(self, "Missing data", "Archetype cannot be empty.")
            return

        self.storage.add_archetype_option(archetype)
        self.storage.upsert_deck_memory(deck_name, archetype)
        self._add_archetype_to_combos(archetype)
        self._load_deck_memory()
        self.opponent_deck.setCurrentText(deck_name)
        self.opponent_archetype.setCurrentText(archetype)
        self._append_status(f"Saved opponent deck: {deck_name} -> {archetype}")

    def _on_manage_decks(self) -> None:
        dialog = DeckManagerDialog(self.storage.get_all_deck_memory(), self)
        if dialog.exec() != QDialog.Accepted:
            return

        deck_memory, error_message = dialog.get_deck_memory()
        if error_message:
            QMessageBox.warning(self, "Invalid data", error_message)
            return

        self.storage.set_deck_memory(deck_memory)
        for archetype in deck_memory.values():
            self.storage.add_archetype_option(archetype)

        self._reload_archetype_options()
        self._load_deck_memory()
        self._append_status(f"Updated deck memory ({len(deck_memory)} entries)")

    def _load_game_defaults(self) -> None:
        defaults = self.storage.get_last_game_defaults()
        for row in self.game_rows:
            row["play_draw"].setCurrentText(str(defaults.get("play_draw", "Play")))

            hand_inputs = row["hand_inputs"]
            default_hand_7 = str(defaults.get("hand_7", defaults.get("hand_type", "Good")))
            hand_inputs[7].setCurrentText(default_hand_7)
            hand_inputs[6].setCurrentText(str(defaults.get("hand_6", default_hand_7)))
            hand_inputs[5].setCurrentText(str(defaults.get("hand_5", default_hand_7)))
            hand_inputs[4].setCurrentText(str(defaults.get("hand_4", default_hand_7)))
            hand_inputs[3].setCurrentText(str(defaults.get("hand_3", default_hand_7)))

            row["mulligan_suggested"].setChecked(bool(defaults.get("mulligan_suggested", False)))
            row["mulligan_count"].setValue(int(defaults.get("mulligan_count", 0)))
            row["draw_type"].setCurrentText(str(defaults.get("draw_type", "Normal")))
            row["result"].setCurrentText(str(defaults.get("result", "Win")))
            self._refresh_row_mulligan_state(row)

    def _on_mulligan_count_changed(self, game_no: int, count: int) -> None:
        if game_no < 1 or game_no > len(self.game_rows):
            return
        row = self.game_rows[game_no - 1]
        if count < 0:
            return
        self._refresh_row_mulligan_state(row)

    def _on_score_changed(self, score: str) -> None:
        expected_games = 3 if score in {"2-1", "1-2"} else 2
        for idx, row in enumerate(self.game_rows, start=1):
            enabled = idx <= expected_games
            row["container"].setEnabled(enabled)

        # Auto-fill per-game outcomes from the selected match score.
        result_pattern = SCORE_TO_GAME_RESULTS.get(score, [])
        for idx, row in enumerate(self.game_rows, start=1):
            if idx <= len(result_pattern):
                row["result"].setCurrentText(result_pattern[idx - 1])

    def _on_hand_sequence_changed(self, game_no: int) -> None:
        if game_no < 1 or game_no > len(self.game_rows):
            return
        row = self.game_rows[game_no - 1]
        hand_inputs = row["hand_inputs"]
        mulligan_count = row["mulligan_count"].value()

        current_hand_size = max(3, 7 - mulligan_count)
        current_hand_type = hand_inputs[current_hand_size].currentText()

        if current_hand_type in self.auto_mulligan_hand_types and mulligan_count < 4:
            row["mulligan_count"].setValue(mulligan_count + 1)
            return

        self._refresh_row_mulligan_state(row)

    def _refresh_row_mulligan_state(self, row: dict[str, object]) -> None:
        mulligan_count = row["mulligan_count"].value()
        enabled_hands_count = mulligan_count + 1

        for idx, hand_size in enumerate(HAND_TRACKED_SIZES):
            enabled = idx < enabled_hands_count
            row["hand_inputs"][hand_size].setEnabled(enabled)

        opening_hand = max(3, 7 - mulligan_count)
        row["opening_hand_label"].setText(f"{opening_hand} cards")

        seen_no_lands = False
        for idx, hand_size in enumerate(HAND_TRACKED_SIZES):
            if idx >= enabled_hands_count:
                break
            if row["hand_inputs"][hand_size].currentText() in self.auto_mulligan_hand_types:
                seen_no_lands = True
                break

        row["mulligan_suggested"].setChecked(seen_no_lands)

    def _collect_games_payload(self) -> list[GameInput]:
        expected_games = 3 if self.score.currentText() in {"2-1", "1-2"} else 2
        games: list[GameInput] = []
        for idx, row in enumerate(self.game_rows, start=1):
            if idx > expected_games:
                continue

            mulligan_count = row["mulligan_count"].value()
            enabled_hands_count = mulligan_count + 1
            hand_sequence = [
                row["hand_inputs"][hand_size].currentText()
                for seq_idx, hand_size in enumerate(HAND_TRACKED_SIZES)
                if seq_idx < enabled_hands_count
            ]

            games.append(
                GameInput(
                    game_no=idx,
                    play_draw=row["play_draw"].currentText(),
                    hand_type=hand_sequence[-1],
                    hand_sequence=hand_sequence,
                    mulligan_suggested=row["mulligan_suggested"].isChecked(),
                    mulligan_count=mulligan_count,
                    opening_hand_size=max(3, 7 - mulligan_count),
                    draw_type=row["draw_type"].currentText(),
                    result=row["result"].currentText(),
                )
            )
        return games

    def _persist_game_defaults(self) -> None:
        first = self.game_rows[0]
        self.storage.save_last_game_defaults(
            {
                "play_draw": first["play_draw"].currentText(),
                "hand_7": first["hand_inputs"][7].currentText(),
                "hand_6": first["hand_inputs"][6].currentText(),
                "hand_5": first["hand_inputs"][5].currentText(),
                "hand_4": first["hand_inputs"][4].currentText(),
                "hand_3": first["hand_inputs"][3].currentText(),
                "mulligan_suggested": first["mulligan_suggested"].isChecked(),
                "mulligan_count": first["mulligan_count"].value(),
                "opening_hand_size": max(3, 7 - first["mulligan_count"].value()),
                "draw_type": first["draw_type"].currentText(),
                "result": first["result"].currentText(),
            }
        )

    def _on_opponent_deck_changed(self, deck_name: str) -> None:
        deck_name = deck_name.strip()
        if not deck_name:
            return

        archetype = self.storage.get_deck_archetype(deck_name)
        if archetype:
            self.opponent_archetype.setCurrentText(archetype)

    def _on_my_deck_changed(self, deck_name: str) -> None:
        deck_name = deck_name.strip()
        if not deck_name:
            return

        archetype = self.storage.get_deck_archetype(deck_name)
        if archetype:
            self.deck_archetype.setCurrentText(archetype)

        if not self.deck_list_name.text().strip():
            self.deck_list_name.setText(f"{deck_name}_V1")

    def _on_import_decklist(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Decklist",
            str(Path.home()),
            "Decklist Files (*.txt *.dek);;All Files (*.*)",
        )
        if not file_path:
            return

        try:
            file_ext = Path(file_path).suffix.lower()
            if file_ext == ".dek":
                content = self._parse_mtgo_dek_file(Path(file_path))
            else:
                content = Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = Path(file_path).read_text(encoding="latin-1")
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Import Failed", str(exc))
            return

        if not content.strip():
            QMessageBox.warning(self, "Empty file", "Selected decklist file is empty.")
            return

        self.imported_decklist_path = str(file_path)
        self.imported_decklist_content = content
        self.decklist_import_label.setText(Path(file_path).name)

        self.deck_list_name.setText(self._deck_list_name_from_file(file_path))
        self._warn_if_unusual_decklist_size(content)
        self._refresh_text_autocomplete_terms(content)

        self._append_status(f"Imported decklist file: {file_path}")

    def _deck_list_name_from_file(self, file_path: str) -> str:
        stem = Path(file_path).stem.strip()
        stem = re.sub(r"^deck\s*[-_]\s*", "", stem, flags=re.IGNORECASE)
        return stem or "Imported_Decklist"

    def _parse_mtgo_dek_file(self, deck_path: Path) -> str:
        text = deck_path.read_text(encoding="utf-8")
        root = ET.fromstring(text)

        mainboard: list[str] = []
        sideboard: list[str] = []

        for card in root.findall(".//Cards"):
            quantity_raw = card.attrib.get("Quantity", "0").strip()
            card_name = card.attrib.get("Name", "").strip()
            sideboard_raw = card.attrib.get("Sideboard", "false").strip().lower()

            if not card_name:
                continue

            try:
                quantity = int(quantity_raw)
            except ValueError:
                quantity = 0

            if quantity <= 0:
                continue

            line = f"{quantity} {card_name}"
            if sideboard_raw == "true":
                sideboard.append(line)
            else:
                mainboard.append(line)

        if not mainboard and not sideboard:
            raise ValueError("No cards found in .dek file.")

        lines: list[str] = []
        lines.extend(mainboard)
        lines.append("")
        lines.extend(sideboard)
        return "\n".join(lines).strip() + "\n"

    def _warn_if_unusual_decklist_size(self, content: str) -> None:
        main_count, side_count, parsed_lines = self._count_decklist_cards(content)
        if parsed_lines == 0:
            self._append_status("Decklist imported, but card quantities were not detected.")
            return

        summary = (
            f"Decklist cards detected: Mainboard {main_count}, Sideboard {side_count}."
        )

        if main_count != 60:
            QMessageBox.warning(
                self,
                "Decklist Size Warning",
                summary + "\n\nMainboard is not 60 cards. This is allowed, but please verify the list.",
            )
        else:
            QMessageBox.information(self, "Decklist Imported", summary)

    def _count_decklist_cards(self, content: str) -> tuple[int, int, int]:
        main_count = 0
        side_count = 0
        parsed_lines = 0
        in_sideboard = False

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                if not in_sideboard and parsed_lines > 0:
                    in_sideboard = True
                continue

            lower = line.lower().rstrip(":")
            if lower in {"sideboard", "sb"}:
                in_sideboard = True
                continue

            match = CARD_LINE_RE.match(line)
            if not match:
                continue

            quantity = int(match.group(1))
            parsed_lines += 1
            if in_sideboard:
                side_count += quantity
            else:
                main_count += quantity

        return main_count, side_count, parsed_lines

    def _on_clear_decklist(self) -> None:
        self.imported_decklist_path = ""
        self.imported_decklist_content = ""
        self.decklist_import_label.setText("No file imported")
        self._refresh_text_autocomplete_terms()

    def _on_create_league(self) -> None:
        deck_name = self.deck_name.currentText().strip()
        deck_archetype = self.deck_archetype.currentText().strip()

        if not deck_name or not deck_archetype:
            QMessageBox.warning(self, "Missing data", "Deck and archetype are required.")
            return

        tournament_structure = self._build_tournament_structure_payload()
        if tournament_structure is None:
            return

        payload = LeagueCreateInput(
            event_type=self.event_type.currentText(),
            format_name=self.format_name.currentText().strip() or "Modern",
            deck_name=deck_name,
            deck_archetype=deck_archetype,
            moxfield_url=self.moxfield_url.text().strip(),
            changes=self.changes.toPlainText().strip(),
            goal=self.goal.toPlainText().strip(),
            concerns=self.concerns.toPlainText().strip(),
            notes=self.notes.toPlainText().strip(),
            deck_list_name=self.deck_list_name.text().strip(),
            deck_list_content=self.imported_decklist_content,
            deck_list_source=self.imported_decklist_path,
            tournament_structure=tournament_structure,
        )

        try:
            result = self.storage.create_league(payload)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Create League Failed", str(exc))
            return

        self.current_league_path = result.league_path
        self.storage.set_active_league_path(result.league_path)
        self._set_active_league_ui(result.league_path)
        self.add_match_btn.setEnabled(True)
        self.end_league_btn.setEnabled(True)
        self._load_deck_memory()
        self._refresh_league_selector(select_path=result.league_path)
        self._load_or_focus_loaded_league(result.league_path)
        self._refresh_match_notes_tabs()
        self._on_clear_decklist()
        self._append_status(f"Created league {result.league_id} at {result.league_path}")

    def _on_add_match(self) -> None:
        if not self.current_league_path:
            QMessageBox.warning(self, "No active league", "Create a league first.")
            return

        try:
            can_add = self.storage.can_add_match(self.current_league_path)
            if not can_add.get("allowed", False):
                if can_add.get("requires_top8_decision", False):
                    choice = QMessageBox.question(
                        self,
                        "Swiss Completed",
                        "Swiss rounds are complete. Did you make Top 8?",
                        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                        QMessageBox.Yes,
                    )
                    if choice == QMessageBox.Cancel:
                        return

                    qualified = choice == QMessageBox.Yes
                    self.storage.set_top8_qualification(self.current_league_path, qualified)

                    if qualified:
                        self._append_status("Top 8 confirmed. Tournament moved to Single Elimination phase.")
                    else:
                        try:
                            summary = self.storage.complete_league(self.current_league_path)
                            self._append_status(
                                "Ended league "
                                f"{summary['league_id']} with record {summary['record']} ({summary['winrate']:.1f}% WR)"
                            )
                        except Exception:
                            # Non-blocking: we still close the active league in UI.
                            pass
                        self._append_status("Top 8 not reached. Tournament marked as completed.")
                        self._set_active_league_ui(None)
                        self.storage.clear_active_league_path()
                        self._refresh_league_selector()
                        self._refresh_match_notes_tabs()
                        return

                    can_add = self.storage.can_add_match(self.current_league_path)
                    if not can_add.get("allowed", False):
                        QMessageBox.warning(self, "Cannot add match", str(can_add.get("reason", "")))
                        return
                else:
                    QMessageBox.warning(self, "Cannot add match", str(can_add.get("reason", "")))
                    return
        except Exception as exc:
            QMessageBox.critical(self, "Validation Failed", str(exc))
            return

        opponent_deck = self.opponent_deck.currentText().strip()
        opponent_archetype = self.opponent_archetype.currentText().strip()

        if not opponent_deck or not opponent_archetype:
            QMessageBox.warning(self, "Missing data", "Opponent deck and archetype are required.")
            return

        payload = MatchCreateInput(
            opponent_deck=opponent_deck,
            opponent_archetype=opponent_archetype,
            score=self.score.currentText(),
            games=self._collect_games_payload(),
            sideboard_notes=self.sideboard_notes.toPlainText().strip(),
            key_moments=self.key_moments.toPlainText().strip(),
            observations=self.observations.toPlainText().strip(),
        )

        try:
            result = self.storage.create_match(self.current_league_path, payload)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Add Match Failed", str(exc))
            return

        self.storage.upsert_deck_memory(opponent_deck, opponent_archetype)
        self._persist_game_defaults()
        self._load_deck_memory()
        self._append_status(f"Added match {result.match_id} ({result.meta.score}, {result.meta.match_result})")

        self.sideboard_notes.clear()
        self.key_moments.clear()
        self.observations.clear()

        try:
            latest_snapshot = self.storage.get_league_snapshot(self.current_league_path)
            latest_meta = latest_snapshot.get("meta", {})
            if str(latest_meta.get("status", "active")).lower() == "completed":
                try:
                    summary = self.storage.complete_league(self.current_league_path)
                    self._append_status(
                        "Ended league "
                        f"{summary['league_id']} with record {summary['record']} ({summary['winrate']:.1f}% WR)"
                    )
                except Exception:
                    pass
                self._append_status("Tournament completed. Active league closed automatically.")
                self.storage.clear_active_league_path()
                self._set_active_league_ui(None)
                self._refresh_league_selector()
        except Exception:
            # Non-blocking: match was already saved.
            pass

        self._refresh_match_notes_tabs()

    def _on_end_league(self) -> None:
        if not self.current_league_path:
            QMessageBox.warning(self, "No active league", "Create a league first.")
            return

        try:
            summary = self.storage.complete_league(self.current_league_path)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "End League Failed", str(exc))
            return

        self.add_match_btn.setEnabled(False)
        self.end_league_btn.setEnabled(False)
        self.storage.clear_active_league_path()
        self.current_league_path = None
        self.current_league_label.setText("Active league: none")
        self._refresh_league_selector()
        self._refresh_match_notes_tabs()
        self._append_status(
            "Ended league "
            f"{summary['league_id']} with record {summary['record']} ({summary['winrate']:.1f}% WR)"
        )

    def _league_label(self, item: dict[str, object]) -> str:
        date = str(item.get("date", ""))
        event_type = str(item.get("event_type", ""))
        league_id = str(item.get("league_id", ""))
        record = f"{int(item.get('wins', 0))}-{int(item.get('losses', 0))}"
        deck_name = str(item.get("deck_name", ""))
        status = str(item.get("status", "active"))
        total = int(item.get("wins", 0)) + int(item.get("losses", 0))
        winrate = (int(item.get("wins", 0)) / total * 100.0) if total > 0 else 0.0
        return f"{date} | {event_type} | {league_id} | {record} ({winrate:.1f}%) | {deck_name} | {status}"

    def _league_sort_key(self, item: dict[str, object]) -> tuple[float, str]:
        wins = int(item.get("wins", 0))
        losses = int(item.get("losses", 0))
        total = wins + losses
        winrate = (wins / total) if total > 0 else 0.0
        date = str(item.get("date", ""))
        mode = self.league_sort.currentText()
        if mode == "Date (oldest)":
            return (0.0, date)
        if mode == "Winrate (high)":
            return (-winrate, f"{date}|{item.get('league_id', '')}")
        if mode == "Winrate (low)":
            return (winrate, f"{date}|{item.get('league_id', '')}")
        return (0.0, f"~{date}")

    def _refresh_league_selector(self, _unused: str | None = None, select_path: str | None = None) -> None:
        leagues = self.storage.list_leagues()
        self._refresh_stats_event_options(leagues)
        filter_value = self.league_filter.currentText().strip().lower() if hasattr(self, "league_filter") else "all"
        if filter_value == "active":
            leagues = [x for x in leagues if str(x.get("status", "")).lower() == "active"]
        elif filter_value == "completed":
            leagues = [x for x in leagues if str(x.get("status", "")).lower() == "completed"]

        sort_mode = self.league_sort.currentText() if hasattr(self, "league_sort") else "Date (newest)"
        if sort_mode == "Date (newest)":
            leagues = sorted(leagues, key=lambda x: str(x.get("date", "")), reverse=True)
        elif sort_mode == "Date (oldest)":
            leagues = sorted(leagues, key=lambda x: str(x.get("date", "")))
        elif sort_mode == "Winrate (high)":
            leagues = sorted(
                leagues,
                key=lambda x: (
                    -(
                        (int(x.get("wins", 0)) / (int(x.get("wins", 0)) + int(x.get("losses", 0))))
                        if (int(x.get("wins", 0)) + int(x.get("losses", 0))) > 0
                        else 0.0
                    ),
                    str(x.get("date", "")),
                ),
            )
        elif sort_mode == "Winrate (low)":
            leagues = sorted(
                leagues,
                key=lambda x: (
                    (
                        (int(x.get("wins", 0)) / (int(x.get("wins", 0)) + int(x.get("losses", 0))))
                        if (int(x.get("wins", 0)) + int(x.get("losses", 0))) > 0
                        else 0.0
                    ),
                    str(x.get("date", "")),
                ),
            )

        previous_path = select_path
        if previous_path is None and self.league_selector.count() > 0:
            previous_path = str(self.league_selector.currentData() or "")

        previous_decklist_path = ""
        if hasattr(self, "decklist_league_selector") and self.decklist_league_selector.count() > 0:
            previous_decklist_path = str(self.decklist_league_selector.currentData() or "")

        self.league_selector.blockSignals(True)
        self.league_selector.clear()
        if hasattr(self, "decklist_league_selector"):
            self.decklist_league_selector.blockSignals(True)
            self.decklist_league_selector.clear()
        for league in leagues:
            path = str(league["path"])
            self.league_selector.addItem(self._league_label(league), path)
            if hasattr(self, "decklist_league_selector"):
                self.decklist_league_selector.addItem(self._league_label(league), path)
        self.league_selector.blockSignals(False)
        if hasattr(self, "decklist_league_selector"):
            self.decklist_league_selector.blockSignals(False)

        if self.league_selector.count() == 0:
            self.load_selected_btn.setEnabled(False)
            self.preview_league_btn.setEnabled(False)
            if hasattr(self, "decklist_league_selector"):
                self.decklist_viewer.clear()
            return

        self.load_selected_btn.setEnabled(True)
        self.preview_league_btn.setEnabled(True)

        if previous_path:
            for idx in range(self.league_selector.count()):
                if str(self.league_selector.itemData(idx)) == previous_path:
                    self.league_selector.setCurrentIndex(idx)
                    if hasattr(self, "decklist_league_selector"):
                        self.decklist_league_selector.setCurrentIndex(idx)
                    return

        self.league_selector.setCurrentIndex(0)
        if hasattr(self, "decklist_league_selector"):
            if previous_decklist_path:
                for idx in range(self.decklist_league_selector.count()):
                    if str(self.decklist_league_selector.itemData(idx)) == previous_decklist_path:
                        self.decklist_league_selector.setCurrentIndex(idx)
                        break
            else:
                self.decklist_league_selector.setCurrentIndex(0)

    def _restore_active_league(self) -> None:
        stored_active_path = self.storage.get_active_league_path()
        active_path = self._normalize_saved_league_path(stored_active_path)
        if not active_path:
            self.storage.clear_active_league_path()
            self._set_active_league_ui(None)
            return

        if active_path != str(stored_active_path or "").strip():
            self.storage.set_active_league_path(active_path)

        self._set_active_league_ui(active_path)
        self._refresh_league_selector(select_path=active_path)
        self._append_status(f"Restored active league: {active_path}")

    def _set_active_league_ui(self, league_path: str | None) -> None:
        normalized_path = self._normalize_saved_league_path(league_path)
        self.current_league_path = normalized_path
        has_active = bool(normalized_path)
        self.add_match_btn.setEnabled(has_active)
        self.end_league_btn.setEnabled(has_active)

        if not has_active:
            self.current_league_label.setText("Active league: none")
            self._refresh_deck_choices_for_format(self.format_name.currentText())
            self._refresh_text_autocomplete_terms("")
            self._refresh_match_notes_tabs()
            return

        league_dir = Path(normalized_path)
        try:
            label_path = str(league_dir.relative_to(self.repo_root))
        except ValueError:
            label_path = str(league_dir)
        self.current_league_label.setText(f"Active league: {label_path}")

        try:
            meta = self.storage.read_json(league_dir / "meta.json")
            league_format = str(meta.get("format", "")).strip()
        except Exception:
            league_format = ""

        if league_format:
            self.format_name.setCurrentText(league_format)
        self._refresh_deck_choices_for_format(league_format or self.format_name.currentText())
        self._refresh_text_autocomplete_terms()
        self._load_or_focus_loaded_league(normalized_path)
        self._refresh_match_notes_tabs()

    def _on_league_selector_changed(self, _index: int) -> None:
        has_item = self.league_selector.currentIndex() >= 0
        self.load_selected_btn.setEnabled(has_item)
        self.preview_league_btn.setEnabled(has_item)

    def _on_load_selected_league(self) -> None:
        league_path = str(self.league_selector.currentData() or "").strip()
        if not league_path:
            QMessageBox.warning(self, "No selection", "Select a league first.")
            return

        try:
            self.storage.set_active_league_path(league_path)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Load League Failed", str(exc))
            return

        self._set_active_league_ui(league_path)
        self._load_or_focus_loaded_league(league_path)
        self._append_status(f"Loaded active league: {league_path}")

    def _on_preview_selected_league(self) -> None:
        league_path = str(self.league_selector.currentData() or "").strip()
        if not league_path:
            QMessageBox.warning(self, "No selection", "Select a league first.")
            return

        try:
            snapshot = self.storage.get_league_snapshot(league_path)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Preview Failed", str(exc))
            return

        content = self._render_league_snapshot(snapshot)
        title = f"League Preview - {snapshot['meta'].get('league_id', 'unknown')}"
        dialog = LeaguePreviewDialog(title, content, self)
        dialog.exec()

    def _on_load_decklist_view(self) -> None:
        league_path = str(self.decklist_league_selector.currentData() or "").strip()
        if not league_path:
            QMessageBox.warning(self, "No selection", "Select a league first.")
            return

        try:
            snapshot = self.storage.get_league_snapshot(league_path)
        except Exception as exc:  # pragma: no cover
            QMessageBox.critical(self, "Load Decklist Failed", str(exc))
            return

        meta = snapshot.get("meta", {})
        decklist_md = str(snapshot.get("decklist", "")).strip()
        context = meta.get("deck_context", {}) if isinstance(meta, dict) else {}
        notes = ""
        if isinstance(context, dict):
            notes = str(context.get("notes", "")).strip()

        lines = [
            f"League: {meta.get('league_id', '')}",
            f"Date: {meta.get('date', '')}",
            f"Event Type: {meta.get('event_type', '')}",
            f"Deck: {meta.get('deck_name', '')} ({meta.get('deck_archetype', '')})",
            f"Deck List Name: {meta.get('deck_list_name', '')}",
            "",
        ]
        if notes:
            lines.extend(["Notes", notes, ""])

        if decklist_md:
            lines.append(decklist_md)
        else:
            lines.append("No decklist saved for this league.")

        self.decklist_viewer.setPlainText("\n".join(lines))

    def _render_league_snapshot(self, snapshot: dict[str, object]) -> str:
        meta = snapshot["meta"]
        stats = snapshot["stats"]
        matches = snapshot["matches"]
        context = meta.get("deck_context", {}) if isinstance(meta, dict) else {}
        notes = str(context.get("notes", "")).strip() if isinstance(context, dict) else ""
        decklist = str(snapshot.get("decklist", "")).strip()

        lines = [
            "League Snapshot",
            "",
            f"Path: {snapshot['path']}",
            f"Date: {meta.get('date', '')}",
            f"Event Type: {meta.get('event_type', '')}",
            f"Format: {meta.get('format', '')}",
            f"Deck: {meta.get('deck_name', '')} ({meta.get('deck_archetype', '')})",
            f"Deck List Name: {meta.get('deck_list_name', '')}",
            f"Status: {meta.get('status', '')}",
            "",
            "Deck Notes",
            notes or "-",
            "",
            "Summary",
            f"Record: {stats.get('wins', 0)}-{stats.get('losses', 0)}",
            f"Winrate: {stats.get('winrate', 0.0):.1f}%",
            f"Mulligan Rate: {stats.get('mulligan_rate', 0.0):.1f}%",
            f"Mana Screw Rate: {stats.get('mana_screw_rate', 0.0):.1f}%",
            f"Mana Flood Rate: {stats.get('mana_flood_rate', 0.0):.1f}%",
            f"Heavy Mulligan Matches: {stats.get('heavy_mulligan_matches', 0)}",
            "",
            "Decklist",
            decklist or "No decklist saved.",
            "",
            "Matches",
            "",
        ]

        if not matches:
            lines.append("No matches logged yet.")
            return "\n".join(lines)

        for match in matches:
            lines.append(
                f"{match.get('match_id', '')}: {match.get('score', '')} ({match.get('match_result', '')}) vs "
                f"{match.get('opponent_deck', '')} [{match.get('opponent_archetype', '')}]"
            )
        return "\n".join(lines)

    def _append_status(self, message: str) -> None:
        self.status.append(message)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
