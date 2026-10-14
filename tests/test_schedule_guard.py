"""Offline delivery scheduling boundaries; no SMTP, network, AI, or real sleeps."""
import contextlib
import datetime as dt
import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import briefing as b
import schedule_guard as gate


def at(value):
    return dt.datetime.fromisoformat("2026-10-13T" + value).replace(tzinfo=b.BEIJING_TZ)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_TOKEN": "fake", "GITHUB_REPOSITORY": "owner/repo"}
        self.factory = Mock()
        self.factory.return_value.read.return_value = (None, None)

    def test_early_and_late_wakes_do_no_api_work(self):
        for value in ("00:00", "03:07", "07:09", "12:00", "23:59"):
            self.assertEqual(gate.decide(self.env, at(value), self.factory)["action"], "skip")
        self.factory.assert_not_called()

    def test_morning_boundaries_hold_until_target_then_send(self):
        for value, hold in (("07:10", "07:30"), ("07:29", "07:30"), ("07:30", ""), ("11:59", "")):
            result = gate.decide(self.env, at(value), self.factory)
            self.assertEqual((result["action"], result["not_before"]), ("send", hold))

    def test_utc_is_converted_before_calendar_and_time_checks(self):
        utc = dt.datetime(2026, 10, 12, 23, 20, tzinfo=dt.timezone.utc)
        result = gate.decide(self.env, utc, self.factory)
        self.assertEqual((result["today"], result["not_before"]), ("2026-10-13", "07:30"))

    def test_all_existing_records_skip_before_generation(self):
        for state in ("sent", "sending", "uncertain", None, "malformed\nsecret"):
            self.factory.return_value.read.return_value = ("existing-sha", state)
            result = gate.decide(self.env, at("07:20"), self.factory)
            self.assertEqual((result["action"], result["not_before"]), ("skip", ""))
            self.assertNotIn("secret", str(result))
        self.factory.return_value.create_or_update.assert_not_called()

    def test_push_and_manual_preview_do_not_read_or_modify_delivery(self):
        cases = ({"GITHUB_EVENT_NAME": "push"},
                 {"GITHUB_EVENT_NAME": "workflow_dispatch"},
                 {"GITHUB_EVENT_NAME": "workflow_dispatch", "MANUAL_DRY_RUN": "true"})
        for env in cases:
            self.assertEqual(gate.decide(env, at("07:20"), self.factory)["action"], "dry-run")
        self.factory.assert_not_called()

    def test_recovery_only_sends_on_authorized_date(self):
        env = dict(self.env, GITHUB_EVENT_NAME="push", RECOVER_DATE="2026-10-13")
        self.assertEqual(gate.decide(env, at("15:00"), self.factory)["action"], "send")
        tomorrow = at("15:00") + dt.timedelta(days=1)
        self.factory.reset_mock()
        self.assertEqual(gate.decide(env, tomorrow, self.factory)["action"], "dry-run")
        self.factory.assert_not_called()

    def test_explicit_manual_send_can_recover_outside_window(self):
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", MANUAL_DRY_RUN="false")
        self.assertEqual(gate.decide(env, at("15:00"), self.factory)["action"], "send")

    def test_missing_credentials_refuse_send(self):
        with self.assertRaises(b.ClaimError):
            gate.decide({"GITHUB_EVENT_NAME": "schedule"}, at("08:00"), self.factory)
        self.factory.assert_not_called()

    def test_unknown_read_fails_closed_without_secret_or_send_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            stream = io.StringIO()
            with patch.dict(os.environ, dict(self.env, GITHUB_OUTPUT=output), clear=True), \
                 patch.object(gate, "now_beijing", return_value=at("08:00")), \
                 patch.object(b.GitHubClaim, "read", side_effect=b.ClaimError("private-response")), \
                 contextlib.redirect_stdout(stream):
                self.assertEqual(gate.main(), 1)
            self.assertNotIn("private-response", stream.getvalue())
            self.assertNotIn('"action": "send"', stream.getvalue())
            self.assertFalse(os.path.exists(output))

    def test_success_writes_only_fixed_action_and_time_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            with patch.dict(os.environ, dict(self.env, GITHUB_OUTPUT=output), clear=True), \
                 patch.object(gate, "now_beijing", return_value=at("07:20")), \
                 patch.object(b.GitHubClaim, "read", return_value=(None, None)), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(), 0)
            with open(output, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "action=send\nnot_before=07:30\n")


class HoldTests(unittest.TestCase):
    def test_never_releases_early_and_bounds_sleep_chunks(self):
        current = [at("07:29")]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            current[0] += dt.timedelta(seconds=seconds)
        b.wait_until_delivery("07:30", "2026-10-13", lambda: current[0], sleep, lambda: 0)
        self.assertEqual(current[0], at("07:30"))
        self.assertEqual(sleeps, [30, 30])

    def test_finished_after_target_needs_no_sleep(self):
        sleep = Mock()
        b.wait_until_delivery("07:30", "2026-10-13", lambda: at("07:35"), sleep)
        sleep.assert_not_called()

    def test_long_or_cross_date_wait_refused(self):
        for current in (at("07:09"), at("07:20") + dt.timedelta(days=1)):
            with self.assertRaises(b.SendRefused):
                b.wait_until_delivery("07:30", "2026-10-13", lambda: current, Mock())

    def test_clock_stall_has_monotonic_deadline(self):
        elapsed = iter((0, 1206))
        with self.assertRaises(b.SendRefused):
            b.wait_until_delivery("07:30", "2026-10-13", lambda: at("07:20"), Mock(), lambda: next(elapsed))

    def test_invalid_or_dry_run_hold_refused_before_acquisition(self):
        with patch.object(b, "gather") as gather, contextlib.redirect_stderr(io.StringIO()):
            for argv in (["--not-before", "07:30"], ["--send", "--not-before", "25:00"]):
                self.assertEqual(b.main(argv), 2)
            gather.assert_not_called()


if __name__ == "__main__":
    unittest.main()
