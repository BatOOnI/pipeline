import os
import subprocess
from typing import Tuple


def _run_git(args, cwd=None, timeout=20) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
        out = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


def git_is_repo(repo_dir):
    ok, msg = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir)
    return ok and msg.strip().lower() == "true"


def git_init(repo_dir):
    os.makedirs(repo_dir, exist_ok=True)
    ok, msg = _run_git(["init"], cwd=repo_dir)
    if not ok:
        return ok, msg
    ok2, msg2 = _run_git(["branch", "-M", "main"], cwd=repo_dir)
    joined = "\n".join(x for x in [msg, msg2] if x)
    return ok and ok2, joined.strip()


def git_set_identity(repo_dir, name=None, email=None):
    msgs = []
    ok = True
    if name:
        a, m = _run_git(["config", "user.name", name], cwd=repo_dir)
        ok &= a
        msgs.append(m)
    if email:
        a, m = _run_git(["config", "user.email", email], cwd=repo_dir)
        ok &= a
        msgs.append(m)
    return ok, "\n".join(x for x in msgs if x)


def git_add_all(repo_dir):
    return _run_git(["add", "."], cwd=repo_dir)


def git_commit(repo_dir, message):
    return _run_git(["commit", "-m", message], cwd=repo_dir)


def git_branch_main(repo_dir):
    return _run_git(["branch", "-M", "main"], cwd=repo_dir)


def git_set_remote(repo_dir, remote_url):
    ok, _ = _run_git(["remote", "get-url", "origin"], cwd=repo_dir)
    if ok:
        return _run_git(["remote", "set-url", "origin", remote_url], cwd=repo_dir)
    return _run_git(["remote", "add", "origin", remote_url], cwd=repo_dir)


def git_push(repo_dir):
    return _run_git(["push", "-u", "origin", "main"], cwd=repo_dir, timeout=120)


def git_tag(repo_dir, tag_name):
    ok1, msg1 = _run_git(["tag", tag_name], cwd=repo_dir)
    ok2, msg2 = _run_git(["push", "origin", tag_name], cwd=repo_dir, timeout=120)
    return (ok1 and ok2), "\n".join(x for x in [msg1, msg2] if x)


def git_has_committable_changes(repo_dir):
    ok, msg = _run_git(["status", "--porcelain"], cwd=repo_dir)
    return ok, msg.strip()


def git_checkpoint(repo_dir, message):
    if not git_is_repo(repo_dir):
        ok, msg = git_init(repo_dir)
        if not ok:
            return False, msg

    ok, msg = git_add_all(repo_dir)
    if not ok:
        return False, msg

    ok, status = git_has_committable_changes(repo_dir)
    if not ok:
        return False, status
    if not status:
        return True, "No git changes to commit"

    return git_commit(repo_dir, message)


def git_restore(repo_dir, ref="HEAD"):
    return _run_git(["restore", "--source", ref, "--worktree", "--staged", "."], cwd=repo_dir)
