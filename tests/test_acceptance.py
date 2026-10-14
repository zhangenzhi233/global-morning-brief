"""独立验收中发现的回归案例；全部离线，不发送消息。"""
import base64
import copy
import datetime
import contextlib
import io
import json
import os
import smtplib
import unittest
from unittest.mock import patch

import briefing as b
from test_briefing import FakeHttp, PEOPLE_LAYOUT, PEOPLE_ARTICLE, _cfg, _FakeSMTP, work_tmpdir


class NewsEvidenceAcceptance(unittest.TestCase):
    def test_duplicate_and_limit_rules_keep_latest_unique_titles(self):
        cfg = _cfg(strict_elements=False, max_items=1)
        first = b._new_candidate("gov", "测试源", "同一标题", "https://www.gov.cn/1")
        first["publish_time"] = "2026-10-08 10:00"
        duplicate = copy.deepcopy(first)
        duplicate["url"] = "https://www.gov.cn/2"
        old = b._new_candidate("gov", "测试源", "较早标题", "https://www.gov.cn/3")
        old["publish_time"] = "2026-10-08 09:00"
        included, excluded = b.filter_candidates([first, duplicate, old], cfg)
        self.assertEqual([c["title"] for c in included], ["同一标题"])
        self.assertEqual(len(excluded), 2)

    def test_topic_country_is_not_event_location(self):
        for body in ("实施要坚持以习近平新时代中国特色社会主义思想为指导。",
                     "加强文化强国建设。", "作者为广东某大学研究院院长。"):
            self.assertEqual(b.extract_location("", body)[0], b.UNKNOWN)

    def test_explicit_place_and_evidence_stay_aligned_after_dateline(self):
        body = "新华社上海10月8日电 国务院10月8日在北京举行会议。"
        place, evidence = b.extract_location("", body)
        self.assertEqual(place, "北京")
        self.assertIn("在北京举行", evidence)
        self.assertNotIn("上海", evidence)

    def test_background_and_author_details_are_not_combined_into_news(self):
        body = "现就该课题展开讨论。\n2023年10月，在北京举行会议。\n作者为广东大学教师。"
        self.assertEqual(b.extract_location("", body)[0], b.UNKNOWN)
        self.assertEqual(b.extract_event_time("", body)[0], b.UNKNOWN)
        self.assertEqual(b.extract_subject("", body)[0], b.UNKNOWN)

    def test_visit_actor_is_the_visitor(self):
        actor, _ = b.extract_subject("多哥部长会议主席福雷将访华", "外交部发言人8日宣布。")
        self.assertEqual(actor, "多哥部长会议主席福雷")

    def test_missing_visible_paper_date_is_rejected(self):
        now = datetime.datetime(2026, 10, 8, 7, 30, tzinfo=b.BEIJING_TZ)
        cfg = _cfg(people_max_layouts=1)
        stale = PEOPLE_ARTICLE.replace("2026年10月08日", "2026年10月07日")
        http = FakeHttp({"node_01.html": PEOPLE_LAYOUT, "content_30184434.html": stale})
        self.assertEqual(b.fetch_people(http, cfg, now), [])

    def test_layout_request_url_does_not_prove_edition_date(self):
        cfg = _cfg(people_max_layouts=1)
        now = datetime.datetime(2026, 10, 8, 7, 30, tzinfo=b.BEIJING_TZ)
        http = FakeHttp({"node_01.html": '<a href="../../../content/202610/08/content_1.html">标题</a>'})
        with self.assertRaises(b.FetchError):
            b.fetch_people(http, cfg, now)

    def test_url_userinfo_is_rejected(self):
        with self.assertRaises(b.FetchError):
            b.HttpClient(b.DEFAULT_CONFIG).check_url("https://user:password@www.gov.cn/x")


