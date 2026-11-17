#!/usr/bin/env python3
"""Offline test suite for brother-tools — no network, no API cost.

Every defect from the 2026-11-17 handoff-readiness review (Codex r1,
CHANGES-REQUIRED) has a negative control here: the test first shows the
BASELINE behavior (the exact old expression) failing the user, then proves
the fixed code handles the same injection. Run:

    python tests\\test_offline.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "transcriber"))
sys.path.insert(0, str(ROOT / "news"))

import asr_lib  # noqa: E402
import watch  # noqa: E402
import transcribe  # noqa: E402
import fetch_news  # noqa: E402
import check_installation  # noqa: E402


def make_wav(path: Path, seconds: float, rate: int = 8000) -> float:
    """Write a real wav file; return its exact duration."""
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return frames / rate


# ---------------------------------------------------------------- HIGH-1
class TestConversionIntegrity(unittest.TestCase):
    """Review finding 1: a failed ffmpeg conversion was accepted as a
    successful, silently incomplete transcript (asr_lib.py:143)."""

    def test_neg_ffmpeg_nonzero_exit_is_loud(self):
        # Baseline: `subprocess.run(...)` with the returncode ignored —
        # a failing encode scrolled on. Inject: rc=1 + one partial segment.
        with tempfile.TemporaryDirectory() as td:
            seg = Path(td) / "seg_000.wav"
            make_wav(seg, 5)
            fake = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"boom")
            with mock.patch.object(asr_lib.subprocess, "run", return_value=fake):
                with self.assertRaises(RuntimeError) as ctx:
                    asr_lib._run_ffmpeg(["ffmpeg", "-y"], timeout=10, what="unit test")
            self.assertIn("exit 1", str(ctx.exception))

    def test_neg_partial_conversion_rejected(self):
        # Inject: ffmpeg produced 2 x 5s segments of a 60s recording.
        with tempfile.TemporaryDirectory() as td:
            segs = [Path(td) / f"seg_{i:03d}.wav" for i in range(2)]
            for s in segs:
                make_wav(s, 5)
            with self.assertRaises(RuntimeError) as ctx:
                asr_lib._verify_conversion(segs, source_duration=60.0)
            self.assertIn("incomplete conversion", str(ctx.exception))
            self.assertIn("cover", str(ctx.exception))

    def test_neg_segment_not_decodable_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "seg_000.wav"
            make_wav(good, 5)
            bad = Path(td) / "seg_001.wav"
            bad.write_bytes(b"\x00" * 1024)  # right size, but not a wav — truncated write
            with self.assertRaises(RuntimeError) as ctx:
                asr_lib._verify_conversion([good, bad], source_duration=10.0)
            self.assertIn("readable wav", str(ctx.exception))

    def test_pos_full_conversion_passes(self):
        # Edge inside the boundary: 60s source, segments cover 59.5s —
        # within the 5% + 1s cut-boundary tolerance, must NOT raise.
        with tempfile.TemporaryDirectory() as td:
            segs = [Path(td) / f"seg_{i:03d}.wav" for i in range(2)]
            make_wav(segs[0], 29.0)
            make_wav(segs[1], 30.5)  # total 59.5s of a 60.0s source
            asr_lib._verify_conversion(segs, source_duration=60.0)  # no raise

    def test_neg_zero_segments_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            asr_lib._verify_conversion([], source_duration=30.0)
        self.assertIn("no audio segments", str(ctx.exception))

    def test_transcribe_surfaces_empty_segment_texts(self):
        # A segment that billed but returned no text used to vanish from the
        # transcript silently; now it is counted and reported.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 10)
            calls = {"n": 0}

            def fake_asr_segment(seg_path, model, key):
                calls["n"] += 1
                return "" if calls["n"] == 1 else "hello world"

            with mock.patch.object(asr_lib, "asr_segment", fake_asr_segment):
                result = asr_lib.transcribe(src, api_key="test-key")
            self.assertEqual(calls["n"], 1)
            self.assertEqual(result["segments_empty"], 1)
            self.assertEqual(result["transcript"], "")

    def test_transcribe_rejects_failing_conversion_end_to_end(self):
        # Full-path injection: ffmpeg exits 0 but writes only 1 segment for a
        # 90s recording (the "missing tail" defect class). Must raise, never
        # transcribe a fraction of the meeting as if it were whole.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "long.wav"
            make_wav(src, 90)

            def fake_ffmpeg(cmd, capture_output, timeout):
                tdir = Path(cmd[-1]).parent
                make_wav(tdir / "seg_000.wav", 29)
                return subprocess.CompletedProcess(args=[], returncode=0,
                                                   stdout=b"", stderr=b"")

            with mock.patch.object(asr_lib, "_which_ff", return_value="C:/fake/ffmpeg.exe"), \
                 mock.patch.object(asr_lib.subprocess, "run", side_effect=fake_ffmpeg):
                with self.assertRaises(RuntimeError) as ctx:
                    asr_lib.transcribe(src, api_key="test-key")
            self.assertIn("incomplete conversion", str(ctx.exception))


# ---------------------------------------------------------------- HIGH-2
class TestWatcherSingleInstance(unittest.TestCase):
    """Review finding 2: a second watcher's startup sweep could move a
    recording the first watcher was processing into failed/ (watch.py:84)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old_lock = watch.LOCK_FILE
        watch.LOCK_FILE = self.dir / "watch.lock"

    def tearDown(self):
        watch.LOCK_FILE = self.old_lock
        self.tmp.cleanup()

    def _write_lock(self, pid: int) -> None:
        watch.LOCK_FILE.write_text(
            json.dumps({"pid": pid, "started": "2026-11-17T10:00:00"}), encoding="utf-8")

    def test_neg_second_watcher_refused_while_first_alive(self):
        # Live PID = a real sleeping process we spawned.
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self._write_lock(sleeper.pid)
            with mock.patch.object(watch, "log"):
                self.assertFalse(watch.acquire_lock(),
                                 "a second watcher must refuse to start")
            self.assertTrue(watch.LOCK_FILE.exists(), "the live lock must survive refusal")
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_pos_stale_lock_taken_over(self):
        # Just-outside edge: PID whose process has already exited.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self._write_lock(dead.pid)
        with mock.patch.object(watch, "log"):
            self.assertTrue(watch.acquire_lock(), "a dead holder's lock must be taken over")
        watch.release_lock()
        self.assertFalse(watch.LOCK_FILE.exists())

    def test_pos_corrupt_lock_is_treated_as_stale(self):
        # Birth shape: a half-written lock from a crash mid-write.
        watch.LOCK_FILE.write_text("{broken json", encoding="utf-8")
        with mock.patch.object(watch, "log"):
            self.assertTrue(watch.acquire_lock())
        watch.release_lock()

    def test_neg_recover_stale_documented_as_lock_guarded(self):
        # The defect mechanism, kept explicit: recover_stale() must stay
        # unreachable while a lock is held — main() wires acquire before it.
        import inspect
        src = inspect.getsource(watch.main)
        self.assertLess(src.index("acquire_lock"), src.index("recover_stale"),
                        "recover_stale() must only run after the lock is held")


