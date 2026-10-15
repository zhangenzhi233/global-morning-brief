#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环球早报（official-morning-brief）

从官方机构及权威媒体的精确域名白名单来源，
提取最新可核验消息的「标题 + 短导语原文」，生成手机友好的 HTML 早报与纯文本版本，
并可经 SMTP 发送到邮箱（默认 dry-run，绝不发送）。

设计原则（详见 SOURCE_POLICY.md）：
  * 来源材料限时、限量采集，简体中文整理可使用用户配置的云端模型，不全文搬运。
  * 发布时间与事件时间分开；不把抓取时间当发布时间、不把媒体机构当事件主体、
    不把电头城市当事件地点。
  * strict_elements=true 时，事件时间/实际事件地点/实际主体任一证据缺失的候选
    不进入新闻列表，并明确报告剔除数；宁可少发，不凑条数。
  * 本程序不做、也不声称「自动事实核查」：它只做原文摘录与保守规则匹配。

Python 3.11+；采集/投递使用标准库，繁简转换依赖固定版本 OpenCC。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import base64
import datetime as _dt
import html
import html.parser
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.headerregistry import Address

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BEIJING_TZ = _dt.timezone(_dt.timedelta(hours=8), name="Asia/Shanghai")
UNKNOWN = "原文未明确"
USER_AGENT = (
    "Mozilla/5.0 (compatible; official-morning-brief/1.0; +https://github.com/)"
)
GITHUB_API = "https://api.github.com"

# 白名单 host（不含泛 .gov.cn 自动放行；必须精确匹配）
DEFAULT_ALLOWED_HOSTS = ['bbc.co.uk', 'bbc.com', 'feeds.bbci.co.uk', 'news.un.org', 'paper.people.com.cn', 'rss.dw.com', 'science.nasa.gov', 'wmo.int', 'www.bbc.co.uk', 'www.bbc.com', 'www.channelnewsasia.com', 'www.dw.com', 'www.fao.org', 'www.federalreserve.gov', 'www.france24.com', 'www.gov.cn', 'www.mfa.gov.cn', 'www.nasa.gov', 'www.news.cn', 'www.rfi.fr']
INTERNATIONAL_SOURCE_KEYS = {"un", "fao", "wmo"}

# 不允许作为事件主体的纯媒体/通讯社机构（防止把媒体机构当事件主体）
MEDIA_ORGS = {
    "新华社", "人民日报", "人民网", "央视", "央视新闻", "中央广播电视总台",
    "中新社", "中国新闻网", "光明日报", "经济日报", "中国日报", "环球时报",
    "科技日报", "工人日报", "中国青年报", "北京日报", "解放日报",
}

# 地点词表（省级行政区 + 常见城市/地区/国家，用于保守地点匹配）
LOCATION_WORDS = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "中国", "全国",
    "深圳", "广州", "杭州", "南京", "武汉", "成都", "西安", "青岛", "大连",
    "宁波", "厦门", "苏州", "天津", "沈阳", "哈尔滨", "长春", "济南", "郑州",
    "长沙", "昆明", "贵阳", "兰州", "银川", "西宁", "乌鲁木齐", "拉萨", "海口",
    "雄安", "粤港澳大湾区", "长三角", "京津冀",
]

# 分类关键词（仅辅助阅读，非 AI 分析）
CATEGORY_RULES = [
    ("国际", ["外交", "外交部", "访华", "两国", "中英", "中美", "中俄", "中欧",
              "中日", "中韩", "中法", "中德", "联合国", "大使", "使节", "国际",
              "多哥", "英国", "美国", "俄罗斯", "欧盟", "东盟", "出访", "双边",
              "多边", "记者会", "答记者问", "例行记者会"]),
    ("科技", ["航天", "卫星", "火箭", "太空", "科技", "5G", "AI", "人工智能",
              "芯片", "北斗", "智能", "数字化", "机器人", "大模型", "算力",
              "6G", "量子", "无人", "自动驾驶", "高端智能农机"]),
    ("民生经济", ["经济", "消费", "市场", "金融", "证券", "汇率", "就业", "养老",
                  "医疗", "教育", "交通", "出行", "假期", "秋粮", "农业", "电力",
                  "大宗商品", "社保", "民生", "住房", "消费券", "收入", "价格",
                  "粮食", "外贸", "进出口", "客流", "人员流动", "客流", "农机",
                  "入境游", "游客", "文旅"]),
]

# 默认配置（config.json 可覆盖）
DEFAULT_CONFIG = {'strict_elements': True,
 'strict_require_fields': ['event_time', 'location', 'subject'],
 'max_items': 24,
 'international_reserved_items': 6,
 'max_candidates_per_source': 10,
 'fresh_hours': 24,
 'http_timeout': 15,
 'max_retries': 0,
 'lede_char_limit': 80,
 'evidence_char_limit': 48,
 'people_max_layouts': 3,
 'allowed_hosts': ['bbc.co.uk',
                   'bbc.com',
                   'feeds.bbci.co.uk',
                   'news.un.org',
                   'paper.people.com.cn',
                   'rss.dw.com',
                   'science.nasa.gov',
                   'wmo.int',
                   'www.bbc.co.uk',
                   'www.bbc.com',
                   'www.channelnewsasia.com',
                   'www.dw.com',
                   'www.fao.org',
                   'www.federalreserve.gov',
                   'www.france24.com',
                   'www.gov.cn',
                   'www.mfa.gov.cn',
                   'www.nasa.gov',
                   'www.news.cn',
                   'www.rfi.fr'],
 'sources': {'gov': {'name': '中国政府网',
                     'list_url': 'https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json',
                     'enabled': True},
             'people': {'name': '人民日报',
                        'layout_base': 'https://paper.people.com.cn/rmrb/pc/layout/{ymd}/node_{nn}.html',
                        'enabled': True},
             'mfa': {'name': '外交部',
                     'list_url': 'https://www.mfa.gov.cn/wjdt_674879/fyrbt_674889/',
                     'enabled': True},
             'un': {'name': '联合国新闻',
                    'kind': 'un_rss',
                    'list_url': 'https://news.un.org/feed/subscribe/zh/news/all/rss.xml',
                    'enabled': True,
                    'max_candidates': 6},
             'fao': {'name': '联合国粮农组织',
                     'kind': 'fao_rss',
                     'list_url': 'https://www.fao.org/feeds/fao-newsroom-rss-zh',
                     'enabled': True,
                     'max_candidates': 6},
             'wmo': {'name': '世界气象组织',
                     'kind': 'wmo_html',
                     'list_url': 'https://wmo.int/zh-hans',
                     'enabled': True,
                     'max_candidates': 6},
             'bbc_world': {'name': 'BBC · 政治与安全',
                           'kind': 'bbc_rss',
                           'list_url': 'https://feeds.bbci.co.uk/news/world/rss.xml',
                           'article_hosts': ['www.bbc.com',
                                             'www.bbc.co.uk',
                                             'bbc.com',
                                             'bbc.co.uk'],
                           'topic': '政治与安全',
                           'max_candidates': 5,
                           'enabled': True},
             'bbc_business': {'name': 'BBC · 经济与金融',
                              'kind': 'bbc_rss',
                              'list_url': 'https://feeds.bbci.co.uk/news/business/rss.xml',
                              'article_hosts': ['www.bbc.com',
                                                'www.bbc.co.uk',
                                                'bbc.com',
                                                'bbc.co.uk'],
                              'topic': '经济与金融',
                              'max_candidates': 5,
                              'enabled': True},
             'bbc_technology': {'name': 'BBC · 科技与产业',
                                'kind': 'bbc_rss',
                                'list_url': 'https://feeds.bbci.co.uk/news/technology/rss.xml',
                                'article_hosts': ['www.bbc.com',
                                                  'www.bbc.co.uk',
                                                  'bbc.com',
                                                  'bbc.co.uk'],
                                'topic': '科技与产业',
                                'max_candidates': 5,
                                'enabled': True},
             'bbc_science': {'name': 'BBC · 气候与环境',
                             'kind': 'bbc_rss',
                             'list_url': 'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml',
                             'article_hosts': ['www.bbc.com',
                                               'www.bbc.co.uk',
                                               'bbc.com',
                                               'bbc.co.uk'],
                             'topic': '气候与环境',
                             'max_candidates': 5,
                             'enabled': True},
             'bbc_health': {'name': 'BBC · 健康与公共卫生',
                            'kind': 'bbc_rss',
                            'list_url': 'https://feeds.bbci.co.uk/news/health/rss.xml',
                            'article_hosts': ['www.bbc.com',
                                              'www.bbc.co.uk',
                                              'bbc.com',
                                              'bbc.co.uk'],
                            'topic': '健康与公共卫生',
                            'max_candidates': 5,
                            'enabled': True},
             'xinhua_world': {'name': '新华社 · 国际',
                              'kind': 'xinhua_html',
                              'list_url': 'https://www.news.cn/world/',
                              'article_hosts': ['www.news.cn'],
                              'topic': '',
                              'max_candidates': 10,
                              'enabled': True},
             'xinhua_tech': {'name': '新华社 · 科技',
                             'kind': 'xinhua_html',
                             'list_url': 'https://www.news.cn/tech/',
                             'article_hosts': ['www.news.cn'],
                             'topic': '科技与产业',
                             'max_candidates': 8,
                             'enabled': True},
             'xinhua_economy': {'name': '新华社 · 经济',
                                'kind': 'xinhua_html',
                                'list_url': 'https://www.news.cn/fortune/',
                                'article_hosts': ['www.news.cn'],
                                'topic': '经济与金融',
                                'max_candidates': 8,
                                'enabled': True},
             'dw': {'name': '德国之声中文',
                    'kind': 'dw_rss',
                    'list_url': 'https://rss.dw.com/rdf/rss-chi-all',
                    'article_hosts': ['www.dw.com'],
                    'topic': '',
                    'max_candidates': 10,
                    'enabled': True},
             'rfi': {'name': '法国国际广播电台中文',
                     'kind': 'rfi_rss',
                     'list_url': 'https://www.rfi.fr/cn/rss',
                     'article_hosts': ['www.rfi.fr'],
                     'topic': '',
                     'max_candidates': 10,
                     'enabled': True},
             'france24': {'name': 'France 24',
                          'kind': 'f24_rss',
                          'list_url': 'https://www.france24.com/en/rss',
                          'article_hosts': ['www.france24.com'],
                          'topic': '',
                          'max_candidates': 8,
                          'enabled': True},
             'cna': {'name': 'CNA（含署名 Reuters / AFP 稿件）',
                     'kind': 'cna_rss',
                     'list_url': 'https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml',
                     'article_hosts': ['www.channelnewsasia.com'],
                     'topic': '',
                     'max_candidates': 12,
                     'enabled': True},
             'nasa': {'name': 'NASA',
                      'kind': 'nasa_rss',
                      'list_url': 'https://www.nasa.gov/feed/',
                      'article_hosts': ['www.nasa.gov', 'science.nasa.gov'],
                      'topic': '科技与产业',
                      'max_candidates': 6,
                      'enabled': True},
             'fed': {'name': '美联储',
                     'kind': 'fed_rss',
                     'list_url': 'https://www.federalreserve.gov/feeds/press_all.xml',
                     'article_hosts': ['www.federalreserve.gov'],
                     'topic': '经济与金融',
                     'max_candidates': 4,
                     'enabled': True}},
 'balanced_selection': True,
 'max_per_source': 6,
 'max_per_region': 10,
 'fetch_workers': 6,
 'include_reading_links': True,
 'max_reading_links': 18}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    pass


