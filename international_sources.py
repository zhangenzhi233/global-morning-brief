"""Official publisher feeds and article metadata; no paid APIs, redirects or guessed dates."""
from __future__ import annotations

import datetime as dt
import html
from html.parser import HTMLParser
import json
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit, quote
import xml.etree.ElementTree as ET

TZ = dt.timezone(dt.timedelta(hours=8))
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


def plain(value):
    p = Text(); p.feed(value or '')
    return re.sub(r'\s+', ' ', html.unescape(''.join(p.parts))).strip()


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip += 1
        if tag in ('p','div','br'): self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        if tag in ('p','div'): self.parts.append(' ')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


class Article(HTMLParser):
    def __init__(self, kind):
        super().__init__(convert_charrefs=True)
        self.kind=kind; self.stack=[]; self.meta={}; self.links=[]
        self.paragraphs=[]; self.leads=[]; self.times=[]; self.jsonld=[]
        self.h1=[]; self.visible_dates=[]; self.byline=[]
        self.capture=None; self.script=None

    def scope(self):
        for tag, attrs in self.stack:
            classes=set(attrs.get('class','').split()); ident=attrs.get('id','')
            if self.kind=='un_rss' and ('field-news-story-lead' in classes or ident=='block-views-block-content-fields-block-lead'): return 'lead'
            if self.kind=='un_rss' and ident=='block-views-block-content-fields-block-body': return 'body'
            if self.kind=='fao_rss' and 'news-detail__body' in classes: return 'body'
            if self.kind=='wmo_html' and 'field--name-body' in classes: return 'body'
            if self.kind=='xinhua_html' and ident in ('detailContent','detail'): return 'body'
            if self.kind=='cna_rss' and 'text-long' in classes: return 'body'
            if self.kind=='dw_rss' and 'rich-text' in classes: return 'body'
            if self.kind=='bbc_rss' and attrs.get('data-component')=='text-block': return 'body'
            if self.kind in ('f24_rss','rfi_rss') and 't-content__body' in classes: return 'body'
            if self.kind=='nasa_rss' and 'entry-content' in classes: return 'body'
            if self.kind=='fed_rss' and ident=='article': return 'body'
        return ''

    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='meta':
            key=(a.get('property') or a.get('name') or '').lower()
            self.meta[key]=a.get('content','')
        if tag=='a' and a.get('href'): self.links.append(a['href'])
        if tag=='script' and a.get('type','').lower()=='application/ld+json': self.script=[]
        if tag not in VOID: self.stack.append((tag,a))
        if self.capture is None:
            classes=set(a.get('class','').split())
            kind=''
            if tag=='p' and self.scope(): kind=self.scope()
            elif tag=='h1': kind='h1'
            elif tag=='time': kind='time'
            elif classes & {'header-time','pub-date','news-detail__date','date-display-single','article__source','article-source','source'}: kind='visible'
            if kind: self.capture=[len(self.stack),kind,[],a]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag=='script' and self.script is not None:
            self.jsonld.append(''.join(self.script)); self.script=None
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
        if index is None: return
        if self.capture and self.capture[1]=='visible' and tag in ('span','em','br'):
            self.capture[2].append(' ')
        if self.capture and index < self.capture[0]:
            _,kind,parts,attrs=self.capture; value=re.sub(r'\s+',' ',''.join(parts)).strip()
            if value:
                if kind=='body': self.paragraphs.append(value)
                elif kind=='lead': self.leads.append(value)
                elif kind=='h1': self.h1.append(value)
                elif kind=='time': self.times.append((attrs.get('datetime',''),value))
                else:
                    self.visible_dates.append(value)
                    if re.search(r'(?:Source\s*:|^来源[：:]|^(?:Reuters|AFP|AP)$)',value,re.I): self.byline.append(value)
            self.capture=None
        self.stack=self.stack[:index]

    def handle_data(self, data):
        if self.script is not None: self.script.append(data); return
        if self.capture and not any(t in ('script','style','figure','figcaption') for t,a in self.stack): self.capture[2].append(data)


def parse_stamp(value):
    value=(value or '').strip()
    try: parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        try: parsed=parsedate_to_datetime(value)
        except (ValueError,TypeError,OverflowError): return None
    if parsed.tzinfo is None: return None
    return parsed


def fresh(stamp, now, hours):
    value=parse_stamp(stamp)
    return value is not None and dt.timedelta(0) <= now-value <= dt.timedelta(hours=hours)


def structured_article(parser):
    # Traverse only top-level @graph/list, not nested related articles or image dates.
    for block in parser.jsonld:
        try: value=json.loads(block)
        except (ValueError,TypeError): continue
        nodes=value if isinstance(value,list) else value.get('@graph',[value]) if isinstance(value,dict) else []
        for node in nodes:
            if not isinstance(node,dict): continue
            types=node.get('@type',[]); types=[types] if isinstance(types,str) else types
            if set(types) & {'NewsArticle','Article','Report','BlogPosting'}: return node
    return {}


def xml_entries(page):
    envelope=re.sub(r'<!\[CDATA\[.*?\]\]>', '',page,flags=re.S)
    if len(page)>3_000_000 or re.search(r'<!\s*(?:DOCTYPE|ENTITY)',envelope,re.I): raise ValueError('Unsafe or oversized feed')
    root=ET.fromstring(page)
    if root.tag.split('}')[-1] not in ('rss','RDF','feed'): raise ValueError('Source returned a non-feed document')
    result=[]
    for item in root.iter():
        if item.tag.split('}')[-1] not in ('item','entry'): continue
        row={}
        for child in item:
            key=child.tag.split('}')[-1]
            if key in ('title','link','pubDate','date','published','description','summary','creator','author'):
                row[key]=(child.text or child.get('href') or '').strip()
        result.append(row)
    return result


