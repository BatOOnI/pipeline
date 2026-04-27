import os
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import config
from agent_loop import clear_runtime_session, run as pipeline_run
from git_tools import (
    git_add_all,
    git_branch_main,
    git_commit,
    git_init,
    git_push,
    git_set_identity,
    git_set_remote,
    git_tag,
)
from providers import list_models
from utils import approx_token_count, coerce_int, ensure_gitignore, open_path, read_json_file, write_json_file


class App:
    LOW_VALUE_PREFIXES = (
        "TASK PROFILE:",
        "MODEL ROUTE:",
        "LOCAL MODEL:",
        "CREATE STRATEGY:",
        "CREATE PHASE:",
        "TRANSFORM PHASE:",
        "MODE CONTROL:",
        "RESCUE MODE:",
        "MODE REASON:",
        "TASK SHAPE:",
        "TASK SHAPE REASON:",
        "PATCH_FILES REPR:",
        "ACTIVE PROJECT ROOT:",
        "PERMISSION MODE:",
        "PATCH HEURISTICS:",
        "RESCUE SUPPRESSED:",
        "LOCAL FALLBACK STEP:",
        "SOURCE FILE POLICY:",
        "DERIVED FILES ALLOWED:",
        "LARGE FILE MODE:",
        "GIT CHECKPOINT OK",
    )

    LOW_VALUE_EXACT = {
        "All touched Python files compiled cleanly",
        "VERIFY SKIP",
        "No Python files changed",
        "GIT CHECKPOINT OK",
    }

    IMPORTANT_MARKERS = (
        "ITER ",
        "PERMISSION DENIED",
        "VERIFY OK",
        "VERIFY FAIL",
        "TRANSFORM VERIFY OK",
        "TRANSFORM VERIFY FAIL",
        "RUN SUMMARY:",
        "FAILURE SUMMARY:",
        "STOP REASON",
        "-> STOP",
        "STOP:",
        "PIPELINE FINISHED",
    )

    ERROR_MARKERS = (
        "ERROR",
        "ERR",
        "FAILED",
        "FAIL",
        "DENIED",
        "BLOCKED",
        "PARSE_ERROR",
        "TRACEBACK",
    )

    SUCCESS_MARKERS = (
        "VERIFY OK",
        "TRANSFORM VERIFY OK",
        "SUCCESSFUL RUN -> STOP",
        "CREATE OUTPUTS VERIFIED -> STOP",
        "TRANSFORM OUTPUTS VERIFIED -> STOP",
        "TRANSFORM VERIFIED NO-OP -> STOP",
        "MODEL INDICATED COMPLETION -> STOP",
        "status=success",
        "outputs=ok",
        "DONE",
    )

    INFO_MARKERS = (
        "Wrote ",
        "SKIP SAME FILE",
        "NO REAL PROGRESS",
        "PROGRESS APPLIED",
        "MATERIAL PROGRESS",
        "ROUTE",
        "Fetched ",
    )

    def __init__(self, root):
        self.root = root
        self.root.title("Pipeline GUI v2")
        self.root.geometry("1100x820")

        self.worker = None
        self.stop_flag = False
        self.timeout_dialog = None
        self.timeout_dialog_event = None
        self.iteration_limit_dialog = None
        self.iteration_limit_dialog_event = None
        self.settings_path = os.path.abspath(os.path.join(os.getcwd(), ".agent", "gui_settings.json"))
        self._saved_prompt_text = ""
        self._saved_session_base_prompt = ""
        self._session_base_prompt = ""
        self._advanced_visible = False
        self._meta_last_values = {}
        self._active_collapse_lines = []
        self._log_records = []
        self._iter_record_seq = 1

        self._build_vars()
        self._load_gui_settings()
        self._build_ui()
        self._apply_loaded_prompt()
        self._bind_events()
        self._refresh_prompt_metrics()
        self._update_session_anchor()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_vars(self):
        self.provider_var = tk.StringVar(value=config.PROVIDER)
        self.mode_control_var = tk.StringVar(value=str(getattr(config, "MODE_CONTROL", "AUTO") or "AUTO").upper())
        self.rescue_mode_var = tk.StringVar(value=str(getattr(config, "RESCUE_MODE", "OFF") or "OFF").upper())
        self.lm_url_var = tk.StringVar(value=config.LMSTUDIO_URL)
        self.lm_model_var = tk.StringVar(value=config.LMSTUDIO_MODEL)
        self.lm_key_var = tk.StringVar(value=str(getattr(config, "LMSTUDIO_API_KEY", "") or ""))
        self.oa_model_var = tk.StringVar(value=config.OPENAI_MODEL)
        self.oa_key_var = tk.StringVar(value=config.OPENAI_API_KEY)
        self.project_root_var = tk.StringVar(value=config.PROJECT_ROOT)
        self.max_iter_var = tk.StringVar(value=str(config.MAX_ITERATIONS))
        self.run_timeout_var = tk.StringVar(value=str(config.RUN_TIMEOUT))
        self.model_timeout_var = tk.StringVar(value=str(config.MODEL_TIMEOUT))
        self.auto_run_var = tk.BooleanVar(value=config.AUTO_RUN_COMMANDS)
        self.collapse_logs_var = tk.BooleanVar(value=True)
        self.run_mode_var = tk.StringVar(value="Start")
        self.session_anchor_var = tk.StringVar(value="Session anchor: (none)")

        self.prompt_chars_var = tk.StringVar(value="0")
        self.estimated_tokens_var = tk.StringVar(value="0")
        self.prompt_char_limit_var = tk.StringVar(value=str(config.PROMPT_CHAR_LIMIT))
        self.patch_files_var = tk.StringVar(value=config.PATCH_FILES)
        self.patch_snippet_lines_var = tk.StringVar(value=str(config.PATCH_SNIPPET_LINES))
        self.max_output_tokens_var = tk.StringVar(value=str(config.MAX_OUTPUT_TOKENS))

        self.repo_dir_var = tk.StringVar(value=os.getcwd())
        self.remote_url_var = tk.StringVar(value=config.DEFAULT_REMOTE_URL)
        self.commit_msg_var = tk.StringVar(value=config.DEFAULT_COMMIT_MESSAGE)
        self.tag_var = tk.StringVar(value=config.DEFAULT_TAG)
        self.git_name_var = tk.StringVar(value="")
        self.git_email_var = tk.StringVar(value="")

    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}
        main = ttk.Frame(self.root, padding=(10, 10, 10, 10))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        topbar = ttk.Frame(main)
        topbar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        topbar.columnconfigure(0, weight=1)
        topbar.columnconfigure(1, weight=0)

        title_row = ttk.Frame(topbar)
        title_row.grid(row=0, column=0, sticky="w")
        ttk.Label(title_row, text="Pipeline Console", font=tkfont.nametofont("TkDefaultFont")).pack(side="left")
        ttk.Label(title_row, text="Status:").pack(side="left", padx=(14, 4))
        self.status_label = ttk.Label(title_row, text="Idle")
        self.status_label.pack(side="left")

        top_actions = ttk.Frame(topbar)
        top_actions.grid(row=0, column=1, sticky="e")
        ttk.Button(top_actions, text="Open Log", command=self.open_log).pack(side="left", padx=(0, 6))
        ttk.Button(top_actions, text="Open Raw Log", command=self.open_raw_log).pack(side="left", padx=(0, 6))
        ttk.Button(top_actions, text="Open Project", command=self.open_project_folder).pack(side="left", padx=(0, 6))
        self.advanced_toggle_btn = ttk.Button(top_actions, text="Show Advanced", command=self._toggle_advanced_panel)
        self.advanced_toggle_btn.pack(side="left")

        log_box = ttk.LabelFrame(main, text="Runtime Log / Answers", padding=(8, 8, 8, 8))
        log_box.grid(row=1, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(1, weight=1)

        log_toolbar = ttk.Frame(log_box)
        log_toolbar.grid(row=0, column=0, sticky="we", pady=(0, 4))
        ttk.Checkbutton(log_toolbar, text="Collapse Repeats", variable=self.collapse_logs_var, command=self._on_collapse_toggle).pack(side="left")
        ttk.Label(log_toolbar, text="Legend: green=success, yellow=progress, red=errors, blue=sections").pack(side="left", padx=(12, 0))
        ttk.Button(log_toolbar, text="Clear View", command=self._clear_log_view).pack(side="right")

        log_body = ttk.Frame(log_box)
        log_body.grid(row=1, column=0, sticky="nsew")
        log_body.columnconfigure(0, weight=1)
        log_body.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_body,
            wrap="none",
            font=tkfont.nametofont("TkFixedFont"),
            spacing1=1,
            spacing2=1,
            spacing3=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(log_body, orient="horizontal", command=self.log_text.xview)
        x_scroll.grid(row=1, column=0, sticky="we")
        self.log_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self._init_log_tags()

        composer_box = ttk.LabelFrame(main, text="Prompt Composer", padding=(8, 8, 8, 8))
        composer_box.grid(row=2, column=0, sticky="we", pady=(8, 0))
        composer_box.columnconfigure(0, weight=1)
        composer_box.rowconfigure(0, weight=1)

        self.prompt_text = tk.Text(composer_box, height=5, wrap="word")
        self.prompt_text.grid(row=0, column=0, sticky="we")

        metrics = ttk.Frame(composer_box)
        metrics.grid(row=1, column=0, sticky="we", pady=(8, 0))
        for col in range(8):
            metrics.columnconfigure(col, weight=0)
        metrics.columnconfigure(7, weight=1)
        ttk.Label(metrics, text="Prompt chars").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.prompt_chars_var, width=8, state="readonly").grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Label(metrics, text="Estimated tokens").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.estimated_tokens_var, width=8, state="readonly").grid(row=0, column=3, sticky="w", padx=(0, 10))
        ttk.Label(metrics, text="Prompt limit").grid(row=0, column=4, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.prompt_char_limit_var, width=8).grid(row=0, column=5, sticky="w", padx=(0, 10))
        ttk.Label(metrics, text="Max output tokens").grid(row=0, column=6, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.max_output_tokens_var, width=8).grid(row=0, column=7, sticky="w")

        run_row = ttk.Frame(composer_box)
        run_row.grid(row=2, column=0, sticky="we", pady=(8, 0))
        run_row.columnconfigure(7, weight=1)
        ttk.Label(run_row, text="Run mode").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            run_row,
            textvariable=self.run_mode_var,
            values=["Start", "Start Fresh", "Continue"],
            width=12,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", padx=(6, 10))
        ttk.Button(run_row, text="Run", command=self.run_selected_mode).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Stop", command=self.stop_pipeline).grid(row=0, column=3, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Clear Session", command=lambda: self.clear_session(stop_running=True)).grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Fetch Models", command=self.fetch_models).grid(row=0, column=5, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Save API Keys", command=self.save_api_key).grid(row=0, column=6, sticky="w")

        anchor_row = ttk.Frame(composer_box)
        anchor_row.grid(row=3, column=0, sticky="we", pady=(6, 0))
        ttk.Label(anchor_row, textvariable=self.session_anchor_var, foreground="#2f4f6f").pack(side="left")

        self.advanced_panel = ttk.LabelFrame(main, text="Advanced Settings", padding=(8, 8, 8, 8))
        self.advanced_panel.grid(row=3, column=0, sticky="we", pady=(8, 0))
        self.advanced_panel.columnconfigure(0, weight=1)

        tabs = ttk.Notebook(self.advanced_panel)
        tabs.grid(row=0, column=0, sticky="we")

        runtime_tab = ttk.Frame(tabs)
        runtime_tab.columnconfigure(1, weight=1)
        runtime_tab.columnconfigure(3, weight=1)
        tabs.add(runtime_tab, text="Runtime")

        ttk.Label(runtime_tab, text="Provider").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(
            runtime_tab,
            textvariable=self.provider_var,
            values=["lmstudio", "openai"],
            width=14,
            state="readonly",
        ).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(runtime_tab, text="Mode control").grid(row=0, column=2, sticky="w", **pad)
        ttk.Combobox(
            runtime_tab,
            textvariable=self.mode_control_var,
            values=["AUTO", "FORCE_CREATE", "FORCE_PATCH"],
            width=16,
            state="readonly",
        ).grid(row=0, column=3, sticky="we", **pad)
        ttk.Label(runtime_tab, text="Max iterations").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(runtime_tab, textvariable=self.max_iter_var, width=12).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(runtime_tab, text="Run timeout (s)").grid(row=1, column=2, sticky="w", **pad)
        ttk.Entry(runtime_tab, textvariable=self.run_timeout_var, width=12).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(runtime_tab, text="Model timeout (s)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(runtime_tab, textvariable=self.model_timeout_var, width=12).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(runtime_tab, text="Rescue mode").grid(row=2, column=2, sticky="w", **pad)
        ttk.Combobox(
            runtime_tab,
            textvariable=self.rescue_mode_var,
            values=["OFF", "ON", "ASK_BEFORE_RESCUE"],
            width=20,
            state="readonly",
        ).grid(row=2, column=3, sticky="w", **pad)
        ttk.Checkbutton(runtime_tab, text="Auto run commands", variable=self.auto_run_var).grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        provider_tab = ttk.Frame(tabs)
        provider_tab.columnconfigure(1, weight=1)
        tabs.add(provider_tab, text="Providers")

        ttk.Label(provider_tab, text="LM URL").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(provider_tab, textvariable=self.lm_url_var).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="LM Model").grid(row=1, column=0, sticky="w", **pad)
        self.lm_model_combo = ttk.Combobox(provider_tab, textvariable=self.lm_model_var)
        self.lm_model_combo.grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="LM API Key").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(provider_tab, textvariable=self.lm_key_var, show="*").grid(row=2, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="OpenAI Model").grid(row=3, column=0, sticky="w", **pad)
        self.oa_model_combo = ttk.Combobox(provider_tab, textvariable=self.oa_model_var)
        self.oa_model_combo.grid(row=3, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="OpenAI API Key").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(provider_tab, textvariable=self.oa_key_var, show="*").grid(row=4, column=1, sticky="we", **pad)

        project_tab = ttk.Frame(tabs)
        project_tab.columnconfigure(1, weight=1)
        tabs.add(project_tab, text="Project / Patch")

        ttk.Label(project_tab, text="Project root").grid(row=0, column=0, sticky="w", **pad)
        project_root_row = ttk.Frame(project_tab)
        project_root_row.grid(row=0, column=1, sticky="we", **pad)
        project_root_row.columnconfigure(0, weight=1)
        ttk.Entry(project_root_row, textvariable=self.project_root_var).grid(row=0, column=0, sticky="we")
        ttk.Button(project_root_row, text="Browse", command=self.browse_project_root).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(project_tab, text="Patch files").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(project_tab, textvariable=self.patch_files_var).grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(project_tab, text="Patch snippet lines").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(project_tab, textvariable=self.patch_snippet_lines_var, width=12).grid(row=2, column=1, sticky="w", **pad)

        git_tab = ttk.Frame(tabs)
        git_tab.columnconfigure(1, weight=1)
        git_tab.columnconfigure(3, weight=1)
        tabs.add(git_tab, text="Git")

        ttk.Label(git_tab, text="Repo dir").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.repo_dir_var).grid(row=0, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(git_tab, text="Browse", command=self.browse_repo_dir).grid(row=0, column=3, sticky="e", **pad)
        ttk.Label(git_tab, text="Remote URL").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.remote_url_var).grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Label(git_tab, text="Git name").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.git_name_var).grid(row=2, column=1, sticky="we", **pad)
        ttk.Label(git_tab, text="Git email").grid(row=2, column=2, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.git_email_var).grid(row=2, column=3, sticky="we", **pad)
        ttk.Label(git_tab, text="Commit msg").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.commit_msg_var).grid(row=3, column=1, sticky="we", **pad)
        ttk.Label(git_tab, text="Tag").grid(row=3, column=2, sticky="w", **pad)
        ttk.Entry(git_tab, textvariable=self.tag_var).grid(row=3, column=3, sticky="we", **pad)
        git_btns = ttk.Frame(git_tab)
        git_btns.grid(row=4, column=0, columnspan=4, sticky="we", **pad)
        for col in range(4):
            git_btns.columnconfigure(col, weight=1)
        ttk.Button(git_btns, text="Init Git", command=self.git_init_repo).grid(row=0, column=0, sticky="we", padx=3, pady=2)
        ttk.Button(git_btns, text="Commit", command=self.git_commit_repo).grid(row=0, column=1, sticky="we", padx=3, pady=2)
        ttk.Button(git_btns, text="Push", command=self.git_push_repo).grid(row=0, column=2, sticky="we", padx=3, pady=2)
        ttk.Button(git_btns, text="Tag + Push", command=self.git_tag_repo).grid(row=0, column=3, sticky="we", padx=3, pady=2)

        self.advanced_panel.grid_remove()

    def _bind_events(self):
        self.prompt_text.bind("<<Modified>>", self._on_prompt_modified)
        self.prompt_text.bind("<Control-Return>", lambda _event: self.run_selected_mode())
        self.max_iter_var.trace_add("write", self._sync_live_runtime_settings)
        self.rescue_mode_var.trace_add("write", self._sync_live_runtime_settings)

    def _toggle_advanced_panel(self):
        self._advanced_visible = not bool(self._advanced_visible)
        if self._advanced_visible:
            self.advanced_panel.grid()
            self.advanced_toggle_btn.config(text="Hide Advanced")
        else:
            self.advanced_panel.grid_remove()
            self.advanced_toggle_btn.config(text="Show Advanced")

    def _update_session_anchor(self):
        anchor = str(self._session_base_prompt or "").strip()
        if not anchor:
            self.session_anchor_var.set("Session anchor: (none yet)")
            return
        preview = anchor if len(anchor) <= 160 else (anchor[:157].rstrip() + "...")
        self.session_anchor_var.set(f"Session anchor: {preview}")

    def run_selected_mode(self):
        mode = str(self.run_mode_var.get() or "").strip().lower()
        if mode == "continue":
            self.start_continue_run()
            return
        if mode == "start fresh":
            self.start_fresh_run()
            return
        self.start_pipeline()

    def _on_prompt_modified(self, _event=None):
        self.prompt_text.edit_modified(False)
        self._refresh_prompt_metrics()

    def _refresh_prompt_metrics(self):
        prompt = self.prompt_text.get("1.0", "end-1c")
        self.prompt_chars_var.set(str(len(prompt)))
        self.estimated_tokens_var.set(str(approx_token_count(prompt)))

    def _sync_live_runtime_settings(self, *_args):
        try:
            config.MAX_ITERATIONS = coerce_int(self.max_iter_var.get(), config.MAX_ITERATIONS, minimum=1, maximum=100)
        except Exception:
            pass
        rescue_mode = str(self.rescue_mode_var.get() or "OFF").strip().upper()
        if rescue_mode not in {"OFF", "ON", "ASK_BEFORE_RESCUE"}:
            rescue_mode = "OFF"
        config.RESCUE_MODE = rescue_mode

    def _normalize_project_root(self, path):
        path = str(path or "").strip()
        if not path:
            return ""
        return os.path.abspath(os.path.normpath(path))

    def _clear_log_view(self):
        self.log_text.delete("1.0", "end")
        self._reset_log_collapse_state()

    def _on_collapse_toggle(self):
        if not self.collapse_logs_var.get():
            self._flush_pending_collapse_to_records()
        self._render_log_view()

    def _reset_log_collapse_state(self):
        self._meta_last_values = {}
        self._active_collapse_lines = []
        self._log_records = []
        self._iter_record_seq = 1

    def _init_log_tags(self):
        base_font = tkfont.nametofont("TkFixedFont").copy()
        bold_font = base_font.copy()
        bold_font.configure(weight="bold")
        tiny_italic = base_font.copy()
        tiny_italic.configure(slant="italic")
        self._log_fonts = {
            "base": base_font,
            "bold": bold_font,
            "tiny_italic": tiny_italic,
        }
        self.log_text.configure(font=base_font)
        self.log_text.tag_configure("log_default", foreground="#222222", font=base_font)
        self.log_text.tag_configure("log_ok", foreground="#1b5e20", font=base_font)
        self.log_text.tag_configure("log_warn", foreground="#8a6d1f", font=base_font)
        self.log_text.tag_configure("log_err", foreground="#b71c1c", font=bold_font)
        self.log_text.tag_configure("log_info", foreground="#1e5aa8", font=base_font)
        self.log_text.tag_configure(
            "log_iter",
            foreground="#ffffff",
            background="#355e8d",
            font=bold_font,
            spacing1=4,
            spacing3=3,
        )
        self.log_text.tag_configure("log_section", foreground="#1e5aa8", font=bold_font, spacing1=2, spacing3=2)
        self.log_text.tag_configure("log_divider", foreground="#6a7f95", font=bold_font)
        self.log_text.tag_configure("log_collapsed", foreground="#5c5c5c", font=tiny_italic)
        self.log_text.tag_configure("iter_toggle", underline=1)
        self.log_text.tag_bind("iter_toggle", "<Button-1>", self._on_iter_toggle_click)

    def _contains_marker(self, line, markers):
        upper_line = line.upper()
        return any(marker.upper() in upper_line for marker in markers)

    def _is_important_line(self, line):
        return self._contains_marker(line, self.IMPORTANT_MARKERS)

    def _format_log_line(self, line):
        line = str(line or "").strip()
        if not line:
            return ""
        if line.startswith("RAW RESPONSE:"):
            payload = line.split(":", 1)[1].strip()
            payload_one_line = re.sub(r"\s+", " ", payload)
            if len(payload_one_line) > 220:
                preview = payload_one_line[:180].rstrip()
                return f"RAW RESPONSE: [preview {len(payload_one_line)} chars] {preview}..."
        return line

    def _collapse_key_for_line(self, line):
        if line in self.LOW_VALUE_EXACT:
            return f"exact::{line}"
        for prefix in self.LOW_VALUE_PREFIXES:
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                last_value = self._meta_last_values.get(prefix)
                self._meta_last_values[prefix] = value
                if value == last_value:
                    return f"meta::{prefix}::{value}"
                return None
        if line.startswith("RAW RESPONSE: [preview "):
            return f"raw_response::{line}"
        if line.startswith("PARSE PATH:"):
            return f"parse::{line}"
        if line.startswith("TRANSFORM PHASE:"):
            return f"transform_phase::{line}"
        if line.startswith("CREATE PHASE:"):
            return f"create_phase::{line}"
        return None

    def _is_low_value_line(self, line):
        if line in self.LOW_VALUE_EXACT:
            return True
        if any(line.startswith(prefix) for prefix in self.LOW_VALUE_PREFIXES):
            return True
        if line.startswith("RAW RESPONSE: [preview "):
            return True
        if line.startswith("PARSE PATH:"):
            return True
        if line.startswith("TRANSFORM PHASE:"):
            return True
        if line.startswith("CREATE PHASE:"):
            return True
        return False

    def _tag_for_line(self, line):
        if line.startswith("ITER "):
            return "log_iter"
        if line.startswith("RUN SUMMARY:") or line.startswith("FAILURE SUMMARY:") or line.startswith("MODE:"):
            return "log_section"
        if self._contains_marker(line, self.ERROR_MARKERS):
            return "log_err"
        if self._contains_marker(line, self.SUCCESS_MARKERS):
            return "log_ok"
        if self._contains_marker(line, self.INFO_MARKERS):
            return "log_warn"
        if line.startswith("STOP REASON") or line.startswith("LOG SAVED TO:") or line.startswith("ACTIVE PROJECT:"):
            return "log_info"
        return "log_default"

    def _append_log_record(self, kind, **kwargs):
        record = {"type": kind}
        record.update(kwargs)
        self._log_records.append(record)

    def _flush_pending_collapse_to_records(self):
        if not self._active_collapse_lines:
            return
        for line in self._active_collapse_lines:
            self._append_log_record("line", text=line, tag=self._tag_for_line(line))
        self._active_collapse_lines = []

    def _find_iter_record(self, iter_id):
        for record in self._log_records:
            if record.get("type") == "iter" and int(record.get("id", -1)) == int(iter_id):
                return record
        return None

    def _on_iter_toggle_click(self, event):
        if not self.collapse_logs_var.get():
            return
        try:
            tags = self.log_text.tag_names(f"@{event.x},{event.y}")
        except Exception:
            return
        iter_tag = next((tag for tag in tags if tag.startswith("iter_toggle_")), "")
        if not iter_tag:
            return
        try:
            iter_id = int(iter_tag.split("_")[-1])
        except Exception:
            return
        record = self._find_iter_record(iter_id)
        if not record or not record.get("hidden_lines"):
            return
        record["expanded"] = not bool(record.get("expanded"))
        self._render_log_view()
        self.log_text.see("end")

    def _render_log_view(self):
        self.log_text.delete("1.0", "end")
        collapse_enabled = bool(self.collapse_logs_var.get())
        for record in self._log_records:
            if record.get("type") == "iter":
                self.log_text.insert("end", ("=" * 88) + "\n", "log_divider")
                iter_text = str(record.get("text", ""))
                hidden_lines = [str(x) for x in list(record.get("hidden_lines") or []) if str(x).strip()]
                if hidden_lines:
                    marker = "[<->]" if (record.get("expanded") or not collapse_enabled) else "[<+>]"
                    suffix = f" ({len(hidden_lines)} hidden)" if collapse_enabled else f" ({len(hidden_lines)} shown)"
                    header = f"{marker} {iter_text}{suffix}"
                else:
                    marker = ""
                    header = iter_text
                header_start = self.log_text.index("end-1c")
                self.log_text.insert("end", header + "\n", "log_iter")
                if hidden_lines and collapse_enabled and marker:
                    marker_end = self.log_text.index(f"{header_start}+{len(marker)}c")
                    iter_toggle_tag = f"iter_toggle_{int(record.get('id', 0))}"
                    self.log_text.tag_add("iter_toggle", header_start, marker_end)
                    self.log_text.tag_add(iter_toggle_tag, header_start, marker_end)
                if hidden_lines and (record.get("expanded") or not collapse_enabled):
                    for hidden_line in hidden_lines:
                        self.log_text.insert("end", f"    {hidden_line}\n", "log_collapsed")
                continue
            line_text = str(record.get("text", ""))
            if line_text:
                self.log_text.insert("end", line_text + "\n", record.get("tag", "log_default"))

    def log(self, msg):
        self.root.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg):
        text = str(msg)
        lines = text.splitlines() or [text]
        collapse_enabled = bool(self.collapse_logs_var.get())
        for raw_line in lines:
            line = self._format_log_line(raw_line)
            if not line:
                continue

            important = self._is_important_line(line)
            collapse_key = self._collapse_key_for_line(line) if (collapse_enabled and not important) else None
            low_value = self._is_low_value_line(line) if (collapse_enabled and not important) else False
            keep_low_value_block = bool(self._active_collapse_lines) and low_value
            if collapse_enabled and (collapse_key or keep_low_value_block):
                self._active_collapse_lines.append(line)
                continue

            if line.startswith("ITER "):
                hidden_lines = list(self._active_collapse_lines)
                self._active_collapse_lines = []
                self._append_log_record(
                    "iter",
                    id=self._iter_record_seq,
                    text=line,
                    hidden_lines=hidden_lines,
                    expanded=False,
                )
                self._iter_record_seq += 1
                continue

            self._flush_pending_collapse_to_records()
            self._append_log_record("line", text=line, tag=self._tag_for_line(line))

        if not collapse_enabled:
            self._flush_pending_collapse_to_records()
        self._render_log_view()
        self.log_text.see("end")

    def apply_config(self):
        config.PROVIDER = self.provider_var.get().strip()
        mode_control = str(self.mode_control_var.get() or "AUTO").strip().upper()
        if mode_control not in {"AUTO", "FORCE_CREATE", "FORCE_PATCH"}:
            mode_control = "AUTO"
        self.mode_control_var.set(mode_control)
        config.MODE_CONTROL = mode_control
        rescue_mode = str(self.rescue_mode_var.get() or "OFF").strip().upper()
        if rescue_mode not in {"OFF", "ON", "ASK_BEFORE_RESCUE"}:
            rescue_mode = "OFF"
        self.rescue_mode_var.set(rescue_mode)
        config.RESCUE_MODE = rescue_mode
        config.LMSTUDIO_URL = self.lm_url_var.get().strip()
        config.LMSTUDIO_MODEL = self.lm_model_var.get().strip()
        config.LMSTUDIO_API_KEY = self.lm_key_var.get().strip()
        config.OPENAI_MODEL = self.oa_model_var.get().strip()
        config.OPENAI_API_KEY = self.oa_key_var.get().strip()
        normalized_project_root = self._normalize_project_root(self.project_root_var.get())
        self.project_root_var.set(normalized_project_root)
        config.PROJECT_ROOT = normalized_project_root
        config.MAX_ITERATIONS = coerce_int(self.max_iter_var.get(), 10, minimum=1, maximum=100)
        config.RUN_TIMEOUT = coerce_int(self.run_timeout_var.get(), 15, minimum=1, maximum=600)
        config.MODEL_TIMEOUT = coerce_int(self.model_timeout_var.get(), 120, minimum=5, maximum=3600)
        config.AUTO_RUN_COMMANDS = bool(self.auto_run_var.get())
        config.PROMPT_CHAR_LIMIT = coerce_int(self.prompt_char_limit_var.get(), 12000, minimum=1500, maximum=50000)
        config.PATCH_FILES = self.patch_files_var.get().strip()
        config.PATCH_SNIPPET_LINES = coerce_int(self.patch_snippet_lines_var.get(), 80, minimum=10, maximum=400)
        config.MAX_OUTPUT_TOKENS = coerce_int(self.max_output_tokens_var.get(), 2000, minimum=128, maximum=16000)

    def _load_gui_settings(self):
        data = read_json_file(self.settings_path, default={})
        if not isinstance(data, dict):
            return
        key = str(data.get("openai_api_key", "")).strip()
        if key:
            self.oa_key_var.set(key)
            config.OPENAI_API_KEY = key
        lm_key = str(data.get("lmstudio_api_key", "")).strip()
        if lm_key:
            self.lm_key_var.set(lm_key)
            config.LMSTUDIO_API_KEY = lm_key
        provider = str(data.get("provider", "")).strip().lower()
        if provider in {"lmstudio", "openai"}:
            self.provider_var.set(provider)
        mode_control = str(data.get("mode_control", "")).strip().upper()
        if mode_control in {"AUTO", "FORCE_CREATE", "FORCE_PATCH"}:
            self.mode_control_var.set(mode_control)
        rescue_mode = str(data.get("rescue_mode", "")).strip().upper()
        if rescue_mode in {"OFF", "ON", "ASK_BEFORE_RESCUE"}:
            self.rescue_mode_var.set(rescue_mode)
        lm_model = str(data.get("lm_model", "")).strip()
        if lm_model:
            self.lm_model_var.set(lm_model)
        oa_model = str(data.get("openai_model", "")).strip()
        if oa_model:
            self.oa_model_var.set(oa_model)
        lm_url = str(data.get("lm_url", "")).strip()
        if lm_url:
            self.lm_url_var.set(lm_url)
        project_root = self._normalize_project_root(data.get("project_root", ""))
        if project_root:
            self.project_root_var.set(project_root)
        patch_files = str(data.get("patch_files", "")).strip()
        if patch_files or "patch_files" in data:
            self.patch_files_var.set(patch_files)
        if "max_iterations" in data:
            self.max_iter_var.set(str(data.get("max_iterations")))
        if "run_timeout" in data:
            self.run_timeout_var.set(str(data.get("run_timeout")))
        if "model_timeout" in data:
            self.model_timeout_var.set(str(data.get("model_timeout")))
        if "prompt_char_limit" in data:
            self.prompt_char_limit_var.set(str(data.get("prompt_char_limit")))
        if "patch_snippet_lines" in data:
            self.patch_snippet_lines_var.set(str(data.get("patch_snippet_lines")))
        if "max_output_tokens" in data:
            self.max_output_tokens_var.set(str(data.get("max_output_tokens")))
        if "auto_run_commands" in data:
            self.auto_run_var.set(bool(data.get("auto_run_commands")))
        run_mode = str(data.get("run_mode", "")).strip()
        if run_mode in {"Start", "Start Fresh", "Continue"}:
            self.run_mode_var.set(run_mode)
        self._saved_prompt_text = str(data.get("last_prompt", "") or "")
        self._saved_session_base_prompt = str(data.get("session_base_prompt", "") or "")
        self._session_base_prompt = str(self._saved_session_base_prompt or "").strip()

    def _save_gui_settings(self):
        prompt_value = self._saved_prompt_text
        if hasattr(self, "prompt_text"):
            prompt_value = self.prompt_text.get("1.0", "end-1c")
            self._saved_prompt_text = prompt_value
        payload = {
            "openai_api_key": self.oa_key_var.get().strip(),
            "lmstudio_api_key": self.lm_key_var.get().strip(),
            "last_prompt": prompt_value,
            "session_base_prompt": str(self._session_base_prompt or ""),
            "run_mode": str(self.run_mode_var.get() or "Start"),
            "provider": self.provider_var.get().strip(),
            "mode_control": self.mode_control_var.get().strip().upper(),
            "rescue_mode": self.rescue_mode_var.get().strip().upper(),
            "lm_url": self.lm_url_var.get().strip(),
            "lm_model": self.lm_model_var.get().strip(),
            "openai_model": self.oa_model_var.get().strip(),
            "project_root": self._normalize_project_root(self.project_root_var.get()),
            "patch_files": self.patch_files_var.get().strip(),
            "max_iterations": self.max_iter_var.get().strip(),
            "run_timeout": self.run_timeout_var.get().strip(),
            "model_timeout": self.model_timeout_var.get().strip(),
            "prompt_char_limit": self.prompt_char_limit_var.get().strip(),
            "patch_snippet_lines": self.patch_snippet_lines_var.get().strip(),
            "max_output_tokens": self.max_output_tokens_var.get().strip(),
            "auto_run_commands": bool(self.auto_run_var.get()),
        }
        write_json_file(self.settings_path, payload)

    def _apply_loaded_prompt(self):
        if not self._saved_prompt_text:
            return
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", self._saved_prompt_text)
        self.prompt_text.edit_modified(False)

    def save_api_key(self):
        self.apply_config()
        self._save_gui_settings()
        self.log("API keys saved to local GUI settings.")

    def _set_model_values(self, provider, models):
        combo = self.lm_model_combo if provider == "lmstudio" else self.oa_model_combo
        variable = self.lm_model_var if provider == "lmstudio" else self.oa_model_var
        combo["values"] = models
        current = variable.get().strip()
        if models and current not in models:
            variable.set(models[0])

    def fetch_models(self):
        self.apply_config()
        provider = self.provider_var.get().strip()
        lm_url = self.lm_url_var.get().strip()
        lm_key = self.lm_key_var.get().strip()
        openai_key = self.oa_key_var.get().strip()
        self.status_label.config(text="Fetching models...")

        def job():
            if provider == "openai" and not openai_key:
                self.root.after(0, lambda: self._finish_fetch_models(provider, None, "OpenAI API key is missing. Enter a key and click 'Save API Keys'."))
                return
            try:
                models = list_models(
                    provider_override=provider,
                    lmstudio_url=lm_url,
                    lmstudio_api_key=lm_key,
                    openai_api_key=openai_key,
                )
            except Exception as exc:
                error_message = str(exc)
                self.root.after(0, lambda: self._finish_fetch_models(provider, None, error_message))
                return
            self.root.after(0, lambda: self._finish_fetch_models(provider, models, ""))

        threading.Thread(target=job, daemon=True).start()

    def _finish_fetch_models(self, provider, models, error_message):
        self.status_label.config(text="Idle")
        if error_message:
            self.log(f"Fetch models error ({provider}): {error_message}")
            messagebox.showwarning("Models", error_message)
            return
        self._set_model_values(provider, models)
        self.log(f"Fetched {len(models)} models for {provider}")

    def handle_model_timeout(self, request_handle, timeout_seconds, timeout_attempt=1):
        decision = {"value": "continue"}
        finished = threading.Event()

        def close_dialog(result):
            if finished.is_set():
                return
            decision["value"] = result
            finished.set()
            dialog = self.timeout_dialog
            self.timeout_dialog = None
            self.timeout_dialog_event = None
            if dialog is not None and dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()

        def show_dialog():
            if request_handle.finished.is_set():
                close_dialog("response")
                return

            if self.timeout_dialog is not None and self.timeout_dialog.winfo_exists():
                try:
                    self.timeout_dialog.destroy()
                except Exception:
                    pass

            dialog = tk.Toplevel(self.root)
            dialog.title("Model Timeout")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            message = (
                f"Model timed out (attempt {max(1, int(timeout_attempt))}).\n"
                f"No response after {int(timeout_seconds)}s.\n"
                "Kill it or keep waiting for one more timeout interval?"
            )
            ttk.Label(
                dialog,
                text=message,
                justify="left",
            ).pack(padx=16, pady=(16, 12))

            button_row = ttk.Frame(dialog)
            button_row.pack(fill="x", padx=16, pady=(0, 16))
            ttk.Button(button_row, text="KILL", command=lambda: self._on_timeout_kill(request_handle, close_dialog)).pack(side="left", padx=(0, 8))
            ttk.Button(button_row, text="CONTINUE", command=lambda: close_dialog("continue")).pack(side="left")
            dialog.protocol("WM_DELETE_WINDOW", lambda: close_dialog("continue"))

            self.timeout_dialog = dialog
            self.timeout_dialog_event = finished

            def poll_request():
                if finished.is_set():
                    return
                if request_handle.finished.is_set():
                    close_dialog("response")
                    return
                dialog.after(200, poll_request)

            poll_request()

        self.root.after(0, show_dialog)
        while not finished.wait(0.2):
            if request_handle.finished.is_set():
                self.root.after(0, lambda: close_dialog("response"))
        return decision["value"]

    def _on_timeout_kill(self, request_handle, close_dialog):
        request_handle.cancel()
        close_dialog("kill")

    def handle_rescue_request(self, reason, from_provider, to_provider):
        decision = {"value": False}
        finished = threading.Event()

        def ask():
            title = "Rescue Confirmation"
            message = (
                f"Switch provider from {from_provider} to {to_provider} rescue?\n\n"
                f"Reason:\n{str(reason)[:500]}"
            )
            decision["value"] = bool(messagebox.askyesno(title, message))
            finished.set()

        self.root.after(0, ask)
        while not finished.wait(0.2):
            pass
        return decision["value"]

    def handle_iteration_limit(self, current_iteration, current_limit):
        decision = {"value": "kill"}
        finished = threading.Event()

        def close_dialog(result):
            if finished.is_set():
                return
            decision["value"] = result
            finished.set()
            dialog = self.iteration_limit_dialog
            self.iteration_limit_dialog = None
            self.iteration_limit_dialog_event = None
            if dialog is not None and dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()

        def show_dialog():
            if self.iteration_limit_dialog is not None and self.iteration_limit_dialog.winfo_exists():
                try:
                    self.iteration_limit_dialog.destroy()
                except Exception:
                    pass
            dialog = tk.Toplevel(self.root)
            dialog.title("Iteration Limit Reached")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            message = (
                f"Reached iteration limit {int(current_iteration)}/{int(current_limit)}.\n"
                "Add more iterations or kill the current run?"
            )
            ttk.Label(dialog, text=message, justify="left").pack(padx=16, pady=(16, 12))
            button_row = ttk.Frame(dialog)
            button_row.pack(fill="x", padx=16, pady=(0, 16))
            ttk.Button(button_row, text="Add +5", command=lambda: close_dialog(5)).pack(side="left", padx=(0, 8))
            ttk.Button(button_row, text="Add +10", command=lambda: close_dialog(10)).pack(side="left", padx=(0, 8))
            ttk.Button(button_row, text="Kill", command=lambda: close_dialog("kill")).pack(side="left")
            dialog.protocol("WM_DELETE_WINDOW", lambda: close_dialog("kill"))
            self.iteration_limit_dialog = dialog
            self.iteration_limit_dialog_event = finished

            def poll_dialog():
                if finished.is_set():
                    return
                if self.stop_flag:
                    close_dialog("kill")
                    return
                dialog.after(200, poll_dialog)

            poll_dialog()

        self.root.after(0, show_dialog)
        while not finished.wait(0.2):
            if self.stop_flag:
                self.root.after(0, lambda: close_dialog("kill"))
        return decision["value"]

    def _launch_pipeline(self, prompt, run_mode_label="MODE: run_start", continue_update=""):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is already running.")
            return False

        if not prompt:
            messagebox.showwarning("Missing prompt", "Enter a task first.")
            return False

        self.apply_config()
        self._save_gui_settings()
        self.stop_flag = False
        self._clear_log_view()
        self.status_label.config(text="Running")
        self._append_log(run_mode_label)

        def job():
            try:
                pipeline_run(
                    prompt,
                    continue_update=continue_update,
                    logger=self.log,
                    stop_checker=lambda: self.stop_flag,
                    model_timeout_handler=self.handle_model_timeout,
                    rescue_decider=self.handle_rescue_request,
                    max_iterations_handler=self.handle_iteration_limit,
                )
                self.log("PIPELINE FINISHED")
                if config.ACTIVE_PROJECT_ROOT:
                    self.log(f"ACTIVE PROJECT: {config.ACTIVE_PROJECT_ROOT}")
                self.root.after(0, lambda: self.status_label.config(text="Idle"))
            except Exception as e:
                self.log(f"ERROR: {e}")
                self.root.after(0, lambda: self.status_label.config(text="Failed"))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()
        return True

    def start_pipeline(self):
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Missing prompt", "Enter a task first.")
            return
        started = self._launch_pipeline(prompt, run_mode_label="MODE: run_start")
        if started:
            self._session_base_prompt = prompt
            self._update_session_anchor()
            self._save_gui_settings()

    def start_continue_run(self):
        followup_prompt = self.prompt_text.get("1.0", "end").strip()
        if not followup_prompt:
            messagebox.showwarning("Missing continue prompt", "Enter a small follow-up instruction first.")
            return
        base_prompt = str(self._session_base_prompt or "").strip()
        if not base_prompt:
            base_prompt = str(self._saved_session_base_prompt or "").strip()
        if not base_prompt:
            messagebox.showwarning("Missing base prompt", "Start a task first, then use Continue.")
            return

        started = self._launch_pipeline(
            base_prompt,
            run_mode_label="MODE: run_continue",
            continue_update=followup_prompt,
        )
        if started:
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.edit_modified(False)
            self._refresh_prompt_metrics()
            self._update_session_anchor()

    def start_fresh_run(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is already running.")
            return
        self.clear_session(stop_running=False)
        self.start_pipeline()

    def stop_pipeline(self):
        self.stop_flag = True
        self.status_label.config(text="Stopping...")

    def clear_session(self, stop_running=False):
        if stop_running:
            self.stop_flag = True
            self.status_label.config(text="Stopping...")
        project_root = self._normalize_project_root(self.project_root_var.get()) or config.ACTIVE_PROJECT_ROOT or ""
        try:
            clear_runtime_session(project_root or None)
        except Exception:
            pass
        config.ACTIVE_PROJECT_ROOT = ""
        self._session_base_prompt = ""
        self._saved_session_base_prompt = ""
        self._update_session_anchor()
        self._save_gui_settings()
        self.status_label.config(text="Session cleared")
        self.log("SESSION CLEARED")

    def open_log(self):
        open_path(os.path.join(os.getcwd(), config.LOG_FILE))

    def open_raw_log(self):
        open_path(os.path.join(os.getcwd(), config.RAW_LOG_FILE))

    def open_project_folder(self):
        target = config.ACTIVE_PROJECT_ROOT or os.path.join(os.getcwd(), config.PROJECT_ROOT)
        open_path(target)

    def browse_repo_dir(self):
        path = filedialog.askdirectory(initialdir=self.repo_dir_var.get() or os.getcwd())
        if path:
            self.repo_dir_var.set(path)

    def browse_project_root(self):
        initial_dir = self._normalize_project_root(self.project_root_var.get()) or os.getcwd()
        path = filedialog.askdirectory(initialdir=initial_dir)
        if path:
            self.project_root_var.set(self._normalize_project_root(path))

    def _git_log(self, title, ok, msg):
        prefix = "[OK]" if ok else "[ERR]"
        self.log(f"{title} {prefix}")
        if msg:
            self.log(msg)

    def git_init_repo(self):
        repo_dir = self.repo_dir_var.get().strip() or os.getcwd()
        os.makedirs(repo_dir, exist_ok=True)
        ensure_gitignore(repo_dir)
        ok, msg = git_init(repo_dir)
        self._git_log("git init", ok, msg)

        name = self.git_name_var.get().strip()
        email = self.git_email_var.get().strip()
        if name or email:
            ok2, msg2 = git_set_identity(repo_dir, name=name or None, email=email or None)
            self._git_log("git config", ok2, msg2)

    def git_commit_repo(self):
        repo_dir = self.repo_dir_var.get().strip() or os.getcwd()
        ensure_gitignore(repo_dir)
        ok, msg = git_add_all(repo_dir)
        self._git_log("git add", ok, msg)
        if not ok:
            return

        commit_msg = self.commit_msg_var.get().strip() or "update"
        ok, msg = git_commit(repo_dir, commit_msg)
        self._git_log("git commit", ok, msg)

    def git_push_repo(self):
        repo_dir = self.repo_dir_var.get().strip() or os.getcwd()
        remote = self.remote_url_var.get().strip()
        if not remote:
            messagebox.showwarning("Missing remote", "Enter remote URL.")
            return

        ok, msg = git_branch_main(repo_dir)
        self._git_log("git branch -M main", ok, msg)

        ok, msg = git_set_remote(repo_dir, remote)
        self._git_log("git remote", ok, msg)
        if not ok:
            return

        ok, msg = git_push(repo_dir)
        self._git_log("git push", ok, msg)

    def git_tag_repo(self):
        repo_dir = self.repo_dir_var.get().strip() or os.getcwd()
        tag_name = self.tag_var.get().strip() or "v1.0-working"
        ok, msg = git_tag(repo_dir, tag_name)
        self._git_log("git tag+push", ok, msg)

    def _on_close(self):
        try:
            self.apply_config()
            self._save_gui_settings()
        except Exception:
            pass
        self.root.destroy()


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
