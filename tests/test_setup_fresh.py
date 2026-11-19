#!/usr/bin/env python3
"""Fresh-install fixture for the round-2 (Codex r2) setup.cmd fix.

Review condition 1: escaping the parens was proven line-by-line; this test
runs the ACTUAL setup.cmd end-to-end in an isolated copy of the repo with no
.env files (what the brother's PC looks like on day one) and asserts that
setup reaches the installation check, creates BOTH config files, and never
dies with a cmd parser error.

Set BROTHER_SETUP_SOURCE=main to build the fixture from the pre-fix baseline
(red-on-baseline run — the baseline test then EXPECTS the abort). No network
calls of consequence: libraries are already installed on the dev box, so pip
is a satisfied no-op; on a truly fresh PC step 2 downloads packages, which is
the real setup doing its job. Run:  python tests\\test_setup_fresh.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get("BROTHER_SETUP_SOURCE", "WORKING")

EXCLUDE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc",
    ".env",           # NEVER copy the dev box's real key files into a fixture
    "output", "inbox", "index.html", "digest.json", "watch.lock",
)


def setup_cmd_source() -> str | None:
    if SOURCE == "WORKING":
        return None
    blob = subprocess.run(["git", "-C", str(REPO), "show",
                           f"{SOURCE}:setup.cmd"],
                          capture_output=True, text=True, check=True)
    return blob.stdout


def build_fixture(tmp: Path) -> Path:
    fx = tmp / "fx"
    shutil.copytree(REPO, fx, ignore=EXCLUDE)
    baseline = setup_cmd_source()
    if baseline is not None:
        (fx / "setup.cmd").write_text(baseline, encoding="utf-8")
    return fx


class TestFreshInstallReachesCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = build_fixture(Path(self.tmp.name))
        # precondition: a fresh install has no key files, and the fixture
        # must not have inherited the dev box's real ones
        self.assertFalse((self.fx / "transcriber" / ".env").exists(),
                         "fixture must be fresh: no transcriber/.env")
        self.assertFalse((self.fx / "news" / ".env").exists(),
                         "fixture must be fresh: no news/.env")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_setup(self) -> subprocess.CompletedProcess:
        # Mirror the brother's environment AFTER he installed Python with
        # "Add python.exe to PATH": the interpreter dir must be on PATH, or
        # setup.cmd would stop at the Python check before ever reaching the
        # blocks under test.
        env = {**os.environ}
        py_dir = Path(sys.executable).parent
        env["PATH"] = f"{py_dir};{env.get('PATH', '')}"
        return subprocess.run(
            ["cmd", "/c", "setup.cmd"], cwd=str(self.fx),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            errors="replace", timeout=300, env=env,
        )

    def test_setup_reaches_the_checker_and_creates_both_env_files(self):
        r = self._run_setup()
        out = r.stdout + r.stderr
        self.assertIn(r.returncode, (0, 1),
                      f"setup must end at the checker, not a parser crash "
                      f"(rc={r.returncode}); output:\n{out[-2000:]}")
        self.assertNotIn("was unexpected", out,
                         "a cmd syntax error means an echo line still breaks the block")
        self.assertIn("Running the installation check", out,
                      "the installation check must actually run")
        self.assertIn("Setup ", out, "the checker's verdict line must print")
        self.assertTrue((self.fx / "transcriber" / ".env").exists(),
                        "the transcriber .env must be created from its example")
        self.assertTrue((self.fx / "news" / ".env").exists(),
                        "the news .env must be created from its example")
        self.assertIn("Zhipu BigModel (transcription)", out,
                      "the guidance must render its parentheses")
        self.assertIn("(optional; without it", out,
                      "the news guidance must render its parentheses too")

    @unittest.skipUnless(SOURCE != "WORKING",
                         "baseline mode only: BROTHER_SETUP_SOURCE=main")
    def test_baseline_exhibits_the_setup_abort(self):
        r = self._run_setup()
        out = r.stdout + r.stderr
        self.assertIn("was unexpected", out,
                      "the baseline must show the cmd syntax error")
        self.assertFalse((self.fx / "news" / ".env").exists(),
                         "the baseline dies before creating the news .env")


if __name__ == "__main__":
    unittest.main(verbosity=2)