class FetchError(Exception):
    """单个来源抓取/解析失败（不中断其它来源）。"""


class ClaimError(Exception):
    """发送前 claim 阶段网络结果未知，禁止发送。"""


class SendRefused(Exception):
    """配置校验失败，拒绝发送。"""


# ---------------------------------------------------------------------------
# 文本/HTML 处理（标准库 html.parser，避免手写脆弱解析器）
# ---------------------------------------------------------------------------

class _TextExtractor(html.parser.HTMLParser):
    """把 HTML 转成保留段落边界的纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript", "textarea"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5",
                     "tr", "blockquote", "section"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "textarea"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "tr",
                     "blockquote", "section"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_tags(fragment: str) -> str:
    """去掉 HTML 标签，返回纯文本。"""
    p = _TextExtractor()
    p.feed(fragment)
    return p.text()


def clean_text(s: str) -> str:
    """折叠空白，规范化换行。"""
    s = s.replace("\u3000", " ")
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _attr_value(attrs: str, attr: str) -> str | None:
    m = re.search(
        r"\b" + re.escape(attr) + r"\s*=\s*([\"'])(.*?)\1", attrs, re.I | re.S
    )
    if m:
        return html.unescape(m.group(2)).strip()
    return None


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I | re.S)


def extract_meta(page: str, name: str) -> str | None:
    """按 name/property 提取 <meta> 的 content。"""
    for tag in _META_TAG_RE.findall(page):
        nm = re.search(r"(?:name|property)\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
        ct = re.search(r"content\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
        if nm and ct and nm.group(2).strip().lower() == name.lower():
            return html.unescape(ct.group(2)).strip()
    return None


_OPEN_TAG_RE = re.compile(r"<(\w+)\b([^>]*)>", re.I | re.S)
_CLOSE_TAG_RE = re.compile(r"</(\w+)\s*>", re.I | re.S)


def extract_element_inner(page: str, id_: str | None = None,
                          class_: str | None = None) -> str | None:
    """按 id 或 class 提取元素内部 HTML（按标签深度匹配同名闭合标签）。"""
    for m in _OPEN_TAG_RE.finditer(page):
        tag = m.group(1).lower()
        attrs = m.group(2)
        ok = False
        if id_ is not None and _attr_value(attrs, "id") == id_:
            ok = True
        if class_ is not None:
            c = _attr_value(attrs, "class")
            if c and class_ in [x.strip() for x in c.split()]:
                ok = True
        if not ok:
            continue
        depth = 1
        i = m.end()
        while i < len(page):
            cm = _CLOSE_TAG_RE.match(page, i)
            om = _OPEN_TAG_RE.match(page, i)
            if cm:
                if cm.group(1).lower() == tag:
                    depth -= 1
                    if depth == 0:
                        return page[m.end():i]
                i = cm.end()
            elif om:
                if om.group(1).lower() == tag:
                    depth += 1
                i = om.end()
            else:
                i += 1
        return page[m.end():]
    return None


def extract_h1(page: str) -> str | None:
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.I | re.S)
    if not m:
        return None
    return clean_text(strip_tags(m.group(1))) or None


def paragraphs(text: str) -> list[str]:
    """把纯文本按段落拆开并清理。"""
    out = []
    for seg in re.split(r"\n+", text):
        seg = clean_text(seg)
        if seg:
            out.append(seg)
    return out


def truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def first_lede(text: str, limit: int) -> str:
    """取第一个非空段落作为短导语。"""
    for p in paragraphs(text):
        if len(p) >= 4:
            return truncate(p, limit)
    return truncate(clean_text(text), limit)


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def now_beijing() -> _dt.datetime:
    return _dt.datetime.now(BEIJING_TZ)


def parse_now_arg(s: str) -> _dt.datetime:
    """解析 --now 参数，如 2026-10-08T07:30:00+08:00。"""
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise ConfigError(f"无法解析 --now 时间：{s}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def beijing_date_str(dt: _dt.datetime) -> str:
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")


def parse_gov_pubtime(s: str) -> _dt.datetime | None:
    """解析中国政府网 firstpublishedtime：2026-10-08-18:48:00。"""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[-\s](\d{2}):(\d{2})(?::(\d{2}))?$", s.strip())
    if not m:
        return None
    y, mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    sec = int(m.group(6)) if m.group(6) else 0
    try:
        return _dt.datetime(y, mo, d, h, mi, sec, tzinfo=BEIJING_TZ)
    except ValueError:
        return None


def parse_cn_date_yyyy_mm_dd(s: str) -> str | None:
    """从『2026年10月8日』提取 YYYY-MM-DD。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if not m:
        return None
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d.isoformat()


