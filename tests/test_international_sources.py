import datetime as dt
import unittest
from international_sources import fetch_records, Article, xml_entries, canonical_link

NOW=dt.datetime(2026,10,9,15,tzinfo=dt.timezone(dt.timedelta(hours=8)))
class HTTP:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def check_url(self,url):
        if not url.startswith(('https://news.un.org/','https://www.channelnewsasia.com/','https://wmo.int/','https://www.news.cn/')):raise ValueError('disallowed')
        return url
    def get_text(self,url):
        self.calls.append(url)
        value=self.pages[url]
        if isinstance(value,Exception):raise value
        return value

def rss(link,stamp,description='NASA on Friday launched a satellite in Japan.'):
    return f'<rss><channel><item><title>Satellite launch</title><link>{link}</link><pubDate>{stamp}</pubDate><description>{description}</description></item></channel></rss>'

class AdapterTests(unittest.TestCase):
    def test_rejects_doctype_and_non_feed_challenge(self):
        for page in ('<!DOCTYPE rss [<!ENTITY x "payload">]><rss/>','<html>captcha</html>'):
            with self.assertRaises(ValueError):xml_entries(page)

    def test_html_inside_cdata_is_not_mistaken_for_xml_entity_declarations(self):
        self.assertEqual(len(xml_entries('<rss><channel><item><title>T</title><description><![CDATA[<!DOCTYPE html><p>Hello</p>]]></description></item></channel></rss>')),1)

    def test_related_article_time_cannot_invalidate_primary_publication(self):
        feed='https://news.un.org/feed';article='https://news.un.org/zh/story/2026/10/1'
        page='<meta property="article:published_time" content="2026-10-09T04:00:00Z"><time>2026年10月9日</time><div id="block-views-block-content-fields-block-body"><p>联合国秘书长周五在纽约宣布一项最新的国际合作计划。</p></div><time>2025年1月1日</time>'
        result=fetch_records(HTTP({feed:rss(article,'Fri, 09 Oct 2026 04:00:00 GMT'),article:page}),'un',{'name':'UN','kind':'un_rss','list_url':feed},NOW)
        self.assertEqual(len(result),1)

    def test_rdf_namespace_date_and_safe_html_description(self):
        page='<rdf:RDF xmlns:rdf="urn:rdf" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>T</title><link>https://news.un.org/x</link><dc:date>2026-10-09T04:00:00Z</dc:date></item></rdf:RDF>'
        self.assertEqual(xml_entries(page)[0]['date'],'2026-10-09T04:00:00Z')

    def test_un_uses_body_metadata_not_coarse_feed_midnight(self):
        feed='https://news.un.org/feed'; article='https://news.un.org/zh/story/2026/10/1'
        pages={feed:rss(article.replace('/zh/story/','/feed/view/zh/story/'),'Wed, 07 Oct 2026 12:00:00 GMT'),
               article:'<meta property="article:published_time" content="2026-10-08T16:45:00-04:00"><time>2026年10月8日</time><p>Photo caption</p><div id="block-views-block-content-fields-block-body"><p>联合国秘书长周四在巴基斯坦伊斯兰堡表示，多边主义面临挑战。</p></div>'}
        result=fetch_records(HTTP(pages),'un',{'name':'联合国','kind':'un_rss','list_url':feed},NOW)
        self.assertEqual(len(result),1); self.assertNotIn('Photo',result[0]['body'])
        self.assertEqual(result[0]['publish_time'],'2026-10-08T16:45:00-04:00')

    def test_wmo_visible_date_conflict_is_rejected(self):
        feed='https://wmo.int/zh-hans'; article=feed+'/media/news/example'
        h=HTTP({feed:f'<a href="{article}">消息</a>',article:'<meta property="article:published_time" content="2026-10-08T09:00:00+02:00"><time>2026年10月9日</time><div class="field--name-body"><p>世界气象组织周五在日内瓦发布了一份研究报告。</p></div>'})
        self.assertEqual(fetch_records(h,'wmo',{'name':'WMO','kind':'wmo_html','list_url':feed},NOW),[])

    def test_wmo_english_links_are_not_silently_labeled_chinese(self):
        feed='https://wmo.int/zh-hans';h=HTTP({feed:'<a href="/media/news/english">English</a>'})
        self.assertEqual(fetch_records(h,'wmo',{'name':'WMO','kind':'wmo_html','list_url':feed},NOW),[])
        self.assertEqual(h.calls,[feed])

    def test_cna_reuters_credit_is_byline_not_image_or_quoted_mention(self):
        feed='https://www.channelnewsasia.com/feed';article='https://www.channelnewsasia.com/world/1'
        page='<h1>NASA mission</h1><script type="application/ld+json">{"@graph":[{"@type":"ImageObject","datePublished":"2025-01-01T00:00:00Z"},{"@type":"NewsArticle","datePublished":"2026-10-09T04:00:00Z"}]}</script><p>Navigation Reuters</p><div class="text-long"><p>NASA on Friday launched a satellite in Japan, the space agency said.</p></div><div class="source source--with-label">Source: Reuters</div>'
        h=HTTP({feed:rss(article,'Fri, 09 Oct 2026 04:00:00 GMT'),article:page})
        result=fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW)[0]
        self.assertEqual(result['origin'],'路透社 Reuters');self.assertNotIn('Navigation',result['body'])
        page=page.replace('Source: Reuters','Source: CNA')
        h.pages[article]=page
        self.assertEqual(fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW)[0]['origin'],'CNA')

    def test_future_or_old_publication_is_never_fresh(self):
        for stamp in ('Fri, 09 Oct 2026 08:00:00 GMT','Wed, 07 Oct 2026 04:00:00 GMT'):
            feed='https://www.channelnewsasia.com/feed';article='https://www.channelnewsasia.com/world/1'
            h=HTTP({feed:rss(article,stamp)})
            self.assertEqual(fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW),[])
            self.assertEqual(h.calls,[feed])

    def test_updated_old_article_not_recycled_as_new(self):
        feed='https://www.channelnewsasia.com/feed';article='https://www.channelnewsasia.com/world/1'
        page='<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-09-01T04:00:00Z","dateModified":"2026-10-09T04:00:00Z"}</script>'
        h=HTTP({feed:rss(article,'Fri, 09 Oct 2026 04:00:00 GMT'),article:page})
        self.assertEqual(fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW),[])

    def test_public_feed_excerpt_survives_article_http_failure_with_basis(self):
        feed='https://www.channelnewsasia.com/feed';article='https://www.channelnewsasia.com/world/1'
        h=HTTP({feed:rss(article,'Fri, 09 Oct 2026 04:00:00 GMT'),article:ValueError('403')})
        result=fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW)
        self.assertEqual(result[0]['content_basis'],'官方 RSS 摘要')

    def test_advertising_domain_not_requested(self):
        feed='https://www.channelnewsasia.com/feed';h=HTTP({feed:rss('https://evil.test/x','Fri, 09 Oct 2026 04:00:00 GMT')})
        self.assertEqual(fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW),[])
        self.assertEqual(h.calls,[feed])

    def test_xinhua_combines_visible_clock_and_date_not_crawl_time(self):
        feed='https://www.news.cn/tech/';article='https://www.news.cn/tech/20261009/abc/c.html'
        page='<meta name="publishdate" content="2026-10-09"><h1>新消息</h1><div class="header-time"><span>2026</span><span>10/09</span><span>11:22:33</span></div><span id="detailContent"><p>中国科学院9日在北京发布一项最新的科研成果，涉及量子技术。</p></span>'
        h=HTTP({feed:f'<a href="{article}">新消息</a>',article:page})
        result=fetch_records(h,'xinhua',{'name':'新华社','kind':'xinhua_html','list_url':feed},NOW)
        self.assertEqual(result[0]['publish_time'],'2026-10-09T11:22:33+08:00')

    def test_naive_date_time_does_not_get_assumed_timezone(self):
        feed='https://www.channelnewsasia.com/feed';article='https://www.channelnewsasia.com/world/1'
        h=HTTP({feed:rss(article,'2026-10-09T04:00:00')})
        self.assertEqual(fetch_records(h,'cna',{'name':'CNA','kind':'cna_rss','list_url':feed},NOW),[])

    def test_canonical_url_removes_tracking_and_upgrades_http_before_fetch(self):
        self.assertEqual(canonical_link('http://news.un.org/feed/view/zh/story/1?x=1','un_rss'),'https://news.un.org/zh/story/1')

if __name__=='__main__':unittest.main()
