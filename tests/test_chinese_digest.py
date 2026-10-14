"""Offline boundary tests; never use real credentials, paid calls, or SMTP."""
import copy
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import chinese_digest as d
import briefing as b
from email_layout import render


def item():
    return {'title': 'Agency launches 2 satellites', 'source': 'NASA',
            'publish_time': '2026-10-09 08:00', 'publish_precision': 'minute',
            'category': '科技与产业', 'coverage_region': '美国',
            'event_time': 'Friday', 'location': 'Florida', 'subject': 'NASA',
            'event_time_evidence': 'Friday', 'location_evidence': 'Florida', 'subject_evidence': 'NASA',
            'url': 'https://www.nasa.gov/test', 'lede': 'NASA launched 2 satellites on Friday in Florida.',
            '_source_text': 'NASA launched 2 satellites on Friday in Florida. The satellites will study solar activity.',
            'content_basis': '正文'}


def reply():
    return {'title': {'text': '美國航天局發射2顆衛星', 'evidence': ['T']},
            'overview': {'text': '美國航天局週五在佛羅里達州發射2顆衛星。', 'evidence': ['S1']},
            'details': [{'text': '這些衛星將研究太陽活動。', 'evidence': ['S2']}],
            'insight': {'text': '這項任務值得關注的是對太陽活動的觀測，其研究結果仍需等待。', 'evidence': ['S2']},
            'elements': {'event_time': '星期五', 'location': '佛羅里達州', 'subject': '美國國家航空航天局'}}


def response(raw=None, finish='stop'):
    content = json.dumps({'choices': [{'finish_reason': finish,
                                       'message': {'content': json.dumps(raw or reply())}}]}).encode()
    return io.BytesIO(content)


