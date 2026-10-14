"""国际中文消息的保守要素识别；只用标题和同一篇文章的首段。"""
from __future__ import annotations

import re

UNKNOWN = '原文未明确'
REGION_PLACES = {
    '亚太': '中国 日本 韩国 朝鲜 蒙古 印度 巴基斯坦 阿富汗 孟加拉国 尼泊尔 不丹 斯里兰卡 马尔代夫 缅甸 泰国 越南 老挝 柬埔寨 马来西亚 新加坡 印度尼西亚 菲律宾 东帝汶 澳大利亚 新西兰 巴布亚新几内亚 斐济 汤加 萨摩亚 瓦努阿图 所罗门群岛 伊斯兰堡 新德里 东京 首尔 雅加达 悉尼 堪培拉 惠灵顿 曼谷 达卡 科伦坡',
    '欧洲': '英国 法国 德国 意大利 西班牙 葡萄牙 瑞士 奥地利 荷兰 比利时 波兰 乌克兰 俄罗斯 白俄罗斯 希腊 瑞典 挪威 芬兰 丹麦 冰岛 爱尔兰 捷克 斯洛伐克 匈牙利 罗马尼亚 保加利亚 塞尔维亚 克罗地亚 波黑 阿尔巴尼亚 北马其顿 摩尔多瓦 爱沙尼亚 拉脱维亚 立陶宛 欧盟 欧洲 伦敦 巴黎 柏林 罗马 日内瓦 维也纳 布鲁塞尔 莫斯科 基辅 马德里',
    '非洲': '苏丹 南苏丹 索马里 肯尼亚 埃塞俄比亚 厄立特里亚 吉布提 乌干达 卢旺达 布隆迪 坦桑尼亚 刚果民主共和国 刚果共和国 中非共和国 乍得 尼日尔 尼日利亚 加纳 多哥 贝宁 塞内加尔 马里 布基纳法索 几内亚 塞拉利昂 利比里亚 科特迪瓦 喀麦隆 加蓬 南非 安哥拉 赞比亚 津巴布韦 莫桑比克 马达加斯加 马拉维 博茨瓦纳 纳米比亚 莱索托 埃斯瓦蒂尼 非洲 内罗毕 喀土穆 坎帕拉 亚的斯亚贝巴 金沙萨 朱巴 约翰内斯堡 开普敦',
    '中东及北非': '以色列 巴勒斯坦 加沙 黎巴嫩 叙利亚 约旦 伊拉克 伊朗 沙特阿拉伯 也门 阿曼 阿联酋 阿拉伯联合酋长国 卡塔尔 巴林 科威特 土耳其 埃及 利比亚 突尼斯 阿尔及利亚 摩洛哥 毛里塔尼亚 中东 北非 贝鲁特 大马士革 安曼 巴格达 德黑兰 利雅得 萨那 多哈 开罗 耶路撒冷 西岸',
    '美洲': '美国 加拿大 墨西哥 巴西 阿根廷 智利 哥伦比亚 秘鲁 厄瓜多尔 玻利维亚 巴拉圭 乌拉圭 委内瑞拉 圭亚那 苏里南 古巴 海地 多米尼加 牙买加 洪都拉斯 危地马拉 萨尔瓦多 尼加拉瓜 哥斯达黎加 巴拿马 美洲 拉丁美洲 加勒比 纽约 华盛顿 渥太华 墨西哥城 巴西利亚 圣保罗 布宜诺斯艾利斯 哈瓦那 太子港',
    '全球及跨地区': '全球 世界各地 南极 北极 太平洋 大西洋 印度洋 地中海 南半球 北半球',
}
PLACE_REGION = {p: region for region, names in REGION_PLACES.items() for p in names.split()}
PLACES = sorted(PLACE_REGION, key=len, reverse=True)

