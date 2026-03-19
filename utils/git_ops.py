"""
Git operations for PR Debug Analyst.
Handles parsing PR links, cloning repos, and checking out PRs.
"""
import os
import re
import subprocess
from typing import Optional
from dataclasses import dataclass

from utils.display import (
    info, success, error, warning, diminfo,
    user_prompt, progress_spinner, progress_done,
    thinking_start, tool_use, tool_result,
)


@dataclass
class PRInfo:
    """Parsed information from a PR link."""
    host: str           # github.com, gitlab.com, etc.
    owner: str          # org or user
    repo: str           # repository name
    pr_number: str      # PR / MR number
    full_url: str       # original URL
    clone_url_https: str
    clone_url_ssh: str

    @property
    def repo_dir_name(self) -> str:
        return self.repo


def parse_pr_link(pr_link: str) -> Optional[PRInfo]:
    """
    Parse a PR/MR link and extract owner, repo, PR number.
    Supports GitHub, GitLab, and Bitbucket.
    """
    patterns = [
        # GitHub: https://github.com/owner/repo/pull/123
        (
            r"https?://(?P<host>github\.com)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)",
            "github",
        ),
        # GitLab: https://gitlab.com/owner/repo/-/merge_requests/123
        (
            r"https?://(?P<host>gitlab\.com)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/-/merge_requests/(?P<num>\d+)",
            "gitlab",
        ),
        # Bitbucket: https://bitbucket.org/owner/repo/pull-requests/123
        (
            r"https?://(?P<host>bitbucket\.org)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull-requests/(?P<num>\d+)",
            "bitbucket",
        ),
    ]

    for pattern, platform in patterns:
        m = re.match(pattern, pr_link.strip())
        if m:
            host = m.group("host")
            owner = m.group("owner")
            repo = m.group("repo")
            num = m.group("num")

            return PRInfo(
                host=host,
                owner=owner,
                repo=repo,
                pr_number=num,
                full_url=pr_link.strip(),
                clone_url_https=f"https://{host}/{owner}/{repo}.git",
                clone_url_ssh=f"git@{host}:{owner}/{repo}.git",
            )

    return None


def _run_git(args: list[str], cwd: str = ".", timeout: int = 300) -> tuple[int, str]:
    """Run a git command and return (return_code, output)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += "\n" + result.stderr.strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return -1, "Git command timed out"
    except FileNotFoundError:
        return -1, "git is not installed or not in PATH"
    except Exception as e:
        return -1, str(e)


def _run_gh(args: list[str], cwd: str = ".", timeout: int = 60) -> tuple[int, str]:
    """Run a gh CLI command and return (return_code, output)."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += "\n" + result.stderr.strip()
        return result.returncode, output
    except FileNotFoundError:
        return -1, "gh CLI not found"
    except Exception as e:
        return -1, str(e)


def _get_pr_branch(pr_info: PRInfo, repo_path: str) -> Optional[str]:
    """
    Get the branch name for a PR using gh CLI or git.
    Returns the branch name or None.
    """
    # Try gh CLI first (most reliable)
    rc, out = _run_gh(
        ["pr", "view", pr_info.pr_number, "--json", "headRefName", "-q", ".headRefName"],
        cwd=repo_path,
    )
    if rc == 0 and out.strip():
        return out.strip()

    # Fallback: fetch the PR ref directly (GitHub convention)
    if "github.com" in pr_info.host:
        return f"pull/{pr_info.pr_number}/head"

    return None


