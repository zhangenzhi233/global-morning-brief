import unittest
import copy
import re
from html.parser import HTMLParser
from email_layout import render


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.hrefs.append(attrs['href'])


class EmailLayoutTests(unittest.TestCase):
    def fixture(self, name, category, missing=None):
        item = {'title':name, 'source':'UN', 'publish_time':'2026-10-09',
                'category':category, 'coverage_region':'全球', 'lede':'中文概述',
                'event_time':'周五', 'location':'纽约', 'subject':'联合国',
                'url':'https://news.un.org/example'}
        if missing is not None:
            item['missing_elements'] = missing
        return item

    def meta(self, reading=None):
        return {'today':'2026-10-09', 'now_display':'2026-10-09 15:30 +0800',
                'mode':'send', 'reading_links':reading or [], 'source_health':{}}

    def test_phone_layout_escapes_content_and_uses_named_link(self):
        item={'title':'<script>alert(1)</script>','source':'BBC','origin':'BBC','publish_time':'2026-10-09 12:30',
              'category':'科技与产业','coverage_region':'亚太','lede':'A short quotation.',
              'missing_elements':['location'],'url':'https://www.bbc.com/news/test?a=1&b=2'}
        meta={'today':'2026-10-09','now_display':'2026-10-09 15:30 +0800','mode':'send','reading_links':[item],'source_health':{}}
        page=render([],meta)
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('href="https://www.bbc.com/news/test?a=1&amp;b=2"',page)
        self.assertNotIn('>https://',page)
        self.assertNotIn('模式',page)
        self.assertIn('max-width:640px',page)
        self.assertIn('待核实要素：地点',page)
        self.assertNotIn('<iframe',page)

    def test_evidence_remains_out_of_email_body_but_event_fields_visible(self):
        item={'title':'News','source':'UN','publish_time':'2026-10-09','category':'政治与安全','lede':'Original',
              'event_time':'周五','location':'纽约','subject':'联合国','event_time_evidence':'internal-audit-only', 'url':'https://news.un.org/1'}
        page=render([item],{'today':'2026-10-09','now_display':'2026-10-09 15:30 +0800','mode':'send','reading_links':[],'source_health':{}})
        for text in ('周五','纽约','联合国'):self.assertIn(text,page)
        self.assertNotIn('internal-audit-only',page)
        self.assertIn('日期级',page)

    def test_all_contents_links_resolve_and_each_story_has_one_number(self):
        formal = [self.fixture('经济正式消息', '经济与金融'),
                  self.fixture('科技正式消息', '科技与产业')]
        reading = [self.fixture('政治消息', '政治与安全', ['location']),
                   self.fixture('科技待核实消息', '科技与产业', ['subject']),
                   self.fixture('新增主题消息', '新增主题', ['event_time'])]
        meta = self.meta(reading)
        before = copy.deepcopy((formal, meta))
        page = render(formal, meta)
        parser = NavigationParser()
        parser.feed(page)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for href in parser.hrefs:
            if href.startswith('#'):
                self.assertIn(href[1:], parser.ids)
        self.assertEqual([href for href in parser.hrefs if href.startswith('#news-')],
                         [f'#news-{n:02d}' for n in range(1, 6)])
        self.assertEqual(parser.hrefs.count('#brief-toc'), 5)
        # Unknown/new categories must not silently lose a story in the email.
        self.assertIn('新增主题消息', page)
        self.assertEqual((formal, meta), before)

    def test_formal_and_incomplete_remain_distinct_inside_same_topic(self):
        complete = self.fixture('齐全消息', '科技与产业')
        incomplete = self.fixture('缺项消息', '科技与产业', ['location'])
        page = render([complete], self.meta([incomplete]))
        complete_body = page.split('id="news-01"', 1)[1].split('id="news-02"', 1)[0]
        incomplete_body = page.split('id="news-02"', 1)[1]
        self.assertIn('三要素齐全', complete_body)
        self.assertIn('<b>地点</b> 纽约', complete_body)
        self.assertIn('要素待核实', incomplete_body)
        self.assertIn('待核实要素：地点', incomplete_body)
        self.assertIn('三要素齐全 1 条 · 要素待核实 1 条', page)

    def test_plain_and_html_share_contents_order_and_story_numbers(self):
        from briefing import render_plain
        formal = [self.fixture('经济正式消息', '经济与金融')]
        meta = self.meta([self.fixture('科技待核实消息', '科技与产业', ['subject'])])
        plain = render_plain(formal, meta)
        titles = re.findall(r'^(\d{2} .+)$', plain, re.M)
        self.assertEqual(titles, ['01 科技待核实消息', '02 经济正式消息'] * 2)
        self.assertIn('要素待核实', plain)
        self.assertIn('三要素齐全', plain)
        page = render(formal, meta)
        self.assertLess(page.index('id="news-01"'), page.index('id="news-02"'))

    def test_empty_issue_has_no_dead_navigation(self):
        page = render([], self.meta())
        self.assertIn('今日暂无合格新消息', page)
        self.assertNotIn('href="#', page)
        self.assertNotIn('本期目录', page)

if __name__=='__main__':unittest.main()
