import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from difflib import SequenceMatcher

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QDialog,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .constants import APP_NAME, APP_ORG
from .engine import AvadaSeoEngine, EngineConfig
from .models import PageContentResult, StrategyResult
from .image_pipeline import (
    PreviewBuildResult,
    build_previews_and_contact_sheet,
    file_to_data_url,
    image_dimensions,
    list_images_in_folder,
    slugify_filename,
)
from .openai_client import OpenAIClient
from .storage import SecureApiKeyStore, SecureWpPasswordStore, SessionDraftStore
from .wordpress_client import WordPressClient, WordPressCredentials


class UiBridge(QObject):
    task_done = Signal(int, object)
    task_error = Signal(int, str)
    task_progress = Signal(int, int, int, str, str)
    timeout_decision_requested = Signal(str, int, int)


@dataclass
class OperationToken:
    cancel_requested: bool = False


class LockedWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        # Prevent accidental value changes while scrolling parent lists.
        if self.view().isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


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


class ImageAssetCard(QGroupBox):
    contentChanged = Signal()
    improveRequested = Signal(str)

    def __init__(self, local_path: str, preview_path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.local_path = local_path
        self.preview_path = preview_path
        self.score_value = 0.0
        self.setTitle(Path(local_path).name)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.selected = QCheckBox("Wybrane")
        self.selected.setChecked(True)
        top.addWidget(self.selected)
        self.status_lbl = QLabel("Nowe")
        self.status_lbl.setStyleSheet("QLabel { background:#e2e8f0; color:#334155; border-radius:5px; padding:4px 8px; }")
        top.addWidget(self.status_lbl)
        self.score_lbl = QLabel("-/10")
        self.score_lbl.setStyleSheet("QLabel { background:#e2e8f0; color:#334155; border-radius:5px; padding:4px 8px; }")
        top.addWidget(self.score_lbl)
        top.addStretch(1)
        self.improve_btn = QPushButton("POPRAW")
        self.improve_btn.clicked.connect(lambda: self.improveRequested.emit(self.local_path))
        top.addWidget(self.improve_btn)
        root.addLayout(top)

        row = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(160, 110)
        self.thumb.setStyleSheet("QLabel { border:1px solid #cbd5e1; background:#f8fafc; }")
        self.thumb.setAlignment(Qt.AlignCenter)
        pix = QPixmap(self.preview_path)
        if not pix.isNull():
            self.thumb.setPixmap(pix.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(self.thumb)

        form = QFormLayout()
        self.seo_filename = QLineEdit(slugify_filename(Path(local_path).stem))
        self.alt = QLineEdit("")
        self.caption = QLineEdit("")
        self.description = QTextEdit()
        self.description.setFixedHeight(70)
        form.addRow("NAZWA (SEO):", self.seo_filename)
        form.addRow("ALT TEXT:", self.alt)
        form.addRow("CAPTION:", self.caption)
        form.addRow("DESCRIPTION:", self.description)
        row.addLayout(form, 1)
        root.addLayout(row)

        self.reason_lbl = QLabel("")
        self.reason_lbl.setWordWrap(True)
        self.reason_lbl.setStyleSheet("QLabel { color:#475569; }")
        root.addWidget(self.reason_lbl)

        self.placeholder_combo = LockedWheelComboBox()
        self.placeholder_combo.addItem("(brak przypisania)")
        p_row = QHBoxLayout()
        p_row.addWidget(QLabel("Placeholder:"))
        p_row.addWidget(self.placeholder_combo, 1)
        root.addLayout(p_row)

        self.selected.stateChanged.connect(self.contentChanged)
        self.seo_filename.textChanged.connect(self.contentChanged)
        self.alt.textChanged.connect(self.contentChanged)
        self.caption.textChanged.connect(self.contentChanged)
        self.description.textChanged.connect(self.contentChanged)
        self.placeholder_combo.currentTextChanged.connect(self.contentChanged)

    def set_placeholder_options(self, placeholder_ids: list[str]) -> None:
        current = self.placeholder_combo.currentText()
        self.placeholder_combo.blockSignals(True)
        self.placeholder_combo.clear()
        self.placeholder_combo.addItem("(brak przypisania)")
        for pid in placeholder_ids:
            self.placeholder_combo.addItem(pid)
        idx = self.placeholder_combo.findText(current)
        if idx >= 0:
            self.placeholder_combo.setCurrentIndex(idx)
        self.placeholder_combo.blockSignals(False)

    def set_placeholder(self, placeholder_id: str) -> None:
        target = placeholder_id.strip() if placeholder_id else "(brak przypisania)"
        idx = self.placeholder_combo.findText(target)
        if idx < 0:
            self.placeholder_combo.addItem(target)
            idx = self.placeholder_combo.findText(target)
        self.placeholder_combo.setCurrentIndex(max(0, idx))

    def assigned_placeholder(self) -> str:
        txt = self.placeholder_combo.currentText().strip()
        return "" if txt.startswith("(") else txt

    def metadata(self) -> Dict[str, str]:
        return {
            "seo_filename": self.seo_filename.text().strip(),
            "alt": self.alt.text().strip(),
            "caption": self.caption.text().strip(),
            "description": self.description.toPlainText().strip(),
        }

    def set_metadata(self, seo_filename: str, alt: str, caption: str, description: str) -> None:
        if seo_filename.strip():
            self.seo_filename.setText(seo_filename.strip())
        self.alt.setText(alt.strip())
        self.caption.setText(caption.strip())
        self.description.setPlainText(description.strip())

    def set_status(self, text: str, level: str = "info") -> None:
        if level == "ok":
            style = "QLabel { background:#dcfce7; color:#166534; border-radius:5px; padding:4px 8px; }"
        elif level == "err":
            style = "QLabel { background:#fee2e2; color:#991b1b; border-radius:5px; padding:4px 8px; }"
        elif level == "warn":
            style = "QLabel { background:#fef3c7; color:#92400e; border-radius:5px; padding:4px 8px; }"
        else:
            style = "QLabel { background:#e2e8f0; color:#334155; border-radius:5px; padding:4px 8px; }"
        self.status_lbl.setStyleSheet(style)
        self.status_lbl.setText(text)

    def set_score_reason(self, score: float, reason: str) -> None:
        score = max(0.0, min(10.0, float(score)))
        self.score_value = score
        self.score_lbl.setText(f"{score:.1f}/10")
        if score >= 8.0:
            score_style = "QLabel { background:#dcfce7; color:#166534; border-radius:5px; padding:4px 8px; }"
        elif score >= 6.0:
            score_style = "QLabel { background:#fef3c7; color:#92400e; border-radius:5px; padding:4px 8px; }"
        else:
            score_style = "QLabel { background:#fee2e2; color:#991b1b; border-radius:5px; padding:4px 8px; }"
        self.score_lbl.setStyleSheet(score_style)
        self.reason_lbl.setText(reason.strip())

    def export_state(self) -> Dict[str, Any]:
        m = self.metadata()
        return {
            "local_path": self.local_path,
            "preview_path": self.preview_path,
            "selected": self.selected.isChecked(),
            "placeholder": self.assigned_placeholder(),
            "seo_filename": m["seo_filename"],
            "alt": m["alt"],
            "caption": m["caption"],
            "description": m["description"],
            "status": self.status_lbl.text(),
            "score_text": self.score_lbl.text(),
            "reason": self.reason_lbl.text(),
        }

    def import_state(self, data: Dict[str, Any]) -> None:
        self.selected.setChecked(bool(data.get("selected", True)))
        self.set_metadata(
            str(data.get("seo_filename", "")),
            str(data.get("alt", "")),
            str(data.get("caption", "")),
            str(data.get("description", "")),
        )
        self.set_placeholder(str(data.get("placeholder", "")))
        status = str(data.get("status", "")).strip()
        if status:
            self.set_status(status, "info")
        score_text = str(data.get("score_text", "")).strip()
        if score_text:
            self.score_lbl.setText(score_text)
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", score_text)
            if m:
                try:
                    self.score_value = float(m.group(1))
                except Exception:
                    self.score_value = 0.0
        reason = str(data.get("reason", "")).strip()
        if reason:
            self.reason_lbl.setText(reason)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AVADA SEO Generator - PySide6")
        self.resize(1500, 980)

        self.engine = AvadaSeoEngine(log=self._log)
        self.key_store = SecureApiKeyStore()
        self.wp_pass_store = SecureWpPasswordStore()
        self.session_store = SessionDraftStore()
        self.settings = QSettings(APP_ORG, APP_NAME)

        self.bridge = UiBridge()
        self.bridge.task_done.connect(self._on_task_done)
        self.bridge.task_error.connect(self._on_task_error)
        self.bridge.task_progress.connect(self._on_task_progress)
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
        self._log_lines: list[str] = []
        self._logs_window: Optional[QDialog] = None
        self._logs_window_text: Optional[QPlainTextEdit] = None
        self.last_validation_report_path: Optional[Path] = None
        self.last_generation_report_path: Optional[Path] = None
        self.polishing_result: Optional[Dict[str, object]] = None
        self.polishing_units_by_id: Dict[str, Dict[str, object]] = {}
        self.polishing_applied_unit_ids: set[str] = set()
        self.polish_baseline_replacements: Dict[str, str] = {}
        self.image_cards: Dict[str, ImageAssetCard] = {}
        self.image_preview_map: Dict[str, str] = {}
        self.image_wp_map: Dict[str, Dict[str, object]] = {}

        self._build_ui()
        self._load_settings()
        self._load_key_secure(silent=True)
        self._load_wp_pass_secure(silent=True)
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
        self.tab_polishing = QWidget()
        self.tab_images = QWidget()
        self.tab_gooo = QWidget()
        self.tab_full_auto = QWidget()
        self.tabs.addTab(self.tab_config, "Konfiguracja")
        self.tabs.addTab(self.tab_placeholders, "Placeholdery")
        self.tabs.addTab(self.tab_strategy_logs, "Strategia i logi")
        self.tabs.addTab(self.tab_polishing, "POLISHING")
        self.tabs.addTab(self.tab_images, "ZDJECIA")
        self.tabs.addTab(self.tab_gooo, "GOOO")
        self.tabs.addTab(self.tab_full_auto, "FULL AUTO")

        self._build_config_tab()
        self._build_placeholders_tab()
        self._build_strategy_logs_tab()
        self._build_polishing_tab()
        self._build_images_tab()
        self._build_gooo_tab()
        self._build_full_auto_tab()

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
        self.preview_btn.clicked.connect(self.open_strategy_preview_tab)
        self.view_report_btn = QPushButton("Zobacz raport po wygenerowaniu")
        self.view_report_btn.clicked.connect(self.show_last_generation_reports)
        self.logs_window_btn = QPushButton("Logi w osobnym oknie")
        self.logs_window_btn.clicked.connect(self.open_logs_window)
        files_layout.addWidget(self.load_template_btn, 4, 0)
        files_layout.addWidget(self.scan_report_btn, 4, 1)
        files_layout.addWidget(self.preview_btn, 4, 2)
        files_layout.addWidget(self.view_report_btn, 4, 3)
        files_layout.addWidget(self.logs_window_btn, 4, 4)
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

        session_box = QGroupBox("Sesja")
        s_layout = QHBoxLayout(session_box)
        self.session_save_btn = QPushButton("SAVE SESSION")
        self.session_load_btn = QPushButton("LOAD SESSION")
        self.session_save_btn.clicked.connect(self.save_session_manual)
        self.session_load_btn.clicked.connect(self.load_session_manual)
        s_layout.addWidget(self.session_save_btn)
        s_layout.addWidget(self.session_load_btn)
        s_layout.addStretch(1)
        layout.addWidget(session_box)

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

    def _build_polishing_tab(self) -> None:
        layout = QVBoxLayout(self.tab_polishing)

        settings_box = QGroupBox("Polishing settings")
        s = QGridLayout(settings_box)
        self.polish_enable = QCheckBox("Enable polishing")
        self.polish_enable.setChecked(False)
        self.polish_threshold = QDoubleSpinBox()
        self.polish_threshold.setRange(1.0, 10.0)
        self.polish_threshold.setSingleStep(0.25)
        self.polish_threshold.setDecimals(2)
        self.polish_threshold.setValue(8.0)
        self.polish_green_from = QDoubleSpinBox()
        self.polish_green_from.setRange(1.0, 10.0)
        self.polish_green_from.setSingleStep(0.25)
        self.polish_green_from.setDecimals(2)
        self.polish_green_from.setValue(8.0)
        self.polish_yellow_from = QDoubleSpinBox()
        self.polish_yellow_from.setRange(1.0, 10.0)
        self.polish_yellow_from.setSingleStep(0.25)
        self.polish_yellow_from.setDecimals(2)
        self.polish_yellow_from.setValue(6.5)
        self.polish_mode = QComboBox()
        self.polish_mode.addItems(["STRICT", "BALANCED", "AGGRESSIVE"])
        self.polish_mode.setCurrentText("BALANCED")
        s.addWidget(self.polish_enable, 0, 0)
        s.addWidget(QLabel("Auto-fix threshold (SCORE < X):"), 0, 1)
        s.addWidget(self.polish_threshold, 0, 2)
        s.addWidget(QLabel("Mode:"), 0, 3)
        s.addWidget(self.polish_mode, 0, 4)
        s.addWidget(QLabel("Green from:"), 1, 1)
        s.addWidget(self.polish_green_from, 1, 2)
        s.addWidget(QLabel("Yellow from:"), 1, 3)
        s.addWidget(self.polish_yellow_from, 1, 4)
        layout.addWidget(settings_box)

        actions_box = QGroupBox("Page actions")
        a = QHBoxLayout(actions_box)
        self.btn_validate_page = QPushButton("VALIDATE PAGE")
        self.btn_validate_lite_page = QPushButton("VALIDATE LITE PAGE")
        self.btn_polish_page = QPushButton("POLISH PAGE")
        self.btn_auto_fix = QPushButton("AUTO FIX BELOW THRESHOLD")
        self.btn_apply_all_polish = QPushButton("APPLY POLISHED TEXT")
        self.btn_revert_all_polish = QPushButton("REVERT POLISHED CHANGES")
        self.btn_validate_page.clicked.connect(self.validate_page_polishing)
        self.btn_validate_lite_page.clicked.connect(self.validate_page_polishing_lite)
        self.btn_polish_page.clicked.connect(self.polish_page)
        self.btn_auto_fix.clicked.connect(self.auto_fix_below_threshold)
        self.btn_apply_all_polish.clicked.connect(self.apply_all_polished_changes)
        self.btn_revert_all_polish.clicked.connect(self.revert_all_polished_changes)
        for b in [
            self.btn_validate_page,
            self.btn_validate_lite_page,
            self.btn_polish_page,
            self.btn_auto_fix,
            self.btn_apply_all_polish,
            self.btn_revert_all_polish,
        ]:
            a.addWidget(b)
        layout.addWidget(actions_box)

        summary_box = QGroupBox("Score summary")
        sm = QGridLayout(summary_box)
        self.polish_page_scores = {}
        score_keys = ["seo", "readability", "conversion", "topic_cleanliness", "internal_linking", "overall"]
        for i, key in enumerate(score_keys):
            sm.addWidget(QLabel(key.upper()), 0, i)
            lbl = QLabel("-")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("QLabel { background:#e2e8f0; color:#334155; border-radius:5px; padding:4px; }")
            sm.addWidget(lbl, 1, i)
            self.polish_page_scores[key] = lbl
        layout.addWidget(summary_box)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.addWidget(QLabel("Issues panel"))
        self.polish_issues_view = QPlainTextEdit()
        self.polish_issues_view.setReadOnly(True)
        self.polish_issues_view.setMaximumHeight(120)
        left_l.addWidget(self.polish_issues_view)
        self.polish_issue_list = QListWidget()
        self.polish_issue_list.setMaximumHeight(160)
        self.polish_issue_list.itemSelectionChanged.connect(self._on_polish_issue_selection_changed)
        left_l.addWidget(self.polish_issue_list)
        self.polish_issue_detail = QPlainTextEdit()
        self.polish_issue_detail.setReadOnly(True)
        self.polish_issue_detail.setMaximumHeight(130)
        left_l.addWidget(self.polish_issue_detail)
        issue_row = QHBoxLayout()
        issue_row.addWidget(QLabel("Issue ID:"))
        self.polish_issue_selector = QComboBox()
        self.polish_issue_selector.setEditable(False)
        issue_row.addWidget(self.polish_issue_selector, 1)
        self.btn_fix_issue = QPushButton("FIX SCORE (issue)")
        self.btn_fix_issue.clicked.connect(self.fix_selected_issue)
        issue_row.addWidget(self.btn_fix_issue)
        left_l.addLayout(issue_row)

        right = QWidget()
        right_l = QVBoxLayout(right)
        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("Units (kliknij):"))
        self.btn_validate_unit = QPushButton("VALIDATE UNIT")
        self.btn_validate_unit.clicked.connect(self.validate_selected_unit)
        self.btn_fix_unit = QPushButton("FIX SCORE (unit)")
        self.btn_fix_unit.clicked.connect(self.fix_selected_unit)
        self.polish_unit_list = QListWidget()
        self.polish_unit_list.setMaximumHeight(150)
        self.polish_unit_list.itemSelectionChanged.connect(self._on_polish_unit_selection_changed)
        self.polish_unit_list.itemClicked.connect(lambda _: self._on_polish_unit_selection_changed())
        right_l.addLayout(unit_row)
        right_l.addWidget(self.polish_unit_list)
        unit_btn_row = QHBoxLayout()
        unit_btn_row.addWidget(self.btn_validate_unit)
        unit_btn_row.addWidget(self.btn_fix_unit)
        unit_btn_row.addStretch(1)
        right_l.addLayout(unit_btn_row)

        self.polish_unit_meta = QLabel("Type: - | Score: -")
        right_l.addWidget(self.polish_unit_meta)
        right_l.addWidget(QLabel("Original generated text"))
        self.polish_unit_original = QTextEdit()
        self.polish_unit_original.setReadOnly(True)
        right_l.addWidget(self.polish_unit_original, 1)
        right_l.addWidget(QLabel("Polished preview"))
        self.polish_unit_polished = QTextEdit()
        self.polish_unit_polished.setReadOnly(False)
        right_l.addWidget(self.polish_unit_polished, 1)
        right_l.addWidget(QLabel("Unit issues"))
        self.polish_unit_issues = QPlainTextEdit()
        self.polish_unit_issues.setReadOnly(True)
        self.polish_unit_issues.setMaximumHeight(130)
        right_l.addWidget(self.polish_unit_issues)
        unit_buttons = QHBoxLayout()
        self.btn_apply_unit_fix = QPushButton("APPLY FIX")
        self.btn_revert_unit_fix = QPushButton("REVERT")
        self.btn_apply_unit_fix.clicked.connect(self.apply_selected_unit_fix)
        self.btn_revert_unit_fix.clicked.connect(self.revert_selected_unit_fix)
        unit_buttons.addWidget(self.btn_apply_unit_fix)
        unit_buttons.addWidget(self.btn_revert_unit_fix)
        right_l.addLayout(unit_buttons)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 760])
        layout.addWidget(splitter, 1)

        self.polish_enable.stateChanged.connect(self._schedule_save)
        self.polish_threshold.valueChanged.connect(self._schedule_save)
        self.polish_green_from.valueChanged.connect(self._schedule_save)
        self.polish_yellow_from.valueChanged.connect(self._schedule_save)
        self.polish_mode.currentTextChanged.connect(self._schedule_save)
        self.polish_threshold.valueChanged.connect(lambda _: self._refresh_polishing_ui())
        self.polish_green_from.valueChanged.connect(lambda _: self._refresh_polishing_ui())
        self.polish_yellow_from.valueChanged.connect(lambda _: self._refresh_polishing_ui())

    def _build_images_tab(self) -> None:
        layout = QVBoxLayout(self.tab_images)

        folder_box = QGroupBox("1) SELECT FOLDER")
        f = QGridLayout(folder_box)
        self.images_folder = QLineEdit(str(Path.cwd() / "photos"))
        self.btn_select_images_folder = QPushButton("SELECT FOLDER")
        self.btn_select_images_folder.clicked.connect(self._select_images_folder)
        self.btn_load_images = QPushButton("Wczytaj obrazy")
        self.btn_load_images.clicked.connect(self.load_images_folder)
        self.images_required_lbl = QLabel("REQUIRED: 0")
        self.images_available_lbl = QLabel("AVAILABLE: 0")
        self.images_warning_lbl = QLabel("")
        self.images_warning_lbl.setStyleSheet("QLabel { color:#b45309; font-weight:600; }")
        f.addWidget(QLabel("Folder:"), 0, 0)
        f.addWidget(self.images_folder, 0, 1, 1, 4)
        f.addWidget(self.btn_select_images_folder, 0, 5)
        f.addWidget(self.btn_load_images, 0, 6)
        f.addWidget(self.images_required_lbl, 1, 0)
        f.addWidget(self.images_available_lbl, 1, 1)
        f.addWidget(self.images_warning_lbl, 1, 2, 1, 5)
        layout.addWidget(folder_box)

        wp_box = QGroupBox("WordPress REST API (Application Passwords)")
        w = QGridLayout(wp_box)
        self.wp_site = QLineEdit("")
        self.wp_user = QLineEdit("")
        self.wp_app_pass = QLineEdit("")
        self.wp_app_pass.setEchoMode(QLineEdit.Password)
        self.wp_save_pass_btn = QPushButton("Zapisz haslo WP")
        self.wp_load_pass_btn = QPushButton("Wczytaj haslo WP")
        self.wp_clear_pass_btn = QPushButton("Usun haslo WP")
        self.wp_test_btn = QPushButton("TEST CONNECTION")
        self.wp_save_pass_btn.clicked.connect(self._save_wp_pass_secure)
        self.wp_load_pass_btn.clicked.connect(lambda: self._load_wp_pass_secure(silent=False))
        self.wp_clear_pass_btn.clicked.connect(self._clear_wp_pass_secure)
        self.wp_test_btn.clicked.connect(self.test_wp_connection)
        w.addWidget(QLabel("WP URL:"), 0, 0)
        w.addWidget(self.wp_site, 0, 1, 1, 4)
        w.addWidget(QLabel("WP User:"), 1, 0)
        w.addWidget(self.wp_user, 1, 1)
        w.addWidget(QLabel("Application Password:"), 1, 2)
        w.addWidget(self.wp_app_pass, 1, 3)
        w.addWidget(self.wp_save_pass_btn, 1, 4)
        w.addWidget(self.wp_load_pass_btn, 1, 5)
        w.addWidget(self.wp_clear_pass_btn, 1, 6)
        w.addWidget(self.wp_test_btn, 0, 6)
        layout.addWidget(wp_box)

        actions_box = QGroupBox("2) Operacje")
        a = QHBoxLayout(actions_box)
        self.btn_images_generate = QPushButton("GENERUJ")
        self.btn_images_upload = QPushButton("ZALADUJ NA WWW")
        self.btn_images_update_links = QPushButton("AKTUALIZUJ LINKI")
        self.btn_images_generate.clicked.connect(self.generate_images_plan)
        self.btn_images_upload.clicked.connect(self.upload_selected_images_wp)
        self.btn_images_update_links.clicked.connect(self.update_image_links_in_placeholders)
        a.addWidget(self.btn_images_generate)
        a.addWidget(self.btn_images_upload)
        a.addWidget(self.btn_images_update_links)
        a.addStretch(1)
        layout.addWidget(actions_box)

        self.images_progress = QProgressBar()
        self.images_progress.setVisible(False)
        self.images_progress.setValue(0)
        layout.addWidget(self.images_progress)

        self.images_map_info = QPlainTextEdit()
        self.images_map_info.setReadOnly(True)
        self.images_map_info.setMaximumHeight(120)
        layout.addWidget(self.images_map_info)

        self.images_scroll = QScrollArea()
        self.images_scroll.setWidgetResizable(True)
        self.images_container = QWidget()
        self.images_layout = QVBoxLayout(self.images_container)
        self.images_layout.addStretch(1)
        self.images_scroll.setWidget(self.images_container)
        layout.addWidget(self.images_scroll, 1)

        for wgt in [self.images_folder, self.wp_site, self.wp_user, self.wp_app_pass]:
            wgt.textChanged.connect(self._schedule_save)

    def _build_gooo_tab(self) -> None:
        layout = QVBoxLayout(self.tab_gooo)

        top = QGroupBox("Publikacja WordPress")
        g = QGridLayout(top)
        self.gooo_title = QLineEdit("")
        self.gooo_slug = QLineEdit("")
        self.gooo_generate_meta_btn = QPushButton("GENERATE")
        self.gooo_generate_meta_btn.clicked.connect(self.generate_gooo_meta)
        self.gooo_focus_keyphrase = QLineEdit("")
        self.gooo_meta_description = QLineEdit("")
        self.gooo_visibility = QComboBox()
        self.gooo_visibility.addItems(["DRAFT", "LIVE"])
        self.gooo_target = QComboBox()
        self.gooo_target.addItems(["PAGE", "PORTFOLIO"])
        self.gooo_refresh_btn = QPushButton("Odswiez dane")
        self.gooo_refresh_btn.clicked.connect(self.refresh_gooo_data)
        g.addWidget(QLabel("Title:"), 0, 0)
        g.addWidget(self.gooo_title, 0, 1, 1, 3)
        g.addWidget(self.gooo_generate_meta_btn, 0, 4)
        g.addWidget(QLabel("Slug (opcjonalny):"), 1, 0)
        g.addWidget(self.gooo_slug, 1, 1, 1, 2)
        g.addWidget(QLabel("Focus keyphrase:"), 2, 0)
        g.addWidget(self.gooo_focus_keyphrase, 2, 1, 1, 2)
        g.addWidget(QLabel("Meta description:"), 2, 3)
        g.addWidget(self.gooo_meta_description, 2, 4, 1, 3)
        g.addWidget(QLabel("Tryb:"), 1, 3)
        g.addWidget(self.gooo_visibility, 1, 4)
        g.addWidget(QLabel("Typ:"), 1, 5)
        g.addWidget(self.gooo_target, 1, 6)
        g.addWidget(self.gooo_refresh_btn, 0, 6)
        layout.addWidget(top)

        self.gooo_changes = QPlainTextEdit()
        self.gooo_changes.setReadOnly(True)
        self.gooo_changes.setMaximumHeight(140)
        layout.addWidget(self.gooo_changes)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Podglad output"))
        self.gooo_output_preview = QPlainTextEdit()
        self.gooo_output_preview.setReadOnly(True)
        ll.addWidget(self.gooo_output_preview)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Raporty (validation + generation)"))
        self.gooo_reports = QPlainTextEdit()
        self.gooo_reports.setReadOnly(True)
        rl.addWidget(self.gooo_reports)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([760, 620])
        layout.addWidget(split, 1)

        self.gooo_upload_btn = QPushButton("UPLOAD")
        self.gooo_upload_btn.setMinimumHeight(90)
        self.gooo_upload_btn.setStyleSheet(
            "QPushButton { background:#dc2626; color:white; border:none; border-radius:10px; font-size:22px; font-weight:800; padding:12px; }"
            "QPushButton:hover { background:#b91c1c; }"
            "QPushButton:pressed { background:#991b1b; }"
        )
        self.gooo_upload_btn.clicked.connect(self.upload_gooo)
        layout.addWidget(self.gooo_upload_btn)

        for w in [self.gooo_title, self.gooo_slug, self.gooo_focus_keyphrase, self.gooo_meta_description]:
            w.textChanged.connect(self._schedule_save)
        for c in [self.gooo_visibility, self.gooo_target]:
            c.currentTextChanged.connect(self._schedule_save)

    def _build_full_auto_tab(self) -> None:
        self._auto_sync_lock = False
        self._auto_steps_order = [
            "template",
            "prompt",
            "images",
            "strategy",
            "content",
            "polishing",
            "images_wp",
            "output",
            "publish",
        ]
        self._auto_step_labels = {
            "template": "Template gotowy",
            "prompt": "Prompt poprawny",
            "images": "Zdjecia gotowe",
            "strategy": "Etap 1 strategia",
            "content": "Etap 2 content",
            "polishing": "Polishing + auto-fix",
            "images_wp": "Plan zdjec + upload WP",
            "output": "Output + raporty",
            "publish": "Publish DRAFT",
        }

        layout = QVBoxLayout(self.tab_full_auto)

        top = QGroupBox("Ustawienia w pigulce")
        t = QGridLayout(top)
        self.auto_template_path = QLineEdit(self.template_path.text())
        self.auto_images_folder = QLineEdit(self.images_folder.text())
        self.auto_prompt = QTextEdit()
        self.auto_prompt.setMinimumHeight(120)
        self.auto_prompt.setPlainText(self.global_prompt.toPlainText())
        self.auto_target = QComboBox()
        self.auto_target.addItems(["PAGE", "PORTFOLIO"])
        self.auto_target.setCurrentText(self.gooo_target.currentText())
        self.auto_browse_template = QPushButton("Wybierz szablon")
        self.auto_browse_images = QPushButton("Wybierz folder zdjec")
        self.auto_browse_template.clicked.connect(self._auto_pick_template)
        self.auto_browse_images.clicked.connect(self._auto_pick_images)
        t.addWidget(QLabel("Szablon:"), 0, 0)
        t.addWidget(self.auto_template_path, 0, 1, 1, 4)
        t.addWidget(self.auto_browse_template, 0, 5)
        t.addWidget(QLabel("Folder zdjec:"), 1, 0)
        t.addWidget(self.auto_images_folder, 1, 1, 1, 4)
        t.addWidget(self.auto_browse_images, 1, 5)
        t.addWidget(QLabel("Target:"), 2, 0)
        t.addWidget(self.auto_target, 2, 1)
        t.addWidget(QLabel("Prompt:"), 3, 0)
        t.addWidget(self.auto_prompt, 3, 1, 1, 5)
        layout.addWidget(top)

        mission = QGroupBox("Stan misji")
        m = QVBoxLayout(mission)
        self.auto_mission_list = QListWidget()
        self.auto_mission_list.setMinimumHeight(240)
        m.addWidget(self.auto_mission_list)
        layout.addWidget(mission)

        self.auto_start_btn = QPushButton("START")
        self.auto_start_btn.setMinimumHeight(74)
        self.auto_start_btn.setStyleSheet(
            "QPushButton { background:#0f766e; color:white; border:none; border-radius:10px; font-size:24px; font-weight:800; padding:10px; }"
            "QPushButton:hover { background:#0d9488; }"
            "QPushButton:pressed { background:#115e59; }"
        )
        self.auto_start_btn.clicked.connect(self.start_full_auto)
        layout.addWidget(self.auto_start_btn)

        layout.addWidget(QLabel("Live logi FULL AUTO"))
        self.auto_logs = QPlainTextEdit()
        self.auto_logs.setReadOnly(True)
        self.auto_logs.setMinimumHeight(200)
        layout.addWidget(self.auto_logs, 1)

        self._auto_reset_mission()

        self.auto_template_path.textChanged.connect(self._auto_sync_to_main)
        self.auto_images_folder.textChanged.connect(self._auto_sync_to_main)
        self.auto_prompt.textChanged.connect(self._auto_sync_to_main)
        self.auto_target.currentTextChanged.connect(self._auto_sync_to_main)
        self.template_path.textChanged.connect(self._auto_sync_from_main)
        self.images_folder.textChanged.connect(self._auto_sync_from_main)
        self.global_prompt.textChanged.connect(self._auto_sync_from_main)
        self.gooo_target.currentTextChanged.connect(self._auto_sync_from_main)

    def _auto_pick_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz szablon", "", "Tekst (*.txt *.html *.php);;Wszystkie (*)")
        if path:
            self.auto_template_path.setText(path)

    def _auto_pick_images(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Wybierz folder ze zdjeciami", self.auto_images_folder.text().strip())
        if path:
            self.auto_images_folder.setText(path)

    def _auto_sync_to_main(self) -> None:
        if self._auto_sync_lock:
            return
        self._auto_sync_lock = True
        self.template_path.setText(self.auto_template_path.text())
        self.images_folder.setText(self.auto_images_folder.text())
        self.global_prompt.setPlainText(self.auto_prompt.toPlainText())
        self.gooo_target.setCurrentText(self.auto_target.currentText())
        self._auto_sync_lock = False
        self._schedule_save()

    def _auto_sync_from_main(self) -> None:
        if self._auto_sync_lock:
            return
        self._auto_sync_lock = True
        self.auto_template_path.setText(self.template_path.text())
        self.auto_images_folder.setText(self.images_folder.text())
        self.auto_prompt.setPlainText(self.global_prompt.toPlainText())
        self.auto_target.setCurrentText(self.gooo_target.currentText())
        self._auto_sync_lock = False

    def _auto_reset_mission(self) -> None:
        self.auto_mission_list.clear()
        for step in self._auto_steps_order:
            item = QListWidgetItem(f"{self._auto_step_labels.get(step, step)} - CZEKA")
            item.setData(Qt.UserRole, step)
            item.setBackground(QColor("#e2e8f0"))
            item.setForeground(QColor("#334155"))
            self.auto_mission_list.addItem(item)

    def _auto_set_step(self, step: str, status: str, note: str = "") -> None:
        color_bg = "#e2e8f0"
        color_fg = "#334155"
        status_txt = "CZEKA"
        if status == "running":
            color_bg, color_fg, status_txt = "#dbeafe", "#1d4ed8", "W TRAKCIE"
        elif status == "done":
            color_bg, color_fg, status_txt = "#dcfce7", "#166534", "OK"
        elif status == "error":
            color_bg, color_fg, status_txt = "#fee2e2", "#991b1b", "BLAD"
        for i in range(self.auto_mission_list.count()):
            it = self.auto_mission_list.item(i)
            if str(it.data(Qt.UserRole)) != step:
                continue
            it.setText(f"{self._auto_step_labels.get(step, step)} - {status_txt}{(' | ' + note) if note else ''}")
            it.setBackground(QColor(color_bg))
            it.setForeground(QColor(color_fg))
            break

    def _image_placeholder_ids(self, include_gallery: bool = False) -> list[str]:
        out: list[str] = []
        for p in self.engine.placeholders:
            if p.kind != "image":
                continue
            if (not include_gallery) and bool(getattr(p, "in_gallery", False)):
                continue
            out.append(p.pid)
        return out

    def _refresh_image_counts(self) -> None:
        required = len(self._image_placeholder_ids())
        available = len(self.image_cards)
        self.images_required_lbl.setText(f"REQUIRED: {required}")
        self.images_available_lbl.setText(f"AVAILABLE: {available}")
        if available < required:
            self.images_warning_lbl.setText(
                "Za malo zdjec - mozna usunac sekcje typu gallery/carousel recznie."
            )
        else:
            self.images_warning_lbl.setText("")

    def _select_images_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Wybierz folder ze zdjeciami", self.images_folder.text().strip())
        if path:
            self.images_folder.setText(path)

    def _rebuild_image_cards(self, preview: PreviewBuildResult) -> None:
        while self.images_layout.count() > 1:
            item = self.images_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.image_cards.clear()
        self.image_preview_map = preview.preview_map.copy()

        placeholder_ids = self._image_placeholder_ids()
        for local_path, preview_path in preview.preview_map.items():
            card = ImageAssetCard(local_path=local_path, preview_path=preview_path)
            card.set_placeholder_options(placeholder_ids)
            card.improveRequested.connect(self.improve_one_image)
            card.contentChanged.connect(self._schedule_save)
            self.image_cards[local_path] = card
            self.images_layout.insertWidget(self.images_layout.count() - 1, card)

        self._refresh_image_counts()
        self._restore_image_cards_from_draft()
        self._refresh_image_map_info()

    def _restore_image_cards_from_draft(self) -> None:
        draft = getattr(self, "_session_draft", {}) or {}
        images_state = draft.get("images_state", {}) if isinstance(draft, dict) else {}
        if not isinstance(images_state, dict):
            return
        for local_path, state in images_state.items():
            card = self.image_cards.get(local_path)
            if card and isinstance(state, dict):
                card.import_state(state)

    def _refresh_image_map_info(self) -> None:
        lines = []
        assigned = 0
        for local, card in self.image_cards.items():
            ph = card.assigned_placeholder()
            if ph:
                assigned += 1
            wp = self.image_wp_map.get(local, {})
            wp_url = str(wp.get("source_url", "")) if isinstance(wp, dict) else ""
            lines.append(f"{Path(local).name} -> {ph or '(brak)'} -> {wp_url or '(not uploaded)'}")
        lines.insert(0, f"Assigned placeholders: {assigned}/{len(self._image_placeholder_ids())}")
        self.images_map_info.setPlainText("\n".join(lines))

    def _best_featured_media_id(self) -> int:
        required_ph = set(self._image_placeholder_ids(include_gallery=False))
        candidates: list[tuple[float, int]] = []
        fallback: list[int] = []
        for local, card in self.image_cards.items():
            if not card.selected.isChecked():
                continue
            wp_item = self.image_wp_map.get(local, {})
            if not isinstance(wp_item, dict):
                continue
            mid = int(wp_item.get("media_id", 0) or 0)
            if mid <= 0:
                continue
            fallback.append(mid)
            ph = card.assigned_placeholder()
            if ph and ph in required_ph:
                candidates.append((float(card.score_value), mid))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return int(candidates[0][1])
        return int(fallback[0]) if fallback else 0

    def load_images_folder(self) -> None:
        folder = self.images_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "Brak", "Wybierz folder ze zdjeciami.")
            return
        cache_root = str(Path.cwd() / "temp" / "images_preview")
        preview = build_previews_and_contact_sheet(folder=folder, cache_root=cache_root)
        if not preview.preview_map:
            QMessageBox.warning(self, "Brak", "Nie znaleziono obrazow JPG/PNG/WEBP w folderze.")
            return
        self._rebuild_image_cards(preview)
        self._set_status_success(f"Wczytano {len(preview.preview_map)} zdjec.")
        self._schedule_save()

    def generate_images_plan(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        if not self.image_cards:
            self.load_images_folder()
            if not self.image_cards:
                return
        placeholder_ids = self._image_placeholder_ids()
        if not placeholder_ids:
            QMessageBox.information(self, "ZDJECIA", "Szablon nie zawiera placeholderow obrazow.")
            return

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            preview = build_previews_and_contact_sheet(
                folder=self.images_folder.text().strip(),
                cache_root=str(Path.cwd() / "temp" / "images_preview"),
            )
            contact_url = file_to_data_url(preview.contact_sheet_path)
            manifest = []
            for local, prev in preview.preview_map.items():
                w, h = image_dimensions(prev)
                manifest.append(
                    {
                        "image_key": Path(local).name,
                        "local_path": local,
                        "preview_path": prev,
                        "width": w,
                        "height": h,
                    }
                )
            data = client.generate_image_plan(
                global_prompt=self.global_prompt.toPlainText().strip(),
                section_schema=self.engine.section_schema,
                image_placeholder_ids=placeholder_ids,
                images_manifest=manifest,
                contact_sheet_data_url=contact_url,
            )
            data["_preview"] = {"preview_map": preview.preview_map, "contact_sheet_path": preview.contact_sheet_path}
            return data

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            preview = data.get("_preview", {}) if isinstance(data, dict) else {}
            preview_map = preview.get("preview_map", {}) if isinstance(preview, dict) else {}
            if preview_map:
                p = PreviewBuildResult(
                    preview_map={str(k): str(v) for k, v in preview_map.items()},
                    contact_sheet_path=str(preview.get("contact_sheet_path", "")),
                    cache_dir=str(Path.cwd() / "temp" / "images_preview"),
                )
                self._rebuild_image_cards(p)

            by_name = {Path(lp).name: lp for lp in self.image_cards.keys()}
            selected = data.get("selected", []) if isinstance(data.get("selected"), list) else []
            used_locals: set[str] = set()
            for item in selected:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("image_key", "")).strip()
                local = by_name.get(key)
                if not local or local not in self.image_cards:
                    continue
                used_locals.add(local)
                card = self.image_cards[local]
                card.selected.setChecked(True)
                card.set_metadata(
                    slugify_filename(str(item.get("seo_filename", "")).strip() or Path(local).stem),
                    str(item.get("alt", "")),
                    str(item.get("caption", "")),
                    str(item.get("description", "")),
                )
                try:
                    score = float(item.get("score", 0.0))
                except Exception:
                    score = 0.0
                short_reason = str(item.get("short_reason", "")).strip() or str(item.get("reason", "")).strip()
                card.set_score_reason(score, short_reason)
                ph = str(item.get("placeholder_id", "")).strip()
                if ph:
                    card.set_placeholder(ph)
                    if ph in self.cards and self.cards[ph].placeholder.kind == "image":
                        self.cards[ph].image_path.setText(local)
                card.set_status("AI: gotowe", "ok")

            for local, card in self.image_cards.items():
                if local not in used_locals:
                    card.selected.setChecked(False)
            self._refresh_image_map_info()
            self._set_status_success("ZDJECIA: plan i metadane wygenerowane.")
            self._schedule_save()

        self._run_task("ZDJECIA: Generuj plan", worker, done)

    def improve_one_image(self, local_path: str) -> None:
        if local_path not in self.image_cards:
            return
        card = self.image_cards[local_path]
        preview_path = self.image_preview_map.get(local_path, "")
        data_url = file_to_data_url(preview_path) if preview_path else ""
        current = card.metadata()
        current["seo_filename"] = slugify_filename(current.get("seo_filename", "") or Path(local_path).stem)

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            return client.improve_one_image_metadata(
                global_prompt=self.global_prompt.toPlainText().strip(),
                image_key=Path(local_path).name,
                current_metadata=current,
                contact_image_data_url=data_url,
                assigned_placeholder_id=card.assigned_placeholder(),
            )

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            card.set_metadata(
                slugify_filename(str(data.get("seo_filename", "")).strip() or current["seo_filename"]),
                str(data.get("alt", "")),
                str(data.get("caption", "")),
                str(data.get("description", "")),
            )
            card.set_status("Poprawione", "ok")
            self._refresh_image_map_info()
            self._set_status_success(f"POPRAW: {Path(local_path).name}")
            self._schedule_save()

        self._run_task(f"ZDJECIA: POPRAW {Path(local_path).name}", worker, done)

    def _wp_creds(self) -> WordPressCredentials:
        site = self.wp_site.text().strip()
        user = self.wp_user.text().strip()
        app = self.wp_app_pass.text().strip()
        if not site or not user or not app:
            raise ValueError("Uzupelnij WP URL, WP User i Application Password.")
        return WordPressCredentials(site_url=site, username=user, application_password=app)

    def upload_selected_images_wp(self) -> None:
        selected = [card for card in self.image_cards.values() if card.selected.isChecked()]
        if not selected:
            QMessageBox.information(self, "ZDJECIA", "Brak wybranych zdjec do uploadu.")
            return
        try:
            creds = self._wp_creds()
        except Exception as exc:
            QMessageBox.warning(self, "WordPress", str(exc))
            return

        self.images_progress.setVisible(True)
        self.images_progress.setRange(0, len(selected))
        self.images_progress.setValue(0)

        def worker(token: OperationToken, op_id: int) -> object:
            client = WordPressClient(creds)
            client.check_auth()
            uploaded: Dict[str, Dict[str, object]] = {}
            errors: Dict[str, str] = {}
            total = len(selected)
            for idx, card in enumerate(selected, start=1):
                if token.cancel_requested:
                    raise RuntimeError("Operacja przerwana przez uzytkownika.")
                local = card.local_path
                meta = card.metadata()
                seo = slugify_filename(meta.get("seo_filename", "") or Path(local).stem)
                try:
                    result = client.upload_media(
                        local_path=local,
                        upload_filename=seo,
                        title=seo.replace("-", " ").title(),
                        alt_text=meta.get("alt", ""),
                        caption=meta.get("caption", ""),
                        description=meta.get("description", ""),
                    )
                    uploaded[local] = result
                    self.bridge.task_progress.emit(op_id, idx, total, local, "uploaded")
                except Exception as exc:
                    errors[local] = str(exc)
                    self.bridge.task_progress.emit(op_id, idx, total, local, "error")
            return {"uploaded": uploaded, "errors": errors}

        def done(result: object) -> None:
            payload = result if isinstance(result, dict) else {}
            out = payload.get("uploaded", {}) if isinstance(payload.get("uploaded"), dict) else {}
            errs = payload.get("errors", {}) if isinstance(payload.get("errors"), dict) else {}
            for local, item in out.items():
                self.image_wp_map[local] = item
                card = self.image_cards.get(local)
                if card:
                    card.set_status("Uploaded", "ok")
            for local in errs.keys():
                card = self.image_cards.get(local)
                if card:
                    card.set_status("Upload error", "err")
            for local, msg in errs.items():
                self._log(f"[WP Upload] {Path(local).name}: {msg}")
            self.images_progress.setVisible(False)
            self._refresh_image_map_info()
            if errs:
                self._set_status_error(f"WP upload: OK={len(out)}, ERR={len(errs)}")
            else:
                self._set_status_success(f"WP upload zakonczony ({len(out)}).")
            self._schedule_save()

        self._run_task("ZDJECIA: Upload WordPress", worker, done)

    def update_image_links_in_placeholders(self) -> None:
        if not self.cards:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        updates = 0
        used_urls = 0
        for local, card in self.image_cards.items():
            ph = card.assigned_placeholder()
            if not ph or ph not in self.cards:
                continue
            wp_item = self.image_wp_map.get(local, {})
            url = str(wp_item.get("source_url", "")).strip() if isinstance(wp_item, dict) else ""
            if not url:
                continue
            ph_card = self.cards.get(ph)
            if ph_card and ph_card.placeholder.kind == "image":
                ph_card.image_path.setText(url)
                updates += 1
                used_urls += 1
        self._refresh_image_map_info()
        self._schedule_save()
        QMessageBox.information(
            self,
            "AKTUALIZUJ LINKI",
            f"Zaktualizowano placeholdery: {updates}\nPodpiete URL z WP: {used_urls}",
        )

    def _save_wp_pass_secure(self) -> None:
        try:
            self.wp_pass_store.save(self.wp_app_pass.text())
            self._set_status_success("Haslo WP zapisane bezpiecznie (DPAPI).")
        except Exception as exc:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie zapisac hasla WP: {exc}")

    def _load_wp_pass_secure(self, silent: bool = False) -> None:
        try:
            pw = self.wp_pass_store.load()
            if pw:
                self.wp_app_pass.setText(pw)
                if not silent:
                    self._set_status_success("Haslo WP wczytane.")
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Blad", f"Nie udalo sie wczytac hasla WP: {exc}")

    def _clear_wp_pass_secure(self) -> None:
        try:
            self.wp_pass_store.clear()
            self.wp_app_pass.clear()
            self._set_status_success("Haslo WP usuniete.")
        except Exception as exc:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie usunac hasla WP: {exc}")

    def test_wp_connection(self) -> None:
        try:
            creds = self._wp_creds()
        except Exception as exc:
            QMessageBox.warning(self, "WordPress", str(exc))
            return

        def worker(token: OperationToken) -> object:
            if token.cancel_requested:
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            client = WordPressClient(creds)
            return client.test_connection_full()

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            if not data.get("ok"):
                self._set_status_error("WP test: blad")
                QMessageBox.warning(self, "WordPress test", json.dumps(data, ensure_ascii=False, indent=2))
                return
            self._set_status_success("WP test: OK")
            msg = [
                "Polaczenie OK.",
                f"User: {data.get('user', {}).get('name', '')} (id={data.get('user', {}).get('user_id', '')})",
                f"Pages endpoint: OK (count={data.get('pages', {}).get('count', '-')})",
                f"Posts endpoint: OK (count={data.get('posts', {}).get('count', '-')})",
                f"Upload test media_id: {data.get('upload_test', {}).get('media_id', '-')}",
                f"Cleanup: {'OK' if data.get('cleanup', {}).get('ok') else 'WARN'}",
            ]
            QMessageBox.information(self, "WordPress test", "\n".join(msg))

        self._run_task("WordPress: Test connection", worker, done)

    def start_full_auto(self) -> None:
        self._auto_sync_to_main()
        self._auto_reset_mission()

        template_path = self.template_path.text().strip()
        prompt = self.global_prompt.toPlainText().strip()
        images_folder = self.images_folder.text().strip()
        target_type = "page" if self.auto_target.currentText().strip().upper() == "PAGE" else "portfolio"

        missing: list[str] = []
        if not template_path or not Path(template_path).exists():
            missing.append("Brak pliku szablonu.")
            self._auto_set_step("template", "error", "Brak pliku")
        else:
            self._auto_set_step("template", "done")
        if len(prompt) < 30:
            missing.append("Prompt jest za krotki (min. 30 znakow).")
            self._auto_set_step("prompt", "error", "Za krotki")
        else:
            self._auto_set_step("prompt", "done")

        required_images = 0
        available_images = 0
        if template_path and Path(template_path).exists():
            raw = self.engine.read_text(Path(template_path))
            parsed = self.engine.parser.parse(raw)
            required_images = len([p for p in parsed.placeholders if p.kind == "image" and not p.in_gallery])
        if images_folder and Path(images_folder).exists():
            available_images = len(list_images_in_folder(images_folder))
        if required_images > 0 and available_images < required_images:
            missing.append(f"Za malo zdjec: wymagane {required_images}, dostepne {available_images}.")
            self._auto_set_step("images", "error", f"{available_images}/{required_images}")
        else:
            self._auto_set_step("images", "done", f"{available_images}/{required_images}")

        try:
            creds = self._wp_creds()
        except Exception as exc:
            missing.append(str(exc))
            creds = None

        if missing:
            QMessageBox.warning(self, "FULL AUTO - braki", "\n".join(missing))
            return

        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("Potwierdz FULL AUTO")
        confirm.setText(
            "Walidacja preflight zakonczona.\n"
            f"Template: OK\nPrompt: OK\nZdjecia: {available_images}/{required_images}\n"
            f"Publikacja: DRAFT ({target_type.upper()})\n\n"
            "Czy uruchomic START?"
        )
        yes_btn = confirm.addButton("TAK, START", QMessageBox.AcceptRole)
        confirm.addButton("Anuluj", QMessageBox.RejectRole)
        confirm.exec()
        if confirm.clickedButton() is not yes_btn:
            return

        config = self._collect_config()
        threshold = float(self.polish_threshold.value())
        polish_mode = self.polish_mode.currentText().strip() or "BALANCED"
        output_path = Path(self.output_path.text().strip())
        output_path.parent.mkdir(parents=True, exist_ok=True)

        def worker(token: OperationToken, op_id: int) -> object:
            def step(step_id: str, state: str, note: str = "") -> None:
                self.bridge.task_progress.emit(op_id, 0, 0, f"auto:{step_id}", f"{state}|{note}")

            if token.cancel_requested:
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            client = self._create_client(token)
            current_step = "template"
            try:
                step("template", "running")
                self.engine.load_template(template_path)
                step("template", "done")

                current_step = "strategy"
                step("strategy", "running")
                strategy = self.engine.generate_strategy(client, prompt)
                step("strategy", "done")

                current_step = "content"
                step("content", "running")
                mapped = self.engine.generate_page_content(
                    client=client,
                    global_prompt=prompt,
                    config=config,
                    extra_prompts={},
                )
                step("content", "done", f"{len(mapped)} blokow")
            except Exception as exc:
                step(current_step, "error", str(exc)[:90])
                raise

            replacements: Dict[str, str] = {}
            for ph in self.engine.placeholders:
                if ph.kind == "text":
                    replacements[ph.pid] = mapped.get(ph.pid, ph.original)
                else:
                    replacements[ph.pid] = ph.original

            try:
                current_step = "polishing"
                step("polishing", "running")
                pol = self.engine.run_polishing_validation(client=client, global_prompt=prompt, replacements=replacements, mode=polish_mode)
                fixed = 0
                for unit in pol.get("units", []):
                    if not isinstance(unit, dict):
                        continue
                    overall = float(unit.get("scores", {}).get("overall", 0.0)) if isinstance(unit.get("scores"), dict) else 0.0
                    if overall >= threshold:
                        continue
                    fixed_u = self.engine.run_polishing_fix_unit(
                        client=client,
                        global_prompt=prompt,
                        unit=unit,
                        mode=polish_mode,
                    )
                    patch = self.engine.polished_unit_to_replacements(
                        fixed_u,
                        str(fixed_u.get("polished_text", "")),
                        replacements,
                    )
                    replacements.update(patch)
                    fixed += 1
                step("polishing", "done", f"fixed={fixed}")
            except Exception as exc:
                step(current_step, "error", str(exc)[:90])
                raise

            try:
                current_step = "images_wp"
                step("images_wp", "running")
                required_ids = [p.pid for p in self.engine.placeholders if p.kind == "image" and not p.in_gallery]
                preview = build_previews_and_contact_sheet(folder=images_folder, cache_root=str(Path.cwd() / "temp" / "images_preview"))
                image_wp_map: Dict[str, Dict[str, object]] = {}
                selected_map: Dict[str, Dict[str, object]] = {}
                featured_media_id = 0
                if required_ids:
                    manifest = []
                    for local, prev in preview.preview_map.items():
                        w, h = image_dimensions(prev)
                        manifest.append(
                            {
                                "image_key": Path(local).name,
                                "local_path": local,
                                "preview_path": prev,
                                "width": w,
                                "height": h,
                            }
                        )
                    plan = client.generate_image_plan(
                        global_prompt=prompt,
                        section_schema=self.engine.section_schema,
                        image_placeholder_ids=required_ids,
                        images_manifest=manifest,
                        contact_sheet_data_url=file_to_data_url(preview.contact_sheet_path),
                    )
                    by_name = {Path(lp).name: lp for lp in preview.preview_map.keys()}
                    wp = WordPressClient(creds)  # type: ignore[arg-type]
                    wp.check_auth()
                    scored_media: list[tuple[float, int]] = []
                    for item in plan.get("selected", []):
                        if not isinstance(item, dict):
                            continue
                        ph = str(item.get("placeholder_id", "")).strip()
                        if not ph or ph not in required_ids:
                            continue
                        key = str(item.get("image_key", "")).strip()
                        local = by_name.get(key, "")
                        if not local:
                            continue
                        seo = slugify_filename(str(item.get("seo_filename", "")).strip() or Path(local).stem)
                        uploaded = wp.upload_media(
                            local_path=local,
                            upload_filename=seo,
                            title=seo.replace("-", " ").title(),
                            alt_text=str(item.get("alt", "")),
                            caption=str(item.get("caption", "")),
                            description=str(item.get("description", "")),
                        )
                        image_wp_map[local] = uploaded
                        selected_map[local] = item
                        replacements[ph] = str(uploaded.get("source_url", ""))
                        try:
                            score = float(item.get("score", 0.0))
                        except Exception:
                            score = 0.0
                        mid = int(uploaded.get("media_id", 0) or 0)
                        if mid > 0:
                            scored_media.append((score, mid))
                    if scored_media:
                        scored_media.sort(key=lambda x: x[0], reverse=True)
                        featured_media_id = int(scored_media[0][1])
                    step("images_wp", "done", f"uploaded={len(image_wp_map)}")
                else:
                    step("images_wp", "done", "no-required-images")
            except Exception as exc:
                step(current_step, "error", str(exc)[:90])
                raise

            try:
                current_step = "output"
                step("output", "running")
                output, final_replacements = self.engine.build_output(replacements)
                output_path.write_text(output, encoding="utf-8")
                validation = self.engine.validate_output_text(output, final_replacements, prompt, config)
                report_path = output_path.with_name(f"{output_path.stem}_validation_report.txt")
                report_path.write_text(validation["report_text"], encoding="utf-8")
                generation_path = output_path.with_name(f"{output_path.stem}_generation_report.txt")
                generation_path.write_text(self.engine.build_generation_report(final_replacements, config), encoding="utf-8")
                step("output", "done")
            except Exception as exc:
                step(current_step, "error", str(exc)[:90])
                raise

            seo_meta = client.generate_seo_title_slug(
                global_prompt=prompt,
                strategy=strategy,
                section_schema=self.engine.section_schema,
                sample_output_excerpt=output[:2200],
            )
            title = str(seo_meta.get("title", "")).strip() or "Generated AVADA Page"
            slug = slugify_filename(str(seo_meta.get("slug", "")).strip() or title)
            focus = self._seo_clean_keyphrase(str(seo_meta.get("focus_keyphrase", "")).strip())
            meta_desc = self._seo_clean_meta_description(str(seo_meta.get("meta_description", "")).strip())

            try:
                current_step = "publish"
                step("publish", "running")
                wp_pub = WordPressClient(creds)  # type: ignore[arg-type]
                pub = wp_pub.publish_content(
                    title=title,
                    content=output,
                    status="draft",
                    target_type=target_type,
                    slug=slug,
                    featured_media_id=featured_media_id,
                    yoast_focus_keyphrase=focus,
                    yoast_meta_description=meta_desc,
                )
                step("publish", "done", f"id={pub.get('id', 0)}")
            except Exception as exc:
                step(current_step, "error", str(exc)[:90])
                raise
            return {
                "final_replacements": final_replacements,
                "polishing_result": pol,
                "image_wp_map": image_wp_map,
                "selected_map": selected_map,
                "preview": {
                    "preview_map": preview.preview_map,
                    "contact_sheet_path": preview.contact_sheet_path,
                },
                "report_path": str(report_path),
                "generation_path": str(generation_path),
                "publish": pub,
                "seo_meta": {"title": title, "slug": slug, "focus_keyphrase": focus, "meta_description": meta_desc},
                "featured_media_id": featured_media_id,
            }

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            self._rebuild_placeholder_cards()
            reps = data.get("final_replacements", {}) if isinstance(data.get("final_replacements"), dict) else {}
            for pid, value in reps.items():
                if pid in self.cards:
                    card = self.cards[pid]
                    if card.placeholder.kind == "text":
                        card.set_result(str(value))
                    else:
                        card.image_path.setText(str(value))

            pol = data.get("polishing_result", {})
            if isinstance(pol, dict):
                self.polishing_result = pol
                self._refresh_polishing_ui()

            wp_map = data.get("image_wp_map", {})
            if isinstance(wp_map, dict):
                self.image_wp_map = wp_map

            prev = data.get("preview", {})
            pmap = prev.get("preview_map", {}) if isinstance(prev, dict) else {}
            if isinstance(pmap, dict) and pmap:
                p = PreviewBuildResult(
                    preview_map={str(k): str(v) for k, v in pmap.items()},
                    contact_sheet_path=str(prev.get("contact_sheet_path", "")),
                    cache_dir=str(Path.cwd() / "temp" / "images_preview"),
                )
                self._rebuild_image_cards(p)
                selected_map = data.get("selected_map", {}) if isinstance(data.get("selected_map"), dict) else {}
                for local, item in selected_map.items():
                    if local not in self.image_cards or not isinstance(item, dict):
                        continue
                    card = self.image_cards[local]
                    card.selected.setChecked(True)
                    card.set_placeholder(str(item.get("placeholder_id", "")))
                    card.set_metadata(
                        slugify_filename(str(item.get("seo_filename", "")).strip() or Path(local).stem),
                        str(item.get("alt", "")),
                        str(item.get("caption", "")),
                        str(item.get("description", "")),
                    )
                    try:
                        sc = float(item.get("score", 0.0))
                    except Exception:
                        sc = 0.0
                    card.set_score_reason(sc, str(item.get("short_reason", "")) or str(item.get("reason", "")))
                    if local in self.image_wp_map:
                        card.set_status("Uploaded", "ok")

            seo = data.get("seo_meta", {}) if isinstance(data.get("seo_meta"), dict) else {}
            self.gooo_title.setText(str(seo.get("title", "")))
            self.gooo_slug.setText(str(seo.get("slug", "")))
            self.gooo_focus_keyphrase.setText(str(seo.get("focus_keyphrase", "")))
            self.gooo_meta_description.setText(str(seo.get("meta_description", "")))
            self.gooo_visibility.setCurrentText("DRAFT")
            self.gooo_target.setCurrentText(self.auto_target.currentText())

            self.last_validation_report_path = Path(str(data.get("report_path", ""))) if data.get("report_path") else None
            self.last_generation_report_path = Path(str(data.get("generation_path", ""))) if data.get("generation_path") else None
            self.refresh_gooo_data()
            self.show_strategy_preview()
            self._schedule_save()
            pub = data.get("publish", {}) if isinstance(data.get("publish"), dict) else {}
            msg = (
                f"FULL AUTO zakonczone.\nID={pub.get('id', 0)}\nStatus={pub.get('status', '')}\n"
                f"Link={pub.get('link', '')}"
            )
            self._set_status_success("FULL AUTO zakonczone.")
            QMessageBox.information(self, "FULL AUTO", msg)

        self._run_task("FULL AUTO: START", worker, done)

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
        line = f"[{ts}] {message}"
        self._log_lines.append(line)
        self.logs_view.appendPlainText(line)
        if hasattr(self, "auto_logs") and self.auto_logs is not None:
            self.auto_logs.appendPlainText(line)
        if self._logs_window_text is not None:
            self._logs_window_text.appendPlainText(line)

    def copy_logs(self) -> None:
        QApplication.clipboard().setText(self.logs_view.toPlainText())
        self._set_status_success("Skopiowano logi do schowka.")

    def clear_logs(self) -> None:
        self._log_lines.clear()
        self.logs_view.clear()
        if self._logs_window_text is not None:
            self._logs_window_text.clear()

    def open_logs_window(self) -> None:
        if self._logs_window is not None:
            self._logs_window.raise_()
            self._logs_window.activateWindow()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Live logi")
        dlg.resize(980, 560)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText("\n".join(self._log_lines))
        lay.addWidget(txt)
        row = QHBoxLayout()
        b_copy = QPushButton("Kopiuj")
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(txt.toPlainText()))
        b_clear = QPushButton("Wyczysc")
        b_clear.clicked.connect(self.clear_logs)
        row.addWidget(b_copy)
        row.addWidget(b_clear)
        row.addStretch(1)
        lay.addLayout(row)
        self._logs_window = dlg
        self._logs_window_text = txt

        def on_close() -> None:
            self._logs_window = None
            self._logs_window_text = None

        dlg.finished.connect(lambda _: on_close())
        dlg.show()

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
        self.settings.setValue("polish_enable", self.polish_enable.isChecked())
        self.settings.setValue("polish_threshold", float(self.polish_threshold.value()))
        self.settings.setValue("polish_green_from", float(self.polish_green_from.value()))
        self.settings.setValue("polish_yellow_from", float(self.polish_yellow_from.value()))
        self.settings.setValue("polish_mode", self.polish_mode.currentText())
        self.settings.setValue("global_prompt", self.global_prompt.toPlainText())
        self.settings.setValue("images_folder", self.images_folder.text())
        self.settings.setValue("wp_site", self.wp_site.text())
        self.settings.setValue("wp_user", self.wp_user.text())
        self.settings.setValue("gooo_title", self.gooo_title.text())
        self.settings.setValue("gooo_slug", self.gooo_slug.text())
        self.settings.setValue("gooo_focus_keyphrase", self.gooo_focus_keyphrase.text())
        self.settings.setValue("gooo_meta_description", self.gooo_meta_description.text())
        self.settings.setValue("gooo_visibility", self.gooo_visibility.currentText())
        self.settings.setValue("gooo_target", self.gooo_target.currentText())
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
        self.polish_enable.setChecked(str(self.settings.value("polish_enable", "false")).lower() in ("1", "true", "yes"))
        self.polish_threshold.setValue(float(self.settings.value("polish_threshold", 8.0)))
        self.polish_green_from.setValue(float(self.settings.value("polish_green_from", 8.0)))
        self.polish_yellow_from.setValue(float(self.settings.value("polish_yellow_from", 6.5)))
        self._set_combo_value(self.polish_mode, self.settings.value("polish_mode", "BALANCED"))
        self.global_prompt.setPlainText(str(self.settings.value("global_prompt", self.global_prompt.toPlainText())))
        self.images_folder.setText(str(self.settings.value("images_folder", self.images_folder.text())))
        self.wp_site.setText(str(self.settings.value("wp_site", "")))
        self.wp_user.setText(str(self.settings.value("wp_user", "")))
        self.gooo_title.setText(str(self.settings.value("gooo_title", "")))
        self.gooo_slug.setText(str(self.settings.value("gooo_slug", "")))
        self.gooo_focus_keyphrase.setText(str(self.settings.value("gooo_focus_keyphrase", "")))
        self.gooo_meta_description.setText(str(self.settings.value("gooo_meta_description", "")))
        self._set_combo_value(self.gooo_visibility, self.settings.value("gooo_visibility", "DRAFT"))
        self._set_combo_value(self.gooo_target, self.settings.value("gooo_target", "PAGE"))
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
        strategy_raw = self.engine.page_strategy.raw if self.engine.page_strategy else {}
        page_content_raw = self.engine.page_content.raw if self.engine.page_content else {}
        page_content_mapped = self.engine.page_content.mapped if self.engine.page_content else {}
        page_content_missing = self.engine.page_content.missing_ids if self.engine.page_content else []
        cards_state = {pid: card.export_state() for pid, card in self.cards.items()}
        images_state = {path: card.export_state() for path, card in self.image_cards.items()}
        self.session_store.save(
            {
                "template_path": self.template_path.text().strip(),
                "images_folder": self.images_folder.text().strip(),
                "output_path": self.output_path.text().strip(),
                "compare_path": self.compare_path.text().strip(),
                "image_metadata_path": self.image_metadata_path.text().strip(),
                "global_prompt": self.global_prompt.toPlainText(),
                "model": self.model.currentText(),
                "generation_mode": self.generation_mode.currentText(),
                "language_mode": self.language_mode.currentText(),
                "link_mode": self.link_mode.currentText(),
                "format_mode": self.format_mode.currentText(),
                "polish_enable": self.polish_enable.isChecked(),
                "polish_threshold": float(self.polish_threshold.value()),
                "polish_green_from": float(self.polish_green_from.value()),
                "polish_yellow_from": float(self.polish_yellow_from.value()),
                "polish_mode": self.polish_mode.currentText(),
                "gooo_title": self.gooo_title.text().strip(),
                "gooo_slug": self.gooo_slug.text().strip(),
                "gooo_focus_keyphrase": self.gooo_focus_keyphrase.text().strip(),
                "gooo_meta_description": self.gooo_meta_description.text().strip(),
                "gooo_visibility": self.gooo_visibility.currentText(),
                "gooo_target": self.gooo_target.currentText(),
                "cards": cards_state,
                "images_state": images_state,
                "image_wp_map": self.image_wp_map,
                "strategy_raw": strategy_raw,
                "page_content_raw": page_content_raw,
                "page_content_mapped": page_content_mapped,
                "page_content_missing": page_content_missing,
                "polishing_result": self.polishing_result if isinstance(self.polishing_result, dict) else {},
                "polishing_applied_unit_ids": sorted(self.polishing_applied_unit_ids),
                "polish_baseline_replacements": self.polish_baseline_replacements,
            }
        )

    def _load_session_draft(self) -> None:
        self._session_draft = self.session_store.load()
        draft = self._session_draft if isinstance(self._session_draft, dict) else {}
        wp_map = draft.get("image_wp_map", {})
        if isinstance(wp_map, dict):
            self.image_wp_map = wp_map
        folder = str(draft.get("images_folder", "")).strip()
        if folder:
            self.images_folder.setText(folder)

    def _restore_strategy_polishing_from_draft(self) -> None:
        draft = getattr(self, "_session_draft", {}) or {}
        if not isinstance(draft, dict):
            return

        strategy_raw = draft.get("strategy_raw", {})
        if isinstance(strategy_raw, dict) and strategy_raw:
            section_map: Dict[int, Dict[str, object]] = {}
            raw_sections = strategy_raw.get("sections", [])
            if isinstance(raw_sections, list):
                for item in raw_sections:
                    if not isinstance(item, dict):
                        continue
                    sid = item.get("section_id")
                    if isinstance(sid, int):
                        section_map[sid] = item
                    elif isinstance(sid, str) and sid.isdigit():
                        section_map[int(sid)] = item
            self.engine.page_strategy = StrategyResult(raw=strategy_raw, sections=section_map)

        page_raw = draft.get("page_content_raw", {})
        page_mapped = draft.get("page_content_mapped", {})
        page_missing = draft.get("page_content_missing", [])
        if isinstance(page_raw, dict) and isinstance(page_mapped, dict) and isinstance(page_missing, list):
            if page_raw or page_mapped:
                self.engine.page_content = PageContentResult(
                    raw=page_raw,
                    mapped={str(k): str(v) for k, v in page_mapped.items()},
                    missing_ids=[str(x) for x in page_missing],
                )

        pol = draft.get("polishing_result", {})
        if isinstance(pol, dict) and pol:
            self.polishing_result = pol
        ids = draft.get("polishing_applied_unit_ids", [])
        if isinstance(ids, list):
            self.polishing_applied_unit_ids = {str(x) for x in ids}
        baseline = draft.get("polish_baseline_replacements", {})
        if isinstance(baseline, dict):
            self.polish_baseline_replacements = {str(k): str(v) for k, v in baseline.items()}

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

    def _run_task(self, name: str, worker_fn: Callable[..., Any], done_fn: Callable[[Any], None]) -> None:
        self._op_seq += 1
        op_id = self._op_seq
        token = OperationToken()
        self._ops[op_id] = {"name": name, "token": token, "done_fn": done_fn}
        self._set_status_running(name)
        self._log(f"[Operation] start id={op_id} name={name}")

        def run() -> None:
            try:
                try:
                    result = worker_fn(token, op_id)
                except TypeError:
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
        self.images_progress.setVisible(False)
        QMessageBox.critical(self, "Blad", error)

    def _on_task_progress(self, op_id: int, current: int, total: int, item_key: str, status: str) -> None:
        if op_id not in self._ops:
            return
        if item_key.startswith("auto:"):
            step = item_key.split(":", 1)[1].strip()
            note = ""
            stat = status
            if "|" in status:
                stat, note = status.split("|", 1)
            self._auto_set_step(step, stat.strip(), note.strip())
            return
        if self.images_progress.isVisible():
            self.images_progress.setRange(0, max(1, total))
            self.images_progress.setValue(max(0, min(current, total)))
            self.images_progress.setFormat(f"{current}/{total}")
        if item_key and item_key in self.image_cards:
            card = self.image_cards[item_key]
            if status == "uploaded":
                card.set_status("Uploaded", "ok")
            elif status == "error":
                card.set_status("Upload error", "err")
            else:
                card.set_status(status, "info")

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
        self.open_strategy_preview_tab()
        self._restore_cards_from_draft()
        self._restore_strategy_polishing_from_draft()
        placeholder_ids = self._image_placeholder_ids()
        for card in self.image_cards.values():
            card.set_placeholder_options(placeholder_ids)
        self._refresh_image_counts()
        self._refresh_image_map_info()
        if self.polishing_result:
            self._refresh_polishing_ui()
        self.refresh_gooo_data()
        self._schedule_save()

    def save_session_manual(self) -> None:
        self._save_settings()
        data = {
            "template_path": self.template_path.text().strip(),
            "images_folder": self.images_folder.text().strip(),
            "output_path": self.output_path.text().strip(),
            "compare_path": self.compare_path.text().strip(),
            "image_metadata_path": self.image_metadata_path.text().strip(),
            "global_prompt": self.global_prompt.toPlainText(),
            "model": self.model.currentText(),
            "generation_mode": self.generation_mode.currentText(),
            "language_mode": self.language_mode.currentText(),
            "link_mode": self.link_mode.currentText(),
            "format_mode": self.format_mode.currentText(),
            "polish_enable": self.polish_enable.isChecked(),
            "polish_threshold": float(self.polish_threshold.value()),
            "polish_green_from": float(self.polish_green_from.value()),
            "polish_yellow_from": float(self.polish_yellow_from.value()),
            "polish_mode": self.polish_mode.currentText(),
            "gooo_title": self.gooo_title.text().strip(),
            "gooo_slug": self.gooo_slug.text().strip(),
            "gooo_focus_keyphrase": self.gooo_focus_keyphrase.text().strip(),
            "gooo_meta_description": self.gooo_meta_description.text().strip(),
            "gooo_visibility": self.gooo_visibility.currentText(),
            "gooo_target": self.gooo_target.currentText(),
            "cards": {pid: card.export_state() for pid, card in self.cards.items()},
            "images_state": {path: card.export_state() for path, card in self.image_cards.items()},
            "image_wp_map": self.image_wp_map,
            "strategy_raw": self.engine.page_strategy.raw if self.engine.page_strategy else {},
            "page_content_raw": self.engine.page_content.raw if self.engine.page_content else {},
            "page_content_mapped": self.engine.page_content.mapped if self.engine.page_content else {},
            "page_content_missing": self.engine.page_content.missing_ids if self.engine.page_content else [],
            "polishing_result": self.polishing_result if isinstance(self.polishing_result, dict) else {},
            "polishing_applied_unit_ids": sorted(self.polishing_applied_unit_ids),
            "polish_baseline_replacements": self.polish_baseline_replacements,
        }
        default_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        start_dir = str(Path(self.settings.value("session_last_path", str(Path.cwd() / default_name))).parent)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz sesje",
            str(Path(start_dir) / default_name),
            "Session JSON (*.json);;Wszystkie (*)",
        )
        if not path:
            return
        try:
            self.session_store.save_to_path(data, path)
            self.settings.setValue("session_last_path", path)
            self._set_status_success(f"Sesja zapisana: {path}")
            QMessageBox.information(self, "Sesja", f"Sesja zapisana:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie zapisac sesji: {exc}")

    def load_session_manual(self) -> None:
        start = str(self.settings.value("session_last_path", str(Path.cwd())))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wczytaj sesje",
            start,
            "Session JSON (*.json);;Wszystkie (*)",
        )
        if not path:
            return
        draft = self.session_store.load_from_path(path)
        if not draft:
            QMessageBox.warning(self, "Sesja", "Nie udalo sie wczytac sesji albo plik jest pusty.")
            return
        self.settings.setValue("session_last_path", path)
        self._session_draft = draft
        wp_map = draft.get("image_wp_map", {})
        if isinstance(wp_map, dict):
            self.image_wp_map = wp_map

        self.template_path.setText(str(draft.get("template_path", self.template_path.text())).strip())
        self.images_folder.setText(str(draft.get("images_folder", self.images_folder.text())).strip())
        self.output_path.setText(str(draft.get("output_path", self.output_path.text())).strip())
        self.compare_path.setText(str(draft.get("compare_path", self.compare_path.text())).strip())
        self.image_metadata_path.setText(str(draft.get("image_metadata_path", self.image_metadata_path.text())).strip())
        self.global_prompt.setPlainText(str(draft.get("global_prompt", self.global_prompt.toPlainText())))
        self._set_combo_value(self.model, draft.get("model", self.model.currentText()))
        self._set_combo_value(self.generation_mode, draft.get("generation_mode", self.generation_mode.currentText()))
        self._set_combo_value(self.language_mode, draft.get("language_mode", self.language_mode.currentText()))
        self._set_combo_value(self.link_mode, draft.get("link_mode", self.link_mode.currentText()))
        self._set_combo_value(self.format_mode, draft.get("format_mode", self.format_mode.currentText()))
        self.polish_enable.setChecked(bool(draft.get("polish_enable", self.polish_enable.isChecked())))
        try:
            self.polish_threshold.setValue(float(draft.get("polish_threshold", self.polish_threshold.value())))
            self.polish_green_from.setValue(float(draft.get("polish_green_from", self.polish_green_from.value())))
            self.polish_yellow_from.setValue(float(draft.get("polish_yellow_from", self.polish_yellow_from.value())))
        except Exception:
            pass
        self._set_combo_value(self.polish_mode, draft.get("polish_mode", self.polish_mode.currentText()))
        self.gooo_title.setText(str(draft.get("gooo_title", self.gooo_title.text())).strip())
        self.gooo_slug.setText(str(draft.get("gooo_slug", self.gooo_slug.text())).strip())
        self.gooo_focus_keyphrase.setText(
            str(draft.get("gooo_focus_keyphrase", self.gooo_focus_keyphrase.text())).strip()
        )
        self.gooo_meta_description.setText(
            str(draft.get("gooo_meta_description", self.gooo_meta_description.text())).strip()
        )
        self._set_combo_value(self.gooo_visibility, draft.get("gooo_visibility", self.gooo_visibility.currentText()))
        self._set_combo_value(self.gooo_target, draft.get("gooo_target", self.gooo_target.currentText()))

        template = str(draft.get("template_path", "")).strip()
        if template and Path(template).exists():
            self.load_template()
        else:
            self._restore_strategy_polishing_from_draft()
            if self.polishing_result:
                self._refresh_polishing_ui()
            if self.images_folder.text().strip() and Path(self.images_folder.text().strip()).exists():
                self.load_images_folder()
                self._restore_image_cards_from_draft()
                self._refresh_image_map_info()

        self.refresh_gooo_data()
        self._set_status_success(f"Sesja wczytana: {path}")

    def refresh_gooo_data(self) -> None:
        if not self.engine.template_raw or not self.engine.placeholders:
            self.gooo_changes.setPlainText("Najpierw wczytaj szablon.")
            return
        config = self._collect_config()
        output, replacements = self.engine.build_output(self._collect_replacements())
        text_changed = 0
        img_changed = 0
        for ph in self.engine.placeholders:
            rep = replacements.get(ph.pid, ph.original)
            if self.engine._normalize_text(rep) != self.engine._normalize_text(ph.original):
                if ph.kind == "text":
                    text_changed += 1
                else:
                    img_changed += 1
        required = len(self._image_placeholder_ids())
        assigned = len([c for c in self.image_cards.values() if c.assigned_placeholder()])
        uploaded = len(self.image_wp_map)
        self.gooo_changes.setPlainText(
            "\n".join(
                [
                    f"Placeholders text changed: {text_changed}",
                    f"Placeholders image changed: {img_changed}",
                    f"Image required/assigned/uploaded: {required}/{assigned}/{uploaded}",
                    f"Mode: {self.gooo_visibility.currentText()} | Target: {self.gooo_target.currentText()}",
                    f"Focus keyphrase words: {len(self.gooo_focus_keyphrase.text().strip().split())}",
                    f"Meta description chars: {len(self.gooo_meta_description.text().strip())}",
                ]
            )
        )
        self.gooo_output_preview.setPlainText(output[:12000] + ("\n...\n[truncated]" if len(output) > 12000 else ""))

        validation = self.engine.validate_output_text(output, replacements, self.global_prompt.toPlainText().strip(), config)
        generation = self.engine.build_generation_report(replacements, config)
        self.gooo_reports.setPlainText(
            "\n".join(
                [
                    "=== VALIDATION ===",
                    validation.get("report_text", ""),
                    "",
                    "=== GENERATION ===",
                    generation,
                ]
            )[:24000]
        )

        if not self.gooo_title.text().strip():
            m = re.search(r"<h1\b[^>]*>(.*?)</h1>", output, flags=re.IGNORECASE | re.DOTALL)
            if m:
                h1 = re.sub(r"<[^>]+>", " ", m.group(1))
                h1 = re.sub(r"\s+", " ", h1).strip()
                if h1:
                    self.gooo_title.setText(h1)

    def upload_gooo(self) -> None:
        if not self.engine.template_raw or not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        try:
            creds = self._wp_creds()
        except Exception as exc:
            QMessageBox.warning(self, "WordPress", str(exc))
            return

        target = self.gooo_target.currentText().strip().upper()
        vis = self.gooo_visibility.currentText().strip().upper()
        status = "publish" if vis == "LIVE" else "draft"
        target_type = "page" if target == "PAGE" else "portfolio"

        output, _ = self.engine.build_output(self._collect_replacements())
        title = self.gooo_title.text().strip() or "Generated AVADA Page"
        slug = self.gooo_slug.text().strip()
        focus_keyphrase = self._seo_clean_keyphrase(self.gooo_focus_keyphrase.text().strip())
        meta_desc = self._seo_clean_meta_description(self.gooo_meta_description.text().strip())
        featured_id = self._best_featured_media_id()

        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("Potwierdz UPLOAD")
        confirm.setText(
            "Czy na pewno wyslac strone do WordPress?\n\n"
            f"Title: {title}\n"
            f"Target: {target}\n"
            f"Visibility: {vis}\n"
            f"Status API: {status}\n"
            f"Slug: {slug or '(auto)'}\n"
            f"Featured media: {featured_id or '(none)'}\n"
            f"Yoast keyphrase: {focus_keyphrase or '(none)'}"
        )
        yes_btn = confirm.addButton("TAK, UPLOAD", QMessageBox.AcceptRole)
        confirm.addButton("Anuluj", QMessageBox.RejectRole)
        confirm.exec()
        if confirm.clickedButton() is not yes_btn:
            return

        def worker(token: OperationToken) -> object:
            if token.cancel_requested:
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            client = WordPressClient(creds)
            client.check_auth()
            return client.publish_content(
                title=title,
                content=output,
                status=status,
                target_type=target_type,
                slug=slug,
                featured_media_id=featured_id,
                yoast_focus_keyphrase=focus_keyphrase,
                yoast_meta_description=meta_desc,
            )

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            link = str(data.get("link", "")).strip()
            rid = data.get("id", "")
            self._set_status_success(f"GOOO upload OK (id={rid}).")
            msg = (
                f"Utworzono wpis ID={rid}\nType={data.get('type','')}\nStatus={data.get('status','')}\n"
                f"Slug={data.get('slug','')}\nFeatured media={data.get('featured_media', 0)}\n"
                f"Yoast saved={data.get('yoast_saved', False)}"
            )
            if data.get("yoast_error"):
                msg += f"\nYoast error: {data.get('yoast_error')}"
            if link:
                msg += f"\nLink: {link}"
            QMessageBox.information(self, "UPLOAD OK", msg)

        self._run_task("GOOO: Upload WordPress", worker, done)

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
        text_qc = self.engine.text_contamination_review(replacements, self.global_prompt.toPlainText().strip())
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
            (
                "Text contamination: "
                f"{text_qc.get('flagged_items', 0)}/{text_qc.get('total_items', 0)}"
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

        text_flagged_lines = []
        for item in text_qc.get("items", []):
            hits = item.get("contamination_terms", [])
            if not hits:
                continue
            pid = item.get("pid", "")
            bt = item.get("block_type", "")
            txt = str(item.get("text", "")).replace("\n", " ").strip()
            text_flagged_lines.append(f"[{pid}] {bt} | {', '.join(hits)}\n  - {txt}")
        if text_flagged_lines:
            self.checklist_review_view.appendPlainText(
                "\n\n=== TEXT CONTAMINATION (WSZYSTKIE TEKSTY) ===\n\n" + "\n\n".join(text_flagged_lines[:80])
            )

    def open_strategy_preview_tab(self) -> None:
        self.show_strategy_preview()
        idx = self.tabs.indexOf(self.tab_strategy_logs)
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    def show_last_generation_reports(self) -> None:
        if not self.last_validation_report_path or not self.last_generation_report_path:
            out = Path(self.output_path.text().strip()) if self.output_path.text().strip() else None
            if out:
                val = out.with_name(f"{out.stem}_validation_report.txt")
                gen = out.with_name(f"{out.stem}_generation_report.txt")
                if val.exists() and gen.exists():
                    self.last_validation_report_path = val
                    self.last_generation_report_path = gen
        if not self.last_validation_report_path or not self.last_generation_report_path:
            QMessageBox.information(self, "Raport", "Nie ma jeszcze raportu. Najpierw uruchom Etap 3.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Raport po wygenerowaniu")
        dlg.resize(1100, 760)
        lay = QVBoxLayout(dlg)
        splitter = QSplitter(Qt.Vertical)
        val_edit = QPlainTextEdit()
        val_edit.setReadOnly(True)
        gen_edit = QPlainTextEdit()
        gen_edit.setReadOnly(True)
        val_text = self.last_validation_report_path.read_text(encoding="utf-8", errors="replace")
        gen_text = self.last_generation_report_path.read_text(encoding="utf-8", errors="replace")
        val_edit.setPlainText(val_text)
        gen_edit.setPlainText(gen_text)
        splitter.addWidget(val_edit)
        splitter.addWidget(gen_edit)
        splitter.setSizes([360, 360])
        lay.addWidget(splitter)
        dlg.exec()

    def generate_strategy(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        def worker(token: OperationToken) -> object:
            return self.engine.generate_strategy(self._create_client(token), self.global_prompt.toPlainText().strip())
        def done(_: object) -> None:
            self.open_strategy_preview_tab()
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
            # Auto-fill SEO title/slug after content generation.
            auto_title = ""
            if self.engine.page_content and isinstance(self.engine.page_content.raw, dict):
                auto_title = str(self.engine.page_content.raw.get("page_title", "")).strip()
            if auto_title:
                self.gooo_title.setText(auto_title)
                if not self.gooo_slug.text().strip():
                    self.gooo_slug.setText(slugify_filename(auto_title))
            else:
                self.generate_gooo_meta(auto_run=True)
            self.open_strategy_preview_tab()
            self._set_status_success("Etap 2 zakonczony.")
            self._schedule_save()
            if self.polish_enable.isChecked():
                self.validate_page_polishing()
        self._run_task("Etap 2: Generowanie contentu", worker, done)

    def generate_gooo_meta(self, auto_run: bool = False) -> None:
        if not self.engine.placeholders:
            if not auto_run:
                QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        output, _ = self.engine.build_output(self._collect_replacements())
        excerpt = output[:2400]

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            return client.generate_seo_title_slug(
                global_prompt=self.global_prompt.toPlainText().strip(),
                strategy=self.engine.page_strategy,
                section_schema=self.engine.section_schema,
                sample_output_excerpt=excerpt,
            )

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            title = str(data.get("title", "")).strip()
            slug = str(data.get("slug", "")).strip()
            keyphrase = self._seo_clean_keyphrase(str(data.get("focus_keyphrase", "")).strip())
            meta_desc = self._seo_clean_meta_description(str(data.get("meta_description", "")).strip())
            if title:
                self.gooo_title.setText(title)
            if slug:
                self.gooo_slug.setText(slugify_filename(slug))
            elif title and not self.gooo_slug.text().strip():
                self.gooo_slug.setText(slugify_filename(title))
            if keyphrase:
                self.gooo_focus_keyphrase.setText(keyphrase)
            if meta_desc:
                self.gooo_meta_description.setText(meta_desc)
            self._set_status_success("GOOO: SEO title/slug wygenerowane.")
            self._schedule_save()

        self._run_task("GOOO: Generate SEO title/slug", worker, done)

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

    @staticmethod
    def _safe_float(value: str, default: float = 8.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _quantize_score(score: float) -> float:
        return max(1.0, min(10.0, round(score * 4.0) / 4.0))

    @staticmethod
    def _seo_clean_meta_description(value: str) -> str:
        txt = re.sub(r"\s+", " ", value or "").strip()
        if len(txt) > 160:
            txt = txt[:157].rstrip(" ,.;:") + "..."
        return txt

    @staticmethod
    def _seo_clean_keyphrase(value: str) -> str:
        txt = re.sub(r"\s+", " ", value or "").strip()
        words = txt.split()
        if len(words) > 6:
            txt = " ".join(words[:6])
        return txt

    def _score_style(self, score: float, threshold: float) -> str:
        if score >= threshold:
            return "QLabel { background:#dcfce7; color:#166534; border-radius:5px; padding:4px; font-weight:700; }"
        if score >= max(1.0, threshold - 1.0):
            return "QLabel { background:#fef3c7; color:#92400e; border-radius:5px; padding:4px; font-weight:700; }"
        return "QLabel { background:#fee2e2; color:#991b1b; border-radius:5px; padding:4px; font-weight:700; }"

    def _ensure_polish_baseline(self) -> None:
        if not self.polish_baseline_replacements:
            self.polish_baseline_replacements = self._collect_replacements().copy()

    def _refresh_polishing_ui(self) -> None:
        threshold = float(self.polish_threshold.value())
        green_from = float(self.polish_green_from.value())
        yellow_from = float(self.polish_yellow_from.value())
        if yellow_from > green_from:
            yellow_from = green_from
        result = self.polishing_result or {}
        page_scores = result.get("page_scores", {}) if isinstance(result, dict) else {}
        for k, lbl in self.polish_page_scores.items():
            val = float(page_scores.get(k, 0.0)) if isinstance(page_scores, dict) else 0.0
            val = self._quantize_score(val) if val > 0 else 0.0
            lbl.setText(f"{val:.2f}" if val > 0 else "-")
            if val > 0:
                lbl.setStyleSheet(self._score_style(val, threshold))

        issues = result.get("issues", []) if isinstance(result, dict) else []
        lines = []
        self.polish_issue_selector.clear()
        self.polish_issue_list.blockSignals(True)
        self.polish_issue_list.clear()
        if isinstance(issues, list):
            for it in issues:
                if not isinstance(it, dict):
                    continue
                iid = str(it.get("id", "")).strip()
                sev = str(it.get("severity", "low"))
                scope = str(it.get("scope", "page"))
                cat = str(it.get("category", ""))
                msg = str(it.get("message", ""))
                uid = str(it.get("unit_id", ""))
                lines.append(f"[{iid or '-'}] {sev.upper()} {scope}/{cat} unit={uid} -> {msg}")
                if iid:
                    self.polish_issue_selector.addItem(iid)
                row = QListWidgetItem(f"{iid or '-'} | {sev.upper()} | {cat} | {msg[:90]}")
                row.setData(Qt.UserRole, it)
                if sev.lower() == "high":
                    row.setBackground(QColor("#fee2e2"))
                    row.setForeground(QColor("#991b1b"))
                elif sev.lower() == "medium":
                    row.setBackground(QColor("#fef3c7"))
                    row.setForeground(QColor("#92400e"))
                else:
                    row.setBackground(QColor("#ecfeff"))
                    row.setForeground(QColor("#155e75"))
                self.polish_issue_list.addItem(row)
        self.polish_issue_list.blockSignals(False)
        self.polish_issues_view.setPlainText("\n".join(lines) if lines else "Brak issue.")
        self.polish_issue_detail.setPlainText("")

        units = result.get("units", []) if isinstance(result, dict) else []
        current_unit = self._current_polish_unit_id()
        self.polish_unit_list.blockSignals(True)
        self.polish_unit_list.clear()
        self.polishing_units_by_id.clear()
        if isinstance(units, list):
            for u in units:
                if not isinstance(u, dict):
                    continue
                uid = str(u.get("unit_id", "")).strip()
                if not uid:
                    continue
                self.polishing_units_by_id[uid] = u
                score = float(u.get("scores", {}).get("overall", 0.0)) if isinstance(u.get("scores"), dict) else 0.0
                score = self._quantize_score(score)
                item = QListWidgetItem(f"{uid} ({score:.2f})")
                item.setData(Qt.UserRole, uid)
                if score >= green_from:
                    item.setBackground(QColor("#dcfce7"))
                    item.setForeground(QColor("#166534"))
                elif score >= yellow_from:
                    item.setBackground(QColor("#fef3c7"))
                    item.setForeground(QColor("#92400e"))
                else:
                    item.setBackground(QColor("#fee2e2"))
                    item.setForeground(QColor("#991b1b"))
                self.polish_unit_list.addItem(item)
        self.polish_unit_list.blockSignals(False)
        target_row = 0
        if current_unit:
            for i in range(self.polish_unit_list.count()):
                it = self.polish_unit_list.item(i)
                if str(it.data(Qt.UserRole)) == current_unit:
                    target_row = i
                    break
        if self.polish_unit_list.count() > 0:
            self.polish_unit_list.setCurrentRow(target_row)
        else:
            self._on_polish_unit_selected("")

    def _current_polish_unit_id(self) -> str:
        item = self.polish_unit_list.currentItem() if hasattr(self, "polish_unit_list") else None
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "").strip()

    def _suggest_units_for_issue(self, issue: Dict[str, object]) -> list[str]:
        uid = str(issue.get("unit_id", "")).strip()
        if uid and uid in self.polishing_units_by_id:
            return [uid]

        category = str(issue.get("category", "")).strip()
        msg = str(issue.get("message", "")).lower()
        msg_tokens = [t for t in re.findall(r"[a-z0-9-]{4,}", msg) if t not in {"page", "section", "local", "seo"}]
        ranked: list[tuple[float, str]] = []
        for unit_id, unit in self.polishing_units_by_id.items():
            scores = unit.get("scores", {})
            cat_score = 10.0
            if isinstance(scores, dict):
                if category and category in scores:
                    try:
                        cat_score = float(scores.get(category, 10.0))
                    except Exception:
                        cat_score = 10.0
                else:
                    try:
                        cat_score = float(scores.get("overall", 10.0))
                    except Exception:
                        cat_score = 10.0
            text = str(unit.get("original_text", "")).lower()
            overlap = 0
            for tok in msg_tokens:
                if tok in text:
                    overlap += 1
            priority = (10.0 - cat_score) + overlap * 1.5
            if priority > 0:
                ranked.append((priority, unit_id))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [uid for _, uid in ranked[:3]]

    def _on_polish_issue_selection_changed(self) -> None:
        item = self.polish_issue_list.currentItem()
        if item is None:
            self.polish_issue_detail.setPlainText("")
            return
        issue = item.data(Qt.UserRole)
        if not isinstance(issue, dict):
            self.polish_issue_detail.setPlainText("")
            return
        iid = str(issue.get("id", "")).strip()
        if iid:
            idx = self.polish_issue_selector.findText(iid)
            if idx >= 0:
                self.polish_issue_selector.setCurrentIndex(idx)
        candidates = self._suggest_units_for_issue(issue)
        msg = str(issue.get("message", ""))
        cat = str(issue.get("category", ""))
        sev = str(issue.get("severity", "low")).upper()
        scope = str(issue.get("scope", "page"))
        self.polish_issue_detail.setPlainText(
            f"[{iid or '-'}] {sev} {scope}/{cat}\n\n{msg}\n\nSugerowane unity: {', '.join(candidates) if candidates else '(brak)'}"
        )
        if candidates:
            target = candidates[0]
            for i in range(self.polish_unit_list.count()):
                it = self.polish_unit_list.item(i)
                if str(it.data(Qt.UserRole)) == target:
                    self.polish_unit_list.setCurrentRow(i)
                    break

    def _on_polish_unit_selection_changed(self) -> None:
        self._on_polish_unit_selected(self._current_polish_unit_id())

    def _render_polish_diff(self, original_text: str, polished_text: str) -> tuple[str, str]:
        def parts(text: str) -> list[str]:
            return re.findall(r"\s+|[^\s]+", text)

        a = parts(original_text)
        b = parts(polished_text)
        m = SequenceMatcher(None, a, b)
        orig_html: list[str] = []
        pol_html: list[str] = []
        for op, a0, a1, b0, b1 in m.get_opcodes():
            if op == "equal":
                seg_a = "".join(a[a0:a1])
                seg_b = "".join(b[b0:b1])
                esc_a = escape(seg_a).replace("\n", "<br>")
                esc_b = escape(seg_b).replace("\n", "<br>")
                orig_html.append(esc_a)
                pol_html.append(esc_b)
            elif op == "replace":
                seg_a = escape("".join(a[a0:a1])).replace("\n", "<br>")
                seg_b = escape("".join(b[b0:b1])).replace("\n", "<br>")
                orig_html.append(f'<span style="background:#fee2e2;">{seg_a}</span>')
                pol_html.append(f'<span style="background:#dcfce7;">{seg_b}</span>')
            elif op == "delete":
                seg_a = escape("".join(a[a0:a1])).replace("\n", "<br>")
                orig_html.append(f'<span style="background:#fee2e2;">{seg_a}</span>')
            elif op == "insert":
                seg_b = escape("".join(b[b0:b1])).replace("\n", "<br>")
                pol_html.append(f'<span style="background:#dcfce7;">{seg_b}</span>')
        orig = "".join(orig_html) or escape(original_text).replace("\n", "<br>")
        pol = "".join(pol_html) or escape(polished_text).replace("\n", "<br>")
        return (
            f"<div style='white-space:pre-wrap; font-family:Segoe UI; font-size:12px;'>{orig}</div>",
            f"<div style='white-space:pre-wrap; font-family:Segoe UI; font-size:12px;'>{pol}</div>",
        )

    def _on_polish_unit_selected(self, unit_id: str) -> None:
        unit = self.polishing_units_by_id.get(unit_id)
        if not unit:
            self.polish_unit_meta.setText("Type: - | Score: -")
            self.polish_unit_original.setHtml("")
            self.polish_unit_polished.setHtml("")
            self.polish_unit_issues.setPlainText("")
            return
        scores = unit.get("scores", {})
        overall = float(scores.get("overall", 0.0)) if isinstance(scores, dict) else 0.0
        self.polish_unit_meta.setText(
            f"Type: {unit.get('unit_type','')} | Score overall: {overall:.1f} | placeholders={len(unit.get('placeholder_ids', []))}"
        )
        orig = str(unit.get("original_text", ""))
        pol = str(unit.get("polished_text", unit.get("original_text", "")))
        html_orig, html_pol = self._render_polish_diff(orig, pol)
        self.polish_unit_original.setHtml(html_orig)
        green_from = float(self.polish_green_from.value())
        if self._quantize_score(overall) >= green_from and (orig.strip() == pol.strip()):
            pol_ok = (
                "<div style='white-space:pre-wrap; font-family:Segoe UI; font-size:12px;'>"
                "<div style='background:#ecfdf5; color:#166534; padding:6px; border-radius:6px; margin-bottom:6px;'>"
                "ALL GOOD - ten unit miesci sie w Twoim zakresie i nie wymaga zmiany."
                "</div>"
                f"{escape(pol).replace(chr(10), '<br>')}"
                "</div>"
            )
            self.polish_unit_polished.setHtml(pol_ok)
        else:
            self.polish_unit_polished.setHtml(html_pol)
        u_issues = unit.get("issues", [])
        txt = []
        if isinstance(u_issues, list):
            for it in u_issues:
                if isinstance(it, dict):
                    txt.append(
                        f"[{it.get('id','-')}] {str(it.get('severity','low')).upper()} {it.get('category','')} -> {it.get('message','')}"
                    )
        self.polish_unit_issues.setPlainText("\n".join(txt) if txt else "Brak issue dla unit.")

    def _replace_unit_in_result(self, unit_id: str, new_unit: Dict[str, object]) -> None:
        if not self.polishing_result or not isinstance(self.polishing_result.get("units"), list):
            return
        units = self.polishing_result["units"]
        for idx, u in enumerate(units):
            if isinstance(u, dict) and str(u.get("unit_id", "")) == unit_id:
                units[idx] = new_unit
                break

    def _apply_replacement_patch_to_cards(self, patch: Dict[str, str]) -> None:
        for pid, value in patch.items():
            if pid not in self.cards:
                continue
            card = self.cards[pid]
            if card.placeholder.kind == "text":
                card.set_result(value)
            else:
                card.image_path.setText(value)
        self._schedule_save()
        self.show_strategy_preview()

    def validate_page_polishing(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        self._ensure_polish_baseline()
        replacements = self._collect_replacements()
        mode = self.polish_mode.currentText().strip() or "BALANCED"

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            return self.engine.run_polishing_validation(
                client=client,
                global_prompt=self.global_prompt.toPlainText().strip(),
                replacements=replacements,
                mode=mode,
            )

        def done(result: object) -> None:
            self.polishing_result = result if isinstance(result, dict) else None
            self._refresh_polishing_ui()
            self._set_status_success("POLISHING: walidacja strony gotowa.")
            idx = self.tabs.indexOf(self.tab_polishing)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
            # Przy samym raporcie od razu generujemy propozycje zmian dla unitow < 8.0 (bez automatycznego apply).
            self._auto_fix_units_below_threshold(threshold=8.0)

        self._run_task("POLISHING: Validate page", worker, done)

    def validate_page_polishing_lite(self) -> None:
        if not self.engine.placeholders:
            QMessageBox.warning(self, "Brak", "Najpierw wczytaj szablon.")
            return
        self._ensure_polish_baseline()
        replacements = self._collect_replacements()
        mode = self.polish_mode.currentText().strip() or "BALANCED"
        threshold = float(self.polish_threshold.value())

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            return self.engine.run_polishing_validation_lite(
                client=client,
                global_prompt=self.global_prompt.toPlainText().strip(),
                replacements=replacements,
                mode=mode,
                threshold=threshold,
                max_api_units=8,
            )

        def done(result: object) -> None:
            self.polishing_result = result if isinstance(result, dict) else None
            self._refresh_polishing_ui()
            meta = self.polishing_result.get("meta", {}) if isinstance(self.polishing_result, dict) else {}
            total_units = int(meta.get("total_units", 0)) if isinstance(meta, dict) else 0
            api_units = int(meta.get("api_units", 0)) if isinstance(meta, dict) else 0
            self._set_status_success(
                f"POLISHING LITE: walidacja gotowa (API units: {api_units}/{total_units})."
            )
            idx = self.tabs.indexOf(self.tab_polishing)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)

        self._run_task("POLISHING: Validate page (lite)", worker, done)

    def polish_page(self) -> None:
        if not self.polish_enable.isChecked():
            QMessageBox.information(self, "POLISHING", "Enable polishing jest wylaczone.")
            return
        if not self.polishing_result:
            self.validate_page_polishing()
            return
        self._auto_fix_units_below_threshold(threshold=10.1)

    def _auto_fix_units_below_threshold(self, threshold: float, only_unit_id: str = "", issue_category: str = "", issue_msg: str = "") -> None:
        if not self.polishing_result or not isinstance(self.polishing_result.get("units"), list):
            QMessageBox.information(self, "POLISHING", "Najpierw uruchom VALIDATE PAGE.")
            return
        mode = self.polish_mode.currentText().strip() or "BALANCED"
        replacements = self._collect_replacements()
        units = self.polishing_result.get("units", [])
        targets: List[Dict[str, object]] = []
        for u in units:
            if not isinstance(u, dict):
                continue
            uid = str(u.get("unit_id", ""))
            if only_unit_id and uid != only_unit_id:
                continue
            scores = u.get("scores", {})
            overall = float(scores.get("overall", 0.0)) if isinstance(scores, dict) else 0.0
            if only_unit_id or overall < threshold:
                targets.append(u)
        if not targets:
            self._set_status_success("POLISHING: brak unitow ponizej progu.")
            return

        def worker(token: OperationToken) -> object:
            client = self._create_client(token)
            fixed = []
            for u in targets:
                if token.cancel_requested:
                    raise RuntimeError("Operacja przerwana przez uzytkownika.")
                fixed_u = self.engine.run_polishing_fix_unit(
                    client=client,
                    global_prompt=self.global_prompt.toPlainText().strip(),
                    unit=u,
                    mode=mode,
                    issue_category=issue_category,
                    issue_message=issue_msg,
                )
                fixed.append(fixed_u)
            return fixed

        def done(result: object) -> None:
            fixed_list = result if isinstance(result, list) else []
            for fu in fixed_list:
                if isinstance(fu, dict):
                    uid = str(fu.get("unit_id", ""))
                    self._replace_unit_in_result(uid, fu)
            self._refresh_polishing_ui()
            self._set_status_success(f"POLISHING: poprawiono {len(fixed_list)} unitow.")

        self._run_task("POLISHING: Fix units", worker, done)

    def auto_fix_below_threshold(self) -> None:
        if not self.polish_enable.isChecked():
            QMessageBox.information(self, "POLISHING", "Enable polishing jest wylaczone.")
            return
        threshold = float(self.polish_threshold.value())
        self._auto_fix_units_below_threshold(threshold=threshold)

    def validate_selected_unit(self) -> None:
        uid = self._current_polish_unit_id()
        if not uid:
            return
        self._auto_fix_units_below_threshold(threshold=10.1, only_unit_id=uid)

    def fix_selected_unit(self) -> None:
        uid = self._current_polish_unit_id()
        if not uid:
            return
        threshold = float(self.polish_threshold.value())
        self._auto_fix_units_below_threshold(threshold=threshold, only_unit_id=uid)

    def fix_selected_issue(self) -> None:
        issue = None
        item = self.polish_issue_list.currentItem()
        if item is not None and isinstance(item.data(Qt.UserRole), dict):
            issue = item.data(Qt.UserRole)
        if issue is None:
            iid = self.polish_issue_selector.currentText().strip()
            if not iid or not self.polishing_result:
                return
            issues = self.polishing_result.get("issues", [])
            if isinstance(issues, list):
                for it in issues:
                    if isinstance(it, dict) and str(it.get("id", "")) == iid:
                        issue = it
                        break
        if not issue:
            return
        uid = str(issue.get("unit_id", "")).strip()
        if not uid:
            candidates = self._suggest_units_for_issue(issue)
            if not candidates:
                QMessageBox.information(self, "POLISHING", "Nie udalo sie wskazac unitu dla tego page-level issue.")
                return
            uid = candidates[0]
        self._auto_fix_units_below_threshold(
            threshold=10.1,
            only_unit_id=uid,
            issue_category=str(issue.get("category", "")),
            issue_msg=str(issue.get("message", "")),
        )

    def apply_selected_unit_fix(self) -> None:
        uid = self._current_polish_unit_id()
        unit = self.polishing_units_by_id.get(uid)
        if not unit:
            return
        current = self._collect_replacements()
        patch = self.engine.polished_unit_to_replacements(unit, self.polish_unit_polished.toPlainText(), current)
        self._apply_replacement_patch_to_cards(patch)
        self.polishing_applied_unit_ids.add(uid)
        self._set_status_success(f"POLISHING: applied fix dla {uid}.")

    def revert_selected_unit_fix(self) -> None:
        uid = self._current_polish_unit_id()
        if not uid or uid not in self.polishing_units_by_id:
            return
        unit = self.polishing_units_by_id[uid]
        if uid not in self.polishing_applied_unit_ids:
            self._on_polish_unit_selected(uid)
            return
        baseline = self.polish_baseline_replacements
        patch = {}
        for pid in unit.get("placeholder_ids", []):
            pid_s = str(pid)
            if pid_s in baseline:
                patch[pid_s] = baseline[pid_s]
        self._apply_replacement_patch_to_cards(patch)
        self.polishing_applied_unit_ids.discard(uid)
        self._on_polish_unit_selected(uid)
        self._set_status_success(f"POLISHING: revert dla {uid}.")

    def apply_all_polished_changes(self) -> None:
        if not self.polishing_result:
            QMessageBox.information(self, "POLISHING", "Brak wyniku POLISHING.")
            return
        current = self._collect_replacements()
        merged_patch: Dict[str, str] = {}
        units = self.polishing_result.get("units", [])
        if isinstance(units, list):
            for u in units:
                if not isinstance(u, dict):
                    continue
                uid = str(u.get("unit_id", ""))
                patch = self.engine.polished_unit_to_replacements(u, str(u.get("polished_text", "")), current)
                merged_patch.update(patch)
                self.polishing_applied_unit_ids.add(uid)
        self._apply_replacement_patch_to_cards(merged_patch)
        self._set_status_success("POLISHING: applied all polished changes.")

    def revert_all_polished_changes(self) -> None:
        if not self.polish_baseline_replacements:
            QMessageBox.information(self, "POLISHING", "Brak baseline do revert.")
            return
        self._apply_replacement_patch_to_cards(self.polish_baseline_replacements.copy())
        self.polishing_applied_unit_ids.clear()
        self._set_status_success("POLISHING: reverted all changes.")

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
        self.last_validation_report_path = report_path
        self.last_generation_report_path = generation_path
        self._set_status_success("Etap 3 zakonczony. Output zapisany.")
        self.open_strategy_preview_tab()
        self.refresh_gooo_data()
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