def parse_date_yyyymmdd(s: str) -> str | None:
    """从 2026-10-08 / 20261008 / 2026-10-08 00:00:00:0 提取 YYYY-MM-DD。"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def is_precise_fresh(pub: _dt.datetime, now: _dt.datetime, fresh_hours: int) -> bool:
    """精确（分钟级）发布时间：必须落在 [now-fresh_hours, now]。"""
    lo = now - _dt.timedelta(hours=fresh_hours)
    return lo <= pub.astimezone(BEIJING_TZ) <= now.astimezone(BEIJING_TZ)


def is_same_day(date_str: str, now: _dt.datetime) -> bool:
    return date_str == beijing_date_str(now)


# ---------------------------------------------------------------------------
# 字段保守提取（只做规则匹配，保留短原文证据）
# ---------------------------------------------------------------------------

_DATELINE_RE = re.compile(
    r"^[\s\u3000]*(?:新华社|中新社|本报|央视|总台|央视新闻|记者)[^\d]{0,12}"
    r"\d{1,2}月\d{1,2}日电[\s\u3000]*"
)


def strip_dateline(text: str) -> str:
    """去掉文首电头（如『新华社北京10月8日电』），避免把电头城市当事件地点。"""
    return _DATELINE_RE.sub("", text, count=1)


def event_lead(body: str) -> str:
    """只从导语识别同一事件，避免拼接后文背景、引用与作者地址。"""
    text = strip_dateline(body)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[0] if lines else "")[:500]


# 事件时间优先级：先匹配『X月X日(至…日)』，再『X日(上午/下午/晚上)』，再时间词。
_EVENT_TIME_PATTERNS = [
    (re.compile(r"(\d{1,2}月\d{1,2}日(?:\s*(?:至|到|—|-|～)\s*\d{1,2}月?\d{1,2}日)?)"),
     "date"),
    (re.compile(r"(\d{1,2}日\s*(?:上午|下午|晚上|凌晨|早间|晚间)?)"), "day"),
    (re.compile(r"(今[天日]|昨日|昨天|近日|日前|本月|今年以来|今年)"), "word"),
    (re.compile(r"(\d{1,2}月\d{1,2}日)"), "date"),
]


def _evidence_window(text: str, start: int, end: int, limit: int) -> str:
    lo = max(0, start - 12)
    hi = min(len(text), end + 16)
    return truncate(clean_text(text[lo:hi]), limit)


def extract_event_time(title: str, body: str, limit: int = 80) -> tuple[str, str]:
    """返回 (事件时间, 证据片段)。未找到时返回 (UNKNOWN, "")。"""
    text = event_lead(body)
    for pat, _kind in _EVENT_TIME_PATTERNS:
        m = pat.search(text)
        if m:
            return clean_text(m.group(1)), _evidence_window(text, m.start(), m.end(), limit)
    # 退而求其次：正文里的『X月X日』（已含在上方 date 模式）
    return UNKNOWN, ""


_LOC_ACTION = (
    r"举行|召开|举办|调研|考察|会见|会晤|签署|启动|投产|开工|通车|运营|"
    r"访问|出席|落地|签约|揭牌|开通|建设|推进|开展|视察"
)
_LOC_PATTERNS = [
    re.compile(r"在([\u4e00-\u9fa5]{2,14}?)(?:" + _LOC_ACTION + r")"),
    re.compile(r"于([\u4e00-\u9fa5]{2,14}?)(?:" + _LOC_ACTION + r")"),
    re.compile(r"(?:前往|赴|访问)([\u4e00-\u9fa5]{2,12})"),
    re.compile(r"^(?:在)?([\u4e00-\u9fa5]{2,6})(?:" + _LOC_ACTION + r")"),
]


def extract_location(title: str, body: str, limit: int = 80) -> tuple[str, str]:
    """保守提取实际事件地点；电头城市已被 strip_dateline 排除。"""
    # 1) 『访华/出访X』等暗含目的地
    m = re.search(r"访(华|美|俄|欧|日|韩|英|法|德)", title)
    if m:
        country = {"华": "中国", "美": "美国", "俄": "俄罗斯", "欧": "欧洲",
                   "日": "日本", "韩": "韩国", "英": "英国", "法": "法国",
                   "德": "德国"}.get(m.group(1), m.group(1))
        return country, truncate(title, limit)
    # 2) 标题/正文中的显式地点模式
    for text in (title, event_lead(body)):
        for pat in _LOC_PATTERNS:
            m = pat.search(text)
            if m:
                loc = clean_text(m.group(1))
                # 只有显式地点语法且名称可识别才接受，不能把文化强国等词当地点。
                if loc in LOCATION_WORDS or re.fullmatch(
                        r"[\u4e00-\u9fa5]{2,12}(?:省|市|县|镇|村|自治区|人民大会堂|航天发射场)", loc):
                    return loc, _evidence_window(text, m.start(), m.end(), limit)
    # 标题以地名开始并直接陈述当地行动时，地理范围有明确证据。
    for loc in sorted(LOCATION_WORDS, key=len, reverse=True):
        if re.match(re.escape(loc) + r"(?:省|市)?(?:依托|加快|推进|启动|开展|发生|发布)", title):
            return loc, truncate(title, limit)
    return UNKNOWN, ""


_SUBJ_PATTERNS = [
    re.compile(r"([\u4e00-\u9fa5·]{2,6})(?:同|与|和)([^，。；;]{2,24}?)"
               r"(?:举行|会见|通话|会谈|会晤|视频|主持|出席|签署|致电|致信|互致|会谈)"),
    re.compile(r"([\u4e00-\u9fa5·]{2,6})(?:表示|强调|指出|宣布|要求|会见|调研|"
               r"考察|出席|主持|签署|致电|致信|应约|就任|会见)"),
    re.compile(r"外交部发言人([\u4e00-\u9fa5·]{2,4})"),
]
_SUBJ_ORG_RE = re.compile(
    r"(国务院|国务院办公厅|外交部|交通运输部|教育部|财政部|商务部|农业农村部|"
    r"工业和信息化部|国家发展改革委|国家统计局|海关总署|税务总局|市场监管总局|"
    r"证监会|中国人民银行|人民银行|银保监会|国资委|[\u4e00-\u9fa5]{2,6}(?:政府|省政府|市政府|委员会))"
)
_SUBJ_REGION_ACTOR_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,6})(?:依托|加快|推进|打造|发布|出台|启动|开展|发展|深化|推动|建设)"
)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = [], []
    for it in items:
        if it and it not in seen:
            seen.append(it)
            out.append(it)
    return out


def extract_subject(title: str, body: str, limit: int = 80) -> tuple[str, str]:
    """保守提取实际主体（人物/机构），排除纯媒体机构。返回 (主体, 证据)。"""
    # 只采用标题中完整的行动主体，或导语中与行动动词紧邻的官方机构。
    spokesperson = re.search(r"外交部发言人([\u4e00-\u9fa5·]{2,4})(?=主持|答记者问|表示)", title)
    if spokesperson:
        return "外交部发言人" + spokesperson.group(1), truncate(title, limit)
    pair = re.match(r"^([\u4e00-\u9fa5·]{2,6})(?:同|与)([^，。；]{2,24}?)(?=举行|会见|会谈|通话|会晤)", title)
    if pair and any(role in pair.group(2) for role in ("主席", "总统", "总理", "大臣", "部长", "书记")):
        return pair.group(1) + "、" + pair.group(2), truncate(title, limit)
    action = r"(?:将|同|与|主持|出席|会见|举行|宣布|发布|表示|强调|指出|签署|赴|应约)"
    m = re.match(r"^([\u4e00-\u9fa5·]{2,24}?)(?=" + action + r")", title)
    if m:
        actor = m.group(1)
        # 普通主题词、地域或媒体不作为人物/机构；职位或明确机构可采用。
        is_role = any(w in actor for w in ("主席", "总统", "总理", "大臣", "部长", "发言人", "书记"))
        is_org = bool(_SUBJ_ORG_RE.fullmatch(actor))
        is_name = 2 <= len(actor) <= 4 and re.match(r"^" + re.escape(actor) + r"(?:同|与|主持|出席|会见|赴)", title)
        if (is_role or is_org or is_name) and actor not in LOCATION_WORDS and not any(w in actor for w in MEDIA_ORGS):
            return actor, truncate(title, limit)
    lead = event_lead(body)
    for m in _SUBJ_ORG_RE.finditer(lead):
        suffix = lead[m.end():m.end() + 12]
        if re.match(r"(?:最新)?(?:发布|宣布|通报|表示|指出|召开|举行|启动|调度|统计|印发|批准|决定)", suffix):
            return m.group(1), _evidence_window(lead, m.start(), m.end(), limit)
    return UNKNOWN, ""


# ---------------------------------------------------------------------------
# 分类（规则辅助阅读）
# ---------------------------------------------------------------------------

def categorize(title: str, lede: str) -> str:
    text = title + " " + lede
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw in text:
                return cat
    return "国内"


# ---------------------------------------------------------------------------
# 网络抓取（TLS 校验、超时、重试、白名单、拒绝重定向）
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FetchError(f"拒绝重定向：{newurl}")


class HttpClient:
    def __init__(self, config: dict) -> None:
        self.timeout = int(config.get("http_timeout", 20))
        self.max_retries = int(config.get("max_retries", 1))
        self.allowed_hosts = set(config.get("allowed_hosts") or DEFAULT_ALLOWED_HOSTS)
        self._ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(_NoRedirect())
        self._opener = opener
        self.failures: list[dict] = []

    def check_url(self, url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise FetchError(f"拒绝非 HTTPS：{url}")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise FetchError(f"拒绝白名单外主机：{host}")
        if parts.username or parts.password or parts.port not in (None, 443):
            raise FetchError("来源 URL 不允许账号信息或非 HTTPS 标准端口")
        return url

    def get(self, url: str) -> bytes:
        url = self.check_url(url)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                          "Accept": "*/*"})
                with self._opener.open(req, timeout=self.timeout) as resp:
                    data = resp.read(3_000_001)
                    if len(data) > 3_000_000:
                        raise FetchError("来源响应超过 3 MB 限制")
                    return data
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    self.failures.append({"url": url, "error": f"HTTP {exc.code}"})
                    raise FetchError(f"HTTP {exc.code}：{url}") from exc
                last_err = exc
            except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
                last_err = exc
        self.failures.append({"url": url, "error": type(last_err).__name__})
        raise FetchError(f"抓取失败（重试后）：{url}：{last_err}")

    def get_text(self, url: str) -> str:
        raw = self.get(url)
        return decode_bytes(raw)


def decode_bytes(raw: bytes) -> str:
    """正确识别 UTF-8 与 GBK。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 来源解析
