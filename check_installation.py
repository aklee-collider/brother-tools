#!/usr/bin/env python3
"""Check installation for both tools — the "does everything work?" button.

Zero network calls, zero API cost: this validates the local machine only
(Python, libraries, ffmpeg, keys present, config files parse, folders
writable). Every problem prints a [FIX] line in plain language; the exit
code is 0 only when nothing needs fixing.

Run: double-click check.cmd, or:  python check_installation.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON_MIN = (3, 9)

# (module needed, what it is for, which requirements file installs it)
PY_MODULES = {
    "requests": ("transcriber: talking to the transcription API",
                 "transcriber/requirements.txt"),
    "dotenv": ("both tools: reading your API keys from .env files",
               "transcriber/requirements.txt + news/requirements.txt"),
}


class Check:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []
        # (tool, item, ok, fix hint) — fix hint printed only when not ok

    def add(self, tool: str, item: str, ok: bool, fix: str = "") -> None:
        self.rows.append((tool, item, ok, fix))

    @property
    def failures(self) -> list[tuple[str, str, str]]:
        return [(t, i, f) for t, i, ok, f in self.rows if not ok]


def check_python(c: Check, root: Path) -> None:
    if sys.version_info >= PYTHON_MIN:
        c.add("both", f"Python {sys.version_info.major}.{sys.version_info.minor}", True)
    else:
        c.add("both", "Python version", False,
              f"Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ is required (found "
              f"{sys.version_info.major}.{sys.version_info.minor}). Install from "
              "https://www.python.org/downloads/ and tick \"Add python.exe to PATH\".")


def check_modules(c: Check, root: Path) -> None:
    import importlib.util
    for mod, (purpose, reqs) in PY_MODULES.items():
        ok = importlib.util.find_spec(mod) is not None
        c.add("both", f"library '{mod}' ({purpose})", ok,
              f"run setup.cmd, or: python -m pip install -r {reqs}")


def check_ffmpeg(c: Check, root: Path) -> None:
    bundled = root / "transcriber" / "ffmpeg"
    found = shutil.which("ffmpeg") or (
        shutil.which("ffmpeg", path=str(bundled)) if bundled.is_dir() else None)
    c.add("transcriber", "ffmpeg (splits long recordings for the API)", found is not None,
          "open PowerShell and run:  winget install --id Gyan.FFmpeg -e\n"
          "      (or put ffmpeg.exe + ffprobe.exe into transcriber\\ffmpeg\\)")


def _env_state(path: Path, wanted_keys: list[str]) -> tuple[bool, list[str], str]:
    """(file exists, missing/empty keys, error detail). Values are NEVER returned."""
    if not path.exists():
        return False, wanted_keys, f"{path} does not exist yet"
    missing: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() in wanted_keys and v.strip():
            wanted_keys.remove(k.strip())
    return True, wanted_keys, ""


def check_env_files(c: Check, root: Path) -> None:
    # transcriber: the GLM key always runs transcription. Summaries run
    # through GLM when SUMMARY_MODEL is set (the one-key setup) or through
    # DeepSeek when its own key is pasted — either is a complete setup.
    tenv = root / "transcriber" / ".env"
    texample = root / "transcriber" / ".env.example"
    exists, missing, _ = _env_state(tenv, ["ZHIPU_API_KEY"])
    if not exists:
        c.add("transcriber", f"API keys file ({tenv.name})", False,
              f"copy {texample.name} to {tenv.name} (same folder) and paste "
              "your key(s) in — never share or commit that file")
    else:
        if missing:
            c.add("transcriber", f"API keys file ({tenv.name})", False,
                  f"missing or empty in {tenv.name}: {', '.join(missing)}")
        else:
            c.add("transcriber", f"API keys file ({tenv.name})", True)
        _, summ_missing, _ = _env_state(tenv, ["SUMMARY_MODEL", "DEEPSEEK_API_KEY"])
        if "SUMMARY_MODEL" in summ_missing and "DEEPSEEK_API_KEY" in summ_missing:
            c.add("transcriber", "summary provider (.env)", False,
                  "no summary provider in .env — either set "
                  "SUMMARY_MODEL=glm-5.3-flash (runs on the same GLM key) or "
                  "paste a DEEPSEEK_API_KEY (platform.deepseek.com)")
        elif "SUMMARY_MODEL" not in summ_missing:
            c.add("transcriber", "summary provider (GLM — one key, SUMMARY_MODEL)", True)
        else:
            c.add("transcriber", "summary provider (DeepSeek, DEEPSEEK_API_KEY)", True)

    # news: DeepSeek powers the AI summaries and is OPTIONAL — without any
    # key the briefing still works and shows raw headlines.
    nenv = root / "news" / ".env"
    nexample = root / "news" / ".env.example"
    exists, missing, _ = _env_state(nenv, ["DEEPSEEK_API_KEY"])
    if not exists:
        c.add("news", f"news keys file ({nenv.name})", False,
              f"copy {nexample.name} to {nenv.name} (same folder) — optional "
              "AI summaries; without a key the briefing shows raw headlines")
    elif missing:
        c.add("news", "news keys (raw-headlines mode — no DEEPSEEK_API_KEY, "
                      "which is optional)", True)
    else:
        c.add("news", "news keys (AI summaries on, DEEPSEEK_API_KEY)", True)


def check_configs(c: Check, root: Path) -> None:
    plans = [
        ("transcriber", root / "transcriber" / "projects.json",
         lambda d: isinstance(d, list) and bool(d) and all(
             isinstance(p, dict) and p.get("key") and p.get("name") for p in d),
         "list of {key, name, context} objects"),
        ("news", root / "news" / "categories.json",
         lambda d: isinstance(d, list) and bool(d) and all(
             isinstance(cat, dict) and cat.get("key") and cat.get("name")
             and cat.get("feeds") for cat in d),
         "list of {key, name, focus, feeds:[{name, url}]} objects"),
    ]
    for tool, path, is_valid, shape in plans:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            c.add(tool, f"config {path.name}", False, f"{path} is missing — restore it from the repo")
            continue
        except ValueError as exc:
            c.add(tool, f"config {path.name}", False,
                  f"not valid JSON ({exc}) — fix or restore from the repo")
            continue
        c.add(tool, f"config {path.name}", is_valid(data),
              f"expected {shape}")


def check_folders(c: Check, root: Path) -> None:
    folders = [("transcriber", root / "transcriber" / "inbox"),
               ("transcriber", root / "transcriber" / "output"),
               ("news", root / "news" / "output")]
    for tool, folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fd, probe_name = tempfile.mkstemp(dir=folder, suffix=".tmp")
            os.close(fd)  # mkstemp leaves the handle open — close before unlinking
            Path(probe_name).unlink()
            c.add(tool, f"folder {folder.name}\\ is writable", True)
        except OSError as exc:
            c.add(tool, f"folder {folder.name}\\ is writable", False, f"{exc}")


def run_checks(root: Path) -> Check:
    c = Check()
    check_python(c, root)
    check_modules(c, root)
    check_ffmpeg(c, root)
    check_env_files(c, root)
    check_configs(c, root)
    check_folders(c, root)
    return c


def main() -> int:
    print("Checking brother-tools installation (no network, no cost) ...\n")
    c = run_checks(HERE)
    last_tool = None
    for tool, item, ok, _fix in c.rows:
        if tool != last_tool:
            print(f"-- {tool} --")
            last_tool = tool
        print(f"  [{'OK' if ok else 'FIX'}] {item}")
    fails = c.failures
    if fails:
        print("\nTo fix:")
        for i, (tool, item, fix) in enumerate(fails, 1):
            print(f"  {i}. {item}\n      -> {fix}")
        print(f"\n{len(fails)} thing(s) need fixing. Re-run this check after fixing.")
        return 1
    print("\nEverything is set up. transcriber: drag a recording onto "
          "transcriber\\transcribe.cmd (or run watch.cmd). news: run news\\refresh.cmd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
