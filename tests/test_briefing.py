# -*- coding: utf-8 -*-
"""offline 单元测试：不访问网络，覆盖关键安全与正确性行为。"""

import contextlib
import datetime as _dt
import json
import os
import shutil
import smtplib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.contextmanager
def work_tmpdir():
    """工作区内的临时目录（沙箱下不可用系统 tempfile，其 ACL 会阻止子目录/清理）。"""
    d = os.path.join(REPO_ROOT, "testtmp_" + os.urandom(8).hex())
    os.makedirs(d)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

import briefing  # noqa: E402
from briefing import (  # noqa: E402
    BEIJING_TZ, DEFAULT_CONFIG, FetchError, HttpClient, LocalClaim,
    SendRefused, UNKNOWN, _NoRedirect,
    decode_bytes, extract_event_time, extract_location,
    extract_subject, filter_candidates, fetch_gov, fetch_mfa, fetch_people,
    is_precise_fresh, is_same_day, parse_date_yyyymmdd, parse_gov_pubtime,
    render_html, render_plain, send_email, strip_dateline, strip_tags,
    validate_address,
)


def _cfg(**over):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in over.items():
        cfg[k] = v
    return cfg


def _dtb(y, mo, d, h=0, mi=0):
    return _dt.datetime(y, mo, d, h, mi, tzinfo=BEIJING_TZ)


# ---------------------------------------------------------------------------
# 时间：过期/未来拒绝、日期精度
# ---------------------------------------------------------------------------

class TimeTests(unittest.TestCase):
    def test_precise_fresh_bounds(self):
        now = _dtb(2026, 10, 8, 7, 30)
        self.assertTrue(is_precise_fresh(_dtb(2026, 10, 8, 7, 0), now, 24))
        self.assertTrue(is_precise_fresh(_dtb(2026, 10, 7, 18, 48), now, 24))
        self.assertFalse(is_precise_fresh(_dtb(2026, 10, 8, 7, 31), now, 24))  # 未来
        self.assertFalse(is_precise_fresh(_dtb(2026, 10, 6, 18, 48), now, 24))  # 过期>24h
        self.assertFalse(is_precise_fresh(_dtb(2026, 10, 8, 23, 0), now, 24))   # 未来

    def test_same_day(self):
        now = _dtb(2026, 10, 8, 7, 30)
        self.assertTrue(is_same_day("2026-10-08", now))
        self.assertFalse(is_same_day("2026-10-07", now))

    def test_parse_gov_pubtime(self):
        dt = parse_gov_pubtime("2026-10-08-18:48:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M"), "2026-10-08 18:48")
        self.assertEqual(dt.utcoffset(), _dt.timedelta(hours=8))

    def test_parse_date_yyyymmdd_variants(self):
        self.assertEqual(parse_date_yyyymmdd("2026-10-08 00:00:00:0"), "2026-10-08")
        self.assertEqual(parse_date_yyyymmdd("20261008"), "2026-10-08")


# ---------------------------------------------------------------------------
# URL 白名单 / 拒绝重定向
# ---------------------------------------------------------------------------

class UrlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.h = HttpClient(_cfg())

    def test_https_whitelist_ok(self):
        self.assertEqual(self.h.check_url("https://www.gov.cn/x"), "https://www.gov.cn/x")
        self.assertEqual(self.h.check_url("https://paper.people.com.cn/a.html"),
                         "https://paper.people.com.cn/a.html")

    def test_reject_non_https(self):
        with self.assertRaises(FetchError):
            self.h.check_url("http://www.gov.cn/x")

    def test_reject_unknown_host(self):
        with self.assertRaises(FetchError):
            self.h.check_url("https://evil.example.com/x")

    def test_reject_lookalike_subdomain(self):
        # 不凭泛 .gov.cn 自动接受未知站点
        with self.assertRaises(FetchError):
            self.h.check_url("https://www.gov.cn.attacker.com/x")

    def test_redirect_rejected(self):
        h = _NoRedirect()
        with self.assertRaises(FetchError):
            h.redirect_request(None, None, 301, None, {}, "https://evil.example.com")


# ---------------------------------------------------------------------------
# 文本处理 / 字段保守提取
# ---------------------------------------------------------------------------