# ---------------------------------------------------------------------------

def _new_candidate(source_key: str, source_name: str, title: str, url: str) -> dict:
    return {
        "source_key": source_key,
        "source": source_name,
        "title": title,
        "url": url,
        "lede": "",
        "publish_time": "",
        "publish_precision": "",
        "publish_tz": "+08:00",
        "event_time": UNKNOWN,
        "event_time_evidence": "",
        "location": UNKNOWN,
        "location_evidence": "",
        "subject": UNKNOWN,
        "subject_evidence": "",
        "category": "国内",
        "origin": "",
    }


def fetch_gov(http: HttpClient, cfg: dict, now: _dt.datetime) -> list[dict]:
    """中国政府网要闻：列表 JSON → 逐条正文核对精确发布时间。"""
    src = cfg["sources"]["gov"]
    text = http.get_text(src["list_url"])
    data = json.loads(text)
    if not isinstance(data, list):
        raise FetchError("gov 列表 JSON 结构异常（非列表）")
    today = beijing_date_str(now)
    yesterday = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    max_cand = int(cfg.get("max_candidates_per_source", 8))
    # 只保留当日/昨日条目（正文里再核对精确时间）
    candidates: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        pub = it.get("DOCRELPUBTIME", "")
        if pub in (today, yesterday):
            candidates.append(it)
        if len(candidates) >= max_cand:
            break
    out: list[dict] = []
    for it in candidates:
        title = (it.get("TITLE") or "").strip()
        url = (it.get("URL") or "").strip()
        if not title or not url:
            continue
        cand = _new_candidate("gov", src["name"], title, url)
        try:
            page = http.get_text(url)
        except FetchError:
            continue  # 单条正文失败，跳过该条（不影响其它来源）
        # 标题
        h1 = extract_h1(page)
        if h1:
            cand["title"] = h1
        # 精确发布时间（firstpublishedtime 优先，其次 pages-date）
        pub_dt = None
        fp = extract_meta(page, "firstpublishedtime")
        if fp:
            pub_dt = parse_gov_pubtime(fp)
        if pub_dt is None:
            date_area = extract_element_inner(page, class_="pages-date") or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", strip_tags(date_area))
            if m:
                pub_dt = parse_gov_pubtime(f"{m.group(1)}-{m.group(2)}:00")
        if pub_dt is not None:
            # 精确时间必须落在 [now-fresh_hours, now]；过期或未来都丢弃
            if is_precise_fresh(pub_dt, now, int(cfg.get("fresh_hours", 24))):
                cand["publish_time"] = pub_dt.strftime("%Y-%m-%d %H:%M")
                cand["publish_precision"] = "minute"
            else:
                continue
        elif it.get("DOCRELPUBTIME") == today:
            # 无精确时间时按当日日期精度（不伪造几点）
            cand["publish_time"] = today
            cand["publish_precision"] = "day"
        else:
            continue  # 昨日条目但无有效精确时间，丢弃
        # 来源（正文里的『来源：新华社』等）
        origin = extract_meta(page, "source")
        if not origin:
            date_area = extract_element_inner(page, class_="pages-date") or ""
            source_match = re.search(r"来源：[\s\u3000]*([^\n\r]+)", strip_tags(date_area))
            origin = source_match.group(1).strip() if source_match else None
        if origin:
            cand["origin"] = origin.strip()
        # 正文与短导语
        body_html = extract_element_inner(page, id_="UCAP-CONTENT") or ""
        body = clean_text(strip_tags(body_html))
        cand["_source_text"] = body[:24000]
        desc = extract_meta(page, "description")
        if desc and len(desc) >= 4:
            cand["lede"] = truncate(clean_text(desc), int(cfg.get("lede_char_limit", 100)))
        else:
            cand["lede"] = first_lede(body, int(cfg.get("lede_char_limit", 100)))
        lim = int(cfg.get("evidence_char_limit", 80))
        cand["event_time"], cand["event_time_evidence"] = extract_event_time(cand["title"], body, lim)
        cand["location"], cand["location_evidence"] = extract_location(cand["title"], body, lim)
        cand["subject"], cand["subject_evidence"] = extract_subject(cand["title"], body, lim)
        cand["category"] = categorize(cand["title"], cand["lede"])
        out.append(cand)
    return out


def _people_extract_articles(page: str) -> list[tuple[str, str]]:
    """从版面页提取 (标题, 相对链接) 列表。"""
    items: list[tuple[str, str]] = []
    for m in re.finditer(r'<a[^>]+href\s*=\s*["\']([^"\']*content[^"\']*\.html)["\'][^>]*>(.*?)</a>',
                         page, re.I | re.S):
        href = html.unescape(m.group(1)).strip()
        text = clean_text(strip_tags(m.group(2)))
        if not text or text in ("导读", "图片报道", "返回目录"):
            continue
        if "/content/" in href or href.startswith("content_"):
            items.append((text, href))
    # 去重（按链接）
    seen, out = set(), []
    for t, h in items:
        if h not in seen:
            seen.add(h)
            out.append((t, h))
    return out


