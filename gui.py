import os
import re
import threading
import time
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
from preflight_doctor import run_preflight_doctor
from providers import list_models
from local_presets import delete_local_preset, load_local_presets, upsert_local_preset
from session_state_store import append_journal_event, journal_path_for, load_latest_session_snapshot
from session_resume import build_resume_summary, format_resume_status
from utils import approx_token_count, coerce_int, ensure_gitignore, open_path, read_json_file, write_json_file


class _SimpleTooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = str(text or "")
        self.tipwindow = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None):
        if self.tipwindow or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except Exception:
            return
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padding=(6, 4),
        )
        label.pack()
        self.tipwindow = tw

    def _hide(self, _event=None):
        tw = self.tipwindow
        self.tipwindow = None
        if tw is not None:
            try:
                tw.destroy()
            except Exception:
                pass


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
        "PROMPT:",
        "PARSE PATH:",
        "RAW RESPONSE:",
        "RAW RESPONSE COLLAPSED:",
        "GIT CHECKPOINT TARGET:",
        "GIT CHECKPOINT CONTEXT:",
        "PROGRESS APPLIED -> CONTINUE",
        "NO REAL PROGRESS -> CONTINUE",
        "NO CHANGES MADE",
        "No files were modified.",
        "No commands were executed.",
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
        self.permission_dialog = None
        self.permission_dialog_event = None
        self.doctor_dialog = None
        self.settings_path = os.path.abspath(os.path.join(os.getcwd(), ".agent", "gui_settings.json"))
        self.presets_path = os.path.abspath(os.path.join(os.getcwd(), ".agent", "prompt_presets.json"))
        self._local_presets = {}
        self._saved_prompt_text = ""
        self._saved_session_base_prompt = ""
        self._session_base_prompt = ""
        self._advanced_visible = False
        self._meta_last_values = {}
        self._active_collapse_lines = []
        self._log_records = []
        self._iter_record_seq = 1
        self._permission_decision_cache = {}
        self._warned_internal_contexts = set()
        self._last_log_ts = time.monotonic()
        self._run_watchdog_token = 0

        self._build_vars()
        self._load_gui_settings()
        self._load_local_presets_state()
        self._build_ui()
        self._apply_loaded_prompt()
        self._bind_events()
        self._refresh_prompt_metrics()
        self._update_session_anchor()
        self._refresh_resume_state(load_prompt=False, write_log=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_vars(self):
        self.provider_var = tk.StringVar(value=config.PROVIDER)
        self.mode_control_var = tk.StringVar(value=str(getattr(config, "MODE_CONTROL", "AUTO") or "AUTO").upper())
        self.rescue_mode_var = tk.StringVar(value=str(getattr(config, "RESCUE_MODE", "OFF") or "OFF").upper())
        self.permission_mode_var = tk.StringVar(value=str(getattr(config, "PERMISSION_MODE", "workspace-write") or "workspace-write").strip().lower())
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
        self.network_enabled_var = tk.BooleanVar(value=bool(getattr(config, "NETWORK_ENABLED", True)))
        self.collapse_logs_var = tk.BooleanVar(value=True)
        self.wrap_logs_var = tk.BooleanVar(value=True)
        self.run_mode_var = tk.StringVar(value="Start")
        self.session_anchor_var = tk.StringVar(value="Session anchor: (none)")
        self.resume_status_var = tk.StringVar(value="Resume: no prior session state")
        self.preset_name_var = tk.StringVar(value="")

        self.prompt_chars_var = tk.StringVar(value="0")
        self.estimated_tokens_var = tk.StringVar(value="0")

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
        ttk.Checkbutton(log_toolbar, text="Wrap Lines", variable=self.wrap_logs_var, command=self._on_wrap_toggle).pack(side="left", padx=(8, 0))
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
        self.log_x_scroll = x_scroll
        self.log_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self._init_log_tags()
        self._apply_log_wrap_mode()

        composer_box = ttk.LabelFrame(main, text="Prompt Composer", padding=(8, 8, 8, 8))
        composer_box.grid(row=2, column=0, sticky="we", pady=(8, 0))
        composer_box.columnconfigure(0, weight=1)
        composer_box.rowconfigure(0, weight=1)

        self.prompt_text = tk.Text(composer_box, height=5, wrap="word")
        self.prompt_text.grid(row=0, column=0, sticky="we")

        metrics = ttk.Frame(composer_box)
        metrics.grid(row=1, column=0, sticky="we", pady=(8, 0))
        for col in range(4):
            metrics.columnconfigure(col, weight=0)
        metrics.columnconfigure(3, weight=1)
        ttk.Label(metrics, text="Prompt chars").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.prompt_chars_var, width=8, state="readonly").grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Label(metrics, text="Estimated tokens").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Entry(metrics, textvariable=self.estimated_tokens_var, width=8, state="readonly").grid(row=0, column=3, sticky="w", padx=(0, 10))

        run_row = ttk.Frame(composer_box)
        run_row.grid(row=2, column=0, sticky="we", pady=(8, 0))
        run_row.columnconfigure(7, weight=1)
        ttk.Label(run_row, text="Run mode").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            run_row,
            textvariable=self.run_mode_var,
            values=["Start", "Start Fresh", "Continue", "Resume"],
            width=12,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", padx=(6, 10))
        ttk.Button(run_row, text="Run", command=self.run_selected_mode).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Stop", command=self.stop_pipeline).grid(row=0, column=3, sticky="w", padx=(0, 6))
        ttk.Button(run_row, text="Clear Session", command=lambda: self.clear_session(stop_running=True)).grid(row=0, column=4, sticky="w", padx=(0, 6))

        anchor_row = ttk.Frame(composer_box)
        anchor_row.grid(row=3, column=0, sticky="we", pady=(6, 0))
        ttk.Label(anchor_row, textvariable=self.session_anchor_var, foreground="#2f4f6f").pack(side="left")
        ttk.Button(anchor_row, text="Resume Status", command=self.refresh_resume_status).pack(side="right", padx=(6, 0))
        ttk.Button(anchor_row, text="Load Last Prompt", command=self.load_last_prompt_from_session).pack(side="right")

        resume_row = ttk.Frame(composer_box)
        resume_row.grid(row=4, column=0, sticky="we", pady=(4, 0))
        ttk.Label(resume_row, textvariable=self.resume_status_var, foreground="#355e8d").pack(side="left")

        preset_row = ttk.Frame(composer_box)
        preset_row.grid(row=5, column=0, sticky="we", pady=(6, 0))
        ttk.Label(preset_row, text="Preset").pack(side="left")
        self.preset_combo = ttk.Combobox(preset_row, textvariable=self.preset_name_var, width=28)
        self.preset_combo.pack(side="left", padx=(6, 8))
        ttk.Button(preset_row, text="Save", command=self.save_prompt_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_row, text="Load", command=self.load_prompt_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_row, text="Insert", command=self.insert_prompt_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_row, text="Delete", command=self.delete_prompt_preset).pack(side="left")
        self._refresh_preset_values()

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
        network_toggle = ttk.Checkbutton(runtime_tab, text="Internet for run_cmd", variable=self.network_enabled_var)
        network_toggle.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        _SimpleTooltip(
            network_toggle,
            "Controls network for tool actions: run_cmd/http_get/download_file.\n"
            "OFF blocks network calls by the pipeline tools.\n"
            "Does not disable provider API traffic (LM Studio/OpenAI).",
        )
        ttk.Label(runtime_tab, text="Permission mode").grid(row=3, column=2, sticky="w", **pad)
        ttk.Combobox(
            runtime_tab,
            textvariable=self.permission_mode_var,
            values=["workspace-write", "read-only", "danger-full-access", "prompt", "allow"],
            width=20,
            state="readonly",
        ).grid(row=3, column=3, sticky="w", **pad)

        provider_tab = ttk.Frame(tabs)
        provider_tab.columnconfigure(1, weight=1)
        tabs.add(provider_tab, text="Providers")

        ttk.Label(provider_tab, text="LM URL").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(provider_tab, textvariable=self.lm_url_var).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="LM Model").grid(row=1, column=0, sticky="w", **pad)
        self.lm_model_combo = ttk.Combobox(provider_tab, textvariable=self.lm_model_var)
        self.lm_model_combo.grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="LM API Key").grid(row=2, column=0, sticky="w", **pad)
        lm_key_row = ttk.Frame(provider_tab)
        lm_key_row.grid(row=2, column=1, sticky="we", **pad)
        lm_key_row.columnconfigure(0, weight=1)
        ttk.Entry(lm_key_row, textvariable=self.lm_key_var, show="*").grid(row=0, column=0, sticky="we")
        ttk.Button(lm_key_row, text="Save API", command=self.save_api_key).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(provider_tab, text="OpenAI Model").grid(row=3, column=0, sticky="w", **pad)
        self.oa_model_combo = ttk.Combobox(provider_tab, textvariable=self.oa_model_var)
        self.oa_model_combo.grid(row=3, column=1, sticky="we", **pad)
        ttk.Label(provider_tab, text="OpenAI API Key").grid(row=4, column=0, sticky="w", **pad)
        oa_key_row = ttk.Frame(provider_tab)
        oa_key_row.grid(row=4, column=1, sticky="we", **pad)
        oa_key_row.columnconfigure(0, weight=1)
        ttk.Entry(oa_key_row, textvariable=self.oa_key_var, show="*").grid(row=0, column=0, sticky="we")
        ttk.Button(oa_key_row, text="Save API", command=self.save_api_key).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(provider_tab, text="Fetch Models", command=self.fetch_models).grid(row=5, column=1, sticky="e", **pad)

        project_tab = ttk.Frame(tabs)
        project_tab.columnconfigure(1, weight=1)
        tabs.add(project_tab, text="Project / Patch")

        ttk.Label(project_tab, text="Project root").grid(row=0, column=0, sticky="w", **pad)
        project_root_row = ttk.Frame(project_tab)
        project_root_row.grid(row=0, column=1, sticky="we", **pad)
        project_root_row.columnconfigure(0, weight=1)
        ttk.Entry(project_root_row, textvariable=self.project_root_var).grid(row=0, column=0, sticky="we")
        ttk.Button(project_root_row, text="Browse", command=self.browse_project_root).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(
            project_tab,
            text="Patch targeting and snippet scope are now automatic.",
        ).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(project_tab, text="Doctor / Preflight", command=self.run_doctor_preflight).grid(
            row=2, column=1, sticky="e", **pad
        )

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

    def _warn_internal(self, context, exc, once=True):
        key = str(context or "").strip() or "internal"
        if once and key in self._warned_internal_contexts:
            return
        if once:
            self._warned_internal_contexts.add(key)
        message = f"INTERNAL WARNING: {key}: {exc}"
        if hasattr(self, "log_text"):
            try:
                self.log(message)
                return
            except Exception:
                pass
        try:
            print(message)
        except Exception:
            pass

    def _load_local_presets_state(self):
        try:
            self._local_presets = load_local_presets(self.presets_path)
        except Exception as exc:
            self._local_presets = {}
            self._warn_internal("local preset load failed", exc)

    def _refresh_preset_values(self):
        names = list(self._local_presets.keys())
        if hasattr(self, "preset_combo"):
            self.preset_combo["values"] = names
        current = str(self.preset_name_var.get() or "").strip()
        if not current and names:
            self.preset_name_var.set(names[0])
        elif current and current not in self._local_presets:
            if names:
                self.preset_name_var.set(names[0])

    def _default_preset_name(self):
        prompt = self.prompt_text.get("1.0", "end-1c").strip() if hasattr(self, "prompt_text") else ""
        if not prompt:
            return ""
        first_line = prompt.splitlines()[0].strip()
        cleaned = re.sub(r"\s+", " ", first_line)
        if len(cleaned) > 48:
            cleaned = cleaned[:48].rstrip()
        return cleaned

    def save_prompt_preset(self):
        name = str(self.preset_name_var.get() or "").strip() or self._default_preset_name()
        prompt = self.prompt_text.get("1.0", "end-1c").strip()
        if not name:
            messagebox.showwarning("Preset", "Enter preset name first.")
            return
        if not prompt:
            messagebox.showwarning("Preset", "Prompt is empty.")
            return
        try:
            self._local_presets = upsert_local_preset(self.presets_path, name, prompt)
        except Exception as exc:
            messagebox.showerror("Preset", str(exc))
            return
        self.preset_name_var.set(name)
        self._refresh_preset_values()
        self._save_gui_settings()
        self.log(f"PRESET SAVED: {name}")

    def load_prompt_preset(self):
        name = str(self.preset_name_var.get() or "").strip()
        if not name:
            messagebox.showwarning("Preset", "Select preset first.")
            return
        text = str(self._local_presets.get(name) or "").strip()
        if not text:
            messagebox.showwarning("Preset", "Preset not found.")
            return
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", text)
        self.prompt_text.edit_modified(False)
        self._refresh_prompt_metrics()
        self._save_gui_settings()
        self.log(f"PRESET LOADED: {name}")

    def insert_prompt_preset(self):
        name = str(self.preset_name_var.get() or "").strip()
        if not name:
            messagebox.showwarning("Preset", "Select preset first.")
            return
        text = str(self._local_presets.get(name) or "").strip()
        if not text:
            messagebox.showwarning("Preset", "Preset not found.")
            return
        current = self.prompt_text.get("1.0", "end-1c")
        if current.strip():
            self.prompt_text.insert("end", "\n\n" + text)
        else:
            self.prompt_text.insert("1.0", text)
        self.prompt_text.edit_modified(False)
        self._refresh_prompt_metrics()
        self._save_gui_settings()
        self.log(f"PRESET INSERTED: {name}")

    def delete_prompt_preset(self):
        name = str(self.preset_name_var.get() or "").strip()
        if not name:
            messagebox.showwarning("Preset", "Select preset first.")
            return
        if not messagebox.askyesno("Preset", f"Delete preset '{name}'?"):
            return
        self._local_presets = delete_local_preset(self.presets_path, name)
        if self.preset_name_var.get().strip() == name:
            self.preset_name_var.set("")
        self._refresh_preset_values()
        self._save_gui_settings()
        self.log(f"PRESET DELETED: {name}")

    def run_selected_mode(self):
        mode = str(self.run_mode_var.get() or "").strip().lower()
        if mode == "resume":
            self.start_resume_run()
            return
        if mode == "continue":
            self.start_continue_run()
            return
        if mode == "start fresh":
            self.start_fresh_run()
            return
        self.start_pipeline()

    def _current_session_paths(self):
        session_path = os.path.abspath(os.path.join(os.getcwd(), config.SESSION_FILE))
        journal_path = journal_path_for(session_path)
        journal_present = bool(
            os.path.exists(journal_path)
            or os.path.exists(f"{journal_path}.1")
            or os.path.exists(f"{journal_path}.2")
            or os.path.exists(f"{journal_path}.3")
        )
        return session_path, journal_path, journal_present

    def _load_resume_summary(self):
        session_path, _journal_path, journal_present = self._current_session_paths()
        snapshot = load_latest_session_snapshot(session_path)
        if not snapshot:
            snapshot = read_json_file(session_path, default={})
            if not isinstance(snapshot, dict):
                snapshot = {}
        return build_resume_summary(
            snapshot,
            session_path=session_path,
            journal_present=journal_present,
        )

    def _refresh_resume_state(self, load_prompt=False, write_log=True):
        summary = self._load_resume_summary()
        self.resume_status_var.set(format_resume_status(summary))
        base_prompt = str(summary.get("base_prompt") or "").strip()
        latest_prompt = str(summary.get("latest_prompt") or "").strip()
        if base_prompt and not str(self._session_base_prompt or "").strip():
            self._session_base_prompt = base_prompt
        if load_prompt and latest_prompt:
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", latest_prompt)
            self.prompt_text.edit_modified(False)
            self._refresh_prompt_metrics()
        self._update_session_anchor()
        if write_log:
            if summary.get("has_state"):
                self.log("RESUME STATUS: state loaded from session/journal")
            else:
                self.log("RESUME STATUS: no existing session state found")
        return summary

    def refresh_resume_status(self):
        self._refresh_resume_state(load_prompt=False, write_log=True)

    def load_last_prompt_from_session(self):
        summary = self._refresh_resume_state(load_prompt=True, write_log=True)
        if summary.get("has_state") and str(summary.get("latest_prompt") or "").strip():
            self.log("RESUME PROMPT: loaded last prompt from session state")
        else:
            self.log("RESUME PROMPT: no stored prompt found")

    def _doctor_summary_text(self, report):
        summary = dict(report.get("summary") or {})
        return (
            f"Doctor summary: overall={summary.get('overall', 'WARN')} "
            f"(OK={int(summary.get('ok', 0) or 0)}, "
            f"WARN={int(summary.get('warn', 0) or 0)}, "
            f"FAIL={int(summary.get('fail', 0) or 0)})"
        )

    def _doctor_journal_available(self):
        session_path = os.path.abspath(os.path.join(os.getcwd(), config.SESSION_FILE))
        journal_path = journal_path_for(session_path)
        if os.path.exists(journal_path):
            return True, session_path, journal_path
        if os.path.exists(f"{journal_path}.1") or os.path.exists(f"{journal_path}.2") or os.path.exists(f"{journal_path}.3"):
            return True, session_path, journal_path
        return False, session_path, journal_path

    def _log_doctor_report_to_journal(self, report):
        available, session_path, _journal_path = self._doctor_journal_available()
        if not available:
            return False
        payload = {
            "summary": dict(report.get("summary") or {}),
            "provider": str(report.get("provider") or ""),
            "project_root": str(report.get("project_root") or ""),
            "checks": list(report.get("results") or []),
        }
        append_journal_event(session_path, "doctor_result", payload)
        return True

    def _copy_doctor_summary(self, report):
        lines = [self._doctor_summary_text(report)]
        for row in list(report.get("results") or []):
            lines.append(
                f"- [{row.get('status', 'WARN')}] {row.get('label', '')}: {row.get('detail', '')}"
            )
        text = "\n".join(lines)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log("DOCTOR: summary copied to clipboard")

    def _show_doctor_dialog(self, report):
        if self.doctor_dialog is not None:
            try:
                self.doctor_dialog.destroy()
            except Exception:
                pass
            self.doctor_dialog = None

        dialog = tk.Toplevel(self.root)
        dialog.title("Doctor / Preflight")
        dialog.geometry("980x460")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        summary_text = self._doctor_summary_text(report)
        summary = dict(report.get("summary") or {})
        overall = str(summary.get("overall") or "WARN").upper()
        color = "#1b5e20" if overall == "OK" else ("#8a6d1f" if overall == "WARN" else "#b71c1c")
        ttk.Label(dialog, text=summary_text, foreground=color).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        table_wrap = ttk.Frame(dialog)
        table_wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)

        columns = ("check", "status", "detail")
        table = ttk.Treeview(table_wrap, columns=columns, show="headings", height=14)
        table.heading("check", text="Check")
        table.heading("status", text="Status")
        table.heading("detail", text="Detail")
        table.column("check", width=290, anchor="w")
        table.column("status", width=80, anchor="center")
        table.column("detail", width=560, anchor="w")

        for row in list(report.get("results") or []):
            status = str(row.get("status") or "WARN").upper()
            tag = "warn"
            if status == "OK":
                tag = "ok"
            elif status == "FAIL":
                tag = "fail"
            table.insert(
                "",
                "end",
                values=(row.get("label", ""), status, row.get("detail", "")),
                tags=(tag,),
            )

        table.tag_configure("ok", foreground="#1b5e20")
        table.tag_configure("warn", foreground="#8a6d1f")
        table.tag_configure("fail", foreground="#b71c1c")

        y_scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=y_scroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        btns = ttk.Frame(dialog)
        btns.grid(row=2, column=0, sticky="e", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Copy Summary", command=lambda: self._copy_doctor_summary(report)).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Close", command=dialog.destroy).pack(side="left")

        self.doctor_dialog = dialog

    def run_doctor_preflight(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is currently running. Stop it before running Doctor.")
            return

        self.apply_config()
        self._save_gui_settings()
        self.status_label.config(text="Doctor running...")
        self.log("DOCTOR: preflight started")

        project_root = self._normalize_project_root(self.project_root_var.get()) or config.ACTIVE_PROJECT_ROOT or ""
        provider = str(self.provider_var.get() or config.PROVIDER or "lmstudio").strip().lower()

        def job():
            try:
                report = run_preflight_doctor(
                    project_root=project_root,
                    selected_provider=provider,
                    cwd=os.getcwd(),
                )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: (
                        self.status_label.config(text="Doctor failed"),
                        self.log(f"DOCTOR FAIL: {exc}"),
                        messagebox.showerror("Doctor / Preflight", str(exc)),
                    ),
                )
                return

            def finish():
                self.status_label.config(text="Idle")
                self.log("DOCTOR: " + self._doctor_summary_text(report))
                for row in list(report.get("results") or []):
                    self.log(f"DOCTOR {row.get('status', 'WARN')}: {row.get('label', '')} | {row.get('detail', '')}")
                journal_logged = False
                try:
                    journal_logged = self._log_doctor_report_to_journal(report)
                except Exception as exc:
                    self.log(f"DOCTOR: journal logging failed ({exc})")
                if journal_logged:
                    self.log("DOCTOR: doctor_result event logged to journal")
                else:
                    self.log("DOCTOR: journal not present; skipped doctor_result event")
                self._show_doctor_dialog(report)

            self.root.after(0, finish)

        threading.Thread(target=job, daemon=True).start()

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
        config.PERMISSION_MODE = self._normalize_permission_mode(self.permission_mode_var.get())
        config.NETWORK_ENABLED = bool(self.network_enabled_var.get())

    def _normalize_project_root(self, path):
        path = str(path or "").strip()
        if not path:
            return ""
        return os.path.abspath(os.path.normpath(path))

    def _normalize_permission_mode(self, value):
        mode = str(value or "workspace-write").strip().lower()
        if mode not in {"read-only", "workspace-write", "danger-full-access", "prompt", "allow"}:
            mode = "workspace-write"
        return mode

    def _clear_log_view(self):
        self.log_text.delete("1.0", "end")
        self._reset_log_collapse_state()

    def _on_collapse_toggle(self):
        if not self.collapse_logs_var.get():
            self._flush_pending_collapse_to_records()
        self._render_log_view()

    def _on_wrap_toggle(self):
        self._apply_log_wrap_mode()

    def _apply_log_wrap_mode(self):
        wrap_enabled = bool(self.wrap_logs_var.get())
        self.log_text.configure(wrap=("word" if wrap_enabled else "none"))
        if hasattr(self, "log_x_scroll") and self.log_x_scroll is not None:
            if wrap_enabled:
                self.log_x_scroll.grid_remove()
            else:
                self.log_x_scroll.grid(row=1, column=0, sticky="we")

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
        self._last_log_ts = time.monotonic()
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

    def _start_run_watchdog(self):
        self._run_watchdog_token += 1
        token = self._run_watchdog_token
        self._last_log_ts = time.monotonic()

        def tick():
            if token != self._run_watchdog_token:
                return
            worker = self.worker
            if worker is None or not worker.is_alive():
                return
            # Keep watchdog silent to avoid noisy repeated log spam while model is working.
            self.root.after(3000, tick)

        self.root.after(3000, tick)

    def _start_worker_bootstrap_probe(self):
        token = self._run_watchdog_token

        def probe():
            if token != self._run_watchdog_token:
                return
            worker = self.worker
            status = str(self.status_label.cget("text") or "")
            if worker is None:
                if status == "Running":
                    self._append_log("RUNNING: worker not initialized yet.")
                return
            if not worker.is_alive() and status == "Running":
                self._append_log("ERROR: worker exited before pipeline emitted startup logs.")
                self.status_label.config(text="Failed")
                return

        self.root.after(2200, probe)

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
        permission_mode = self._normalize_permission_mode(self.permission_mode_var.get())
        self.permission_mode_var.set(permission_mode)
        config.PERMISSION_MODE = permission_mode
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
        config.NETWORK_ENABLED = bool(self.network_enabled_var.get())

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
        permission_mode = self._normalize_permission_mode(data.get("permission_mode", ""))
        if permission_mode:
            self.permission_mode_var.set(permission_mode)
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
        if "max_iterations" in data:
            self.max_iter_var.set(str(data.get("max_iterations")))
        if "run_timeout" in data:
            self.run_timeout_var.set(str(data.get("run_timeout")))
        if "model_timeout" in data:
            self.model_timeout_var.set(str(data.get("model_timeout")))
        if "auto_run_commands" in data:
            self.auto_run_var.set(bool(data.get("auto_run_commands")))
        if "network_enabled" in data:
            self.network_enabled_var.set(bool(data.get("network_enabled")))
        if "wrap_logs" in data:
            self.wrap_logs_var.set(bool(data.get("wrap_logs")))
        run_mode = str(data.get("run_mode", "")).strip()
        if run_mode in {"Start", "Start Fresh", "Continue", "Resume"}:
            self.run_mode_var.set(run_mode)
        preset_name = str(data.get("preset_name", "")).strip()
        if preset_name:
            self.preset_name_var.set(preset_name)
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
            "preset_name": str(self.preset_name_var.get() or "").strip(),
            "provider": self.provider_var.get().strip(),
            "mode_control": self.mode_control_var.get().strip().upper(),
            "rescue_mode": self.rescue_mode_var.get().strip().upper(),
            "permission_mode": self._normalize_permission_mode(self.permission_mode_var.get()),
            "lm_url": self.lm_url_var.get().strip(),
            "lm_model": self.lm_model_var.get().strip(),
            "openai_model": self.oa_model_var.get().strip(),
            "project_root": self._normalize_project_root(self.project_root_var.get()),
            "max_iterations": self.max_iter_var.get().strip(),
            "run_timeout": self.run_timeout_var.get().strip(),
            "model_timeout": self.model_timeout_var.get().strip(),
            "auto_run_commands": bool(self.auto_run_var.get()),
            "network_enabled": bool(self.network_enabled_var.get()),
            "wrap_logs": bool(self.wrap_logs_var.get()),
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

    def _permission_request_key(self, request):
        if not isinstance(request, dict):
            return ""
        action = str(request.get("action_type") or "").strip().lower()
        required_mode = str(request.get("required_mode") or "").strip().lower()
        target = str(request.get("target") or "").strip().lower()
        if not target and isinstance(request.get("args"), dict):
            args = dict(request.get("args") or {})
            target = str(args.get("path") or args.get("cmd") or "").strip().lower()
        return f"{action}|{required_mode}|{target}"

    def handle_permission_request(self, request):
        request = dict(request or {})
        cache_key = self._permission_request_key(request)
        if cache_key and cache_key in self._permission_decision_cache:
            cached_allowed = bool(self._permission_decision_cache.get(cache_key))
            if cached_allowed:
                return {"decision": "allow", "reason": "Approved by remembered decision for this run."}
            return {"decision": "deny", "reason": "Denied by remembered decision for this run."}

        decision = {"value": {"decision": "deny", "reason": "Approval denied."}}
        finished = threading.Event()

        def close_dialog(result):
            if finished.is_set():
                return
            decision["value"] = result
            finished.set()
            dialog = self.permission_dialog
            self.permission_dialog = None
            self.permission_dialog_event = None
            if dialog is not None and dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()

        def show_dialog():
            if self.permission_dialog is not None and self.permission_dialog.winfo_exists():
                try:
                    self.permission_dialog.destroy()
                except Exception:
                    pass

            action_type = str(request.get("action_type") or "").strip()
            current_mode = str(request.get("current_mode") or "").strip()
            required_mode = str(request.get("required_mode") or "").strip()
            reason = str(request.get("reason") or "Permission check requires confirmation.").strip()
            target = str(request.get("target") or "").strip()
            args = dict(request.get("args") or {})
            if not target:
                target = str(args.get("path") or args.get("cmd") or "").strip()
            target_preview = target if len(target) <= 220 else (target[:217].rstrip() + "...")
            details = (
                f"Action: {action_type or '(unknown)'}\n"
                f"Current mode: {current_mode or '(unknown)'}\n"
                f"Required mode: {required_mode or '(unknown)'}\n"
                f"Target: {target_preview or '(none)'}\n\n"
                f"Reason:\n{reason}"
            )

            dialog = tk.Toplevel(self.root)
            dialog.title("Permission Approval")
            dialog.transient(self.root)
            dialog.grab_set()
            try:
                dialog.attributes("-topmost", True)
                dialog.after(400, lambda: dialog.attributes("-topmost", False))
            except Exception:
                pass
            dialog.resizable(False, False)
            ttk.Label(
                dialog,
                text="Pipeline requested an action that needs approval.",
                foreground="#8a6d1f",
            ).pack(anchor="w", padx=14, pady=(12, 8))
            body = tk.Text(dialog, height=10, width=88, wrap="word")
            body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
            body.insert("1.0", details)
            body.configure(state="disabled")

            btns = ttk.Frame(dialog)
            btns.pack(fill="x", padx=14, pady=(0, 12))

            def choose(allow, remember=False):
                result = {
                    "decision": "allow" if allow else "deny",
                    "reason": "Approved by user." if allow else "Denied by user.",
                }
                if remember and cache_key:
                    self._permission_decision_cache[cache_key] = bool(allow)
                    result["reason"] = (
                        "Approved and remembered for this run."
                        if allow
                        else "Denied and remembered for this run."
                    )
                self.log(
                    f"PERMISSION PROMPT: {'allow' if allow else 'deny'} "
                    f"action={action_type or '(unknown)'} remember={'yes' if remember else 'no'}"
                )
                close_dialog(result)

            ttk.Button(btns, text="Allow Once", command=lambda: choose(True, remember=False)).pack(side="left", padx=(0, 6))
            ttk.Button(btns, text="Allow + Remember", command=lambda: choose(True, remember=True)).pack(side="left", padx=(0, 12))
            ttk.Button(btns, text="Deny Once", command=lambda: choose(False, remember=False)).pack(side="left", padx=(0, 6))
            ttk.Button(btns, text="Deny + Remember", command=lambda: choose(False, remember=True)).pack(side="left")

            dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False, remember=False))
            self.permission_dialog = dialog
            self.permission_dialog_event = finished

            def poll_dialog():
                if finished.is_set():
                    return
                if self.stop_flag:
                    close_dialog({"decision": "deny", "reason": "Run stopping; permission request cancelled."})
                    return
                dialog.after(200, poll_dialog)

            poll_dialog()

        self.root.after(0, show_dialog)
        while not finished.wait(0.2):
            if self.stop_flag:
                self.root.after(
                    0,
                    lambda: close_dialog({"decision": "deny", "reason": "Run stopping; permission request cancelled."}),
                )
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
        self._permission_decision_cache = {}
        self._clear_log_view()
        self.status_label.config(text="Running")
        self._append_log(run_mode_label)
        self._append_log("RUNNING: starting worker thread...")
        self._start_run_watchdog()
        self._start_worker_bootstrap_probe()
        followup = str(continue_update or "").strip()
        if followup:
            self._append_log("CONTINUE PROMPT BEGIN")
            for row in followup.splitlines():
                line = str(row or "").rstrip()
                if line:
                    self._append_log(f"CONTINUE PROMPT: {line}")
            self._append_log("CONTINUE PROMPT END")

        def job():
            try:
                self.log("RUNNING: worker thread started, entering pipeline loop...")
                pipeline_run(
                    prompt,
                    continue_update=continue_update,
                    logger=self.log,
                    stop_checker=lambda: self.stop_flag,
                    model_timeout_handler=self.handle_model_timeout,
                    rescue_decider=self.handle_rescue_request,
                    max_iterations_handler=self.handle_iteration_limit,
                    permission_decider=self.handle_permission_request,
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
            self._refresh_resume_state(load_prompt=False, write_log=False)
            self._save_gui_settings()

    def start_continue_run(self):
        followup_prompt = self.prompt_text.get("1.0", "end").strip()
        if not followup_prompt:
            messagebox.showwarning("Missing continue prompt", "Enter a small follow-up instruction first.")
            return
        base_prompt = str(self._session_base_prompt or "").strip()
        if not base_prompt:
            self._refresh_resume_state(load_prompt=False, write_log=False)
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
            self._update_session_anchor()
            self._refresh_resume_state(load_prompt=False, write_log=False)

    def start_resume_run(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is already running.")
            return
        summary = self._refresh_resume_state(load_prompt=False, write_log=False)
        base_prompt = str(summary.get("base_prompt") or "").strip()
        if not base_prompt:
            messagebox.showwarning("Resume unavailable", "No previous session prompt found. Use Start first.")
            return
        followup_prompt = self.prompt_text.get("1.0", "end").strip()
        if not followup_prompt:
            followup_prompt = "Continue from current session state. Finish remaining work only."
        started = self._launch_pipeline(
            base_prompt,
            run_mode_label="MODE: run_resume",
            continue_update=followup_prompt,
        )
        if started:
            self.log("RESUME MODE: continuing from last session snapshot/journal")
            self._refresh_resume_state(load_prompt=False, write_log=False)

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
        except Exception as exc:
            self._warn_internal("clear runtime session failed", exc)
        config.ACTIVE_PROJECT_ROOT = ""
        self._session_base_prompt = ""
        self._saved_session_base_prompt = ""
        self._update_session_anchor()
        self._refresh_resume_state(load_prompt=False, write_log=False)
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
        except Exception as exc:
            self._warn_internal("save settings on close failed", exc)
        self.root.destroy()


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