class FieldExtractionTests(unittest.TestCase):
    def test_strip_dateline(self):
        body = "新华社北京10月8日电 何立峰8日下午应约同英国财政大臣希利举行视频通话。"
        self.assertNotIn("北京", strip_dateline(body))
        self.assertIn("何立峰", strip_dateline(body))

    def test_extract_event_time_avoids_dateline(self):
        body = "新华社北京10月8日电 何立峰8日下午应约同英国财政大臣希利举行视频通话。"
        val, ev = extract_event_time("", body)
        self.assertIn("8日下午", val)
        self.assertNotIn("10月8日电", val)

    def test_extract_location_ignores_dateline_city(self):
        # 电头城市北京不得被当作事件地点
        body = "新华社北京10月8日电 何立峰同英国财政大臣希利举行视频通话。"
        val, _ = extract_location("", body)
        self.assertEqual(val, UNKNOWN)

    def test_extract_location_from_title_region(self):
        val, ev = extract_location("山西依托有机旱作发展'特''优'农业", "")
        self.assertEqual(val, "山西")
        self.assertIn("山西", ev)

    def test_extract_subject_person_pair(self):
        title = "何立峰同英国财政大臣希利举行视频通话"
        val, _ = extract_subject(title, "")
        self.assertIn("何立峰", val)
        self.assertIn("希利", val)

    def test_extract_subject_not_media_org(self):
        # 电头『新华社』不得成为事件主体
        body = "新华社北京10月8日电 何立峰表示，中英双方应共同落实两国领导人共识。"
        val, _ = extract_subject("", body)
        self.assertNotIn("新华社", val)

    def test_decode_utf8_gbk(self):
        self.assertEqual(decode_bytes("中文".encode("utf-8")), "中文")
        self.assertEqual(decode_bytes("中文".encode("gb18030")), "中文")

    def test_strip_tags_keeps_text(self):
        self.assertIn("何立峰", strip_tags("<h1><p> 何立峰 </p></h1>"))


# ---------------------------------------------------------------------------
# 抓取：日期精度 / 固定 meta 2013 误判
# ---------------------------------------------------------------------------

PEOPLE_LAYOUT = """
<html><body>
<p class="date left">人民日报 <span class="newstime">2026年10月08日 Thu</span></p>
<ul class="news-list">
<li><a href="../../../content/202610/08/content_30184434.html"> 全社会跨区域人员流动量预计21.44亿人次 </a></li>
</ul>
</body></html>
"""

PEOPLE_ARTICLE = """
<html><head>
<meta name='publishdate' content='2013-07-17'>
<title> 全社会跨区域人员流动量预计21.44亿人次 </title>
</head><body>
<h1><p> 全社会跨区域人员流动量预计21.44亿人次 </p></h1>
<span class="newstime">2026年10月08日</span>
<div id="ozoom">
<!--enpcontent--><p> 本报北京10月7日电（记者韩鑫）记者从交通运输部获悉：10月1日至7日，累计全社会跨区域人员流动量预计21.44亿人次。</p><!--/enpcontent-->
</div>
</body></html>
"""

GOV_LIST_JSON = json.dumps([{
    "TITLE": "何立峰同英国财政大臣希利举行视频通话",
    "URL": "https://www.gov.cn/yaowen/liebiao/202610/content_7082751.htm",
    "DOCRELPUBTIME": "2026-10-08",
}])

GOV_ARTICLE = """
<html><head>
<meta name="firstpublishedtime" content="2026-10-08-18:48:00">
<meta name="description" content='中英经济财金对话中方牵头人、国务院副总理何立峰8日下午应约同英国财政大臣希利举行视频通话。'>
<title>何立峰同英国财政大臣希利举行视频通话__中国政府网</title>
</head><body>
<h1 id="ti">何立峰同英国财政大臣希利举行视频通话</h1>
<div class="pages-date">2026-10-08 18:48 <span class="font">来源：新华社</span></div>
<div class="pages_content" id="UCAP-CONTENT">
<p>新华社北京10月8日电 何立峰8日下午应约同英国财政大臣希利举行视频通话，就下一步加强中英经济财金合作交换意见。</p>
</div>
</body></html>
"""

MFA_LIST = """
<html><body><div class="newsList"><ul class="list1">
<li><a href="./202610/t20261008_12036635.shtml" target="_blank">2026年10月8日外交部发言人毛宁主持例行记者会（2026-10-08） </a></li>
</ul></div></body></html>
"""

