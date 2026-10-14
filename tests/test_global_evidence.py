import datetime
import unittest
from unittest.mock import patch

import briefing as b
import global_evidence as g


class InternationalEvidenceTests(unittest.TestCase):
    def test_statement_time_place_and_named_role_stay_in_the_same_lead(self):
        body = '联合国秘书长古特雷斯周三在巴基斯坦伊斯兰堡表示，多边主义面临四大挑战。'
        result = g.extract_elements('古特雷斯：多边主义面临四大挑战', body)
        self.assertEqual(result['event_time'], '周三')
        self.assertEqual(result['location'], '巴基斯坦伊斯兰堡')
        self.assertEqual(result['subject'], '联合国秘书长古特雷斯')
        for key in ('event_time', 'location', 'subject'):
            self.assertIn(result[key], result[key + '_evidence'])

    def test_reported_country_does_not_replace_press_conference_place(self):
        title = '世卫组织：肯尼亚追踪密切接触者'
        body = '世界卫生组织总干事谭德塞周三在日内瓦举行的记者会上表示，肯尼亚正在开展调查。'
        result = g.extract_elements(title, body)
        self.assertEqual(result['location'], '日内瓦')
        self.assertEqual(result['subject'], '世界卫生组织总干事谭德塞')
        self.assertEqual(g.coverage_region(title, result['location']), '非洲')

    def test_publication_date_and_publisher_are_not_invented_as_event_fields(self):
        result = g.extract_elements('一份新报告', '该报告涉及全球许多国家。')
        self.assertEqual(result, {**{k:g.UNKNOWN for k in ('event_time','location','subject')},
                                  **{k + '_evidence':'' for k in ('event_time','location','subject')}})

    def test_country_in_policy_topic_is_not_event_location(self):
        result = g.extract_elements('联合国发布研究', '联合国周三发布有关美国债务与教育政策的研究。')
        self.assertEqual(result['location'], g.UNKNOWN)
        self.assertEqual(result['subject'], '联合国')

    def test_later_background_cannot_fill_missing_lead_elements(self):
        body = '这份评估说明有关背景。\n联合国周三在纽约发布一份报告。'
        result = g.extract_elements('一项评估', body)
        self.assertEqual(result['event_time'], g.UNKNOWN)
        self.assertEqual(result['location'], g.UNKNOWN)
        self.assertEqual(result['subject'], g.UNKNOWN)

    def test_dateline_by_itself_is_not_a_location_evidence(self):
        result = g.extract_elements('研究结果公布', '罗马 — 粮农组织周五发布了一项新的研究。')
        self.assertEqual(result['location'], g.UNKNOWN)

    def test_unknown_place_is_not_accepted_by_location_grammar(self):
        result = g.extract_elements('联合国表示', '联合国周三在国际合作领域表示关切。')
        self.assertEqual(result['location'], g.UNKNOWN)

    def test_explicit_country_heading_is_retained(self):
        result = g.extract_elements('苏丹：粮农组织启动疫苗接种行动', '粮农组织周三启动了一项新的行动。')
        self.assertEqual(result['location'], '苏丹')
        self.assertEqual(result['subject'], '粮农组织')


class GlobalSelectionTests(unittest.TestCase):
    def test_recent_domestic_items_do_not_crowd_out_global_regions(self):
        candidates = []
        for i, key, region in ((1,'gov','中国'), (2,'gov','中国'), (3,'gov','中国'),
                                (4,'un','非洲'), (5,'un','非洲'), (6,'un','亚太')):
            c = b._new_candidate(key, '测试源', f'消息{i}', f'https://example.org/{i}')
            c.update(publish_time=f'2026-10-08 {23-i:02}:00', coverage_region=region)
            candidates.append(c)
        items, excluded = b.filter_candidates(candidates, {'strict_elements':False,
                'max_items':3, 'international_reserved_items':2})
        self.assertEqual([c['title'] for c in items], ['消息1','消息4','消息6'])
        self.assertEqual(len(excluded), 3)

    def test_empty_international_pool_does_not_use_old_items_to_fill_places(self):
        candidates = [b._new_candidate('gov','测试源','消息','https://www.gov.cn/1')]
        items, _ = b.filter_candidates(candidates, {'strict_elements':False,
                'max_items':3, 'international_reserved_items':2})
        self.assertEqual(len(items), 1)

    def test_international_adapter_does_not_accept_naive_publish_time(self):
        # 即使来源适配返回了错误时间，聚合层仍不能猜测其时区。
        now = datetime.datetime(2026,10,8,23,tzinfo=b.BEIJING_TZ)
        record = {'title':'消息','body':'联合国周三在纽约发布报告。','url':'https://news.un.org/zh/story/x',
                  'publish_time':'2026-10-08T12:00:00','publish_precision':'minute'}
        cfg = b.DEFAULT_CONFIG
        with patch('international_sources.fetch_records', return_value=[record]):
            self.assertEqual(b.fetch_international(b.HttpClient(cfg),cfg,now,'un'), [])


if __name__ == '__main__':
    unittest.main()
