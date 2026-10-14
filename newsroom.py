"""Transparent editorial rules: major regions, topic balance, source caps, no generated facts."""
import re
from collections import Counter
from difflib import SequenceMatcher

TOPICS = {
 '科技与产业': ['科技','科学','人工智能','芯片','航天','卫星','机器人','量子','网络攻击','网络安全','半导体','NASA','JAXA','SpaceX','OpenAI','Nvidia','AI','technology','science','cyber','chip','space','satellite','research','robot'],
 '经济与金融': ['经济','金融','贸易','关税','通胀','利率','央行','股市','证券','汇率','就业','货币','能源价格','IPO','economy','economic','market','trade','tariff','inflation','rates','bank','finance','financial','business','oil price'],
 '健康与公共卫生': ['卫生','健康','世卫','疫情','疫苗','医疗','疾病','癌症','health','WHO','disease','vaccine','hospital','outbreak','cancer'],
 '气候与环境': ['气候','环境','气象','洪水','干旱','飓风','台风','排放','生态','地震','climate','environment','flood','drought','hurricane','emission','earthquake','storm'],
 '社会与文化': ['教育','住房','社会','文化','艺术','体育','人口','移民','housing','education','culture','sport','football','school','migration','protest'],
 '政治与安全': ['政治','外交','总统','首相','总理','议会','选举','军事','战争','停火','冲突','politic','president','minister','election','war','peace','security','military','ceasefire','government'],
}
MAJOR_ZH = ['中国','美国','欧盟','欧洲','英国','法国','德国','意大利','西班牙','俄罗斯','乌克兰','日本','韩国','印度','印度尼西亚','澳大利亚','加拿大','巴西','墨西哥','土耳其','沙特','伊朗','以色列','南非','台湾','香港','东盟']
MAJOR_EN = r'\b(?:US|U\.S\.|United States|China|Chinese|EU|European|Europe|UK|Britain|British|France|French|Germany|German|Italy|Spain|Russia|Russian|Ukraine|Ukrainian|Japan|Japanese|South Korea|India|Indian|Indonesia|Australia|Australian|Canada|Brazil|Mexico|Turkey|Saudi|Iran|Israel|South Africa|Taiwan|Hong Kong|ASEAN)\b'
SYSTEMIC = r'全球|世界经济|国际货币|联合国安理会|大流行|全球供应链|全球气候|\b(?:global|worldwide|pandemic|United Nations Security Council|world economy)\b'


def topic(title, lead, hint=''):
    # Headline carries more weight than incidental words in the body.
    text=title+' '+lead[:400]
    scores={}
    for category, words in TOPICS.items():
        score=0
        for word in words:
            pattern=re.escape(word) if re.search('[\u4e00-\u9fff]',word) else r'\b'+re.escape(word)+r'(?:s|al|ing)?\b'
            score += 3*bool(re.search(pattern,title,re.I)) + bool(re.search(pattern,text,re.I))
        scores[category]=score
    if max(scores.values())==0: return hint if hint in TOPICS else '社会与文化'
    return max(scores,key=scores.get)


def priority(item):
    text=item['title']+' '+item.get('lede','')+' '+item.get('location','')
    return 2*bool(any(p in text for p in MAJOR_ZH) or re.search(MAJOR_EN,text,re.I)) + 2*bool(re.search(SYSTEMIC,text,re.I))


def balanced(items, config):
    limit=max(0,int(config.get('max_items',24)))
    ordered=sorted(items,key=lambda c:(priority(c),c.get('publish_time','')),reverse=True)
    selected=[]; skipped=[]; sources=Counter(); regions=Counter(); topics=Counter()
    source_cap=int(config.get('max_per_source',6)); region_cap=int(config.get('max_per_region',10))
    def take(c):
        if len(selected)>=limit or any(c is it for it in selected): return False
        if sources[c['source_key']]>=source_cap or regions[c.get('coverage_region','全球及跨地区')]>=region_cap: return False
        # Near-identical syndicated headlines count once, preserving the first link/credit.
        normalized=re.sub(r'\W','',c['title']).lower()
        for it in selected:
            other=re.sub(r'\W','',it['title']).lower()
            if normalized and SequenceMatcher(None,normalized,other).ratio()>=.90: return False
        selected.append(c); sources[c['source_key']]+=1
        regions[c.get('coverage_region','全球及跨地区')]+=1; topics[c['category']]+=1
        return True
    # At least one recent eligible item per topic and region before adding second choices.
    for category in TOPICS:
        for c in ordered:
            if c['category']==category and take(c): break
    for region in ('亚太','美洲','欧洲','中东及北非','非洲','全球及跨地区','中国'):
        if regions[region]: continue
        for c in ordered:
            if c.get('coverage_region','中国')==region and take(c): break
    for category in TOPICS:
        for c in ordered:
            if topics[category]>=2: break
            if c['category']==category: take(c)
    for c in ordered: take(c)
    for c in items:
        if not any(c is it for it in selected):
            c['_excluded_reason']='选稿上限、来源/地区配额或近似转载去重'; skipped.append(c)
    selected.sort(key=lambda c:c.get('publish_time',''),reverse=True)
    selected.sort(key=lambda c:list(TOPICS).index(c['category']))
    return selected,skipped


def reading_links(excluded, selected, config):
    """Only fresh publisher candidates excluded for missing elements; user explicitly opted in."""
    if not config.get('include_reading_links',False): return []
    pool=[]; urls={it['url'] for it in selected}; titles={it['title'].lower() for it in selected}
    for c in excluded:
        if not c.get('_excluded_reason','').startswith('缺要素：'): continue
        if c['url'] in urls or c['title'].lower() in titles: continue
        copy=dict(c)
        copy['category']=topic(c['title'],c.get('lede',''),c.get('category',''))
        copy.setdefault('coverage_region','中国')
        copy['missing_elements']=[k for k in ('event_time','location','subject') if c.get(k) in ('','原文未明确',None) or not c.get(k+'_evidence')]
        pool.append(copy);urls.add(c['url']);titles.add(c['title'].lower())
    reading_config=dict(config,max_items=int(config.get('max_reading_links',18)))
    return balanced(pool,reading_config)[0]