def _resolve_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def fetch_people(http: HttpClient, cfg: dict, now: _dt.datetime) -> list[dict]:
    """人民日报：当日第 01-03 版，仅同一天当期，日期精度=day。"""
    src = cfg["sources"]["people"]
    d = now.astimezone(BEIJING_TZ)
    ymd = d.strftime("%Y%m/%d")
    base = src["layout_base"]
    max_layouts = int(cfg.get("people_max_layouts", 3))
    max_cand = int(cfg.get("max_candidates_per_source", 8))
    out: list[dict] = []
    collected: list[tuple[str, str]] = []
    fetched_any = False
    last_err: FetchError | None = None
    for nn in range(1, max_layouts + 1):
        layout_url = base.format(ymd=ymd, nn=f"{nn:02d}")
        try:
            page = http.get_text(layout_url)
            fetched_any = True
        except FetchError as exc:
            last_err = exc
            continue  # 单个版面失败不拖垮整个来源
        # 版面日期必须等于当日（同一天当期才可信）
        m = re.search(r'class=["\']newstime["\'][^>]*>\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', page)
        if not m:
            m = re.search(r"人民日报\s*<span[^>]*>\s*(\d{4})年(\d{1,2})月(\d{1,2})日", page)
        if not m:
            raise FetchError("人民日报版面缺少可见版期，不能根据请求 URL 推定日期")
        else:
            layout_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if not is_same_day(layout_date, now):
            continue  # 不是当日版面，跳过（不冒充当日）
        for t, h in _people_extract_articles(page):
            collected.append((t, _resolve_url(layout_url, h)))
        if len(collected) >= max_cand:
            break
    if not fetched_any and last_err is not None:
        raise last_err  # 所有版面都抓取失败才视为源错误
    for title, url in collected[:max_cand]:
        cand = _new_candidate("people", src["name"], title, url)
        cand["publish_time"] = beijing_date_str(now)
        cand["publish_precision"] = "day"
        try:
            page = http.get_text(url)
        except FetchError:
            continue
        article_date = extract_element_inner(page, class_="newstime") or ""
        if not is_same_day(parse_cn_date_yyyy_mm_dd(strip_tags(article_date)) or "", now):
            continue
        h1 = extract_h1(page)
        if h1:
            cand["title"] = h1
        # 正文（ozoom 或 enpcontent 区域）
        body_html = extract_element_inner(page, id_="ozoom") or ""
        if not body_html:
            body_html = extract_element_inner(page, id_="articleContent") or ""
        body = clean_text(strip_tags(body_html))
        cand["_source_text"] = body[:24000]
        cand["lede"] = first_lede(body, int(cfg.get("lede_char_limit", 100)))
        lim = int(cfg.get("evidence_char_limit", 80))
        cand["event_time"], cand["event_time_evidence"] = extract_event_time(cand["title"], body, lim)
        cand["location"], cand["location_evidence"] = extract_location(cand["title"], body, lim)
        cand["subject"], cand["subject_evidence"] = extract_subject(cand["title"], body, lim)
        cand["category"] = categorize(cand["title"], cand["lede"])
        out.append(cand)
    return out


def fetch_mfa(http: HttpClient, cfg: dict, now: _dt.datetime) -> list[dict]:
    """外交部例行记者会：列表最新一条，区分『发布实录』与实录中事件。"""
    src = cfg["sources"]["mfa"]
    page = http.get_text(src["list_url"])
    # 列表项：<li><a href="./202610/t20261008_12036635.shtml" ...>标题（YYYY-MM-DD）</a></li>
    items = re.findall(
        r'<a\s+[^>]*href\s*=\s*["\']\./(\d{6}/t\d{8}_\d+\.shtml)["\'][^>]*>(.*?)</a>',
        page, re.I | re.S)
    if not items:
        # 兼容 ./202610/t20261008_12036635.shtml 前有 ./ 的变体
        items = re.findall(
            r'<a\s+[^>]*href\s*=\s*["\'](\./(?:\.\./)*\d{6}/t\d{8}_\d+\.shtml)["\'][^>]*>(.*?)</a>',
            page, re.I | re.S)
    if not items:
        raise FetchError("外交部列表未解析到条目")
    rel, raw_title = items[0]
    title = clean_text(strip_tags(raw_title))
    # 解析日期（标题里『2026年10月8日』）
    dstr = parse_cn_date_yyyy_mm_dd(title)
    if not dstr or not is_same_day(dstr, now):
        return []  # 最新一条不是当日（例行记者会非每日举行），不冒充
    url = _resolve_url(src["list_url"], rel)
    cand = _new_candidate("mfa", src["name"], title, url)
    cand["publish_time"] = dstr
    cand["publish_precision"] = "day"
    try:
        article = http.get_text(url)
    except FetchError:
        return []
    h1 = extract_h1(article)
    if h1:
        cand["title"] = h1
    # 正文：例行记者会实录（区分『发布了实录』与实录中的问答事件，不合并为多条）
    body_html = (extract_element_inner(article, id_="content")
                 or extract_element_inner(article, class_="content")
                 or extract_element_inner(article, id_="UCAP-CONTENT") or "")
    body = clean_text(strip_tags(body_html))
    cand["_source_text"] = body[:24000]
    cand["lede"] = truncate(cand["title"], int(cfg.get("lede_char_limit", 100)))
    lim = int(cfg.get("evidence_char_limit", 80))
    # 事件时间：记者会举行日（与标题一致的日期精度）
    m = re.search(r"(\d{1,2})月(\d{1,2})日", title)
    cand["event_time"] = clean_text(m.group(0)) if m else dstr
    cand["event_time_evidence"] = truncate(title, lim)
    cand["location"] = UNKNOWN
    cand["location_evidence"] = ""
    cand["subject"], cand["subject_evidence"] = extract_subject(cand["title"], body, lim)
    cand["category"] = "国际"
    out = [cand]
    return out


# ---------------------------------------------------------------------------
# 过滤（strict_elements）与汇总
# ---------------------------------------------------------------------------

def fetch_international(http: HttpClient, cfg: dict, now: _dt.datetime, key: str) -> list[dict]:
    from international_sources import fetch_records
    from global_evidence import extract_elements, coverage_region
    src = cfg["sources"][key]
    records = fetch_records(http, key, src, now,
                            fresh_hours=int(cfg.get("fresh_hours", 24)),
                            max_candidates=int(cfg.get("max_candidates_per_source", 24)))
    candidates = []
    for record in records:
        precision = record.get("publish_precision")
        stamp = record.get("publish_time", "")
        if precision == "minute":
            published = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if published.tzinfo is None or not is_precise_fresh(published, now, int(cfg.get("fresh_hours", 24))):
                continue
            display_time = published.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        elif precision == "day" and is_same_day(stamp, now):
            display_time = stamp
        else:
            continue
        title, body, url = record["title"], record["body"], record["url"]
        http.check_url(url)
        cand = _new_candidate(key, src.get("display_name",src["name"]), title, url)
        cand["_source_text"] = body[:24000]
        cand.update(extract_elements(title, body, int(cfg.get("evidence_char_limit", 32))))
        from newsroom import topic
        cand.update({"publish_time": display_time, "publish_precision": precision,
                     "publish_time_original": stamp, "origin": record.get("origin", src["name"]),
                     "content_basis": record.get("content_basis", "正文"),
                     "lede": first_lede(re.sub(r'^(?:新华社|中新社)[\u4e00-\u9fff·]{1,18}\d{1,2}月\d{1,2}日电\s*(?:[（(][^）)]*[）)])?\s*', '', body), int(cfg.get("lede_char_limit", 80))),
                     "category": topic(title, body, src.get("topic", "")),
                     "coverage_region": coverage_region(title, cand["location"])})
        if cand["category"] == "国内":
            cand["category"] = "国际"
        candidates.append(cand)
    return candidates


