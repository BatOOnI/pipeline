import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
from agent_loop import run as pipeline_run
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
    def __init__(self, root):
        self.root = root
        self.root.title("Pipeline GUI v2")
        self.root.geometry("1100x820")

        self.worker = None
        self.stop_flag = False
        self.timeout_dialog = None
        self.timeout_dialog_event = None
        self.settings_path = os.path.abspath(os.path.join(os.getcwd(), ".agent", "gui_settings.json"))
        self._saved_prompt_text = ""

        self._build_vars()
        self._load_gui_settings()
        self._build_ui()
        self._apply_loaded_prompt()
        self._bind_events()
        self._refresh_prompt_metrics()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_vars(self):
        self.provider_var = tk.StringVar(value=config.PROVIDER)
        self.lm_url_var = tk.StringVar(value=config.LMSTUDIO_URL)
        self.lm_model_var = tk.StringVar(value=config.LMSTUDIO_MODEL)
        self.oa_model_var = tk.StringVar(value=config.OPENAI_MODEL)
        self.oa_key_var = tk.StringVar(value=config.OPENAI_API_KEY)
        self.project_root_var = tk.StringVar(value=config.PROJECT_ROOT)
        self.max_iter_var = tk.StringVar(value=str(config.MAX_ITERATIONS))
        self.run_timeout_var = tk.StringVar(value=str(config.RUN_TIMEOUT))
        self.model_timeout_var = tk.StringVar(value=str(config.MODEL_TIMEOUT))
        self.auto_run_var = tk.BooleanVar(value=config.AUTO_RUN_COMMANDS)

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
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="both", expand=False, padx=8, pady=8)

        pipeline_box = ttk.LabelFrame(top, text="Pipeline")
        pipeline_box.pack(fill="x")
        pipeline_box.columnconfigure(3, weight=1)
        pipeline_box.columnconfigure(5, weight=1)

        ttk.Label(pipeline_box, text="Prompt").grid(row=0, column=0, sticky="w", **pad)
        self.prompt_text = tk.Text(pipeline_box, height=7, wrap="word")
        self.prompt_text.grid(row=1, column=0, columnspan=6, sticky="nsew", **pad)

        metrics = ttk.Frame(pipeline_box)
        metrics.grid(row=2, column=0, columnspan=6, sticky="we", **pad)
        ttk.Label(metrics, text="Prompt chars").pack(side="left")
        ttk.Entry(metrics, textvariable=self.prompt_chars_var, width=10, state="readonly").pack(side="left", padx=(4, 12))
        ttk.Label(metrics, text="Estimated tokens").pack(side="left")
        ttk.Entry(metrics, textvariable=self.estimated_tokens_var, width=10, state="readonly").pack(side="left", padx=(4, 12))
        ttk.Label(metrics, text="Prompt chars limit").pack(side="left")
        ttk.Entry(metrics, textvariable=self.prompt_char_limit_var, width=10).pack(side="left", padx=(4, 12))
        ttk.Label(metrics, text="Max output tokens").pack(side="left")
        ttk.Entry(metrics, textvariable=self.max_output_tokens_var, width=10).pack(side="left", padx=(4, 12))

        ttk.Label(pipeline_box, text="Provider").grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(
            pipeline_box,
            textvariable=self.provider_var,
            values=["lmstudio", "openai"],
            width=12,
            state="readonly",
        ).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(pipeline_box, text="LM URL").grid(row=3, column=2, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.lm_url_var, width=42).grid(row=3, column=3, sticky="we", **pad)

        ttk.Label(pipeline_box, text="LM Model").grid(row=3, column=4, sticky="w", **pad)
        self.lm_model_combo = ttk.Combobox(pipeline_box, textvariable=self.lm_model_var, width=24)
        self.lm_model_combo.grid(row=3, column=5, sticky="we", **pad)

        ttk.Label(pipeline_box, text="OpenAI Model").grid(row=4, column=0, sticky="w", **pad)
        self.oa_model_combo = ttk.Combobox(pipeline_box, textvariable=self.oa_model_var, width=18)
        self.oa_model_combo.grid(row=4, column=1, sticky="we", **pad)

        ttk.Label(pipeline_box, text="OpenAI Key").grid(row=4, column=2, sticky="w", **pad)
        key_row = ttk.Frame(pipeline_box)
        key_row.grid(row=4, column=3, sticky="we", **pad)
        key_row.columnconfigure(0, weight=1)
        ttk.Entry(key_row, textvariable=self.oa_key_var, width=34, show="*").grid(row=0, column=0, sticky="we")
        ttk.Button(key_row, text="Save API Key", command=self.save_api_key).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(pipeline_box, text="Project root").grid(row=4, column=4, sticky="w", **pad)
        project_root_row = ttk.Frame(pipeline_box)
        project_root_row.grid(row=4, column=5, sticky="we", **pad)
        project_root_row.columnconfigure(0, weight=1)
        ttk.Entry(project_root_row, textvariable=self.project_root_var, width=24).grid(row=0, column=0, sticky="we")
        ttk.Button(project_root_row, text="Browse", command=self.browse_project_root).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(pipeline_box, text="Patch files").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.patch_files_var, width=40).grid(row=5, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(pipeline_box, text="Patch snippet lines").grid(row=5, column=3, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.patch_snippet_lines_var, width=10).grid(row=5, column=4, sticky="w", **pad)

        ttk.Label(pipeline_box, text="Max iterations").grid(row=6, column=0, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.max_iter_var, width=8).grid(row=6, column=1, sticky="w", **pad)

        ttk.Label(pipeline_box, text="Run timeout").grid(row=6, column=2, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.run_timeout_var, width=8).grid(row=6, column=3, sticky="w", **pad)

        ttk.Label(pipeline_box, text="Model timeout").grid(row=6, column=4, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.model_timeout_var, width=8).grid(row=6, column=5, sticky="w", **pad)

        ttk.Checkbutton(pipeline_box, text="Auto run commands", variable=self.auto_run_var).grid(row=7, column=0, columnspan=2, sticky="w", **pad)

        btn_row = ttk.Frame(pipeline_box)
        btn_row.grid(row=8, column=0, columnspan=6, sticky="we", **pad)

        self.status_label = ttk.Label(btn_row, text="Idle")
        self.status_label.pack(side="left", padx=6)
        ttk.Button(btn_row, text="Fetch Models", command=self.fetch_models).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Start", command=self.start_pipeline).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Stop", command=self.stop_pipeline).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Open Log", command=self.open_log).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Open Project Folder", command=self.open_project_folder).pack(side="left", padx=6)

        git_box = ttk.LabelFrame(top, text="Git")
        git_box.pack(fill="x", pady=(8, 0))

        ttk.Label(git_box, text="Repo dir").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(git_box, textvariable=self.repo_dir_var, width=70).grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(git_box, text="Browse", command=self.browse_repo_dir).grid(row=0, column=4, sticky="w", **pad)

        ttk.Label(git_box, text="Remote URL").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(git_box, textvariable=self.remote_url_var, width=70).grid(row=1, column=1, columnspan=4, sticky="we", **pad)

        ttk.Label(git_box, text="Commit msg").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(git_box, textvariable=self.commit_msg_var, width=28).grid(row=2, column=1, sticky="we", **pad)

        ttk.Label(git_box, text="Tag").grid(row=2, column=2, sticky="w", **pad)
        ttk.Entry(git_box, textvariable=self.tag_var, width=20).grid(row=2, column=3, sticky="we", **pad)

        git_btns = ttk.Frame(git_box)
        git_btns.grid(row=3, column=0, columnspan=5, sticky="w", **pad)
        ttk.Button(git_btns, text="Init Git", command=self.git_init_repo).pack(side="left", padx=6)
        ttk.Button(git_btns, text="Commit", command=self.git_commit_repo).pack(side="left", padx=6)
        ttk.Button(git_btns, text="Push", command=self.git_push_repo).pack(side="left", padx=6)
        ttk.Button(git_btns, text="Tag + Push", command=self.git_tag_repo).pack(side="left", padx=6)

        log_box = ttk.LabelFrame(self.root, text="Log")
        log_box.pack(fill="both", expand=True, padx=8, pady=8)

        self.log_text = tk.Text(log_box, wrap="word")
        self.log_text.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        scroll.pack(fill="y", side="right")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _bind_events(self):
        self.prompt_text.bind("<<Modified>>", self._on_prompt_modified)

    def _on_prompt_modified(self, _event=None):
        self.prompt_text.edit_modified(False)
        self._refresh_prompt_metrics()

    def _refresh_prompt_metrics(self):
        prompt = self.prompt_text.get("1.0", "end-1c")
        self.prompt_chars_var.set(str(len(prompt)))
        self.estimated_tokens_var.set(str(approx_token_count(prompt)))

    def _normalize_project_root(self, path):
        path = str(path or "").strip()
        if not path:
            return ""
        return os.path.abspath(os.path.normpath(path))

    def log(self, msg):
        self.root.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg):
        self.log_text.insert("end", str(msg) + "\n")
        self.log_text.see("end")

    def apply_config(self):
        config.PROVIDER = self.provider_var.get().strip()
        config.LMSTUDIO_URL = self.lm_url_var.get().strip()
        config.LMSTUDIO_MODEL = self.lm_model_var.get().strip()
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
        self._saved_prompt_text = str(data.get("last_prompt", "") or "")

    def _save_gui_settings(self):
        prompt_value = self._saved_prompt_text
        if hasattr(self, "prompt_text"):
            prompt_value = self.prompt_text.get("1.0", "end-1c")
            self._saved_prompt_text = prompt_value
        payload = {
            "openai_api_key": self.oa_key_var.get().strip(),
            "last_prompt": prompt_value,
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
        self.log("OpenAI API key saved to local GUI settings.")

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
        openai_key = self.oa_key_var.get().strip()
        self.status_label.config(text="Fetching models...")

        def job():
            if provider == "openai" and not openai_key:
                self.root.after(0, lambda: self._finish_fetch_models(provider, None, "OpenAI API key is missing. Enter a key and click 'Save API Key'."))
                return
            try:
                models = list_models(
                    provider_override=provider,
                    lmstudio_url=lm_url,
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

    def start_pipeline(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is already running.")
            return

        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Missing prompt", "Enter a task first.")
            return

        self.apply_config()
        self._save_gui_settings()
        self.stop_flag = False
        self.log_text.delete("1.0", "end")
        self.status_label.config(text="Running")

        def job():
            try:
                pipeline_run(
                    prompt,
                    logger=self.log,
                    stop_checker=lambda: self.stop_flag,
                    model_timeout_handler=self.handle_model_timeout,
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

    def stop_pipeline(self):
        self.stop_flag = True
        self.status_label.config(text="Stopping...")

    def open_log(self):
        open_path(os.path.join(os.getcwd(), config.LOG_FILE))

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
