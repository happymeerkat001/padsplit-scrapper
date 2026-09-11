#!/usr/bin/env python3
"""Offline unit tests for explicit send / collection runtime gates."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import runtime


class RuntimeFlagTests(unittest.TestCase):
    def test_ci_never_sends_even_when_legacy_flag_is_on(self) -> None:
        env = {
            "CI": "true",
            "LOCKOUT_REPLY_ENABLE": "1",
            "PADSPLIT_SEND_LOCKOUT": "1",
            "LEAK_REPLY_ENABLE": "true",
        }
        self.assertTrue(runtime.running_in_ci(env))
        self.assertFalse(runtime.send_enabled("lockout", env))
        self.assertFalse(runtime.send_enabled("leak", env))
        self.assertTrue(runtime.collection_only(env))
        self.assertEqual(tuple(runtime.enabled_send_actions(env)), ())

    def test_legacy_aliases_enable_when_ci_is_off(self) -> None:
        env = {"CI": "", "GITHUB_ACTIONS": "", "LOCKOUT_REPLY_ENABLE": "1"}
        self.assertTrue(runtime.send_enabled("lockout", env))
        self.assertFalse(runtime.send_enabled("leak", env))

    def test_explicit_false_wins_over_later_true_alias(self) -> None:
        env = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_SEND_LOCKOUT": "0",
            "LOCKOUT_REPLY_ENABLE": "1",
        }
        self.assertFalse(runtime.send_enabled("lockout", env))

    def test_unknown_action_raises(self) -> None:
        with self.assertRaises(KeyError):
            runtime.send_enabled("not-an-action", {"CI": ""})

    def test_darwin_is_not_implicit_send_permission(self) -> None:
        env = {"CI": "", "GITHUB_ACTIONS": ""}
        with patch.object(sys, "platform", "darwin"):
            self.assertFalse(runtime.send_enabled("lockout", env))
            self.assertFalse(runtime.send_enabled("field_mms", env))
            self.assertFalse(runtime.send_enabled("seo", env))
            self.assertFalse(runtime.action_hooks_enabled(env))

    def test_collection_only_and_action_hooks(self) -> None:
        off = {"CI": "", "GITHUB_ACTIONS": ""}
        self.assertTrue(runtime.collection_only(off))
        hooks = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        }
        self.assertTrue(runtime.action_hooks_enabled(hooks))
        self.assertFalse(runtime.collection_only(hooks))
        forced = dict(hooks)
        forced["PADSPLIT_COLLECTION_ONLY"] = "1"
        self.assertTrue(runtime.collection_only(forced))
        self.assertFalse(runtime.action_hooks_enabled(forced))

    def test_git_publish_off_by_default_and_off_in_ci(self) -> None:
        self.assertFalse(runtime.git_publish_enabled({"CI": "", "GITHUB_ACTIONS": ""}))
        self.assertTrue(
            runtime.git_publish_enabled({"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_GIT_PUBLISH": "1"})
        )
        self.assertFalse(runtime.git_publish_enabled({"CI": "true", "PADSPLIT_GIT_PUBLISH": "1"}))

    def test_state_and_lock_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PADSPLIT_STATE_DIR": tmpdir, "PADSPLIT_LOCK_DIR": tmpdir}
            self.assertEqual(runtime.state_dir(env), Path(tmpdir))
            self.assertEqual(runtime.lock_dir("lockout", env), Path(tmpdir) / "lockout")
        env = {"TMPDIR": "/tmp", "PADSPLIT_STATE_DIR": "", "PADSPLIT_LOCK_DIR": ""}
        self.assertEqual(runtime.lock_dir("leak", env), Path("/tmp") / "padsplit-leak.lock")
        self.assertEqual(runtime.state_dir(env), runtime.REPO_ROOT / ".runtime-state")

    def test_host_identity_is_nonempty(self) -> None:
        self.assertTrue(runtime.host_identity())


if __name__ == "__main__":
    unittest.main()