def canonical_link(url, kind):
    parts=urlsplit(url)
    if parts.scheme not in ('https','http'): return ''
    # Some publisher feeds still declare HTTP links. Upgrade before any request.
    path=parts.path
    if kind=='un_rss' and parts.hostname=='news.un.org': path=path.replace('/feed/view/zh/story/','/zh/story/')
    return urlunsplit(('https',parts.netloc,quote(path,safe='/%:@-._~'),'', ''))


def fetch_records(http, source_key, source_cfg, now, fresh_hours=24, max_candidates=10):
    kind=source_cfg['kind']; list_url=source_cfg['list_url']
    limit=int(source_cfg.get('max_candidates',max_candidates)); page=http.get_text(list_url)
    entries=[]
    if kind in ('xinhua_html','wmo_html'):
        listing=Article(kind); listing.feed(page)
        seen=set()
        for href in listing.links:
            link=canonical_link(urljoin(list_url,href),kind)
            path=urlsplit(link).path
            if kind=='xinhua_html':
                match=re.search(r'/(\d{8})/[^/]+/c\.html$',path)
                if not match or urlsplit(link).hostname!='www.news.cn': continue
                day=dt.datetime.strptime(match.group(1),'%Y%m%d').date()
                if not 0 <= (now.astimezone(TZ).date()-day).days <= 1: continue
            elif not re.match(r'^/zh-hans/media/(?:news|press-release)/',path): continue
            if link not in seen: entries.append({'link':link}); seen.add(link)
        if kind=='xinhua_html': entries.sort(key=lambda e:e['link'],reverse=True)
    else: entries=xml_entries(page)
    records=[]; considered=0
    for entry in entries:
        feed_stamp=entry.get('pubDate') or entry.get('published') or entry.get('date') or ''
        if feed_stamp and not fresh(feed_stamp,now,72 if kind=='un_rss' else fresh_hours): continue
        link=canonical_link(entry.get('link',''),kind)
        if not link: continue
        # List items may point to advertisements or other domains. Never follow them.
        if urlsplit(link).hostname not in set(source_cfg.get('article_hosts',[urlsplit(list_url).hostname])): continue
        http.check_url(link)
        if considered>=limit: break
        considered+=1
        parser=Article(kind); parsed=False
        try:
            parser.feed(http.get_text(link)); parsed=True
        except Exception:
            # Public RSS titles/descriptions remain valid publisher material; never fabricate a full article.
            if kind in ('un_rss','fao_rss','wmo_html','xinhua_html'): continue
        data=structured_article(parser)
        stamp=parser.meta.get('article:published_time') or data.get('datePublished') or ''
        if kind=='xinhua_html':
            day=parser.meta.get('publishdate','')
            clock=next((re.search(r'\b(\d{2}:\d{2}:\d{2})\b',v).group(1) for v in parser.visible_dates if re.search(r'\b\d{2}:\d{2}:\d{2}\b',v)),None)
            stamp=day+'T'+clock+'+08:00' if day and clock else day
        if not stamp: stamp=feed_stamp
        if not stamp: continue
        exact=parse_stamp(stamp)
        precision='minute' if exact else 'day'
        if precision=='minute':
            if not fresh(stamp,now,fresh_hours): continue
            # Explicit visible date and publication metadata must agree in the original timezone.
            visible_days=[]
            for _,text in parser.times[:1]: visible_days.extend(re.findall(r'(\d{4})年(\d{1,2})月(\d{1,2})日',text))
            for text in parser.visible_dates: visible_days.extend(re.findall(r'(\d{4})年(\d{1,2})月(\d{1,2})日',text))
            if kind=='wmo_html' and parser.paragraphs:
                # WMO also puts the release date at the start of the actual lead.
                visible_days.extend(re.findall(r'^(\d{4})年(\d{1,2})月(\d{1,2})日',parser.paragraphs[0]))
            if any(dt.date(*map(int,d)) != exact.date() for d in visible_days): continue
        elif not re.fullmatch(r'\d{4}-\d{2}-\d{2}',stamp) or stamp!=now.astimezone(TZ).date().isoformat(): continue
        # Prefer the publisher's real headline and leading article paragraphs, never site navigation.
        title=plain(parser.h1[0] if parser.h1 else data.get('headline') or entry.get('title',''))
        paras=parser.leads+parser.paragraphs
        # Photo captions and daily astronomy archive features are not current event news.
        if kind=='nasa_rss' and ('/image-article/' in link or '/blogs/' in link): continue
        body='\n'.join(dict.fromkeys(p for p in paras if len(p)>=25))
        if not body: body=plain(entry.get('description') or entry.get('summary') or '')
        if not title or not body: continue
        origin=source_cfg['name']
        if kind=='xinhua_html':
            origin='新华社' if re.match(r'^新华社',body.strip()) else parser.meta.get('source') or origin
        credited=' '.join(parser.byline+[entry.get('creator','')])
        # This is a byline/source credit, not a mention in a quotation or photo copyright.
        credits=[]
        if re.search(r'\bReuters\b|路透社',credited,re.I): credits.append('路透社 Reuters')
        if re.search(r'\bAFP\b|法新社',credited,re.I): credits.append('法新社 AFP')
        if re.search(r'\bAssociated Press\b|\bAP\b|美联社',credited,re.I): credits.append('美联社 AP')
        if credits: origin=' / '.join(credits)
        records.append({'title':title,'url':link,'body':body,'publish_time':exact.isoformat() if exact else stamp,
                        'publish_precision':precision,'origin':origin,
                        'content_basis':'正文' if parsed and paras else '官方 RSS 摘要'})
    return records