# ---------------------------------------------------------------- MED-3
class TestResumeFromTranscript(unittest.TestCase):
    """Review finding 3: a summary failure meant re-running transcription
    and paying the ASR cost again (transcribe.py:194)."""

    def _valid_summary_data(self):
        return {"summary": "s", "project": None, "priorities": [], "events": [],
                "insights": {"outcome": "unclear", "objections": [], "recommendations": []}}

    def test_resume_skips_asr_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out_20261117-100000"
            out.mkdir()
            (out / "transcript.txt").write_text("we met to plan the launch", encoding="utf-8")
            (out / "asr_meta.json").write_text(json.dumps({
                "title": "meeting", "audio": "meeting.m4a",
                "meeting_day": "2026-11-15", "date_note": "assumed",
                "duration_s": 120.0, "segments": 5, "segments_empty": 0,
                "cost_yuan_est": 0.12, "model": "glm-asr-2512"}), encoding="utf-8")

            def forbidden_asr(*a, **k):
                raise AssertionError("ASR ran on resume — that is the paid re-run defect")

            with mock.patch.object(transcribe.asr_lib, "transcribe", forbidden_asr), \
                 mock.patch.object(transcribe.summarize, "summarize",
                                   return_value=self._valid_summary_data()), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            for name in ("summary.md", "tasks.md", "events.ics"):
                self.assertTrue((out / name).exists(), f"{name} missing after resume")
            # date came from the meta, not from today
            self.assertIn("2026-11-15", (out / "summary.md").read_text(encoding="utf-8"))

    def test_resume_date_correction_uses_given_date(self):
        # MED-7 companion: correcting the assumed date re-summarizes cheaply.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "transcript.txt").write_text("plan", encoding="utf-8")
            (out / "asr_meta.json").write_text(json.dumps({
                "title": "m", "meeting_day": "2026-11-17", "duration_s": 1.0,
                "segments": 1, "cost_yuan_est": 0.0, "model": "x"}), encoding="utf-8")
            captured = {}

            def fake_summarize(transcript, meeting_day, projects, project_key):
                captured["meeting_day"] = meeting_day
                return self._valid_summary_data()

            with mock.patch.object(transcribe.summarize, "summarize", fake_summarize), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out),
                                    "--date", "2026-11-02"]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            self.assertEqual(captured["meeting_day"], "2026-11-02")
            self.assertIn("corrected date", (out / "summary.md").read_text(encoding="utf-8"))

    def test_neg_resume_without_meta_refused(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "transcript.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 1)


# ---------------------------------------------------------------- MED-4
class TestCheckInstallation(unittest.TestCase):
    """Review finding 4: setup spans many steps and explains nothing when
    one is missing; the checker must state each gap in plain language."""

    def _fixture(self, complete: bool) -> Path:
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "transcriber" / "ffmpeg").mkdir(parents=True)
        (root / "news").mkdir()
        if complete:
            (root / "transcriber" / ".env").write_text(
                "ZHIPU_API_KEY=xxx\nDEEPSEEK_API_KEY=yyy\n", encoding="utf-8")
            (root / "news" / ".env").write_text("DEEPSEEK_API_KEY=yyy\n", encoding="utf-8")
            (root / "transcriber" / "projects.json").write_text(
                json.dumps([{"key": "k", "name": "n", "context": "c"}]), encoding="utf-8")
            (root / "news" / "categories.json").write_text(json.dumps(
                [{"key": "c", "name": "C", "focus": "f",
                  "feeds": [{"name": "F", "url": "https://example.org/rss"}]}]),
                encoding="utf-8")
        else:
            # the just-outside edge: files exist but are unusable
            (root / "transcriber" / ".env").write_text("ZHIPU_API_KEY=\n", encoding="utf-8")
            (root / "news" / ".env").write_text("# nothing here\n", encoding="utf-8")
            (root / "transcriber" / "projects.json").write_text("{oops", encoding="utf-8")
            (root / "news" / "categories.json").write_text("[]", encoding="utf-8")
        self.addCleanup(td.cleanup)
        return root

    def _rows(self, result, substring):
        return [r for r in result.rows if substring in r[1]]

    def test_complete_fixture_reports_no_config_gaps(self):
        c = check_installation.run_checks(self._fixture(True))
        env_rows = self._rows(c, "API keys file")
        self.assertTrue(env_rows and all(ok for _, _, ok, _ in env_rows),
                        f"complete fixture flagged: {env_rows}")
        proj = self._rows(c, "projects.json")
        cats = self._rows(c, "categories.json")
        self.assertTrue(proj and proj[0][2], "valid projects.json must pass")
        self.assertTrue(cats and cats[0][2], "valid categories.json must pass")

    def test_broken_fixture_names_each_gap(self):
        c = check_installation.run_checks(self._fixture(False))
        fails = c.failures
        joined = " | ".join(f for _, _, f in fails)
        self.assertTrue(any("transcriber/.env" in i or ".env" in i for _, i, _ in fails),
                        "empty key must be flagged")
        self.assertIn("missing or empty", joined)
        self.assertIn("not valid JSON", joined)
        self.assertTrue(any("categories.json" in i for _, i, _ in fails),
                        "empty categories list must be flagged")

    def test_missing_env_file_gets_copy_instruction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "transcriber").mkdir()
            (root / "news").mkdir()
            c = check_installation.run_checks(root)
            missing = [f for _, i, f in c.failures if "copy" in f and ".env.example" in f]
            self.assertEqual(len(missing), 2,
                             "both missing .env files must point at .env.example")