ORGANIZATIONS = sorted({
    '美国国务院', '美国联邦储备委员会', '美联储', '欧洲中央银行', '欧洲央行', '欧盟委员会',
    '欧洲议会', '日本银行', '英格兰银行', '国际能源署', '美国国家航空航天局',
    '中国科学院', '中国科学技术大学', '清华大学', '北京大学', '中国企业', '中国科研团队',
    '中国国家航天局', '国家统计局', '中国人民银行', '韩国央行', '美国政府', '美国国务院',
    '俄罗斯卫生部门', '俄罗斯卫生部', '俄罗斯政府', '国际刑事法院', '中国海警',
    '英伟达', '微软', '谷歌', '苹果公司', 'OpenAI', '三星电子', '台积电', '丰田',
    '联合国贸易和发展会议', '联合国贸发会议', '联合国粮食及农业组织', '联合国粮农组织',
    '联合国儿童基金会', '联合国难民署', '联合国开发计划署', '联合国环境规划署',
    '联合国人道主义事务协调厅', '联合国人口基金', '联合国教科文组织',
    '联合国世界粮食计划署', '世界粮食计划署', '世界卫生组织', '世卫组织',
    '世界气象组织', '粮农组织', '国际移民组织', '国际劳工组织',
    '国际原子能机构', '国际货币基金组织', '世界银行', '联合国安理会',
    '联合国大会', '联合国', 'WMO', 'WHO', 'FAO',
}, key=len, reverse=True)
_ACTION = r'(?:表示|指出|宣布|发布|警告|强调|呼吁|敦促|启动|举行|召开|通报|报告|确认|决定|完成|发生|抵达|访问|出席|签署|批准|发射|研发|发现|通过|下调|上调|维持|推出|开幕)'
_TIMES = re.compile(r'\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日(?:至\d{1,2}日)?|周[一二三四五六日天]|星期[一二三四五六日天]|今天|今日|昨天|昨日|近日|日前|本周[一二三四五六日天]|\d{1,2}日(?:上午|下午|晚上)?')
_ORG_RE = '(?:' + '|'.join(re.escape(o) for o in ORGANIZATIONS) + ')'
_ROLE = r'(?:联合国常务副秘书长|联合国副秘书长|联合国秘书长|' + _ORG_RE + r'(?:总干事|总干事代表|总裁|主席|执行主任|负责人|发言人))'
_ROLE_RE = re.compile('(' + _ROLE + r'[\u4e00-\u9fff·]{0,16}?)(?=(?:周|星期|今天|今日|昨日|昨天|\d|在|将|' + _ACTION + r'))')


