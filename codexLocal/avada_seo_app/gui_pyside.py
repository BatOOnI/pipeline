import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .constants import APP_NAME, APP_ORG
from .engine import AvadaSeoEngine, EngineConfig
from .openai_client import OpenAIClient
from .storage import SecureApiKeyStore, SessionDraftStore


class UiBridge(QObject):
    task_done = Signal(int, object)
    task_error = Signal(int, str)
    timeout_decision_requested = Signal(str, int, int)


@dataclass
class OperationToken:
    cancel_requested: bool = False


class PlaceholderCard(QGroupBox):
    generateTextRequested = Signal(str)
    generateImageMetaRequested = Signal(str)
    contentChanged = Signal()

    def __init__(self, placeholder: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.placeholder = placeholder
        if placeholder.block_type == "fusion_li_item":
            self.setTitle(f"{placeholder.pid} [CHECKLIST ITEM]")
            self.setStyleSheet("QGroupBox { border: 1px solid #f5d08a; background: #fffbeb; }")
        else:
            self.setTitle(f"{placeholder.pid} [{placeholder.block_type}:{placeholder.field}]")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        if placeholder.kind == "text":
            root.addWidget(QLabel(f"Sugerowana dlugosc: {placeholder.suggested_words} slow"))
            row1 = QHBoxLayout()
            row1.addWidget(QLabel("Dodatkowy prompt:"))
            self.extra_prompt = QTextEdit()
            self.extra_prompt.setFixedHeight(58)
            row1.addWidget(self.extra_prompt, 1)
            self.generate_text_btn = QPushButton("Zrob tekst")
            self.generate_text_btn.clicked.connect(lambda: self.generateTextRequested.emit(self.placeholder.pid))
            row1.addWidget(self.generate_text_btn)
            root.addLayout(row1)
            root.addWidget(QLabel("Wynik:"))
            self.result = QTextEdit()
            self.result.setMinimumHeight(118)
            self.result.setPlainText(placeholder.original.strip())
            root.addWidget(self.result)
            self.extra_prompt.textChanged.connect(self.contentChanged)
            self.result.textChanged.connect(self.contentChanged)
            return

        row0 = QHBoxLayout()
        row0.addWidget(QLabel("Sciezka/URL zdjecia:"))
        self.image_path = QLineEdit(placeholder.original.strip())
        row0.addWidget(self.image_path, 1)
        browse = QPushButton("Wybierz zdjecie")
        browse.clicked.connect(self._browse_image)
        row0.addWidget(browse)
        root.addLayout(row0)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Prompt metadanych:"))
        self.image_prompt = QTextEdit()
        self.image_prompt.setFixedHeight(58)
        row1.addWidget(self.image_prompt, 1)
        self.generate_meta_btn = QPushButton("Generuj metadane")
        self.generate_meta_btn.clicked.connect(lambda: self.generateImageMetaRequested.emit(self.placeholder.pid))
        row1.addWidget(self.generate_meta_btn)
        root.addLayout(row1)

        form = QFormLayout()
        self.image_filename = QLineEdit()
        self.image_alt = QLineEdit()
        self.image_description = QTextEdit()
        self.image_description.setFixedHeight(68)
        form.addRow("Nazwa pliku:", self.image_filename)
        form.addRow("ALT:", self.image_alt)
        form.addRow("Opis:", self.image_description)
        root.addLayout(form)

        self.image_path.textChanged.connect(self.contentChanged)
        self.image_prompt.textChanged.connect(self.contentChanged)
        self.image_filename.textChanged.connect(self.contentChanged)
        self.image_alt.textChanged.connect(self.contentChanged)
        self.image_description.textChanged.connect(self.contentChanged)

    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Wybierz plik dla {self.placeholder.pid}",
            "",
            "Obrazy (*.png *.jpg *.jpeg *.webp *.gif *.avif);;Wszystkie (*)",
        )
        if path:
            self.image_path.setText(path)

    def get_extra_prompt(self) -> str:
        if self.placeholder.kind != "text":
            return ""
        return self.extra_prompt.toPlainText().strip()

    def get_result(self) -> str:
        if self.placeholder.kind != "text":
            return ""
        return self.result.toPlainText().strip()

    def set_result(self, text: str) -> None:
        if self.placeholder.kind == "text":
            self.result.setPlainText(text)

    def get_image_value(self) -> str:
        if self.placeholder.kind != "image":
            return ""
        return self.image_path.text().strip()

    def get_image_prompt(self) -> str:
        if self.placeholder.kind != "image":
            return ""
        return self.image_prompt.toPlainText().strip()

    def set_image_metadata(self, filename: str, alt: str, description: str) -> None:
        if self.placeholder.kind != "image":
            return
        self.image_filename.setText(filename)
        self.image_alt.setText(alt)
        self.image_description.setPlainText(description)

    def get_image_metadata(self) -> Dict[str, str]:
        if self.placeholder.kind != "image":
            return {}
        return {
            "filename": self.image_filename.text().strip(),
            "alt": self.image_alt.text().strip(),
            "description": self.image_description.toPlainText().strip(),
        }

    def export_state(self) -> Dict[str, Any]:
        if self.placeholder.kind == "text":
            return {"extra_prompt": self.get_extra_prompt(), "result": self.get_result()}
        meta = self.get_image_metadata()
        return {
            "image_path": self.get_image_value(),
            "image_prompt": self.get_image_prompt(),
            "filename": meta.get("filename", ""),
            "alt": meta.get("alt", ""),
            "description": meta.get("description", ""),
        }

    def import_state(self, data: Dict[str, Any]) -> None:
        if self.placeholder.kind == "text":
            self.extra_prompt.setPlainText(str(data.get("extra_prompt", "")))
            val = str(data.get("result", "")).strip()
            if val:
                self.result.setPlainText(val)
            return
        self.image_path.setText(str(data.get("image_path", self.placeholder.original)))
        self.image_prompt.setPlainText(str(data.get("image_prompt", "")))
        self.image_filename.setText(str(data.get("filename", "")))
        self.image_alt.setText(str(data.get("alt", "")))
        self.image_description.setPlainText(str(data.get("description", "")))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AVADA SEO Generator - PySide6")
        self.resize(1500, 980)

        self.engine = AvadaSeoEngine(log=self._log)
        self.key_store = SecureApiKeyStore()
        self.session_store = SessionDraftStore()
        self.settings = QSettings(APP_ORG, APP_NAME)

        self.bridge = UiBridge()
        self.bridge.task_done.connect(self._on_task_done)
        self.bridge.task_error.connect(self._on_task_error)
        self.bridge.timeout_decision_requested.connect(self._on_timeout_decision_requested)

        self._threads: list[threading.Thread] = []
        self._op_seq = 0
        self._ops: Dict[int, Dict[str, Any]] = {}
        self._timeout_waiters: Dict[str, Any] = {}
        self._timeout_lock = threading.Lock()

        self._status_mode = "idle"
        self._status_base_text = "Gotowy"
        self._status_dots = 0
        self.cards: Dict[str, PlaceholderCard] = {}

        self._build_ui()
        self._load_settings()
        self._load_key_secure(silent=True)
        self._load_session_draft()
        self._apply_styles()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #f5f7fb; }
            QTabWidget::pane { border: 1px solid #d8dee9; background: white; }
            QTabBar::tab { padding: 10px 16px; background: #e9eef7; margin-right: 4px; border-radius: 6px; color: #23344d; }
            QTabBar::tab:selected { background: #6f86a6; color: white; font-weight: 600; }
            QGroupBox { border: 1px solid #d4dce8; border-radius: 8px; margin-top: 8px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #173a63; }
            QPushButton { background: #6f86a6; color: white; border: none; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #627895; }
            QPushButton:pressed { background: #566a83; }
            QPushButton:disabled { background: #b8c4d3; color: #eef3fb; }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox { border: 1px solid #c8d3e3; border-radius: 6px; padding: 6px; background: #fff; }
            """
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tab_config = QWidget()
        self.tab_placeholders = QWidget()
        self.tab_strategy_logs = QWidget()
        self.tabs.addTab(self.tab_config, "Konfiguracja")
        self.tabs.addTab(self.tab_placeholders, "Placeholdery")
        self.tabs.addTab(self.tab_strategy_logs, "Strategia i logi")

        self._build_config_tab()
        self._build_placeholders_tab()
        self._build_strategy_logs_tab()

        status_row = QHBoxLayout()
        self.status_chip = QLabel("IDLE")
        self.status_chip.setFixedWidth(110)
        self.status_chip.setAlignment(Qt.AlignCenter)
        self.status_text = QLabel("Wczytaj szablon, aby rozpoczac.")
        self.status_text.setWordWrap(True)
        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 0)
        self.status_progress.setVisible(False)
        self.status_progress.setFixedWidth(260)
        stop_btn = QPushButton("Przerwij aktywne")
        stop_btn.clicked.connect(self._cancel_active_operation)

        status_row.addWidget(self.status_chip)
        status_row.addWidget(self.status_text, 1)
        status_row.addWidget(self.status_progress)
        status_row.addWidget(stop_btn)
        root.addLayout(status_row)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._animate_status)
        self._set_status_idle("Wczytaj szablon, aby rozpoczac.")

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.timeout.connect(self._save_settings_and_draft)
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.timeout.connect(self.show_strategy_preview)

    def _build_config_tab(self) -> None:
        layout = QVBoxLayout(self.tab_config)

        files_box = QGroupBox("Pliki")
        files_layout = QGridLayout(files_box)
        self.template_path = QLineEdit(str(Path.cwd() / "szablon.txt"))
        self.output_path = QLineEdit(str(Path.cwd() / "output_szablon.txt"))
        self.compare_path = QLineEdit(str(Path.cwd() / "output_szablon.txt"))
        self.image_metadata_path = QLineEdit(str(Path.cwd() / "image_metadata.json"))
        self._add_path_row(files_layout, 0, "Plik szablonu:", self.template_path, self._pick_template)
        self._add_path_row(files_layout, 1, "Plik wyjsciowy:", self.output_path, self._pick_output)
        self._add_path_row(files_layout, 2, "Raport porownania:", self.compare_path, self._pick_compare)
        self._add_path_row(files_layout, 3, "Plik metadanych obrazow:", self.image_metadata_path, self._pick_metadata_output)
        self.load_template_btn = QPushButton("Wczytaj szablon")
        self.load_template_btn.clicked.connect(self.load_template)
        self.scan_report_btn = QPushButton("Raport skanowania")
        self.scan_report_btn.clicked.connect(self.show_scan_report)
        self.preview_btn = QPushButton("Podglad strategii")
        self.preview_btn.clicked.connect(self.show_strategy_preview)
        files_layout.addWidget(self.load_template_btn, 4, 0)
        files_layout.addWidget(self.scan_report_btn, 4, 1)
        files_layout.addWidget(self.preview_btn, 4, 2)
        layout.addWidget(files_box)

        api_box = QGroupBox("OpenAI")
        api_layout = QGridLayout(api_box)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(["gpt-5.4-mini", "gpt-5.4", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"])
        api_layout.addWidget(QLabel("OpenAI API Key:"), 0, 0)
        api_layout.addWidget(self.api_key, 0, 1, 1, 3)
        save_key = QPushButton("Zapisz klucz")
        save_key.clicked.connect(self._save_key_secure)
        load_key = QPushButton("Wczytaj klucz")
        load_key.clicked.connect(lambda: self._load_key_secure(silent=False))
        clear_key = QPushButton("Usun klucz")
        clear_key.clicked.connect(self._clear_key_secure)
        api_layout.addWidget(save_key, 0, 4)
        api_layout.addWidget(load_key, 0, 5)
        api_layout.addWidget(clear_key, 0, 6)
        api_layout.addWidget(QLabel("Model:"), 1, 0)
        api_layout.addWidget(self.model, 1, 1)
        refresh_models = QPushButton("Aktualizuj modele")
        refresh_models.clicked.connect(self.refresh_models)
        api_layout.addWidget(refresh_models, 1, 2)

        self.generation_mode = QComboBox()
        self.generation_mode.addItems(["text-only", "text+image"])
        self.language_mode = QComboBox()
        self.language_mode.addItems(["auto", "english-only", "polish-only"])
        self.link_mode = QComboBox()
        self.link_mode.addItems(["strict-html-internal", "normal"])
        self.format_mode = QComboBox()
        self.format_mode.addItems(["avada-strict", "normal"])
        api_layout.addWidget(QLabel("Tryb:"), 1, 3)
        api_layout.addWidget(self.generation_mode, 1, 4)
        api_layout.addWidget(QLabel("Jezyk:"), 1, 5)
        api_layout.addWidget(self.language_mode, 1, 6)
        api_layout.addWidget(QLabel("Linki:"), 2, 0)
        api_layout.addWidget(self.link_mode, 2, 1)
        api_layout.addWidget(QLabel("Format:"), 2, 2)
        api_layout.addWidget(self.format_mode, 2, 3)
        layout.addWidget(api_box)

        prompt_box = QGroupBox("Glowny prompt strony")
        p_layout = QVBoxLayout(prompt_box)
        self.global_prompt = QTextEdit()
        self.global_prompt.setMinimumHeight(220)
        self.global_prompt.setPlainText(
            "Ta strona jest o ... (np. remontach lazienek w Southend, przewagach firmy i grupie docelowej)."
        )
        p_layout.addWidget(self.global_prompt)
        layout.addWidget(prompt_box)

        action_box = QGroupBox("Etapy")
        a_layout = QHBoxLayout(action_box)
        self.stage1_btn = QPushButton("Etap 1: Generuj strategie")
        self.stage2_btn = QPushButton("Etap 2: Generuj content")
        self.stage3_btn = QPushButton("Etap 3: Generuj finalny plik")
        self.stage4_btn = QPushButton("Etap 4: Metadane obrazow")
        self.stage5_btn = QPushButton("Etap 5: Zapisz metadane")
        self.stage1_btn.clicked.connect(self.generate_strategy)
        self.stage2_btn.clicked.connect(self.generate_all)
        self.stage3_btn.clicked.connect(self.build_output)
        self.stage4_btn.clicked.connect(self.generate_all_image_metadata)
        self.stage5_btn.clicked.connect(self.save_image_metadata)
        for b in [self.stage1_btn, self.stage2_btn, self.stage3_btn, self.stage4_btn, self.stage5_btn]:
            a_layout.addWidget(b)
        layout.addWidget(action_box)

        for w in [self.template_path, self.output_path, self.compare_path, self.image_metadata_path, self.api_key]:
            w.textChanged.connect(self._schedule_save)
        self.global_prompt.textChanged.connect(self._schedule_save)
        for c in [self.model, self.generation_mode, self.language_mode, self.link_mode, self.format_mode]:
            c.currentTextChanged.connect(self._schedule_save)

    def _build_placeholders_tab(self) -> None:
        layout = QVBoxLayout(self.tab_placeholders)
        self.placeholder_summary = QLabel("Brak placeholderow. Wczytaj szablon.")
        layout.addWidget(self.placeholder_summary)
        self.placeholders_scroll = QScrollArea()
        self.placeholders_scroll.setWidgetResizable(True)
        self.placeholders_container = QWidget()
        self.placeholders_layout = QVBoxLayout(self.placeholders_container)
        self.placeholders_layout.addStretch(1)
        self.placeholders_scroll.setWidget(self.placeholders_container)
        layout.addWidget(self.placeholders_scroll, 1)

    def _build_strategy_logs_tab(self) -> None:
        layout = QVBoxLayout(self.tab_strategy_logs)
        splitter = QSplitter(Qt.Vertical)
        top = QWidget()
        top_l = QVBoxLayout(top)
        top_l.addWidget(QLabel("Podglad strategii i mapowania"))
        self.checklist_qc_badge = QLabel("Checklist QA: brak danych")
        self.checklist_qc_badge.setStyleSheet(
            "QLabel { background: #e2e8f0; color: #334155; border-radius: 6px; padding: 6px; font-weight: 600; }"
        )
        top_l.addWidget(self.checklist_qc_badge)
        self.strategy_preview = QPlainTextEdit()
        self.strategy_preview.setReadOnly(True)
        top_l.addWidget(self.strategy_preview)
        top_l.addWidget(QLabel("Checklist review (kontaminacja tematu):"))
        self.checklist_review_view = QPlainTextEdit()
        self.checklist_review_view.setReadOnly(True)
        self.checklist_review_view.setMaximumHeight(180)
        top_l.addWidget(self.checklist_review_view)
        bottom = QWidget()
        b_l = QVBoxLayout(bottom)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Live logi"))
        copy_btn = QPushButton("Kopiuj")
        copy_btn.clicked.connect(self.copy_logs)
        clear_btn = QPushButton("Wyczysc")
        clear_btn.clicked.connect(self.clear_logs)
        hdr.addStretch(1)
        hdr.addWidget(copy_btn)
        hdr.addWidget(clear_btn)
        b_l.addLayout(hdr)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        b_l.addWidget(self.logs_view)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([430, 320])
        layout.addWidget(splitter)

    def _add_path_row(self, layout: QGridLayout, row: int, label: str, edit: QLineEdit, pick_fn: Callable[[], None]) -> None:
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(edit, row, 1, 1, 4)
        btn = QPushButton("Wybierz")
        btn.clicked.connect(pick_fn)
        layout.addWidget(btn, row, 5)

    def _pick_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz szablon", "", "Tekst (*.txt *.html *.php);;Wszystkie (*)")
        if path:
            self.template_path.setText(path)

    def _pick_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz output", "", "Tekst (*.txt);;Wszystkie (*)")
        if path:
            self.output_path.setText(path)

    def _pick_compare(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik porownania", "", "Tekst (*.txt *.html *.php);;Wszystkie (*)")
        if path:
            self.compare_path.setText(path)

    def _pick_metadata_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz metadane", "", "JSON (*.json);;Wszystkie (*)")
        if path:
            self.image_metadata_path.setText(path)

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs_view.appendPlainText(f"[{ts}] {message}")

    def copy_logs(self) -> None:
        QApplication.clipboard().setText(self.logs_view.toPlainText())
        self._set_status_success("Skopiowano logi do schowka.")

    def clear_logs(self) -> None:
        self.logs_view.clear()

    def _schedule_save(self) -> None:
        self._save_debounce.start(700)
        self._preview_debounce.start(900)

    def _save_settings_and_draft(self) -> None:
        self._save_settings()
        self._save_session_draft()

    def _save_settings(self) -> None:
        self.settings.setValue("template_path", self.template_path.text())
        self.settings.setValue("output_path", self.output_path.text())
        self.settings.setValue("compare_path", self.compare_path.text())
        self.settings.setValue("image_metadata_path", self.image_metadata_path.text())
        self.settings.setValue("model", self.model.currentText())
        self.settings.setValue("generation_mode", self.generation_mode.currentText())
        self.settings.setValue("language_mode", self.language_mode.currentText())
        self.settings.setValue("link_mode", self.link_mode.currentText())
        self.settings.setValue("format_mode", self.format_mode.currentText())
        self.settings.setValue("global_prompt", self.global_prompt.toPlainText())
        self.settings.setValue("current_tab", self.tabs.currentIndex())
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()

    def _load_settings(self) -> None:
        self.template_path.setText(str(self.settings.value("template_path", self.template_path.text())))
        self.output_path.setText(str(self.settings.value("output_path", self.output_path.text())))
        self.compare_path.setText(str(self.settings.value("compare_path", self.compare_path.text())))
        self.image_metadata_path.setText(str(self.settings.value("image_metadata_path", self.image_metadata_path.text())))
        self._set_combo_value(self.model, self.settings.value("model", self.model.currentText()))
        self._set_combo_value(self.generation_mode, self.settings.value("generation_mode", "text-only"))
        self._set_combo_value(self.language_mode, self.settings.value("language_mode", "auto"))
        self._set_combo_value(self.link_mode, self.settings.value("link_mode", "strict-html-internal"))
        self._set_combo_value(self.format_mode, self.settings.value("format_mode", "avada-strict"))
        self.global_prompt.setPlainText(str(self.settings.value("global_prompt", self.global_prompt.toPlainText())))
        tab_idx = int(self.settings.value("current_tab", 0))
        if 0 <= tab_idx < self.tabs.count():
            self.tabs.setCurrentIndex(tab_idx)
        geom = self.settings.value("geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: Any) -> None:
        val = str(value or "")
        idx = combo.findText(val)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.isEditable():
            combo.setEditText(val)

    def _save_session_draft(self) -> None:
        cards_state = {pid: card.export_state() for pid, card in self.cards.items()}
        self.session_store.save({"template_path": self.template_path.text().strip(), "cards": cards_state})

    def _load_session_draft(self) -> None:
        self._session_draft = self.session_store.load()

    def _restore_cards_from_draft(self) -> None:
        draft = getattr(self, "_session_draft", {}) or {}
        cards = draft.get("cards", {}) if isinstance(draft, dict) else {}
        if not isinstance(cards, dict):
            return
        for pid, card in self.cards.items():
            if pid in cards and isinstance(cards[pid], dict):
                card.import_state(cards[pid])

    def _create_client(self, token: OperationToken) -> OpenAIClient:
        return OpenAIClient(
            api_key=self.api_key.text().strip(),
            model=self.model.currentText().strip(),
            read_timeout_seconds=60,
            is_cancelled=lambda: token.cancel_requested,
            on_timeout_decision=lambda a, t: self._timeout_decision_blocking(token, a, t),
            log=self._log,
        )

    def _timeout_decision_blocking(self, token: OperationToken, attempt: int, timeout_sec: int) -> bool:
        req_id = str(uuid.uuid4())
        event = threading.Event()
        holder = {"wait": True, "token": token, "event": event}
        with self._timeout_lock:
            self._timeout_waiters[req_id] = holder
        self.bridge.timeout_decision_requested.emit(req_id, attempt, timeout_sec)
        event.wait()
        return bool(holder.get("wait", True))

    def _on_timeout_decision_requested(self, req_id: str, attempt: int, timeout_sec: int) -> None:
        with self._timeout_lock:
            holder = self._timeout_waiters.get(req_id)
        if not holder:
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Timeout zapytania API")
        msg.setText(
            f"Brak odpowiedzi przez {timeout_sec} sekund (proba {attempt}).\n"
            "Czekaj = kontynuuj monitorowanie biezacego zadania.\nKill = przerwij."
        )
        wait_btn = msg.addButton("Czekaj", QMessageBox.AcceptRole)
        kill_btn = msg.addButton("Kill", QMessageBox.DestructiveRole)
        msg.setDefaultButton(wait_btn)
        msg.exec()
        holder["wait"] = msg.clickedButton() == wait_btn
        if msg.clickedButton() == kill_btn and holder.get("token") is not None:
            holder["token"].cancel_requested = True
        holder["event"].set()
        with self._timeout_lock:
            self._timeout_waiters.pop(req_id, None)

    def _run_task(self, name: str, worker_fn: Callable[[OperationToken], Any], done_fn: Callable[[Any], None]) -> None:
        self._op_seq += 1
        op_id = self._op_seq
        token = OperationToken()
        self._ops[op_id] = {"name": name, "token": token, "done_fn": done_fn}
        self._set_status_running(name)
        self._log(f"[Operation] start id={op_id} name={name}")

        def run() -> None:
            try:
                result = worker_fn(token)
                self.bridge.task_done.emit(op_id, result)
            except Exception as exc:
                self.bridge.task_error.emit(op_id, str(exc))

        t = threading.Thread(target=run, daemon=True)
        self._threads.append(t)
        t.start()

    def _on_task_done(self, op_id: int, result: object) -> None:
        op = self._ops.pop(op_id, None)
        if not op:
            return
        self._log(f"[Operation] end id={op_id} name={op.get('name')}")
        done_fn = op.get("done_fn")
        if callable(done_fn):
            done_fn(result)
        self._set_status_success(f"Zakonczono: {op.get('name')}")

    def _on_task_error(self, op_id: int, error: str) -> None:
        op = self._ops.pop(op_id, None)
        name = op.get("name") if op else "Operacja"
        self._log(f"[Operation] error id={op_id} name={name} err={error}")
        self._set_status_error(f"Blad: {name}")
        QMessageBox.critical(self, "Blad", error)

    def _cancel_active_operation(self) -> None:
        if not self._ops:
            self._set_status_idle("Brak aktywnej operacji.")
            return
        last_id = sorted(self._ops.keys())[-1]
        token = self._ops[last_id].get("token")
        if token:
            token.cancel_requested = True
        self._set_status_error(f"Przerwanie zaznaczone: {self._ops[last_id].get('name')}")

    def _set_status_chip(self, text: str, bg: str, fg: str = "white") -> None:
        self.status_chip.setText(text)
        self.status_chip.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 12px; padding: 6px; font-weight: 700; }}"
        )

    def _set_status_running(self, text: str) -> None:
        self._status_mode = "running"
        self._status_base_text = text
        self._status_dots = 0
        self.status_progress.setVisible(True)
        self._set_status_chip("PRACA", "#f59e0b")
        self.status_text.setText(text)
        self._status_timer.start(350)

    def _set_status_success(self, text: str) -> None:
        self._status_mode = "success"
        self._status_timer.stop()
        self.status_progress.setVisible(False)
        self._set_status_chip("OK", "#16a34a")
        self.status_text.setText(text)

    def _set_status_error(self, text: str) -> None:
        self._status_mode = "error"
        self._status_timer.stop()
        self.status_progress.setVisible(False)
        self._set_status_chip("BLAD", "#dc2626")
        self.status_text.setText(text)

    def _set_status_idle(self, text: str) -> None:
        self._status_mode = "idle"
        self._status_timer.stop()
        self.status_progress.setVisible(False)
        self._set_status_chip("IDLE", "#64748b")
        self.status_text.setText(text)

    def _animate_status(self) -> None:
        if self._status_mode != "running":
            return
        self._status_dots = (self._status_dots + 1) % 4
        dots = "." * self._status_dots
        pulse = "#f59e0b" if self._status_dots % 2 == 0 else "#d97706"
        self._set_status_chip("PRACA", pulse)
        self.status_text.setText(f"{self._status_base_text}{dots}")

    def _collect_config(self) -> EngineConfig:
        return EngineConfig(
            generation_mode=self.generation_mode.currentText(),
            language_mode=self.language_mode.currentText(),
            link_mode=self.link_mode.currentText(),
            format_mode=self.format_mode.currentText(),
        )

    def load_template(self) -> None:
        path = self.template_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Brak", "Podaj sciezke szablonu.")
            return
        try:
            counts = self.engine.load_template(path)
        except Exception as exc:
            QMessageBox.critical(self, "Blad", str(exc))
            self._set_status_error("Nie udalo sie wczytac szablonu.")
            return
        self._rebuild_placeholder_cards()
        text_count = len([p for p in self.engine.placeholders if p.kind == "text"])
        img_count = len([p for p in self.engine.placeholders if p.kind == "image"])
        checklist_count = len([p for p in self.engine.placeholders if p.kind == "text" and p.block_type == "fusion_li_item"])
        self.placeholder_summary.setText(
            f"Wczytano {text_count} blokow tekstu, {checklist_count} checklist item i {img_count} blokow obrazow."
        )
        self._set_status_success("Szablon wczytany.")
        self._log(f"Wczytano placeholdery: text={text_count}, image={img_count}, scan={counts}")
        self.show_strategy_preview()
        self._restore_cards_from_draft()
        self._schedule_save()

    def _rebuild_placeholder_cards(self) -> None:
        while self.placeholders_layout.count() > 1:
            item = self.placeholders_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.cards.clear()
        for ph in self.engine.placeholders:
            card = PlaceholderCard(ph)
            self.cards[ph.pid] = card
            card.generateTextRequested.connect(self.generate_one)
            card.generateImageMetaRequested.connect(self.generate_one_image_metadata)
            card.contentChanged.connect(self._schedule_save)
            self.placeholders_layout.insertWidget(self.placeholders_layout.count() - 1, card)

    def show_scan_report(self) -> None:
        if not self.engine.last_scan_report:
            QMessageBox.information(self, "Raport", "Najpierw wczytaj szablon.")
            return
        lines = ["Wykryte elementy AVADA:"]
        for key, value in self.engine.last_scan_report.items():
            lines.append(f"- {key}: {value}")
        lines.append(f"- Lacznie placeholderow: {len(self.engine.placeholders)}")
        QMessageBox.information(self, "Raport skanowania", "\n".join(lines))

    def show_strategy_preview(self) -> None:
        replacements = self._collect_replacements() if self.cards else None
        checklist_qc = self.engine.checklist_review(replacements, self.global_prompt.toPlainText().strip())
        summary_lines = [
            f"Placeholdery lacznie: {len(self.engine.placeholders)}",
            f"Sekcje inferred: {len(self.engine.section_schema)}",
            f"Sekcje w strategii: {len(self.engine.page_strategy.sections) if self.engine.page_strategy else 0}",
            f"Wygenerowane klucze: {len(self.engine.page_content.mapped) if self.engine.page_content else 0}",
            f"Braki mapowania: {len(self.engine.page_content.missing_ids) if self.engine.page_content else 0}",
            (
                "Checklist contamination: "
                f"{checklist_qc.get('flagged_items', 0)}/{checklist_qc.get('total_items', 0)}"
            ),
        ]
        body = [
            "=== PODSUMOWANIE ===",
            *summary_lines,
            "\n=== SECTION SCHEMA ===",
            json.dumps(self.engine.section_schema, ensure_ascii=False, indent=2),
            "\n=== STRATEGY JSON ===",
            json.dumps(self.engine.page_strategy.raw if self.engine.page_strategy else {}, ensure_ascii=False, indent=2),
            "\n=== GENERATED CONTENT JSON ===",
            json.dumps(self.engine.page_content.raw if self.engine.page_content else {}, ensure_ascii=False, indent=2),
            "\n=== MAPPING REPORT ===",
            json.dumps(self.engine.last_mapping_report, ensure_ascii=False, indent=2),
        ]
        self.strategy_preview.setPlainText("\n".join(body))

        flagged = int(checklist_qc.get("flagged_items", 0))
        total = int(checklist_qc.get("total_items", 0))
        if total == 0:
            self.checklist_qc_badge.setText("Checklist QA: brak checklist w szablonie")
            self.checklist_qc_badge.setStyleSheet(
                "QLabel { background: #e2e8f0; color: #334155; border-radius: 6px; padding: 6px; font-weight: 600; }"
            )
        elif flagged == 0:
            self.checklist_qc_badge.setText(f"Checklist QA: OK (0/{total} z kontaminacja)")
            self.checklist_qc_badge.setStyleSheet(
                "QLabel { background: #dcfce7; color: #166534; border-radius: 6px; padding: 6px; font-weight: 700; }"
            )
        else:
            self.checklist_qc_badge.setText(f"Checklist QA: UWAGA ({flagged}/{total} z kontaminacja)")
            self.checklist_qc_badge.setStyleSheet(
                "QLabel { background: #fee2e2; color: #991b1b; border-radius: 6px; padding: 6px; font-weight: 700; }"
            )

        qc_lines = []
        for item in checklist_qc.get("items", []):
            pid = item.get("pid", "")
            section_id = item.get("section_id", "")
            text = str(item.get("text", "")).replace("\n", " ").strip()
            hits = item.get("contamination_terms", [])
            changed = "TAK" if item.get("changed") else "NIE"
            if hits:
                qc_lines.append(
                    f"[{pid}] sekcja {section_id} | changed={changed} | CONTAMINATION={', '.join(hits)}\n"
                    f"  - {text}"
                )
            else:
                qc_lines.append(f"[{pid}] sekcja {section_id} | changed={changed}\n  - {text}")
        self.checklist_review_view.setPlainText("\n\n".join(qc_lines) if qc_lines else "Brak danych checklist.")

    def generate_strategy(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        def worker(token: OperationToken) -> object:
            return self.engine.generate_strategy(self._create_client(token), self.global_prompt.toPlainText().strip())
        def done(_: object) -> None:
            self.show_strategy_preview()
            self._set_status_success("Etap 1 zakonczony. Mozesz uruchomic Etap 2.")
        self._run_task("Etap 1: Generowanie strategii", worker, done)

    def generate_all(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        config = self._collect_config()
        extra_prompts = {pid: card.get_extra_prompt() for pid, card in self.cards.items() if card.placeholder.kind == "text"}
        def worker(token: OperationToken) -> object:
            return self.engine.generate_page_content(
                client=self._create_client(token),
                global_prompt=self.global_prompt.toPlainText().strip(),
                config=config,
                extra_prompts=extra_prompts,
            )
        def done(result: object) -> None:
            mapped = result if isinstance(result, dict) else {}
            for pid, content in mapped.items():
                if pid in self.cards and self.cards[pid].placeholder.kind == "text":
                    self.cards[pid].set_result(str(content))
            self.show_strategy_preview()
            self._set_status_success("Etap 2 zakonczony.")
            self._schedule_save()
        self._run_task("Etap 2: Generowanie contentu", worker, done)

    def generate_one(self, pid: str) -> None:
        if pid not in self.cards:
            return
        card = self.cards[pid]
        config = self._collect_config()
        def worker(token: OperationToken) -> object:
            return self.engine.generate_one_text(
                client=self._create_client(token),
                global_prompt=self.global_prompt.toPlainText().strip(),
                placeholder_id=pid,
                optional_prompt=card.get_extra_prompt(),
                config=config,
            )
        def done(result: object) -> None:
            card.set_result(str(result or ""))
            self._set_status_success(f"Wygenerowano {pid}.")
            self._schedule_save()
        self._run_task(f"Generowanie {pid}", worker, done)

    def generate_one_image_metadata(self, pid: str) -> None:
        if pid not in self.cards:
            return
        card = self.cards[pid]
        def worker(token: OperationToken) -> object:
            return self.engine.generate_one_image_metadata(
                client=self._create_client(token),
                global_prompt=self.global_prompt.toPlainText().strip(),
                placeholder_id=pid,
                image_reference=card.get_image_value() or card.placeholder.original,
                optional_prompt=card.get_image_prompt(),
            )
        def done(result: object) -> None:
            meta = result if isinstance(result, dict) else {}
            card.set_image_metadata(meta.get("filename", ""), meta.get("alt", ""), meta.get("description", ""))
            self._set_status_success(f"Metadane gotowe: {pid}")
            self._schedule_save()
        self._run_task(f"Metadane {pid}", worker, done)

    def generate_all_image_metadata(self) -> None:
        image_ids = [pid for pid, c in self.cards.items() if c.placeholder.kind == "image"]
        if not image_ids:
            QMessageBox.warning(self, "Brak", "Brak placeholderow obrazow.")
            return
        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            out: Dict[str, Dict[str, str]] = {}
            gp = self.global_prompt.toPlainText().strip()
            for pid in image_ids:
                if token.cancel_requested:
                    raise RuntimeError("Operacja przerwana przez uzytkownika.")
                card = self.cards[pid]
                out[pid] = self.engine.generate_one_image_metadata(
                    client=client,
                    global_prompt=gp,
                    placeholder_id=pid,
                    image_reference=card.get_image_value() or card.placeholder.original,
                    optional_prompt=card.get_image_prompt(),
                )
            return out
        def done(result: object) -> None:
            all_meta = result if isinstance(result, dict) else {}
            for pid, meta in all_meta.items():
                if pid in self.cards:
                    self.cards[pid].set_image_metadata(meta.get("filename", ""), meta.get("alt", ""), meta.get("description", ""))
            self._set_status_success("Etap 4 zakonczony.")
            self._schedule_save()
        self._run_task("Etap 4: Metadane obrazow", worker, done)

    def _collect_replacements(self) -> Dict[str, str]:
        replacements: Dict[str, str] = {}
        for pid, card in self.cards.items():
            replacements[pid] = card.get_result() if card.placeholder.kind == "text" else card.get_image_value()
            if not replacements[pid]:
                replacements[pid] = card.placeholder.original
        return replacements

    def build_output(self) -> None:
        if not self.engine.template_raw or not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        out_path = Path(self.output_path.text().strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        config = self._collect_config()
        output, replacements = self.engine.build_output(self._collect_replacements())
        out_path.write_text(output, encoding="utf-8")
        validation = self.engine.validate_output_text(output, replacements, self.global_prompt.toPlainText().strip(), config)
        report_path = out_path.with_name(f"{out_path.stem}_validation_report.txt")
        report_path.write_text(validation["report_text"], encoding="utf-8")
        generation_path = out_path.with_name(f"{out_path.stem}_generation_report.txt")
        generation_path.write_text(self.engine.build_generation_report(replacements, config), encoding="utf-8")
        self._set_status_success("Etap 3 zakonczony. Output zapisany.")
        self._schedule_save()
        QMessageBox.information(
            self,
            "Sukces",
            "\n".join(
                [
                    f"Wygenerowano plik: {out_path}",
                    f"Walidacja: {validation['critical']} krytycznych, {validation['warnings']} ostrzezen",
                    f"Raport walidacji: {report_path}",
                    f"Raport generacji: {generation_path}",
                ]
            ),
        )

    def save_image_metadata(self) -> None:
        out = []
        for ph in self.engine.placeholders:
            if ph.kind != "image" or ph.pid not in self.cards:
                continue
            card = self.cards[ph.pid]
            meta = card.get_image_metadata()
            out.append(
                {
                    "placeholder": ph.pid,
                    "block_type": ph.block_type,
                    "field": ph.field,
                    "image_source": card.get_image_value() or ph.original,
                    "filename": meta.get("filename", ""),
                    "alt": meta.get("alt", ""),
                    "description": meta.get("description", ""),
                }
            )
        path = Path(self.image_metadata_path.text().strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        self._set_status_success("Etap 5 zakonczony. Metadane zapisane.")
        self._schedule_save()
        QMessageBox.information(self, "Sukces", f"Zapisano metadane: {path}")

    def refresh_models(self) -> None:
        key = self.api_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Brak", "Najpierw podaj klucz API.")
            return
        def worker(_: OperationToken) -> object:
            import requests
            response = requests.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=(20, 60))
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:250]}")
            data = response.json().get("data", [])
            return sorted([str(m.get("id", "")) for m in data if isinstance(m, dict) and m.get("id")])
        def done(result: object) -> None:
            mids = result if isinstance(result, list) else []
            if not mids:
                self._set_status_error("Nie znaleziono modeli.")
                return
            current = self.model.currentText()
            self.model.clear()
            self.model.addItems(mids)
            self._set_combo_value(self.model, current if current in mids else mids[0])
            self._set_status_success(f"Zaktualizowano modele ({len(mids)}).")
            self._schedule_save()
        self._run_task("Aktualizacja modeli", worker, done)

    def _save_key_secure(self) -> None:
        try:
            self.key_store.save(self.api_key.text())
            self._set_status_success("Klucz API zapisany bezpiecznie (DPAPI).")
        except Exception as exc:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie zapisac klucza: {exc}")

    def _load_key_secure(self, silent: bool = False) -> None:
        try:
            key = self.key_store.load()
            if key:
                self.api_key.setText(key)
                if not silent:
                    self._set_status_success("Klucz API wczytany.")
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Blad", f"Nie udalo sie wczytac klucza: {exc}")

    def _clear_key_secure(self) -> None:
        try:
            self.key_store.clear()
            self.api_key.clear()
            self._set_status_success("Klucz API usuniety.")
        except Exception as exc:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie usunac klucza: {exc}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_settings_and_draft()
        super().closeEvent(event)


def run_app() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    window = MainWindow()
    quit_action = QAction("Quit", window)
    quit_action.triggered.connect(window.close)
    window.addAction(quit_action)
    window.show()
    app.exec()
