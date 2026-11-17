"""`clipper update`: pull the latest Clipper from GitHub into this install, then refresh whatever the new version
needs: Python packages when pyproject.toml changed, the renderer's packages when remotion/package*.json changed,
and the Claude Code skills when they were installed. Your streams, clips and settings (jobs/, config.json) are
not part of the repo, so they're never touched."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .common import ROOT, tool


def _git(*args, check=True):
    p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{(p.stderr or p.stdout).strip()}")
    return p.stdout.rstrip("\n")         # keep leading spaces: `status --porcelain` lines start with one


def _skills_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"


def sync_skills(quiet=False) -> list:
    """Copy skills/<name>/SKILL.md into Claude Code, if the user installed Clipper's skills (the /clip skill is
    there). New skills that came with an update are added too."""
    dst_root = _skills_dir()
    if not (dst_root / "clip" / "SKILL.md").exists():
        return []
    done = []
    for src in sorted((ROOT / "skills").glob("*/SKILL.md")):
        dst = dst_root / src.parent.name / "SKILL.md"
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        done.append(src.parent.name)
    if done and not quiet:
        print("Claude Code skills updated: " + ", ".join("/" + d for d in done))
    return done


def _pip_install():
    if shutil.which("uv"):
        cmd = ["uv", "pip", "install", "-q", "-p", sys.executable, "-e", str(ROOT)]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-q", "-e", str(ROOT)]
    extras = []
    try:                      # keep speech-to-text if it was installed
        import sherpa_onnx  # noqa: F401
        extras = ["sherpa-onnx>=1.12"]
    except ImportError:
        pass
    print("Updating Python packages...")
    subprocess.run(cmd + extras, check=True)


def _npm_install():
    rem = ROOT / "remotion"
    if not (rem / "node_modules").is_dir():
        return                # the renderer was never set up; leave it to the installer
    print("Updating the Short renderer's packages...")
    subprocess.run([tool("npm"), "ci", "--no-audit", "--no-fund", "--loglevel=error"], cwd=rem, check=True)


def update():
    if not (ROOT / ".git").exists():
        raise SystemExit(f"{ROOT} isn't a git copy of Clipper, so it can't update itself. Run the installer again.")
    if not shutil.which("git"):
        raise SystemExit("git isn't installed. Install it and run `clipper update` again.")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit("These Clipper files were changed on this computer, so the update would overwrite them:\n"
                         + "\n".join("  " + l[3:] for l in dirty.splitlines())
                         + "\nKeep the changes somewhere else, then run `git -C \"" + str(ROOT) + "\" checkout -- .` "
                         "and `clipper update` again.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise SystemExit("This copy isn't on a branch. Run `git -C \"" + str(ROOT) + "\" checkout main` first.")
    old = _git("rev-parse", "HEAD")
    print(f"Checking GitHub for a newer Clipper ({branch})...")
    _git("fetch", "--quiet", "origin", branch)
    behind = _git("rev-list", "--count", f"HEAD..origin/{branch}")
    if behind == "0":
        print("Clipper is up to date.")
        sync_skills()
        return
    merged = subprocess.run(["git", "-C", str(ROOT), "merge", "--ff-only", "--quiet", f"origin/{branch}"],
                            capture_output=True, text=True)
    if merged.returncode != 0:
        raise SystemExit("This copy has its own commits, so it can't simply move to the GitHub version:\n"
                         + merged.stderr.strip())
    new = _git("rev-parse", "HEAD")
    print(f"Updated {old[:7]} -> {new[:7]}:")
    print(_git("log", "--format=  %h %s", f"{old}..{new}"))
    changed = set(_git("diff", "--name-only", old, new).splitlines())
    if "pyproject.toml" in changed:
        _pip_install()
    if changed & {"remotion/package.json", "remotion/package-lock.json"}:
        _npm_install()
    sync_skills()
    if any(f.startswith("clipper/") for f in changed):
        print("If the dashboard is open, stop it (Ctrl+C) and run `clipper serve` again to use the new version.")
    print("Done.")