def _excerpt(text: str, start: int, end: int, limit: int) -> str:
    """证据必须完整包含匹配值，而不能被截断成另一含义。"""
    spare = max(0, limit - (end - start))
    left = min(start, spare // 2)
    return text[start - left:end + spare - left].strip()


def _known_place(value: str) -> bool:
    # 地名间允许来源明确写出的首都/方向；不能让未知议题词被当作地点。
    rest = value
    for place in PLACES:
        rest = rest.replace(place, '')
    rest = re.sub(r'首都|东北部|西北部|东南部|西南部|北部|南部|东部|西部|境内|地区|附近|的|和|、|与|及', '', rest)
    return not rest and any(p in value for p in PLACES)


def extract_elements(title: str, body: str, limit: int = 32) -> dict:
    """不把发布时间、来源机构或图片说明自动当成新闻事件要素。"""
    lead = next((line.strip() for line in body.splitlines() if line.strip()), '')[:500]
    # 电头中的记者所在地与发稿日期都不能充当事件要素。
    lead = re.sub(r'^(?:新华社|中新社)[\u4e00-\u9fff·]{1,18}\d{1,2}月\d{1,2}日电\s*', '', lead)
    lead = re.sub(r'^[（(](?:法新社|路透社|美联社)[^）)]*电[）)]\s*', '', lead)
    if not re.search(r'[\u4e00-\u9fff]', lead):
        return extract_english(title, lead, limit=max(limit, 70))
    result = {k: UNKNOWN for k in ('event_time', 'location', 'subject')}
    result.update({k + '_evidence': '' for k in ('event_time', 'location', 'subject')})
    match = _TIMES.search(lead)
    if match:
        result['event_time'] = match.group()
        result['event_time_evidence'] = _excerpt(lead, *match.span(), limit)
    for text in (title, lead):
        # 地点必须由明确的空间语法或标题中的国家范围标注支持。
        candidates = list(re.finditer(r'(?:在|于|位于|赴|抵达|前往)([\u4e00-\u9fff]{2,24}?)(?=(?:' + _ACTION + r'|举行|召开|的(?:会议|记者会|发布会)|[，。；]))', text))
        prefix = re.match(r'^([\u4e00-\u9fff]{2,18})[：:]', text)
        if prefix:
            candidates.insert(0, prefix)
        for m in candidates:
            value = m.group(1)
            if _known_place(value):
                result['location'] = value
                result['location_evidence'] = _excerpt(text, *m.span(), limit)
                break
        if result['location'] != UNKNOWN:
            break
    for text in (title, lead):
        person = re.search(r'((?:美国|中国|俄罗斯|英国|法国|德国|日本|韩国|印度|加拿大|澳大利亚|巴西|以色列|伊朗|土耳其|乌克兰|南非)(?:总统|总理|首相|外长|财政部长|政府|央行|议会|最高法院)[\u4e00-\u9fff·]{0,12}?)(?=(?:在|于|\d|周|星期|将|' + _ACTION + '))',text)
        if person:
            result['subject']=person.group(1)
            result['subject_evidence']=_excerpt(text,*person.span(1),limit)
            break
        role = _ROLE_RE.search(text)
        if role:
            result['subject'] = role.group(1)
            result['subject_evidence'] = _excerpt(text, *role.span(1), limit)
            break
        # 机构与行动直接相连；只允许中间出现明确的时间/副词。
        organization = re.search('(' + _ORG_RE + r')(?=(?:于)?(?:\d{1,2}月\d{1,2}日|\d{1,2}日|周[一二三四五六日天]|今天|今日|近日)?(?:最新|正式|将|已)?' + _ACTION + ')', text)
        if organization:
            result['subject'] = organization.group(1)
            result['subject_evidence'] = _excerpt(text, *organization.span(1), limit)
            break
    return result


def coverage_region(title: str, location: str) -> str:
    # 报道范围标签与实际事件地点分别处理；不拿标题国家词反填 location。
    for text in (title, location):
        for place in PLACES:
            if place in text:
                return PLACE_REGION[place]
        for pattern, region, label in EN_PLACES:
            if re.search(pattern, text, re.I): return region
    return '全球及跨地区'


# Exact lexical patterns, including word boundaries to avoid US matching Russia.
EN_PLACES = [
 (r'\b(?:US|U\.S\.|United States|America|Washington|New York|White House|Canada|Ottawa|Mexico|Brazil|Argentina)\b','美洲','美洲'),
 (r'\b(?:China|Chinese|Beijing|Shanghai|Taiwan|Hong Kong|Japan|Japanese|Tokyo|South Korea|Seoul|India|Indian|New Delhi|Australia|Australian|Sydney|Canberra|Indonesia|Singapore|Pakistan)\b','亚太','亚太'),
 (r'\b(?:UK|Britain|British|London|France|French|Paris|Germany|German|Berlin|European Union|EU|Europe|European|Brussels|Russia|Russian|Moscow|Ukraine|Ukrainian|Kyiv|Italy|Spain|Madrid|Switzerland|Geneva)\b','欧洲','欧洲'),
 (r'\b(?:Israel|Israeli|Iran|Iranian|Saudi Arabia|Saudi|Turkey|Turkish|Gaza|Lebanon|Egypt|Cairo|Middle East)\b','中东及北非','中东及北非'),
 (r'\b(?:South Africa|Nigeria|Kenya|Sudan|African Union|Africa|African)\b','非洲','非洲'),
 (r'\b(?:global|worldwide|United Nations)\b','全球及跨地区','全球'),
]
EN_NAMES = sorted({
 'United Nations','World Health Organization','Federal Reserve Board','Federal Reserve',
 'European Central Bank','Bank of England','Bank of Japan','European Commission',
 'NASA','JAXA','ESA','OpenAI','Microsoft','Google','Alphabet','Apple','Nvidia','Samsung','TSMC',
 'SpaceX','Firmus','Toyota','United States','US President','U.S. President','President Donald Trump',
 'Donald Trump','President Xi Jinping','Xi Jinping','Vladimir Putin','President Vladimir Putin',
 'Prime Minister Keir Starmer','Keir Starmer','Emmanuel Macron','Volodymyr Zelensky',
 'South Korean banks','Japanese companies',
}, key=len, reverse=True)


def extract_english(title: str, lead: str, limit: int = 70) -> dict:
    result={k:UNKNOWN for k in ('event_time','location','subject')}
    result.update({k+'_evidence':'' for k in ('event_time','location','subject')})
    # Remove reporter datelines before extracting all event fields.
    lead=re.sub(r'^[A-Z][A-Z /-]+,?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\s*(?:\(Reuters\))?\s*[:—-]\s*','',lead)
    lead=re.sub(r'^[A-Z][A-Z /-]+:\s*','',lead)
    lead=re.sub(r'^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\s*:\s*','',lead)
    lead=re.split(r'(?<=[.!?])\s+(?=[A-Z])',lead,maxsplit=1)[0]
    time=re.search(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|today|yesterday|last week|this week|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Oct\.?|Nov\.?|Dec\.?)\s+\d{1,2}(?:,?\s+\d{4})?)\b',lead,re.I)
    if time:
        result['event_time']=time.group(); result['event_time_evidence']=_excerpt(lead,*time.span(),limit)
    # Spatial prepositions or an explicit local-market/territory scope; dateline alone is excluded.
    places=r'United States|White House|New York|Washington|China|Beijing|Japan|Tokyo|South Korea|Seoul|India|Australia|Sydney|Canada|Britain|London|France|Paris|Germany|Berlin|European Union|Europe|Brussels|Russia|Moscow|Ukraine|Kyiv|Spain|Madrid|Brazil|Israel|Iran|Gaza|South Africa|Geneva|Tanegashima Space Center|Mars|Moon|space|X|online'
    place=next((m for m in re.finditer(r'\b(?:in|at|across|over|throughout|to)\s+((?:the\s+)?(?:'+places+r'))\b',lead,re.I)
                if not re.search(r'(?:traced|linked|attributed|connected|ties)\s*$',lead[max(0,m.start()-25):m.start()],re.I)),None)
    if not place: place=re.search(r'\b(?:post(?:ed)?|statement|announced|announces|said).{0,35}\bon\s+(X|online)\b',lead,re.I)
    if not place: place=re.search(r"\b((?:Australia|China|Japan|India|Britain|Canada|Brazil|Germany|France|South Korea))(?=['’]s\s+(?:capital markets|share market|economy|government|parliament))",lead)
    if place:
        result['location']=place.group(1); result['location_evidence']=_excerpt(lead,*place.span(),limit)
        if place.group(1) in ('X','online') and time and time.group().lower() in ('last week','yesterday'):
            # A later online statement must not supply the place of an earlier action.
            result['location']=UNKNOWN; result['location_evidence']=''
    names='|'.join(re.escape(n) for n in EN_NAMES)
    subject=re.search(r'\b('+names+r')\b(?=(?:\s+(?:on\s+)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|today|yesterday|last week|this week)(?:\s+\((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\))?)?\s+(?:said|says|announced|announces|released|releases|launched|launches|warned|warns|fired|reported|approved|pulled|secured|joined|will|is|are|has|have)\b)',lead,re.I)
    if not subject:
        subject=re.search(r'\b((?:US|U\.S\.|Chinese|Japanese|British|French|German|Indian|Russian|Ukrainian)\s+(?:President|Prime Minister|government|parliament|central bank)(?:\s+[A-Z][a-z]+){0,3})\b',lead)
    if subject:
        result['subject']=subject.group(1); result['subject_evidence']=_excerpt(lead,*subject.span(1),limit)
    return result