class DigestValidationTests(unittest.TestCase):
    def setUp(self):
        self.item = item()
        self.blocks = d.source_material(self.item)

    def test_traditional_is_converted_and_eligibility_is_unchanged(self):
        before = copy.deepcopy(self.item)
        digest = d.validate_digest(reply(), self.item, self.blocks)
        self.assertEqual(self.item, before)
        self.assertEqual(digest['title']['text'], '美国航天局发射2颗卫星')
        self.assertIn('佛罗里达', digest['elements']['location'])

    def test_missing_place_cannot_be_filled_by_model(self):
        self.item['location'] = b.UNKNOWN
        with self.assertRaisesRegex(d.DigestError, 'elements_changed'):
            d.validate_digest(reply(), self.item, self.blocks)

    def test_missing_place_remains_missing_after_success(self):
        self.item['location'] = b.UNKNOWN
        raw = reply(); raw['elements']['location'] = ''
        self.assertEqual(d.validate_digest(raw, self.item, self.blocks)['elements']['location'], '')

    def test_evidence_ids_must_exist_in_this_article(self):
        raw = reply(); raw['overview']['evidence'] = ['OTHER_ARTICLE']
        with self.assertRaisesRegex(d.DigestError, 'invalid_evidence'):
            d.validate_digest(raw, self.item, self.blocks)

    def test_numbers_must_occur_in_cited_evidence(self):
        raw = reply(); raw['overview']['text'] = '美国航天局发射99颗卫星。'
        with self.assertRaisesRegex(d.DigestError, 'unsupported_number'):
            d.validate_digest(raw, self.item, self.blocks)

    def test_number_from_unrelated_block_is_rejected(self):
        raw = reply(); raw['details'][0]['text'] = '这些卫星共有2颗。'
        with self.assertRaisesRegex(d.DigestError, 'unsupported_number'):
            d.validate_digest(raw, self.item, self.blocks)

    def test_foreign_paragraph_and_markup_rejected(self):
        for text in ('NASA launched two satellites to study activity in the solar system.',
                     '新闻详情<script>恶意脚本</script>', '查看新闻https://example.org'):
            raw = reply(); raw['overview']['text'] = text
            with self.assertRaises(d.DigestError):
                d.validate_digest(raw, self.item, self.blocks)

    def test_bad_schema_and_unbounded_output_rejected(self):
        for raw in ([], {**reply(), 'new_fact': '虚构'}, {**reply(), 'details': [reply()['overview']] * 4}):
            with self.assertRaises(d.DigestError):
                d.validate_digest(raw, self.item, self.blocks)

    def test_source_material_bounded_and_game_widgets_removed(self):
        self.item['_source_text'] += ' CNA Games. This is a game, not the article.'
        blocks = d.source_material(self.item)
        self.assertNotIn('game', json.dumps(blocks).lower())
        self.item['_source_text'] = ('这是正文材料，其中包含很多详细事实。\n' * 2000)
        self.assertLessEqual(sum(len(v) for v in d.source_material(self.item).values()), 9400)

    def test_inline_game_widget_does_not_remove_subsequent_article_details(self):
        self.item['_source_text'] = 'First news paragraph is here.\nCNA Games Play now Show More Show Less OpenAI did not disclose identities.\nThe report identified nearly 100 articles.'
        text=json.dumps(d.source_material(self.item))
        self.assertNotIn('CNA Games',text)
        self.assertIn('did not disclose',text)
        self.assertIn('100 articles',text)

    def test_translated_month_number_is_supported(self):
        self.blocks['S1']+=' On October 8.'
        raw=reply();raw['overview']['text']='美国航天局在10月8日发布相关消息。'
        self.assertEqual(d.validate_digest(raw,self.item,self.blocks)['status'],'ok')

    def test_equivalent_monetary_and_mass_units(self):
        self.assertEqual(d._numbers('£15m and 39,500 tonnes'),d._numbers('1500万英镑和3.95万吨'))
        self.assertEqual(d._numbers('2.7 billion'),d._numbers('27亿'))
        self.assertEqual(d._numbers('H-1B visa'),d._numbers('H-1B签证'))
        self.assertEqual(d._numbers('H-1B visa'),d._numbers('H1B签证'))
        self.assertEqual(d._numbers('H-1B visa'),d._numbers('微软被指滥用H-1B签证'))
        self.assertEqual(d._numbers('H-1B visa'),d._numbers('美国H1B签证限制'))
        self.assertEqual(d._numbers('H-1B visa'),d._numbers('H‑1B签证'))
        self.assertNotEqual(d._numbers('H-1B visa'),d._numbers('H2B签证'))
        self.assertNotEqual(d._numbers('15 million'),d._numbers('1500亿'))

    def test_chinese_day_field_is_preserved_without_model_expansion(self):
        self.item['event_time']='8日';raw=reply();raw['elements']['event_time']='10月8日'
        self.assertEqual(d.validate_digest(raw,self.item,self.blocks)['elements']['event_time'],'8日')

    def test_account_attribution_is_not_event_location(self):
        from global_evidence import extract_elements
        result=extract_elements('OpenAI says it banned accounts',
                                'SINGAPORE: OpenAI on Thursday (Oct 8) said it banned accounts traced to Russia and Iran.')
        self.assertEqual(result['location'],b.UNKNOWN)
        self.assertEqual(result['subject'],'OpenAI')