MFA_ARTICLE = """
<html><head><title>2026年10月8日外交部发言人毛宁主持例行记者会</title></head>
<body><h1>2026年10月8日外交部发言人毛宁主持例行记者会</h1>
<div class="content">外交部发言人毛宁主持例行记者会并回答提问。</div>
</body></html>
"""


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_text(self, url):
        self.calls.append(url)
        for prefix, content in self.routes.items():
            if url.startswith(prefix) or prefix in url:
                return content
        raise FetchError(f"未配置：{url}")


class FetchTests(unittest.TestCase):
    def test_people_uses_layout_date_not_fixed_meta(self):
        # 固定 meta publishdate=2013-07-17 不得被当作真实发布时间
        http = FakeHttp({
            "node_01.html": PEOPLE_LAYOUT,
            "content_30184434.html": PEOPLE_ARTICLE,
        })
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 7, 30)
        out = fetch_people(http, cfg, now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["publish_precision"], "day")
        self.assertEqual(out[0]["publish_time"], "2026-10-08")
        self.assertNotEqual(out[0]["publish_time"], "2013-07-17")

    def test_people_skips_non_today_layout(self):
        layout = PEOPLE_LAYOUT.replace("2026年10月08日", "2026年10月07日")
        http = FakeHttp({"node_01.html": layout})
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 7, 30)
        self.assertEqual(fetch_people(http, cfg, now), [])

    def test_gov_uses_precise_time(self):
        http = FakeHttp({"YAOWENLIEBIAO.json": GOV_LIST_JSON,
                         "content_7082751.htm": GOV_ARTICLE})
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 19, 0)
        out = fetch_gov(http, cfg, now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["publish_precision"], "minute")
        self.assertEqual(out[0]["publish_time"], "2026-10-08 18:48")
        self.assertEqual(out[0]["title"], "何立峰同英国财政大臣希利举行视频通话")

    def test_gov_future_precise_time_dropped(self):
        http = FakeHttp({"YAOWENLIEBIAO.json": GOV_LIST_JSON,
                         "content_7082751.htm": GOV_ARTICLE})
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 7, 30)  # 早于 18:48
        out = fetch_gov(http, cfg, now)
        # 18:48 未来，且 DOCRELPUBTIME 非当日(相对 now 已是当日但精确时间未来) → 丢弃
        self.assertEqual(out, [])

    def test_mfa_one_item_subject(self):
        http = FakeHttp({"fyrbt_674889/": MFA_LIST, "t20261008_12036635.shtml": MFA_ARTICLE})
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 7, 30)
        out = fetch_mfa(http, cfg, now)
        self.assertEqual(len(out), 1)
        self.assertIn("外交部发言人毛宁", out[0]["subject"])
        self.assertEqual(out[0]["publish_precision"], "day")

    def test_mfa_stale_list_empty(self):
        stale = MFA_LIST.replace("2026年10月8日", "2026年10月6日")
        http = FakeHttp({"fyrbt_674889/": stale})
        cfg = _cfg()
        now = _dtb(2026, 10, 8, 7, 30)
        self.assertEqual(fetch_mfa(http, cfg, now), [])


# ---------------------------------------------------------------------------
# 缺要素剔除（strict_elements）
# ---------------------------------------------------------------------------

class StrictFilterTests(unittest.TestCase):
    def _cand(self, **over):
        c = {
            "source_key": "gov", "source": "中国政府网",
            "title": "T", "url": "https://www.gov.cn/x",
            "event_time": "8日下午", "location": "北京", "subject": "何立峰",
        }
        c.update(over)
        return c

    def test_strict_excludes_missing_location(self):
        items, excluded = filter_candidates([self._cand(location=UNKNOWN)], _cfg(strict_elements=True))
        self.assertEqual(items, [])
        self.assertEqual(len(excluded), 1)
        self.assertIn("location", excluded[0]["_excluded_reason"])

    def test_non_strict_keeps_unknown(self):
        items, excluded = filter_candidates(
            [self._cand(location=UNKNOWN, subject=UNKNOWN, event_time=UNKNOWN)],
            _cfg(strict_elements=False))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["location"], UNKNOWN)

    def test_strict_requires_all(self):
        c = self._cand(event_time=UNKNOWN)
        items, excluded = filter_candidates([c], _cfg(strict_elements=True))
        self.assertEqual(items, [])
        self.assertEqual(len(excluded), 1)


# ---------------------------------------------------------------------------
# HTML 注入转义
# ---------------------------------------------------------------------------