def filter_candidates(candidates: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """返回 (进入列表的 items, 被剔除的 excluded)。"""
    strict = bool(cfg.get("strict_elements", True))
    require = cfg.get("strict_require_fields") or ["event_time", "location", "subject"]
    items, excluded = [], []
    seen_titles, seen_urls = set(), set()
    for c in candidates:
        key = re.sub(r"[\s，。、：:！!？?·（）()“”‘’]", "", c["title"])
        if key in seen_titles or c["url"] in seen_urls:
            c["_excluded_reason"] = "相同标题或原文地址重复"
            excluded.append(c)
            continue
        if strict:
            missing = [f for f in require if (c.get(f) or "").strip() in ("", UNKNOWN)
                       or not c.get(f + "_evidence")]
            if missing:
                c["_excluded_reason"] = "缺要素：" + "、".join(missing)
                excluded.append(c)
                continue
        seen_titles.add(key)
        seen_urls.add(c["url"])
        items.append(c)
    items.sort(key=lambda item: item.get("publish_time", ""), reverse=True)
    if cfg.get("balanced_selection"):
        from newsroom import balanced, topic
        for item in items:
            item["category"] = topic(item["title"], item.get("lede", ""), item.get("category", ""))
            item.setdefault("coverage_region", "中国")
        selected, skipped = balanced(items, cfg)
        return selected, excluded + skipped
    limit = max(0, int(cfg.get("max_items", 12)))
    reserve = min(limit, max(0, int(cfg.get("international_reserved_items", 0))))
    international = [c for c in items if c.get("source_key") in INTERNATIONAL_SOURCE_KEYS]
    preferred, seen_regions = [], set()
    # 先选各报道地区的一条，再按发布时间补满预留位置。
    for c in international:
        region = c.get("coverage_region", "全球及跨地区")
        if region not in seen_regions and len(preferred) < reserve:
            preferred.append(c)
            seen_regions.add(region)
    for c in international:
        if len(preferred) >= reserve:
            break
        if not any(c is chosen for chosen in preferred):
            preferred.append(c)
    chosen_ids = {id(c) for c in preferred}
    selected = preferred + [c for c in items if id(c) not in chosen_ids][:max(0, limit - len(preferred))]
    chosen_ids = {id(c) for c in selected}
    for c in items:
        if id(c) in chosen_ids:
            continue
        c["_excluded_reason"] = "达到本期条数上限"
        excluded.append(c)
    selected.sort(key=lambda item: item.get("publish_time", ""), reverse=True)
    return selected, excluded


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _fmt_field(value: str) -> str:
    return value if value and value != UNKNOWN else UNKNOWN


def source_status(sh: dict) -> str:
    return {"ok": "读取正常", "empty": "没有当日候选", "partial": "部分请求失败",
            "error": "读取失败"}.get(sh.get("status"), "读取正常" if sh.get("ok") else "读取失败")


def render_item_txt(idx: int, it: dict) -> str:
    lines = []
    region = it.get('coverage_region', '中国')
    lines.append(f"{idx}. [{region} · {it['category']}] {it['title']}")
    pub = it["publish_time"]
    prec = "分钟级" if it["publish_precision"] == "minute" else "日期级"
    lines.append(f"   来源：{it['source']}　发布：{pub}（{prec}）")
    if it.get("origin"):
        lines.append(f"   原发：{it['origin']}")
    lines.append(f"   事件时间：{_fmt_field(it['event_time'])}")
    lines.append(f"   地点：{_fmt_field(it['location'])}")
    lines.append(f"   人物或机构：{_fmt_field(it['subject'])}")
    if it.get("lede"):
        lines.append(f"   【原文摘录】{it['lede']}")
    evs = []
    if it.get("event_time_evidence"):
        evs.append(f"事件时间「{it['event_time_evidence']}」")
    if it.get("location_evidence"):
        evs.append(f"地点「{it['location_evidence']}」")
    if it.get("subject_evidence"):
        evs.append(f"主体「{it['subject_evidence']}」")
    if evs:
        lines.append(f"   【原文证据】" + "；".join(evs))
    lines.append(f"   原文链接：{it['url']}")
    return "\n".join(lines)


def render_plain(items: list[dict], meta: dict) -> str:
    from email_layout import title, credit, reading_sections
    labels = {"event_time": "事件时间", "location": "地点", "subject": "人物或机构"}
    lines = [f"环球早报 {meta['today']}", f"采集截止：{meta['now_display']}",
             f"正式新闻 {len(items)} 条 · 最新报道链接 {len(meta.get('reading_links', []))} 条", ""]
    sections = reading_sections(items, meta.get('reading_links', []))
    if meta.get('sample_notice'):
        lines.append(meta['sample_notice'])
    if not sections:
        lines.append("今日未取得可核验新消息；三要素缺项报道另列。")
    else:
        lines.extend(["【本期目录】", "记住新闻编号，方便下次查找。"])
        for category, _target, entries in sections:
            lines.append("【" + category + "】")
            lines.extend(f"{number:02d} {title(it)}" for number, it, _formal in entries)
    for category, _target, entries in sections:
        lines.extend(["", "【" + category + "】"])
        for number, it, formal in entries:
            status = "三要素齐全" if formal else "要素待核实"
            lines.extend(["", f"{number:02d} {title(it)}", status,
                          f"来源：{credit(it)}；发布：{it['publish_time']}（北京时间）"])
            digest = it.get('digest')
            if digest:
                if digest['status'] == 'unavailable':
                    lines.append("中文整理暂未生成，保留来源入口。")
                else:
                    lines.append(digest['overview']['text'])
                    lines.extend("· " + d['text'] for d in digest.get('details', []))
                    if digest.get('insight'):
                        lines.append("【简短解读 · 推断】" + digest['insight']['text'])
                    status_note = {"ok":"简体中文整理 · AI辅助","partial":"简体中文整理 · 未通过校验的片段已省略"}.get(digest['status'],"简体中文摘编 · 解读暂未生成")
                    if digest.get('provenance') == 'fictional_sample':
                        status_note = "人工构造的虚构样张 · 非真实新闻或接口输出"
                    lines.append(status_note)
                    if digest.get('basis') != '正文':
                        lines.append("仅据RSS摘要整理，未读取完整正文。")
                elements = digest['elements']
            else:
                lines.append(it.get('lede', ''))
                elements = it
            if it.get('missing_elements'):
                lines.append("待核实要素：" + "、".join(labels[k] for k in it['missing_elements']))
            else:
                lines.extend(labels[k] + "：" + (elements.get(k) or "中文字段暂未生成") for k in labels)
            lines.append("核对原文：" + it['url'])
    lines.extend(["", "【来源状态】"])
    for sh in meta.get('source_health', {}).values():
        lines.append(sh['name'] + "：" + source_status(sh) + ("；" + sh['error'] if sh.get('error') else ""))
    lines.extend(["", "消息取自官方机构与权威媒体。报道事实与简短解读分开呈现；解读属于推断，AI整理可能有误。", "原文缺失要素不补写。来源和核验依据保存在运行报告中。"])
    return "\n".join(lines)


def render_html(items: list[dict], meta: dict) -> str:
    from email_layout import render
    return render(items, meta)


# ---------------------------------------------------------------------------
# 邮箱校验
# ---------------------------------------------------------------------------

def validate_address(addr: str, field: str) -> str:
    if not addr or not isinstance(addr, str):
        raise SendRefused(f"{field} 为空")
    if any(ch in addr for ch in ("\r", "\n", "\x00")):
        raise SendRefused(f"{field} 含非法换行")
    if addr.count("@") != 1 or " " in addr or "\t" in addr:
        raise SendRefused(f"{field} 格式非法")
    local, domain = addr.split("@", 1)
    if not local or not domain or "." not in domain:
        raise SendRefused(f"{field} 格式非法")
    return addr


# ---------------------------------------------------------------------------
# 邮件发送（标准库 smtplib，SSL465 / STARTTLS587）
# ---------------------------------------------------------------------------

def _smtp_connect(env: dict) -> tuple[object, bool]:
    host = env.get("SMTP_HOST") or "smtp.qq.com"
    port_s = env.get("SMTP_PORT") or "465"
    try:
        port = int(port_s)
    except ValueError as exc:
        raise SendRefused(f"SMTP_PORT 非法：{port_s}") from exc
    if port == 465:
        conn = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        return conn, True
    elif port == 587:
        conn = smtplib.SMTP(host, port, timeout=30)
        conn.starttls(context=ssl.create_default_context())
        return conn, False
    else:
        raise SendRefused(f"仅支持 SSL465 / STARTTLS587，当前 SMTP_PORT={port}")


def send_email(env: dict, html_body: str, text_body: str,
               connect_fn=_smtp_connect) -> str:
    """发送邮件。返回 'sent'（服务器已接受）或 'uncertain'。"""
    user = validate_address(env.get("SMTP_USER", ""), "SMTP_USER")
    password = env.get("SMTP_PASSWORD", "")
    if not password:
        raise SendRefused("SMTP_PASSWORD 为空")
    to_list = []
    for t in (env.get("MAIL_TO", "") or "").replace(";", ",").split(","):
        t = t.strip()
        if t:
            to_list.append(validate_address(t, "MAIL_TO"))
    if not to_list:
        raise SendRefused("MAIL_TO 为空")
    subject = f"环球早报 {env.get('TODAY', '')}".strip()
    conn = None
    try:
        conn, _ssl_mode = connect_fn(env)
        conn.login(user, password)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = Address(display_name=env.get("SMTP_FROM_NAME", "环球早报"), addr_spec=user)
        msg["To"] = ", ".join(to_list)
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        refused = conn.send_message(msg)
        if refused:
            return "uncertain"  # 部分收件人被拒绝时不声称全部接收，也不重发。
        # 服务器确认接受后才视为 sent
        try:
            conn.quit()
        except (smtplib.SMTPException, OSError, TimeoutError):
            pass  # DATA 已得到接受回执，QUIT 失败不会撤销该回执。
        return "sent"
    except smtplib.SMTPRecipientsRefused:
        raise SendRefused("收件人被拒绝")
    except smtplib.SMTPAuthenticationError:
        raise SendRefused("SMTP 认证失败（用户名/授权码错误）")
    except (smtplib.SMTPException, OSError, TimeoutError, ssl.SSLError):
        # 超时或发送后状态未知 → uncertain，绝不盲重发
        return "uncertain"
    finally:
        if conn is not None:
            try:
                if conn.sock is not None:
                    conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 同日发送 claim（防重复）
# ---------------------------------------------------------------------------

def delivery_payload(state: str, detail: str = "") -> dict:
    return {
        "state": state,
        "detail": detail,
        "created_at": now_beijing().isoformat(),
    }


class GitHubClaim:
    """通过 GitHub Contents API 在默认分支 .delivery/YYYY-MM-DD.json 原子 claim。

    竞争保护：GET 读取当前 sha 后，PUT 携带该 sha；不存在（404）时 PUT 不带 sha 创建，
    并发创建由 GitHub 以 409/422 拒绝后到者。claim 网络结果未知时抛 ClaimError，禁止发送。
    """

    def __init__(self, token: str, repo: str, date_str: str) -> None:
        self.token = token
        self.repo = repo
        self.path = f".delivery/{date_str}.json"
        self.api_url = f"{GITHUB_API}/repos/{repo}/contents/{self.path}"

    def _request(self, method: str, body: dict | None = None) -> tuple[int, dict | None]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.api_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"{}")
            except Exception:
                payload = None
            return exc.code, payload
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ClaimError(f"claim 网络结果未知：{exc}") from exc

    def read(self) -> tuple[str | None, str | None]:
        """返回 (sha, state)；文件不存在时返回 (None, None)。"""
        status, body = self._request("GET")
        if status == 404:
            return None, None
        if status == 200 and isinstance(body, dict):
            sha = body.get("sha")
            if not sha:
                raise ClaimError("已有发送记录缺少 sha，禁止覆盖或重发")
            state = None
            try:
                content = base64.b64decode(body.get("content", "")).decode("utf-8")
                state = (json.loads(content) or {}).get("state")
            except Exception:
                state = None
            return sha, state
        raise ClaimError(f"claim 读取状态未知（HTTP {status}）")

    def _payload(self, state: str) -> str:
        return base64.b64encode(
            json.dumps(delivery_payload(state), ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

    def create_or_update(self, state: str, sha: str | None) -> str | None:
        """PUT 创建/更新；成功返回新 sha，竞争失败返回 None。"""
        if sha:
            return None  # 已存在记录只能由本次持有者 update_state，不能重新认领。
        body: dict = {"message": f"morning-brief {state}", "content": self._payload(state)}
        if sha:
            body["sha"] = sha
        status, resp = self._request("PUT", body)
        if status in (200, 201):
            content_sha = ((resp or {}).get("content") or {}).get("sha")
            if not content_sha:
                raise ClaimError("GitHub 未返回发送记录的 content.sha，禁止发送")
            return content_sha
        if status in (409, 422):
            return None  # 竞争失败
        raise ClaimError(f"claim 创建/更新状态未知（HTTP {status}）")

    def update_state(self, state: str, sha: str) -> None:
        if not sha:
            raise ClaimError("claim 更新缺少 sha")
        body = {"message": f"morning-brief {state}", "content": self._payload(state),
                "sha": sha}
        status, _ = self._request("PUT", body)
        if status not in (200, 201):
            # 更新失败也不能重发：下次运行见『sending』即 skip
            raise ClaimError(f"claim 更新状态未知（HTTP {status}），已发送不重试")


class LocalClaim:
    """本地 O_EXCL 同日 claim（仅 --local-state 显式要求时）。"""

    def __init__(self, base_dir: str, date_str: str) -> None:
        self.dir = os.path.join(base_dir, ".delivery")
        self.path = os.path.join(self.dir, f"{date_str}.json")

    def read_state(self) -> str | None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return (json.load(f) or {}).get("state")
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def create_sending(self, sha: str | None = None) -> bool:
        os.makedirs(self.dir, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(delivery_payload("sending"), f, ensure_ascii=False)
        return True

    def update_state(self, state: str, sha: str | None = None) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(delivery_payload(state), f, ensure_ascii=False)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        _deep_merge(cfg, user)
    # 环境变量只允许覆盖非敏感运行参数
    for key in ("fresh_hours", "max_items", "max_candidates_per_source",
                "people_max_layouts", "http_timeout", "max_retries"):
        v = os.environ.get(f"BRIEF_{key.upper()}")
        if v is not None:
            try:
                cfg[key] = int(v)
            except ValueError:
                pass
    return cfg


def _deep_merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def gather(cfg: dict, now: _dt.datetime) -> tuple[list[dict], dict]:
    health: dict[str, dict] = {}
    all_cands: list[dict] = []
    def one_source(pair):
        key, src = pair
        http = HttpClient(cfg)
        if not src.get("enabled", True):
            return key, [], {"name": src["name"], "ok": False, "error": "已禁用", "candidates": 0}
        try:
            failure_start = len(http.failures)
            if key == "gov":
                cands = fetch_gov(http, cfg, now)
            elif key == "people":
                cands = fetch_people(http, cfg, now)
            elif key == "mfa":
                cands = fetch_mfa(http, cfg, now)
            elif src.get("kind"):
                cands = fetch_international(http, cfg, now, key)
            else:
                raise ConfigError("未知来源适配器")
            failures = http.failures[failure_start:]
            state = {"name": src["name"], "ok": not failures,
                           "status": "partial" if failures and cands else "error" if failures else "empty" if not cands else "ok",
                           "error": f"{len(failures)} 个来源请求失败" if failures else "",
                           "candidates": len(cands), "failed_requests": failures}
            return key, cands, state
        except Exception as exc:  # noqa: BLE001 - 单源错误不冒充成功，也不中断其它来源
            return key, [], {"name": src["name"], "ok": False, "status": "error",
                           "error": f"{type(exc).__name__}: {exc}", "candidates": 0}
    with ThreadPoolExecutor(max_workers=max(1,min(8,int(cfg.get("fetch_workers",1))))) as pool:
        for key, candidates, state in pool.map(one_source, cfg.get("sources",{}).items()):
            health[key] = state
            all_cands.extend(candidates)
    return all_cands, health


def build_report(items: list[dict], excluded: list[dict], health: dict,
                 cfg: dict, now: _dt.datetime, mode: str, delivery: dict) -> dict:
    from newsroom import reading_links
    from chinese_digest import public_item
    reading = delivery.get("_reading")
    if reading is None:
        reading = reading_links(excluded, items, cfg)
    return {
        "program": "official-morning-brief",
        "generated_at": now_beijing().isoformat(),
        "now": now.isoformat(),
        "now_source": "--now" if delivery.get("now_injected") else "system",
        "mode": mode,
        "strict_elements": bool(cfg.get("strict_elements", True)),
        "today": beijing_date_str(now),
        "source_health": health,
        "coverage": {"topics": dict(Counter(it["category"] for it in items)),
                     "regions": dict(Counter(it.get("coverage_region", "中国") for it in items)),
                     "reading_topics": dict(Counter(it["category"] for it in reading)),
                     "reading_regions": dict(Counter(it.get("coverage_region", "中国") for it in reading))},
        "counts": {
            "candidates_total": len(items) + len(excluded),
            "excluded": len(excluded),
            "included": len(items),
            "items": len(items),
        },
        "excluded": [{"title": e["title"], "source": e["source"], "url": e["url"],
                      "reason": e.get("_excluded_reason", "")} for e in excluded],
        "items": [public_item(it) for it in items],
        "reading_links": [public_item(it) for it in reading],
        "chinese_digest": delivery.get("_digest_health", {}),
        "summary_text": (f"今日共 {len(items)} 条通过来源、日期与要素规则的消息（剔除 {len(excluded)} 条）"
                         if items else "今日未取得可核验新消息"),
        "delivery": {k: v for k, v in delivery.items() if not k.startswith('_')},
    }


def wait_until_delivery(hhmm: str, date_str: str, clock=None, sleeper=None, monotonic=None) -> None:
    """Hold a prepared issue until its target, for at most 20 minutes; never cross dates."""
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hhmm):
        raise SendRefused("--not-before 必须为 HH:MM")
    clock = clock or now_beijing
    sleeper = sleeper or time.sleep
    monotonic = monotonic or time.monotonic
    target = _dt.datetime.fromisoformat(date_str + "T" + hhmm).replace(tzinfo=BEIJING_TZ)
    deadline = monotonic() + 1205
    while True:
        current = clock().astimezone(BEIJING_TZ)
        if beijing_date_str(current) != date_str:
            raise SendRefused("早报日期已变化，禁止发送过期一期")
        remaining = (target - current).total_seconds()
        if remaining <= 0:
            return
        if remaining > 1200 or monotonic() >= deadline:
            raise SendRefused("等待发送超出20分钟上限")
        sleeper(min(30, remaining))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="环球早报")
    ap.add_argument("--dry-run", action="store_true", help="仅生成产物，绝不发送（默认）")
    ap.add_argument("--send", action="store_true", help="生成并发送邮件（需 SMTP 与 GitHub 凭据）")
    ap.add_argument("--now", metavar="TIME",
                    help="测试用：注入当前时间（仅限 dry-run），如 2026-10-08T07:30:00+08:00")
    ap.add_argument("--output-dir", default="run-output", help="产物输出目录（默认 run-output）")
    ap.add_argument("--config", default="config.json", help="配置文件路径")
    ap.add_argument("--local-state", action="store_true",
                    help="--send 时用本地 O_EXCL claim（仅显式要求；GitHub 模式默认 durable）")
    ap.add_argument("--mail-to", default="", help="覆盖 MAIL_TO（测试用）")
    ap.add_argument("--digest-preview", action="store_true", help="仅试运行：打印最多3条中文整理用于验收")
    ap.add_argument("--not-before", default="", help="仅发送：北京时间 HH:MM 后投递，最多等待20分钟")
    args = ap.parse_args(argv)

    if args.send and args.dry_run:
        print("错误：--send 与 --dry-run 互斥", file=sys.stderr)
        return 2
    if args.send and args.digest_preview:
        print("错误：--digest-preview 仅用于试运行", file=sys.stderr)
        return 2
    mode = "send" if args.send else "dry-run"
    if args.not_before and (not args.send or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", args.not_before)):
        print("错误：--not-before 只允许发送模式的 HH:MM", file=sys.stderr)
        return 2

    # --now 仅允许 dry-run；send 禁止注入历史时间
    now_injected = args.now is not None
    if args.now:
        if args.send:
            print("错误：--send 不允许注入 --now 历史时间", file=sys.stderr)
            return 2
        now = parse_now_arg(args.now)
    else:
        now = now_beijing()

    cfg = load_config(args.config)

    # 抓取 + 过滤
    candidates, health = gather(cfg, now)
    items, excluded = filter_candidates(candidates, cfg)
    from newsroom import reading_links
    reading = reading_links(excluded, items, cfg)
    from chinese_digest import enrich, public_item
    digest_health = enrich(items, reading, cfg)
    if args.digest_preview:
        samples = []
        seen_sources = set()
        # Prefer foreign-language inputs so cloud translation can be inspected.
        for it in sorted(items + reading, key=lambda x: not bool(re.search(r'[A-Za-z]{4}', x['title']))):
            if it['digest']['status'] != 'ok' or it['source'] in seen_sources:
                continue
            seen_sources.add(it['source'])
            samples.append({"source": it['source'], "url": it['url'], "original_title": it['title'],
                            "digest": it['digest']})
            if len(samples) == 3:
                break
        print(json.dumps({"digest_preview": samples}, ensure_ascii=False))

    # 渲染产物（dry-run 与 send 都写）
    os.makedirs(args.output_dir, exist_ok=True)
    today = beijing_date_str(now)
    meta = {
        "today": today,
        "now_display": now.strftime("%Y-%m-%d %H:%M %z"),
        "mode": mode,
        "excluded": len(excluded),
        "source_health": health,
        "reading_links": reading,
        "digest_health": digest_health,
    }
    brief_json = {"today": today, "generated_at": now.isoformat(),
                  "items": [public_item(it) for it in items],
                  "reading_links": [public_item(it) for it in reading],
                  "chinese_digest": digest_health, "excluded_count": len(excluded)}
    plain = render_plain(items, meta)
    html_doc = render_html(items, meta)
    with open(os.path.join(args.output_dir, "brief.txt"), "w", encoding="utf-8") as f:
        f.write(plain)
    with open(os.path.join(args.output_dir, "brief.html"), "w", encoding="utf-8") as f:
        f.write(html_doc)
    with open(os.path.join(args.output_dir, "brief.json"), "w", encoding="utf-8") as f:
        json.dump(brief_json, f, ensure_ascii=False, indent=2)

    delivery = {"state": "dry-run", "sent": False, "detail": "",
                "now_injected": now_injected}

    if args.send:
        try:
            if args.not_before:
                wait_until_delivery(args.not_before, today)
            elif beijing_date_str(now_beijing()) != today:
                raise SendRefused("早报日期已变化，禁止发送过期一期")
            env = dict(os.environ)
            if args.mail_to:
                env["MAIL_TO"] = args.mail_to
            env["TODAY"] = today
            # 校验发送配置（凭据绝不写日志/产物）
            validate_address(env.get("SMTP_USER", ""), "SMTP_USER")
            validate_address((env.get("MAIL_TO", "") or "").split(",")[0].strip(), "MAIL_TO")
            if not env.get("SMTP_PASSWORD"):
                raise SendRefused("SMTP_PASSWORD 为空")

            # claim：GitHub 模式要求 GITHUB_TOKEN + GITHUB_REPOSITORY（durable）
            if args.local_state:
                claim: object = LocalClaim(os.getcwd(), today)
            else:
                token = env.get("GITHUB_TOKEN", "")
                repo = env.get("GITHUB_REPOSITORY", "")
                if not token or not repo:
                    raise SendRefused("--send 需 GITHUB_TOKEN 与 GITHUB_REPOSITORY（GitHub 模式）")
                claim = GitHubClaim(token, repo, today)

            if args.local_state:
                state = claim.read_state()  # type: ignore[union-attr]
                if state in ("sending", "sent", "uncertain"):
                    delivery = {"state": "skipped", "sent": False,
                                "detail": f"本日已存在 {state} 记录，跳过以免重复"}
                else:
                    claimed = claim.create_sending()  # type: ignore[union-attr]
                    if not claimed:
                        delivery = {"state": "skipped", "sent": False,
                                    "detail": "本地同日 claim 已存在，跳过"}
                    else:
                        delivery = {"state": "claimed", "sent": False, "detail": ""}
                        result = send_email(env, html_doc, plain)
                        claim.update_state(result)  # type: ignore[union-attr]
                        delivery = {"state": result, "sent": result == "sent",
                                    "detail": "SMTP accepted" if result == "sent"
                                    else "SMTP 状态未知（uncertain）"}
            else:
                sha, state = claim.read()  # type: ignore[union-attr]
                if sha is not None:
                    delivery = {"state": "skipped", "sent": False,
                                "detail": f"本日已存在 {state} 记录，跳过以免重复"}
                else:
                    new_sha = claim.create_or_update("sending", sha)  # type: ignore[union-attr]
                    if new_sha is None:
                        delivery = {"state": "skipped", "sent": False,
                                    "detail": "竞争失败或同日已存在，跳过"}
                    else:
                        delivery = {"state": "claimed", "sent": False, "detail": ""}
                        result = send_email(env, html_doc, plain)
                        # 更新状态为 sent/uncertain；更新失败也不能重发
                        try:
                            claim.update_state(result, new_sha)  # type: ignore[union-attr]
                        except ClaimError:
                            pass
                        delivery = {"state": result, "sent": result == "sent",
                                    "detail": "SMTP accepted" if result == "sent"
                                    else "SMTP 状态未知（uncertain）"}
        except (SendRefused, ClaimError) as exc:
            delivery = {"state": "uncertain", "sent": False, "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 发送路径兜底，绝不因异常盲重发
            delivery = {"state": "uncertain", "sent": False,
                        "detail": f"{type(exc).__name__}: {exc}"}

    delivery["_reading"] = reading
    delivery["_digest_health"] = digest_health
    report = build_report(items, excluded, health, cfg, now, mode, delivery)
    with open(os.path.join(args.output_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 控制台简要输出（不含任何凭据/原文全文）
    print(json.dumps({
        "mode": mode,
        "today": today,
        "included": len(items),
        "reading_links": len(reading),
        "excluded": len(excluded),
        "source_health": {k: v.get("status", "ok" if v.get("ok") else "error")
                          for k, v in health.items()},
        "delivery_state": delivery["state"],
        "chinese_digest": digest_health,
        "output_dir": args.output_dir,
    }, ensure_ascii=False))
    return 1 if args.send and delivery["state"] == "uncertain" else 0


if __name__ == "__main__":
    sys.exit(main())