def setup_project_from_pr(pr_link: str) -> Optional[str]:
    """
    Main entry: Given a PR link, set up the project directory and return its path.

    Flow:
    1. Parse the PR link
    2. Ask user for parent directory
    3. If repo exists → warn, ask to reset HEAD
    4. If repo doesn't exist → clone it
    5. Checkout the PR branch
    6. Return the project path
    """
    # ── Parse PR link ──
    pr_info = parse_pr_link(pr_link)
    if not pr_info:
        warning("Could not parse repository info from the PR link.")
        info("Falling back to manual project path entry.")
        return None

    info(f"Detected: {pr_info.owner}/{pr_info.repo} PR #{pr_info.pr_number}")

    # ── Parent directory — default to home ──
    default_parent = os.path.expanduser("~")
    info(f"Where should the repo be cloned? Default: {default_parent}")

    while True:
        raw = user_prompt(f"Parent directory [{default_parent}]: ").strip()
        parent_dir = os.path.expanduser(raw) if raw else default_parent

        if os.path.isdir(parent_dir):
            break
        else:
            try:
                os.makedirs(parent_dir, exist_ok=True)
                success(f"Created: {parent_dir}")
                break
            except OSError:
                error(f"Cannot access or create: {parent_dir}")

    repo_path = os.path.join(parent_dir, pr_info.repo_dir_name)

    # ── Check if repo already exists ──
    if os.path.isdir(repo_path) and os.path.isdir(os.path.join(repo_path, ".git")):
        return _handle_existing_repo(repo_path, pr_info)
    elif os.path.isdir(repo_path):
        warning(f"Directory {repo_path} exists but is not a git repo.")
        info("Will clone into it after removing. Or choose a different parent.")
        choice = user_prompt("Remove and re-clone? (y/n): ").strip().lower()
        if choice not in ("y", "yes"):
            info("Aborting clone. Falling back to manual path entry.")
            return None
        import shutil
        shutil.rmtree(repo_path, ignore_errors=True)
        return _clone_and_checkout(parent_dir, pr_info)
    else:
        return _clone_and_checkout(parent_dir, pr_info)


def _handle_existing_repo(repo_path: str, pr_info: PRInfo) -> Optional[str]:
    """Handle the case where the repo already exists locally."""
    success(f"Repo already exists: {repo_path}")

    # Show current state
    rc, branch_out = _run_git(["branch", "--show-current"], cwd=repo_path)
    current_branch = branch_out.strip() if rc == 0 else "unknown"

    rc, status_out = _run_git(["status", "--porcelain"], cwd=repo_path)
    has_changes = bool(status_out.strip()) if rc == 0 else False

    info(f"Current branch: {current_branch}")
    if has_changes:
        warning("There are uncommitted changes in this repo.")

    # Warn and ask
    warning(f"To check out PR #{pr_info.pr_number}, we need to:")
    info("  1. Fetch latest from remote")
    info("  2. Reset HEAD to a clean state")
    info("  3. Checkout the PR branch")
    if has_changes:
        warning("  ⚠  Uncommitted changes will be LOST.")

    print()
    choice = user_prompt("Proceed with reset and checkout? (y/n): ").strip().lower()

    if choice in ("y", "yes"):
        return _reset_and_checkout(repo_path, pr_info)
    else:
        # User rejected — give them manual steps
        info("No problem. Here are the manual steps you can run:")
        print()
        pr_branch = _get_pr_branch(pr_info, repo_path)
        if pr_branch and pr_branch.startswith("pull/"):
            info(f"  git fetch origin pull/{pr_info.pr_number}/head:pr-{pr_info.pr_number}")
            info(f"  git checkout pr-{pr_info.pr_number}")
        elif pr_branch:
            info(f"  git fetch origin {pr_branch}")
            info(f"  git checkout {pr_branch}")
        else:
            info(f"  gh pr checkout {pr_info.pr_number}")
        print()

        choice2 = user_prompt("Press Enter when ready (or type 'skip' to use current state): ").strip().lower()
        if choice2 == "skip":
            info("Using repo in its current state.")
        else:
            info("Assuming you've checked out the PR manually.")

        return repo_path


def _reset_and_checkout(repo_path: str, pr_info: PRInfo) -> Optional[str]:
    """Reset the existing repo and checkout the PR."""
    thinking_start("preparing repository for PR checkout")

    # Step 1: Fetch
    tool_use("Git", "fetch origin")
    progress_spinner("Fetching latest from remote")
    rc, out = _run_git(["fetch", "origin", "--prune"], cwd=repo_path, timeout=120)
    progress_done()
    if rc != 0:
        tool_result("Git", "error", f"fetch failed: {out[:100]}")
        error(f"Git fetch failed: {out}")
        return repo_path  # still usable, just not updated

    tool_result("Git", "success", "fetched latest")

    # Step 2: Clean working tree
    tool_use("Git", "reset --hard HEAD")
    rc, _ = _run_git(["reset", "--hard", "HEAD"], cwd=repo_path)
    rc2, _ = _run_git(["clean", "-fd"], cwd=repo_path)
    tool_result("Git", "success", "working tree cleaned")

    # Step 3: Checkout PR
    return _checkout_pr(repo_path, pr_info)