class DeliveryAcceptance(unittest.TestCase):
    def test_send_pipeline_claims_before_delivery_and_never_repeats_existing_marker(self):
        env = {"SMTP_USER": "sender@example.org", "MAIL_TO": "reader@example.org",
               "SMTP_PASSWORD": "fake-test-password", "GITHUB_TOKEN": "fake-token",
               "GITHUB_REPOSITORY": "example/test-repo"}
        now = datetime.datetime(2026, 10, 8, 7, 30, tzinfo=b.BEIJING_TZ)
        with work_tmpdir() as out, patch.dict(os.environ, env, clear=True), \
                patch.object(b, "gather", return_value=([], {})), \
                patch.object(b, "now_beijing", return_value=now), \
                patch.object(b, "send_email", return_value="sent") as send, \
                contextlib.redirect_stdout(io.StringIO()):
            replies = [(404, None), (201, {"content": {"sha": "marker-sha"}}), (200, {})]
            with patch.object(b.GitHubClaim, "_request", side_effect=replies) as requests:
                self.assertEqual(b.main(["--send", "--output-dir", out]), 0)
                self.assertEqual([call.args[0] for call in requests.call_args_list], ["GET", "PUT", "PUT"])
                send.assert_called_once()
            existing = {"sha": "marker-sha", "content": base64.b64encode(b"corrupt-marker").decode()}
            with patch.object(b.GitHubClaim, "_request", return_value=(200, existing)) as requests:
                self.assertEqual(b.main(["--send", "--output-dir", out]), 0)
                self.assertEqual(requests.call_count, 1)
                send.assert_called_once()  # 第二次运行没有再次发送。

    def test_uncertain_send_pipeline_reports_failure(self):
        env = {"SMTP_USER": "sender@example.org", "MAIL_TO": "reader@example.org",
               "SMTP_PASSWORD": "fake-test-password", "GITHUB_TOKEN": "fake-token",
               "GITHUB_REPOSITORY": "example/test-repo"}
        with work_tmpdir() as out, patch.dict(os.environ, env, clear=True), \
                patch.object(b, "gather", return_value=([], {})), \
                patch.object(b, "send_email", return_value="uncertain"), \
                patch.object(b.GitHubClaim, "_request", side_effect=[(404, None), (201, {"content": {"sha": "marker-sha"}}), (200, {})]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(["--send", "--output-dir", out]), 1)
            with open(os.path.join(out, "report.json"), encoding="utf-8") as report:
                delivery = json.load(report)["delivery"]
            self.assertEqual(delivery["state"], "uncertain")
            self.assertFalse(delivery["sent"])

    def test_github_claim_uses_documented_nested_content_sha(self):
        claim = b.GitHubClaim("fake-token", "example/brief", "2026-10-08")
        with patch.object(claim, "_request", return_value=(201, {"content": {"sha": "blob-sha"}, "commit": {"sha": "commit-sha"}})):
            self.assertEqual(claim.create_or_update("sending", None), "blob-sha")

    def test_existing_claim_cannot_be_overwritten(self):
        claim = b.GitHubClaim("fake-token", "example/brief", "2026-10-08")
        with patch.object(claim, "_request") as request:
            self.assertIsNone(claim.create_or_update("sending", "existing-sha"))
            request.assert_not_called()

    def test_corrupt_existing_claim_keeps_its_sha(self):
        claim = b.GitHubClaim("fake-token", "example/brief", "2026-10-08")
        body = {"sha": "existing-sha", "content": base64.b64encode(b"corrupt").decode()}
        with patch.object(claim, "_request", return_value=(200, body)):
            self.assertEqual(claim.read(), ("existing-sha", None))

    def test_claim_reply_without_sha_refuses_send(self):
        claim = b.GitHubClaim("fake-token", "example/brief", "2026-10-08")
        with patch.object(claim, "_request", return_value=(201, {"content": {}})):
            with self.assertRaises(b.ClaimError):
                claim.create_or_update("sending", None)

    def test_smtp_acceptance_survives_quit_failure(self):
        smtp = _FakeSMTP()
        smtp.quit = lambda: (_ for _ in ()).throw(smtplib.SMTPServerDisconnected("QUIT failed"))
        env = {"SMTP_USER": "test@example.com", "SMTP_PASSWORD": "fake-test-password", "MAIL_TO": "reader@example.com"}
        self.assertEqual(b.send_email(env, "<p>test</p>", "test", connect_fn=lambda _: (smtp, True)), "sent")


if __name__ == "__main__":
    unittest.main()