class RenderTests(unittest.TestCase):
    def test_html_escapes_title_and_evidence(self):
        it = {
            "title": "<script>alert(1)</script>", "source": "中国政府网",
            "url": "https://www.gov.cn/x", "category": "国内",
            "publish_time": "2026-10-08 18:48", "publish_precision": "minute",
            "event_time": "<img onerror=x>", "location": "北京",
            "subject": "何立峰", "lede": "<b>bold</b>",
            "event_time_evidence": "", "location_evidence": "", "subject_evidence": "",
        }
        meta = {"today": "2026-10-08", "now_display": "x", "mode": "dry-run",
                "excluded": 0, "source_health": {}}
        html_doc = render_html([it], meta)
        self.assertNotIn("<script>alert", html_doc)
        self.assertIn("&lt;script&gt;", html_doc)
        self.assertNotIn("<img onerror", html_doc)

    def test_plain_render_empty_message(self):
        meta = {"today": "2026-10-08", "now_display": "x", "mode": "dry-run",
                "excluded": 0,
                "source_health": {"gov": {"name": "中国政府网", "ok": False, "error": "网络错误"}}}
        txt = render_plain([], meta)
        self.assertIn("今日未取得可核验新消息", txt)
        self.assertIn("网络错误", txt)


# ---------------------------------------------------------------------------
# 同日 claim 防重复
# ---------------------------------------------------------------------------

class LocalClaimTests(unittest.TestCase):
    def test_claim_blocks_duplicate_same_day(self):
        with work_tmpdir() as d:
            c1 = LocalClaim(d, "2026-10-08")
            c2 = LocalClaim(d, "2026-10-08")
            self.assertTrue(c1.create_sending())
            self.assertEqual(c1.read_state(), "sending")
            self.assertFalse(c2.create_sending())  # O_EXCL 拒绝重复

    def test_different_day_ok(self):
        with work_tmpdir() as d:
            self.assertTrue(LocalClaim(d, "2026-10-08").create_sending())
            self.assertTrue(LocalClaim(d, "2026-10-09").create_sending())


# ---------------------------------------------------------------------------
# 邮箱：转义 / 校验 / SMTP uncertain 不重发
# ---------------------------------------------------------------------------

class EmailTests(unittest.TestCase):
    def test_validate_address_rejects_newline(self):
        with self.assertRaises(SendRefused):
            validate_address("a@b.com\r\nBcc: x@y.com", "SMTP_USER")
        with self.assertRaises(SendRefused):
            validate_address("not-an-email", "MAIL_TO")

    def _env(self, **over):
        env = {"SMTP_HOST": "smtp.qq.com", "SMTP_PORT": "465",
               "SMTP_USER": "me@qq.com", "SMTP_PASSWORD": "secret",
               "MAIL_TO": "to@qq.com", "TODAY": "2026-10-08"}
        env.update(over)
        return env

    def test_send_sent_on_accept(self):
        fake = _FakeSMTP()
        def connect(env):
            return fake, False
        self.assertEqual(send_email(self._env(), "<html></html>", "txt", connect_fn=connect), "sent")
        self.assertTrue(fake.sent)

    def test_send_uncertain_on_timeout(self):
        def connect(env):
            raise TimeoutError("connect timeout")
        self.assertEqual(send_email(self._env(), "h", "t", connect_fn=connect), "uncertain")

    def test_send_uncertain_when_smtp_fails_mid_send(self):
        fake = _FakeSMTP(fail_on_send=True)
        def connect(env):
            return fake, False
        self.assertEqual(send_email(self._env(), "h", "t", connect_fn=connect), "uncertain")

    def test_send_refused_bad_auth(self):
        def connect(env):
            return _FakeSMTP(), False
        class AuthFail(_FakeSMTP):
            def login(self, u, p):
                raise smtplib.SMTPAuthenticationError(535, b"bad")
        def connect2(env):
            return AuthFail(), False
        with self.assertRaises(SendRefused):
            send_email(self._env(), "h", "t", connect_fn=connect2)


class _FakeSMTP:
    def __init__(self, fail_on_send=False):
        self.sock = None
        self.fail_on_send = fail_on_send
        self.sent = False

    def login(self, u, p):
        pass

    def send_message(self, msg):
        self.sent = True
        if self.fail_on_send:
            raise smtplib.SMTPException("connection lost after DATA")

    def quit(self):
        pass

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