def _clone_and_checkout(parent_dir: str, pr_info: PRInfo) -> Optional[str]:
    """Clone the repo and checkout the PR branch."""
    repo_path = os.path.join(parent_dir, pr_info.repo_dir_name)

    thinking_start(f"cloning {pr_info.owner}/{pr_info.repo}")

    # Try HTTPS first, then SSH
    tool_use("Git", f"clone {pr_info.clone_url_https}")
    progress_spinner(f"Cloning {pr_info.owner}/{pr_info.repo}")

    rc, out = _run_git(
        ["clone", pr_info.clone_url_https, pr_info.repo_dir_name],
        cwd=parent_dir,
        timeout=600,
    )

    if rc != 0:
        progress_done("HTTPS failed, trying SSH")
        diminfo(f"HTTPS clone failed: {out[:100]}")
        tool_use("Git", f"clone {pr_info.clone_url_ssh}")
        progress_spinner(f"Cloning via SSH")

        rc, out = _run_git(
            ["clone", pr_info.clone_url_ssh, pr_info.repo_dir_name],
            cwd=parent_dir,
            timeout=600,
        )

    progress_done()

    if rc != 0:
        tool_result("Git", "error", f"clone failed: {out[:100]}")
        error(f"Could not clone repository: {out}")
        return None

    tool_result("Git", "success", f"cloned to {repo_path}")

    # Checkout the PR
    return _checkout_pr(repo_path, pr_info)


def _checkout_pr(repo_path: str, pr_info: PRInfo) -> Optional[str]:
    """Checkout a specific PR in the given repo."""

    # Method 1: Try gh pr checkout (cleanest)
    tool_use("Git", f"checkout PR #{pr_info.pr_number}")
    progress_spinner(f"Checking out PR #{pr_info.pr_number}")

    rc, out = _run_gh(["pr", "checkout", pr_info.pr_number], cwd=repo_path, timeout=120)

    if rc == 0:
        progress_done()
        # Get the branch name we're on now
        _, branch = _run_git(["branch", "--show-current"], cwd=repo_path)
        tool_result("Git", "success", f"on branch: {branch.strip()}")
        success(f"Checked out PR #{pr_info.pr_number} → branch: {branch.strip()}")
        return repo_path

    # Method 2: Fetch PR ref directly (GitHub)
    if "github.com" in pr_info.host:
        local_branch = f"pr-{pr_info.pr_number}"
        rc, out = _run_git(
            ["fetch", "origin", f"pull/{pr_info.pr_number}/head:{local_branch}"],
            cwd=repo_path,
            timeout=120,
        )
        if rc == 0:
            rc2, _ = _run_git(["checkout", local_branch], cwd=repo_path)
            if rc2 == 0:
                progress_done()
                tool_result("Git", "success", f"on branch: {local_branch}")
                success(f"Checked out PR #{pr_info.pr_number} → branch: {local_branch}")
                return repo_path

    # Method 3: Try getting the branch name from the PR
    pr_branch = _get_pr_branch(pr_info, repo_path)
    if pr_branch and not pr_branch.startswith("pull/"):
        rc, _ = _run_git(["checkout", pr_branch], cwd=repo_path)
        if rc != 0:
            rc, _ = _run_git(["checkout", "-b", pr_branch, f"origin/{pr_branch}"], cwd=repo_path)
        if rc == 0:
            progress_done()
            tool_result("Git", "success", f"on branch: {pr_branch}")
            success(f"Checked out PR #{pr_info.pr_number} → branch: {pr_branch}")
            return repo_path

    progress_done()
    tool_result("Git", "warning", "could not auto-checkout PR branch")
    warning(f"Could not automatically checkout PR #{pr_info.pr_number}")
    info("The repo is cloned. Please checkout the PR branch manually:")
    info(f"  cd {repo_path}")
    info(f"  gh pr checkout {pr_info.pr_number}")
    print()

    choice = user_prompt("Press Enter when ready, or 'skip' to use default branch: ").strip().lower()
    return repo_path
