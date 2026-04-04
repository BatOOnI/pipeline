import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
from agent_loop import run as pipeline_run
from git_tools import (
    git_init, git_add_all, git_commit, git_branch_main, git_set_remote,
    git_push, git_tag, git_set_identity
)
from utils import ensure_gitignore, open_path

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Pipeline GUI v2")
        self.root.geometry("980x760")

        self.worker = None
        self.stop_flag = False

        self._build_vars()
        self._build_ui()

    def _build_vars(self):
        self.provider_var = tk.StringVar(value=config.PROVIDER)
        self.lm_url_var = tk.StringVar(value=config.LMSTUDIO_URL)
        self.lm_model_var = tk.StringVar(value=config.LMSTUDIO_MODEL)
        self.oa_model_var = tk.StringVar(value=config.OPENAI_MODEL)
        self.oa_key_var = tk.StringVar(value=config.OPENAI_API_KEY)
        self.project_root_var = tk.StringVar(value=config.PROJECT_ROOT)
        self.max_iter_var = tk.StringVar(value=str(config.MAX_ITERATIONS))
        self.run_timeout_var = tk.StringVar(value=str(config.RUN_TIMEOUT))
        self.auto_run_var = tk.BooleanVar(value=config.AUTO_RUN_COMMANDS)

        self.repo_dir_var = tk.StringVar(value=os.getcwd())
        self.remote_url_var = tk.StringVar(value=config.DEFAULT_REMOTE_URL)
        self.commit_msg_var = tk.StringVar(value=config.DEFAULT_COMMIT_MESSAGE)
        self.tag_var = tk.StringVar(value=config.DEFAULT_TAG)
        self.git_name_var = tk.StringVar(value="")
        self.git_email_var = tk.StringVar(value="")

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=8)

        pipeline_box = ttk.LabelFrame(top, text="Pipeline")
        pipeline_box.pack(fill="x")

        ttk.Label(pipeline_box, text="Prompt").grid(row=0, column=0, sticky="w", **pad)
        self.prompt_text = tk.Text(pipeline_box, height=6, wrap="word")
        self.prompt_text.grid(row=1, column=0, columnspan=6, sticky="nsew", **pad)

        ttk.Label(pipeline_box, text="Provider").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(pipeline_box, textvariable=self.provider_var, values=["lmstudio", "openai"], width=12, state="readonly").grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(pipeline_box, text="LM URL").grid(row=2, column=2, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.lm_url_var, width=42).grid(row=2, column=3, sticky="we", **pad)

        ttk.Label(pipeline_box, text="LM Model").grid(row=2, column=4, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.lm_model_var, width=24).grid(row=2, column=5, sticky="we", **pad)

        ttk.Label(pipeline_box, text="OpenAI Model").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.oa_model_var, width=18).grid(row=3, column=1, sticky="we", **pad)

        ttk.Label(pipeline_box, text="OpenAI Key").grid(row=3, column=2, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.oa_key_var, width=42, show="*").grid(row=3, column=3, sticky="we", **pad)

        ttk.Label(pipeline_box, text="Project root").grid(row=3, column=4, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.project_root_var, width=24).grid(row=3, column=5, sticky="we", **pad)

        ttk.Label(pipeline_box, text="Max iterations").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.max_iter_var, width=8).grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(pipeline_box, text="Run timeout").grid(row=4, column=2, sticky="w", **pad)
        ttk.Entry(pipeline_box, textvariable=self.run_timeout_var, width=8).grid(row=4, column=3, sticky="w", **pad)

        ttk.Checkbutton(pipeline_box, text="Auto run commands", variable=self.auto_run_var).grid(row=4, column=4, columnspan=2, sticky="w", **pad)

        btn_row = ttk.Frame(pipeline_box)
        btn_row.grid(row=5, column=0, columnspan=6, sticky="we", **pad)

        self.status_label = ttk.Label(btn_row, text="Idle")
        self.status_label.pack(side="left", padx=6)
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
        config.PROJECT_ROOT = self.project_root_var.get().strip() or "TEST"
        config.MAX_ITERATIONS = int(self.max_iter_var.get().strip() or "10")
        config.RUN_TIMEOUT = int(self.run_timeout_var.get().strip() or "15")
        config.AUTO_RUN_COMMANDS = bool(self.auto_run_var.get())

    def start_pipeline(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Pipeline is already running.")
            return

        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Missing prompt", "Enter a task first.")
            return

        self.apply_config()
        self.stop_flag = False
        self.log_text.delete("1.0", "end")
        self.status_label.config(text="Running")

        def job():
            try:
                pipeline_run(prompt, logger=self.log, stop_checker=lambda: self.stop_flag)
                self.log("PIPELINE FINISHED")
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
        open_path(os.path.join(os.getcwd(), config.PROJECT_ROOT))

    def browse_repo_dir(self):
        path = filedialog.askdirectory(initialdir=self.repo_dir_var.get() or os.getcwd())
        if path:
            self.repo_dir_var.set(path)

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

def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