# ---------------------------------------------------------------- MED-5
class TestBackgroundDependability(unittest.TestCase):
    """Review finding 5: background runs were invisible and missed runs
    silently skipped (news/install_daily.cmd:13)."""

    def test_run_daily_self_test_writes_log(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "run_daily.py"
            script.write_text((ROOT / "news" / "run_daily.py").read_text(encoding="utf-8"),
                              encoding="utf-8")
            r = subprocess.run([sys.executable, "-I", str(script), "--self-test"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            log = Path(td) / "output" / "news_log.txt"
            self.assertTrue(log.exists(), "the persistent log must exist after a run")
            self.assertIn("self-test OK", log.read_text(encoding="utf-8"))

    def test_log_rotation_keeps_one_generation(self):
        with tempfile.TemporaryDirectory() as td:
            import run_daily
            old_dir, old_log = run_daily.LOG_DIR, run_daily.LOG_FILE
            run_daily.LOG_DIR = Path(td)
            run_daily.LOG_FILE = Path(td) / "news_log.txt"
            try:
                run_daily.LOG_FILE.write_text("x" * (run_daily.LOG_MAX_BYTES + 10),
                                              encoding="utf-8")
                run_daily._rotate_if_big()
                self.assertFalse(run_daily.LOG_FILE.exists(), "oversized log renamed")
                self.assertTrue((Path(td) / "news_log.old.txt").exists())
            finally:
                run_daily.LOG_DIR, run_daily.LOG_FILE = old_dir, old_log

    def test_schedtasks_xml_sets_start_when_available(self):
        # The install script's promise, checked at the mechanism level: the
        # PowerShell settings object it applies must carry StartWhenAvailable.
        ps = ("New-ScheduledTaskSettingsSet -StartWhenAvailable "
              "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"({ps}).StartWhenAvailable"], capture_output=True,
                           text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("True", r.stdout.strip())


# ---------------------------------------------------------------- MED-6
class TestNewsValidation(unittest.TestCase):
    """Review finding 6: an empty AI reply published as success, and URLs
    differing only in query params collapsed into one (fetch_news.py:176)."""

    def test_neg_baseline_dedupe_collapsed_query_only_links(self):
        # The exact old expression, shown failing the user first:
        url_a = "https://example.org/story?utm=feed"
        url_b = "https://example.org/story?id=456"
        old = lambda u: u.split("?")[0].rstrip("/")  # noqa: E731
        self.assertEqual(old(url_a), old(url_b),
                         "baseline: two distinct articles shared one identity")

    def test_pos_full_url_identity_keeps_both(self):
        url_a = "https://Example.org/story/?utm=feed"
        url_b = "https://example.org/story?id=456"
        self.assertNotEqual(fetch_news._link_identity(url_a),
                            fetch_news._link_identity(url_b))
        # normal dedupe still works: same story, cosmetically different
        self.assertEqual(fetch_news._link_identity("https://x.com/a#top"),
                         fetch_news._link_identity("https://x.com/a/"))

    def _candidates(self):
        return [{"id": 1, "title": "Story one", "link": "https://e.org/1",
                 "source": "S", "published": None, "age_h": 1, "snippet": ""}]

    def _curate_reply(self, content: str):
        resp = mock.Mock()
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": content}}]}).encode("utf-8")
        return resp

    def test_neg_empty_curation_refused(self):
        # `{"categories": []}` parses fine — baseline published it as a
        # successful briefing with zero picks. Now: loud refusal.
        cats = [{"key": "tech", "name": "Tech", "focus": "f"}]
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             mock.patch.object(fetch_news.urllib.request, "urlopen",
                               return_value=self._curate_reply('{"categories": []}')):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_news.curate(self._candidates(), cats, "2026-11-17")
        self.assertIn("no usable selections", str(ctx.exception))

    def test_pos_curation_with_valid_pick_succeeds(self):
        cats = [{"key": "tech", "name": "Tech", "focus": "f"}]
        reply = ('{"categories": [{"key": "tech", "summary": "s",'
                 ' "items": [{"id": 1, "takeaway": "t"}]}]}')
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             mock.patch.object(fetch_news.urllib.request, "urlopen",
                               return_value=self._curate_reply(reply)):
            out = fetch_news.curate(self._candidates(), cats, "2026-11-17")
        self.assertEqual(out["tech"]["items"][0]["title"], "Story one")

    def test_page_shows_curation_failure_note(self):
        html_out = fetch_news.build_page(
            [{"key": "tech", "name": "Tech"}], {}, "2026-11-17T00:00:00Z",
            "raw", [], curation_note="AI curation failed this run — showing raw headlines.")
        self.assertIn("AI curation failed this run", html_out)


# ---------------------------------------------------------------- MED-7
class TestMeetingDateVisibility(unittest.TestCase):
    """Review finding 7: the meeting date was assumed silently (mtime or
    today) and calendar items carried it without saying so."""

    def _valid_summary_data(self):
        return {"summary": "s", "project": None, "priorities": [], "events": [
            {"date": "2026-11-20", "subject": "Follow-up", "notes": "n"}],
            "insights": {"outcome": "unclear", "objections": [], "recommendations": []}}

    def test_summary_labels_assumed_date(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 2)
            meta_note = {}

            def fake_asr(path, **k):
                return {"transcript": "hello", "duration_s": 2.0, "segments": 1,
                        "segments_empty": 0, "cost_yuan_est": 0.01, "model": "m"}

            def fake_summarize(transcript, meeting_day, projects, project_key):
                meta_note["day"] = meeting_day
                return self._valid_summary_data()

            out = Path(td) / "o"
            with mock.patch.object(transcribe.asr_lib, "transcribe", fake_asr), \
                 mock.patch.object(transcribe.summarize, "summarize", fake_summarize), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", str(src), "--date", "2026-11-01",
                                    "--date-assumed", "--out", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("assumed from the recording file", summary)
            self.assertIn("--resume", summary, "the correction path is spelled out")
            meta = json.loads((out / "asr_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["meeting_day"], "2026-11-01")
            self.assertIn("date_note", meta)

    def test_manual_today_default_is_labelled(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 2)

            def fake_asr(path, **k):
                return {"transcript": "hello", "duration_s": 2.0, "segments": 1,
                        "segments_empty": 0, "cost_yuan_est": 0.01, "model": "m"}

            out = Path(td) / "o"
            with mock.patch.object(transcribe.asr_lib, "transcribe", fake_asr), \
                 mock.patch.object(transcribe.summarize, "summarize",
                                   return_value=self._valid_summary_data()), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", str(src), "--out", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("--date YYYY-MM-DD", summary,
                          "the today-default must say how to correct it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
