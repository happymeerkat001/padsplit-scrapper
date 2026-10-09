#!/usr/bin/env python3
"""Scheduled run scripts stay Mac-compatible and can shadow-run on Linux."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUN_SCRIPTS = (
    "run_morning.sh",
    "run_afternoon.sh",
    "run_field_mms.sh",
    "run_seo_monthly.sh",
)
SYSTEMD_DIR = ROOT / "deploy" / "systemd"

DATE_STUB = """#!/bin/sh
if [ "$1" = "+%H" ]; then
  printf '%s\\n' 07
  exit 0
fi
exec /bin/date "$@"
"""

GIT_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_LOG"
case "$*" in
  *"diff --cached --quiet"*) exit 1 ;;
esac
exit 0
"""

UNAME_DARWIN = """#!/bin/sh
if [ "$1" = "-s" ]; then
  printf '%s\\n' Darwin
  exit 0
fi
exec /usr/bin/uname "$@"
"""

UNAME_LINUX = """#!/bin/sh
if [ "$1" = "-s" ]; then
  printf '%s\\n' Linux
  exit 0
fi
exec /usr/bin/uname "$@"
"""

PYTHON_STUB = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = Path(os.environ["STUB_PY_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write("\\t".join(sys.argv) + "\\n")
    for key in (
        "PADSPLIT_NO_PUSH",
        "PADSPLIT_FROM_FILE",
        "PADSPLIT_ALREADY",
        "PADSPLIT_EXPORTED",
        "FIELD_MMS_SKIP_GOOGLE_VOICE",
        "FIELD_MMS_SKIP_MESSAGES",
        "LOCK_DIR",
    ):
        handle.write(f"ENV {key}=" + os.environ.get(key, "") + "\\n")
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _shells() -> list[str]:
    found = ["bash"]
    if shutil.which("zsh"):
        found.append("zsh")
    return found


class RunScriptPortableTests(unittest.TestCase):
    def test_scripts_drop_mac_hardcodes_and_use_bash_shebang(self) -> None:
        for name in RUN_SCRIPTS:
            text = (ROOT / name).read_text()
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), name)
            self.assertNotIn("/Users/leon", text)
            self.assertNotIn("/private/tmp", text)
            self.assertIn("scripts/run_common.sh", text)
        common = (ROOT / "scripts" / "run_common.sh").read_text()
        self.assertNotIn("/Users/leon", common)
        self.assertIn("/private/tmp", common)
        self.assertIn("${PADSPLIT_LOCK_DIR:-${TMPDIR:-/tmp}}", common)
        self.assertIn("PADSPLIT_ENV_FILE", common)
        self.assertIn("PADSPLIT_PYTHON", common)
        self.assertIn("PADSPLIT_WORKSPACE", common)

    def test_systemd_examples_are_chicago_and_secret_free(self) -> None:
        units = sorted(SYSTEMD_DIR.glob("padsplit-*"))
        self.assertGreaterEqual(len(units), 11)
        blob = "\n".join(path.read_text() for path in units)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("sk-", blob)
        self.assertNotIn("API_KEY=", blob)
        for name in (
            "padsplit-morning.service",
            "padsplit-afternoon.service",
            "padsplit-field-mms.service",
            "padsplit-seo-monthly.service",
            "padsplit-smarthome-watcher.service",
        ):
            text = (SYSTEMD_DIR / name).read_text()
            self.assertIn("User=padsplit", text)
            self.assertIn("EnvironmentFile=/etc/padsplit/.env", text)
            self.assertIn("OnFailure=padsplit-onfailure@%p.service", text)
        self.assertIn(
            "OnCalendar=*-*-* 06:00:00 America/Chicago",
            (SYSTEMD_DIR / "padsplit-morning.timer").read_text(),
        )
        self.assertIn(
            "OnCalendar=*-*-* 14:00:00 America/Chicago",
            (SYSTEMD_DIR / "padsplit-afternoon.timer").read_text(),
        )
        self.assertIn(
            "OnCalendar=*-*-* 07:00:00 America/Chicago",
            (SYSTEMD_DIR / "padsplit-field-mms.timer").read_text(),
        )
        self.assertIn(
            "OnCalendar=*-*-01 09:00:00 America/Chicago",
            (SYSTEMD_DIR / "padsplit-seo-monthly.timer").read_text(),
        )
        self.assertIn(
            "OnCalendar=*-*-* *:00:00 America/Chicago",
            (SYSTEMD_DIR / "padsplit-smarthome-watcher.timer").read_text(),
        )
        for timer in SYSTEMD_DIR.glob("*.timer"):
            self.assertIn("America/Chicago", timer.read_text(), timer.name)
            self.assertNotIn("\nTimezone=", "\n" + timer.read_text(), timer.name)
        watcher = (SYSTEMD_DIR / "padsplit-smarthome-watcher.service").read_text()
        self.assertIn("smarthome.watcher", watcher)
        failure = (SYSTEMD_DIR / "padsplit-onfailure@.service").read_text()
        self.assertIn("User=padsplit", failure)
        self.assertIn("%i", failure)

    def test_no_push_skips_git_and_mac_only_steps_on_linux(self) -> None:
        for shell in _shells():
            with self.subTest(shell=shell):
                self._assert_linux_shadow(shell)

    def test_shebang_runs_morning_without_an_explicit_shell(self) -> None:
        result = self._run("run_morning.sh", [], no_push_in_file=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.git_log, "")
        self.assertIn("Skipping Obsidian daily digest (not macOS)", result.stdout)

    def test_default_still_pulls_and_pushes_when_no_push_unset(self) -> None:
        result = self._run("run_morning.sh", [], no_push_in_file=False, shell="bash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pull --rebase", result.git_log)
        self.assertIn(" push\n", result.git_log)
        self.assertNotIn("PADSPLIT_NO_PUSH=1; skipping git", result.stdout)
        self.assertIn("thermostat/scraper.py", result.py_log)

    def test_lock_parent_is_private_tmp_on_darwin_only(self) -> None:
        for shell in _shells():
            with self.subTest(shell=shell):
                self.assertEqual(
                    self._lock_parent(shell, uname_stub=UNAME_DARWIN, tmpdir="/var/tmp/session"),
                    "/private/tmp",
                )
                self.assertEqual(
                    self._lock_parent(
                        shell,
                        uname_stub=UNAME_DARWIN,
                        tmpdir="/var/tmp/session",
                        lock_dir="/custom/locks/",
                    ),
                    "/custom/locks",
                )
                self.assertEqual(
                    self._lock_parent(shell, uname_stub=UNAME_LINUX, tmpdir="/var/tmp/session/"),
                    "/var/tmp/session",
                )
                self.assertEqual(
                    self._lock_parent(shell, uname_stub=UNAME_LINUX, tmpdir=""),
                    "/tmp",
                )
                self.assertEqual(
                    self._lock_parent(
                        shell,
                        uname_stub=UNAME_LINUX,
                        tmpdir="/var/tmp/session",
                        lock_dir="/custom/locks",
                    ),
                    "/custom/locks",
                )

    def test_lock_dir_override_is_used_by_the_morning_script(self) -> None:
        with tempfile.TemporaryDirectory() as override:
            linux = self._run(
                "run_morning.sh",
                [],
                no_push_in_file=True,
                lock_parent=override,
            )
            self.assertEqual(linux.returncode, 0, linux.stderr)
            self.assertIn(f"ENV LOCK_DIR={override}/padsplit-scraper-morning.lock\n", linux.py_log)
            self.assertFalse(linux.lock_left)
            darwin = self._run(
                "run_morning.sh",
                ["uname"],
                no_push_in_file=True,
                lock_parent=override,
            )
            self.assertEqual(darwin.returncode, 0, darwin.stderr)
            self.assertIn(f"ENV LOCK_DIR={override}/padsplit-scraper-morning.lock\n", darwin.py_log)
            self.assertNotIn("/private/tmp", darwin.py_log)
            self.assertFalse(darwin.lock_left)

    def test_darwin_obsidian_respects_vault_path(self) -> None:
        missing = self._run(
            "run_morning.sh",
            ["uname"],
            no_push_in_file=True,
            extra_env={"OBSIDIAN_DAILY_NOTES_DIR": ""},
        )
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("Skipping Obsidian daily digest (vault path missing)", missing.stdout)
        self.assertNotIn("obsidian_daily_digest.py", missing.py_log)
        self.assertEqual(missing.git_log, "")

        with tempfile.TemporaryDirectory() as vault:
            present = self._run(
                "run_morning.sh",
                ["uname"],
                no_push_in_file=True,
                extra_env={"OBSIDIAN_DAILY_NOTES_DIR": vault},
            )
        self.assertEqual(present.returncode, 0, present.stderr)
        self.assertIn("obsidian_daily_digest.py", present.py_log)
        self.assertNotIn("Skipping Obsidian daily digest", present.stdout)

    def test_darwin_field_mms_skips_only_missing_mac_paths(self) -> None:
        missing = self._run("run_field_mms.sh", ["uname"], no_push_in_file=True)
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("Chrome profile path missing", missing.stdout)
        self.assertIn("Messages.app path missing", missing.stdout)
        self.assertIn("ENV FIELD_MMS_SKIP_GOOGLE_VOICE=1", missing.py_log)
        self.assertIn("ENV FIELD_MMS_SKIP_MESSAGES=1", missing.py_log)
        self.assertIn("field_mms.py", missing.py_log)

        with tempfile.TemporaryDirectory() as mac_root:
            root = Path(mac_root)
            chrome = root / "Chrome Profile"
            messages = root / "Messages.app"
            chrome.mkdir()
            messages.mkdir()
            present = self._run(
                "run_field_mms.sh",
                ["uname"],
                no_push_in_file=True,
                extra_env={
                    "FIELD_MMS_CHROME_USER_DATA_DIR": str(chrome),
                    "PADSPLIT_MESSAGES_APP": str(messages),
                },
            )
        self.assertEqual(present.returncode, 0, present.stderr)
        self.assertNotIn("Skipping", present.stdout)
        self.assertIn("ENV FIELD_MMS_SKIP_GOOGLE_VOICE=\n", present.py_log)
        self.assertIn("ENV FIELD_MMS_SKIP_MESSAGES=\n", present.py_log)
        self.assertIn("field_mms.py", present.py_log)

    def _assert_linux_shadow(self, shell: str) -> None:
        morning = self._run("run_morning.sh", [], no_push_in_file=True, shell=shell)
        self.assertEqual(morning.returncode, 0, morning.stderr)
        self.assertEqual(morning.git_log, "")
        self.assertNotIn("git push", morning.stdout)
        self.assertIn("PADSPLIT_NO_PUSH=1; skipping git pull", morning.stdout)
        self.assertIn("Would commit:", morning.stdout)
        self.assertIn("docs/data/occupancy.json", morning.stdout)
        self.assertIn("docs/data/latest.json", morning.stdout)
        self.assertIn("Skipping Obsidian daily digest (not macOS)", morning.stdout)
        self.assertIn("padsplit_scraper/scraper.py", morning.py_log)
        self.assertIn("thermostat/scraper.py", morning.py_log)
        self.assertNotIn("obsidian_daily_digest.py", morning.py_log)
        self.assertIn("ENV PADSPLIT_NO_PUSH=1", morning.py_log)
        self.assertIn("ENV PADSPLIT_FROM_FILE=from-file", morning.py_log)
        self.assertIn("ENV PADSPLIT_ALREADY=from-env", morning.py_log)
        self.assertIn("ENV PADSPLIT_EXPORTED=exported-value", morning.py_log)
        self.assertIn(f"ENV LOCK_DIR={morning.lock_dir}\n", morning.py_log)
        self.assertNotIn("/private/tmp", morning.py_log)
        self.assertFalse(morning.lock_left)

        afternoon = self._run("run_afternoon.sh", [], no_push_in_file=True, shell=shell)
        self.assertEqual(afternoon.returncode, 0, afternoon.stderr)
        self.assertEqual(afternoon.git_log, "")
        self.assertIn("Would commit:", afternoon.stdout)
        self.assertIn("--messages-only", afternoon.py_log)
        self.assertNotIn("docs/data/occupancy.json", afternoon.stdout)
        self.assertFalse(afternoon.lock_left)

        field = self._run("run_field_mms.sh", [], no_push_in_file=True, shell=shell)
        self.assertEqual(field.returncode, 0, field.stderr)
        self.assertEqual(field.git_log, "")
        self.assertIn(
            "Skipping Mac-only Google Voice/Playwright Chrome-profile fallback (not macOS)",
            field.stdout,
        )
        self.assertIn("Skipping Mac-only Messages.app (not macOS)", field.stdout)
        self.assertIn("ENV FIELD_MMS_SKIP_GOOGLE_VOICE=1", field.py_log)
        self.assertIn("ENV FIELD_MMS_SKIP_MESSAGES=1", field.py_log)
        self.assertIn("field_mms.py", field.py_log)
        self.assertFalse(field.lock_left)

        seo = self._run("run_seo_monthly.sh", [], no_push_in_file=True, shell=shell)
        self.assertEqual(seo.returncode, 0, seo.stderr)
        self.assertEqual(seo.git_log, "")
        self.assertIn("seo_monthly.py", seo.py_log)
        self.assertFalse(seo.lock_left)

    def _run(
        self,
        script_name: str,
        extra_bins: list[str],
        *,
        no_push_in_file: bool,
        shell: str | None = None,
        extra_env: dict[str, str] | None = None,
        lock_parent: str | None = None,
    ) -> "_RunResult":
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            py_log = root / "python.log"
            git_log = root / "git.log"
            _write_exe(bindir / "date", DATE_STUB)
            _write_exe(bindir / "git", GIT_STUB)
            _write_exe(bindir / "python-stub", PYTHON_STUB)
            if "uname" in extra_bins:
                _write_exe(bindir / "uname", UNAME_DARWIN)
            env_file = root / "server.env"
            lines = [
                "PADSPLIT_FROM_FILE=from-file",
                "PADSPLIT_ALREADY=from-file",
                "export PADSPLIT_EXPORTED=exported-value",
            ]
            if no_push_in_file:
                lines.insert(0, "PADSPLIT_NO_PUSH=1")
            env_file.write_text("\n".join(lines) + "\n")
            env = os.environ.copy()
            for key in (
                "CI",
                "GITHUB_ACTIONS",
                "PADSPLIT_NO_PUSH",
                "PADSPLIT_PYTHON",
                "PADSPLIT_ENV_FILE",
                "PADSPLIT_WORKSPACE",
                "FIELD_MMS_SKIP_GOOGLE_VOICE",
                "FIELD_MMS_SKIP_MESSAGES",
                "FIELD_MMS_CHROME_USER_DATA_DIR",
                "PADSPLIT_MESSAGES_APP",
                "OBSIDIAN_DAILY_NOTES_DIR",
                "PADSPLIT_LOCK_DIR",
            ):
                env.pop(key, None)
            env.update(
                {
                    "PATH": f"{bindir}:/usr/bin:/bin",
                    "TMPDIR": str(root / "tmp"),
                    "HOME": str(root / "home"),
                    "PADSPLIT_PYTHON": str(bindir / "python-stub"),
                    "PADSPLIT_ENV_FILE": str(env_file),
                    "PADSPLIT_ALREADY": "from-env",
                    "STUB_PY_LOG": str(py_log),
                    "GIT_LOG": str(git_log),
                }
            )
            (root / "tmp").mkdir()
            (root / "home").mkdir()
            if extra_env:
                for key, value in extra_env.items():
                    if value == "":
                        env.pop(key, None)
                    else:
                        env[key] = value
            darwin = "uname" in extra_bins
            requested = lock_parent if lock_parent is not None else env.get("PADSPLIT_LOCK_DIR")
            if requested is None and darwin:
                # Keep Darwin-uname script runs off the real /private/tmp.
                requested = str(root / "locks")
            if requested:
                env["PADSPLIT_LOCK_DIR"] = requested
                Path(requested).mkdir(parents=True, exist_ok=True)
                lock_root = Path(requested)
            else:
                lock_root = Path(env["TMPDIR"])
            script = ROOT / script_name
            if shell is None:
                cmd = [str(script)]
            else:
                cmd = [shell, str(script)]
            completed = subprocess.run(
                cmd,
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            lock_names = {
                "run_morning.sh": "padsplit-scraper-morning.lock",
                "run_afternoon.sh": "padsplit-scraper-afternoon.lock",
                "run_field_mms.sh": "padsplit-field-mms.lock",
                "run_seo_monthly.sh": "padsplit-seo-monthly.lock",
            }
            return _RunResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                py_log=py_log.read_text() if py_log.exists() else "",
                git_log=git_log.read_text() if git_log.exists() else "",
                lock_dir=lock_root / lock_names[script_name],
            )

    def _lock_parent(
        self,
        shell: str,
        *,
        uname_stub: str,
        tmpdir: str,
        lock_dir: str = "",
    ) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            _write_exe(bindir / "uname", uname_stub)
            env = os.environ.copy()
            env.pop("PADSPLIT_LOCK_DIR", None)
            if tmpdir == "":
                env.pop("TMPDIR", None)
            else:
                env["TMPDIR"] = tmpdir
            if lock_dir:
                env["PADSPLIT_LOCK_DIR"] = lock_dir
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            script = (
                "set -euo pipefail\n"
                f'. "{ROOT}/scripts/run_common.sh"\n'
                "padsplit_lock_parent\n"
            )
            completed = subprocess.run(
                [shell, "-c", script],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()


class _RunResult:
    def __init__(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        py_log: str,
        git_log: str,
        lock_dir: Path,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.py_log = py_log
        self.git_log = git_log
        self.lock_dir = lock_dir
        self.lock_left = lock_dir.exists()


if __name__ == "__main__":
    unittest.main()
