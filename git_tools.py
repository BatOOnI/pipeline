import subprocess
from typing import Tuple


def _run_git(args, cwd=None) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False
        )
        out = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


def git_init(repo_dir):
    return _run_git(["init"], cwd=repo_dir)


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
    return _run_git(["push", "-u", "origin", "main"], cwd=repo_dir)


def git_tag(repo_dir, tag_name):
    ok1, msg1 = _run_git(["tag", tag_name], cwd=repo_dir)
    ok2, msg2 = _run_git(["push", "origin", tag_name], cwd=repo_dir)
    return (ok1 and ok2), "\n".join(x for x in [msg1, msg2] if x)