class DigestClientTests(unittest.TestCase):
    def setUp(self):
        self.client = d.DigestClient('fake-test-key')
        self.item = item(); self.blocks = d.source_material(self.item)

    def test_deepseek_payload_has_no_tools_or_source_url_fetches(self):
        with patch.object(self.client.opener, 'open', return_value=response()) as call:
            result = self.client.summarize(self.item, self.blocks)
        req = call.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, 'https://api.deepseek.com/chat/completions')
        self.assertEqual(payload['model'], 'deepseek-flash')
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertNotIn('tools', payload)
        self.assertNotIn('https://www.nasa.gov', req.data.decode())
        self.assertNotIn('fake-test-key', json.dumps(result))

    def test_no_expensive_or_arbitrary_endpoint_model(self):
        for model in ('deepseek-v4-pro', 'https://attacker.org', 'deepseek-chat'):
            with self.assertRaises(d.DigestError):
                d.DigestClient('fake-test-key', model=model)

    def test_redirects_cannot_forward_bearer_key(self):
        self.assertIsNone(d._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://attacker.org'))

    def test_http_body_and_secret_are_not_in_error(self):
        err = urllib.error.HTTPError(d.ENDPOINT, 401, 'fake-test-key', {}, io.BytesIO(b'fake-test-key'))
        with patch.object(self.client.opener, 'open', side_effect=err):
            with self.assertRaisesRegex(d.DigestError, '^http_401$'):
                self.client.summarize(self.item, self.blocks)

    def test_timeout_body_size_bad_json_and_truncation(self):
        bad = [TimeoutError('fake-test-key'), io.BytesIO(b'x' * (d.LIMIT + 1)),
               io.BytesIO(b'not json'), response(finish='length')]
        for value in bad:
            with patch.object(self.client.opener, 'open', side_effect=value if isinstance(value, Exception) else None,
                              return_value=value):
                with self.assertRaises(d.DigestError) as caught:
                    self.client.summarize(self.item, self.blocks)
                self.assertNotIn('fake-test-key', str(caught.exception))

    def test_repair_uses_rejected_output_as_data_and_does_not_persist_it(self):
        bad=reply();bad['overview']['text']='美国航天局发射99颗卫星。'
        article=item()
        with patch.object(self.client.opener,'open',side_effect=[response(bad),response()]) as call:
            health=d.enrich([article],[],{},env={},client=self.client,sleep=lambda _:None)
        self.assertEqual((health['requests'],health['generated']),(2,1))
        material=json.loads(json.loads(call.call_args_list[1].args[0].data)['messages'][1]['content'])
        self.assertEqual(material['repair']['feedback']['previous_output'],bad)
        self.assertNotIn('previous_output',json.dumps(d.public_item(article)))
        self.assertNotIn('fake-test-key',json.dumps(material))


class DigestPipelineTests(unittest.TestCase):
    def test_bad_numeric_fragment_is_omitted_without_discarding_valid_chinese_facts(self):
        raw=reply();raw['details'][0]['text']='这项任务成本为9999万元。'
        article=item();digest=d.retain_valid_parts(raw,article,d.source_material(article))
        self.assertEqual(digest['status'],'partial')
        self.assertNotIn('9999',json.dumps(digest,ensure_ascii=False))
        self.assertIn('美国航天局',digest['overview']['text'])
        self.assertEqual(digest['omitted']['details_0'],'unsupported_number')
        self.assertEqual(digest['omitted']['elements_location'],'unverified_translation_omitted')

    def test_no_valid_factual_content_cannot_be_retained_as_analysis_only(self):
        raw=reply();raw['overview']['text']='原文没有的9999颗卫星。';raw['details']=[]
        self.assertIsNone(d.retain_valid_parts(raw,item(),d.source_material(item())))

    def test_one_targeted_repair_counts_toward_request_budget(self):
        class Client:
            def __init__(self):self.calls=[]
            def summarize(self,article,blocks,feedback=''):
                self.calls.append(feedback)
                if not feedback:raise d.DigestError('unsupported_number','删除没有来源的年份')
                return d.validate_digest(reply(),article,blocks)
        client=Client()
        health=d.enrich([item()],[],{},env={},client=client,sleep=lambda _:None)
        self.assertEqual((health['requests'],health['generated']),(2,1))
        self.assertEqual(client.calls,['','删除没有来源的年份'])
        client=Client()
        health=d.enrich([item()],[],{'chinese_digest':{'max_requests':1}},env={},client=client)
        self.assertEqual(health['requests'],1)
        self.assertEqual(len(client.calls),1)

    def test_missing_key_uses_chinese_excerpt_or_localized_failure(self):
        cn = item(); cn['title'] = '衛星發射'; cn['_source_text'] = '航天局發射衛星，計劃研究太陽活動。'
        en = item()
        health = d.enrich([cn], [en], {}, env={})
        self.assertEqual((health['requests'], health['excerpt'], health['unavailable']), (0, 1, 1))
        self.assertEqual(cn['digest']['title']['text'], '卫星发射')
        self.assertIsNone(cn['digest']['insight'])
        page = render([cn], {'today': '2026-10-09', 'now_display': '2026-10-09 15:30 +0800',
                           'mode': 'send', 'reading_links': [{**en, 'missing_elements': ['location']}],
                           'digest_health': health, 'source_health': {}})
        self.assertNotIn('Agency launches', page)
        self.assertNotIn('NASA launched', page)
        self.assertIn('本条中文整理暂未生成', page)
        self.assertIn('密钥尚未配置', page)

    def test_auth_or_quota_failure_stops_remaining_calls(self):
        for error in ('http_401', 'http_402', 'http_429'):
            class Client:
                calls = 0
                def summarize(self, article, blocks):
                    self.calls += 1
                    raise d.DigestError(error)
            client = Client()
            health = d.enrich([item(), item()], [], {}, env={}, client=client, sleep=lambda _: None)
            self.assertEqual(client.calls, 1)
            self.assertEqual(health['unavailable'], 2)

    def test_run_request_cap_and_original_evidence_preserved(self):
        class Client:
            def summarize(self, article, blocks):
                return d.validate_digest(reply(), article, blocks)
        items = [item(), item()]; original = copy.deepcopy(items[0])
        health = d.enrich(items, [], {'chinese_digest': {'max_requests': 1}}, env={}, client=Client())
        self.assertEqual(health['requests'], 1)
        self.assertEqual(health['generated'], 1)
        for field in d.FIELDS:
            self.assertEqual(items[0][field], original[field])
            self.assertEqual(items[0][field + '_evidence'], original[field + '_evidence'])

    def test_disabled_push_never_calls_live_client(self):
        with patch.object(d, 'DigestClient') as factory:
            d.enrich([item()], [], {}, env={'NEWS_AI_LIVE': '0', 'NEWS_AI_API_KEY': 'fake-test-key'})
        factory.assert_not_called()

    def test_report_keeps_same_enriched_readings_without_full_text(self):
        import datetime as dt
        it = item(); it['digest'] = d.validate_digest(reply(), it, d.source_material(it))
        it['missing_elements'] = ['location']
        now = dt.datetime(2026, 10, 9, 9, 0, tzinfo=b.BEIJING_TZ)
        report = b.build_report([], [], {}, {}, now, 'dry-run',
                                {'state': 'dry-run', '_reading': [it], '_digest_health': {'generated': 1}})
        self.assertEqual(report['reading_links'][0]['digest'], it['digest'])
        self.assertNotIn('_source_text', json.dumps(report))
        self.assertNotIn('_reading', report['delivery'])
        self.assertEqual(report['chinese_digest']['generated'], 1)

    def test_rss_basis_and_inference_are_visible_in_html_and_plain(self):
        it = item(); it['content_basis'] = '官方 RSS 摘要'
        it['digest'] = d.validate_digest(reply(), it, d.source_material(it))
        meta = {'today': '2026-10-09', 'now_display': '2026-10-09 15:30 +0800', 'mode': 'send',
                'reading_links': [], 'source_health': {}, 'excluded': 0}
        for text in (render([it], meta), b.render_plain([it], meta)):
            self.assertIn('仅据RSS摘要整理', text)
            self.assertIn('简短解读 · 推断', text)
            self.assertNotIn('Agency launches', text)
            self.assertNotIn('佛羅里達', text)


if __name__ == '__main__':
    unittest.main()
