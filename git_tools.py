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


def _git_repo_context(repo_dir):
    target_root = os.path.abspath(repo_dir)
    branch = ""
    remote = ""
    repo_root = ""
    if git_is_repo(target_root):
        ok_root, root_msg = _run_git(["rev-parse", "--show-toplevel"], cwd=target_root)
        if ok_root and str(root_msg or "").strip():
            repo_root = os.path.abspath(str(root_msg).splitlines()[0].strip())
        ok_branch, branch_msg = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_root)
        if ok_branch and str(branch_msg or "").strip():
            branch = str(branch_msg).splitlines()[0].strip()
        ok_remote, remote_msg = _run_git(["remote", "get-url", "origin"], cwd=target_root)
        if ok_remote and str(remote_msg or "").strip():
            remote = str(remote_msg).splitlines()[0].strip()
    return {
        "target_root": target_root,
        "repo_root": repo_root,
        "branch": branch,
        "remote": remote,
    }


def git_checkpoint_context(repo_dir):
    return _git_repo_context(repo_dir)


def git_checkpoint(repo_dir, message):
    target_root = os.path.abspath(repo_dir)
    if os.path.isfile(target_root):
        return False, f"Refusing checkpoint: target is a file, not a repo root ({target_root})"
    os.makedirs(target_root, exist_ok=True)

    local_git_dir = os.path.isdir(os.path.join(target_root, ".git"))
    if local_git_dir:
        pass
    elif git_is_repo(target_root):
        context = _git_repo_context(target_root)
        repo_root = context.get("repo_root") or target_root
        if os.path.normcase(repo_root) != os.path.normcase(target_root):
            ok, msg = git_init(target_root)
            if not ok:
                return False, (
                    "Refusing checkpoint: repo root mismatch and failed to initialize local repo "
                    f"(target={target_root}, repo_root={repo_root}) -> {msg}"
                )
    else:
        ok, msg = git_init(target_root)
        if not ok:
            return False, msg

    ok, msg = git_add_all(target_root)
    if not ok:
        return False, msg

    ok, status = git_has_committable_changes(target_root)
    if not ok:
        return False, status

    context = _git_repo_context(target_root)
    context_lines = [
        f"repo_root={context.get('repo_root') or target_root}",
        f"branch={context.get('branch') or '(unknown)'}",
        f"remote={context.get('remote') or '(none)'}",
    ]
    if not status:
        return True, "\n".join(context_lines + ["No git changes to commit"])

    ok, commit_msg = git_commit(target_root, message)
    return ok, "\n".join(context_lines + ([commit_msg] if commit_msg else []))


def git_restore(repo_dir, ref="HEAD"):
    return _run_git(["restore", "--source", ref, "--worktree", "--staged", "."], cwd=repo_dir)
