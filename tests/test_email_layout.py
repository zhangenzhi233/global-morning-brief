import unittest
from email_layout import render

class EmailLayoutTests(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
