import unittest
import global_evidence as g
import newsroom as n

def item(title,key,category,region,publish='2026-10-09 12:00'):
    return {'title':title,'source_key':key,'category':category,'coverage_region':region,'publish_time':publish,'lede':'','location':''}

class EditorialTests(unittest.TestCase):
    def test_trade_story_is_economy_not_automatically_politics(self):
        self.assertEqual(n.topic('US president announces new tariffs on trade',''),'经济与金融')
        self.assertEqual(n.topic('日本芯片技术研究获进展',''),'科技与产业')
        self.assertEqual(n.topic('WHO warns of disease outbreak',''),'健康与公共卫生')

    def test_major_country_priority_does_not_match_us_inside_russia(self):
        self.assertEqual(g.coverage_region('Russian government statement',''),'欧洲')
        self.assertEqual(g.coverage_region('US economy report',''),'美洲')
        self.assertGreater(n.priority(item('日本央行消息','a','经济与金融','亚太')),n.priority(item('多哥本地会议','b','政治与安全','非洲')))

    def test_topics_survive_a_flood_of_political_news(self):
        items=[item(f'美国总统消息{i}',f'politics{i}','政治与安全','美洲') for i in range(20)]
        items+=[item('中国芯片研究','science','科技与产业','亚太'),item('欧洲利率变化','econ','经济与金融','欧洲')]
        chosen,_=n.balanced(items,{'max_items':4,'max_per_source':6,'max_per_region':10})
        self.assertEqual({c['category'] for c in chosen},{'政治与安全','科技与产业','经济与金融'})

    def test_region_balance_and_source_cap(self):
        items=[item(f'美国消息{i}','one','政治与安全','美洲') for i in range(10)]
        items+=[item('日本消息','two','政治与安全','亚太'),item('德国消息','three','政治与安全','欧洲')]
        chosen,_=n.balanced(items,{'max_items':5,'max_per_source':2,'max_per_region':3})
        self.assertEqual({c['coverage_region'] for c in chosen},{'美洲','欧洲','亚太'})
        self.assertLessEqual(sum(c['source_key']=='one' for c in chosen),2)

    def test_dateline_date_and_city_are_not_event_fields(self):
        r=g.extract_elements('科研消息','新华社北京10月9日电 该项研究取得了阶段性进展。')
        self.assertEqual(r['event_time'],g.UNKNOWN);self.assertEqual(r['location'],g.UNKNOWN)
        r=g.extract_elements('Market','SYDNEY, Oct 9 : The share market was volatile.')
        self.assertEqual(r['event_time'],g.UNKNOWN);self.assertEqual(r['location'],g.UNKNOWN)

    def test_english_concrete_event_fields_and_evidence(self):
        r=g.extract_elements('NASA satellite launch','NASA on Friday launched a satellite in Japan, the agency said.')
        self.assertEqual((r['event_time'],r['location'],r['subject']),('Friday','Japan','NASA'))
        for key in ('event_time','location','subject'): self.assertIn(r[key],r[key+'_evidence'])

    def test_publisher_is_not_a_missing_event_subject(self):
        r=g.extract_elements('BBC update','On Friday a new meeting was held in London.')
        self.assertEqual(r['subject'],g.UNKNOWN)

    def test_no_news_does_not_fill_quotas(self):
        self.assertEqual(n.balanced([],{'max_items':24}),([],[]))

    def test_reading_links_are_opt_in_and_keep_missing_fields_explicit(self):
        c=item('日本芯片研究','science','科技与产业','亚太')
        c.update(url='https://example.org/1',location=g.UNKNOWN,subject='研究团队',subject_evidence='研究团队',event_time='周五',event_time_evidence='周五',_excluded_reason='缺要素：location')
        self.assertEqual(n.reading_links([c],[],{}),[])
        r=n.reading_links([c],[],{'include_reading_links':True})
        self.assertEqual(r[0]['missing_elements'],['location'])
        self.assertEqual(r[0]['location'],g.UNKNOWN)
        self.assertEqual(n.reading_links([dict(c,_excluded_reason='日期过期')],[],{'include_reading_links':True}),[])

    def test_online_statement_does_not_supply_location_of_last_weeks_firing(self):
        r=g.extract_elements('OpenAI statement','Oct 9 : OpenAI fired three researchers last week, the company said in a post on X on Friday.')
        self.assertEqual(r['event_time'],'last week')
        self.assertEqual(r['location'],g.UNKNOWN)

if __name__=='__main__':unittest.main()
