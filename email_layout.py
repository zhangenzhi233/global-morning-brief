"""Mobile email layout with inline fallback styles; no scripts, remote images or fonts."""
import datetime as dt
from html import escape as esc
from newsroom import TOPICS

LABELS={'event_time':'时间','location':'地点','subject':'人物或机构'}


def published(it):
    raw=it.get('publish_time','')
    try:
        day=dt.date.fromisoformat(raw[:10])
        clock=raw[11:16] if len(raw)>=16 else ''
        return f'{day.month}月{day.day}日'+(' '+clock if clock else ' · 日期级')
    except ValueError:return raw


def credit(it):
    names={'BBC':'英国广播公司（BBC）','Reuters':'路透社','AFP':'法新社','AP':'美联社',
           'CNA':'亚洲新闻台（CNA）','France 24':'法国24新闻台','France24':'法国24新闻台',
           'NASA':'美国国家航空航天局','DW':'德国之声'}
    source=names.get(it.get('source',''),it.get('source',''))
    origin=names.get(it.get('origin',''),it.get('origin',''))
    if origin and origin!=source: return origin+' · 刊载于'+source
    return source


def title(it):
    return it.get('digest',{}).get('title',{}).get('text',it['title'])


def digest_body(it):
    digest=it.get('digest')
    if not digest:
        return f'<div style="font-size:14px;line-height:1.7;color:#536170;margin-top:7px;">{esc(it.get("lede",""))}</div>'
    if digest['status']=='unavailable':
        return '<p style="font-size:14px;line-height:1.8;color:#7d6870;">中文整理暂不可用，本条保留来源入口；不使用未翻译的正文替代。</p>'
    overview=digest['overview']['text']
    parts=[f'<p style="font-size:15px;line-height:1.9;color:#394b5c;margin:10px 0;">{esc(overview)}</p>'] if overview else []
    if digest.get('details'):
        rows=''.join(f'<tr><td style="width:13px;vertical-align:top;color:#ad8749;">·</td><td style="padding-bottom:7px;">{esc(d["text"])}</td></tr>' for d in digest['details'])
        parts.append(f'<div style="font-size:12px;color:#617385;margin:13px 0 7px;font-weight:600;">关键细节</div><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;line-height:1.85;color:#394b5c;table-layout:fixed;">{rows}</table>')
    if digest.get('insight'):
        parts.append(f'<div style="background:#f4f6f8;border-left:2px solid #b99a62;padding:10px 12px;margin:10px 0;font-size:14px;line-height:1.85;color:#485b6c;"><b style="font-size:12px;color:#7c663e;">简短解读 · 推断</b><br>{esc(digest["insight"]["text"])}</div>')
    status='简体中文整理 · AI辅助' if digest['status']=='ok' else '简体中文摘编 · 解读暂未生成'
    if digest['status']=='partial':status='简体中文整理 · 未通过校验的片段已省略'
    if digest.get('provenance')=='editor_sample':status='人工依据原文整理的格式样张 · 尚非接口实测输出'
    if digest.get('provenance')=='fictional_sample':status='人工构造的虚构样张 · 非真实新闻或接口输出'
    if digest.get('basis')!='正文':status+=' · 仅据RSS摘要整理'
    parts.append(f'<div style="font-size:11px;line-height:1.7;color:#81909d;margin-top:8px;">{esc(status)}</div>')
    return ''.join(parts)


def reading_sections(items, reading):
    """One display order shared by the HTML contents, article numbers and text email."""
    groups = {}
    for formal, group in ((True, items), (False, reading)):
        for it in group:
            category = it.get('category') or '其他报道'
            groups.setdefault(category, []).append((it, formal))
    categories = [category for category in TOPICS if category in groups]
    categories.extend(category for category in groups if category not in TOPICS)
    sections = []
    number = 1
    for index, category in enumerate(categories, 1):
        entries = [(number + offset, it, formal)
                   for offset, (it, formal) in enumerate(groups[category])]
        sections.append((category, f'topic-{index}', entries))
        number += len(entries)
    return sections


def anchor(target):
    # Keep both id and the legacy name attribute for email clients with older renderers.
    return f'<a id="{esc(target)}" name="{esc(target)}"></a>'


def article_heading(it, number, formal):
    status = '三要素齐全' if formal else '要素待核实'
    color = '#286457' if formal else '#85643b'
    return f'''{anchor(f'news-{number:02d}')}
      <div style="font-size:12px;line-height:1.7;margin-bottom:7px;">
        <b style="font-size:17px;color:#253e52;margin-right:10px;">{number:02d}</b>
        <span style="color:{color};">{status}</span>
      </div>
      <div style="font-size:12px;line-height:1.7;color:#687484;">{esc(it.get('coverage_region','中国'))} · {esc(credit(it))}</div>
      <h3 style="font-size:19px;line-height:1.6;font-weight:600;color:#172635;margin:7px 0 9px;overflow-wrap:anywhere;">{esc(title(it))}</h3>'''


def article_links(it):
    return f'''<div style="font-size:12px;line-height:2;margin-top:10px;">
      <a href="{esc(it['url'])}" style="display:inline-block;color:#276c8a;text-decoration:underline;margin-right:20px;">核对原文 ↗</a>
      <a href="#brief-toc" style="display:inline-block;color:#52677a;text-decoration:underline;">返回目录 ↑</a>
    </div>'''


def reading_row(it, number):
    missing='、'.join(LABELS[k] for k in it['missing_elements'])
    return f'''<tr><td style="padding:20px 0;border-bottom:1px solid #e6e8eb;">
      {article_heading(it, number, False)}
      {digest_body(it)}
      <div style="font-size:11px;line-height:1.7;color:#7d6870;margin-top:7px;">发布 {esc(published(it))}（北京时间） · 待核实要素：{esc(missing)}</div>
      {article_links(it)}
    </td></tr>'''


def formal_card(it, number):
    elements=it.get('digest',{}).get('elements',it)
    fields='<br>'.join(f'<b>{LABELS[k]}</b> {esc(elements.get(k) or "中文字段暂未生成")}' for k in LABELS)
    return f'''<tr><td style="padding:18px 0;border-bottom:1px solid #e6e8eb;">
      {article_heading(it, number, True)}
      {digest_body(it)}
      <div style="background:#f3f5f7;border-left:2px solid #c49a50;padding:9px 12px;font-size:12px;line-height:1.8;color:#536170;">
        {fields}
      </div>
      <div style="font-size:11px;color:#81909d;margin-top:9px;">发布 {esc(published(it))}（北京时间）</div>
      {article_links(it)}
    </td></tr>'''


def section(title,subtitle,rows,target=None):
    return f'''<tr><td style="padding:26px 22px 0;">
      {anchor(target) if target else ''}
      <h2 style="font-size:19px;line-height:1.5;margin:0;padding-bottom:10px;border-bottom:2px solid #253e52;color:#253e52;">{esc(title)}</h2>
      <div style="font-size:11px;line-height:1.7;color:#81909d;margin-top:6px;">{esc(subtitle)}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;table-layout:fixed;">{rows}</table>
    </td></tr>'''


def contents(sections):
    if not sections:
        return ''
    groups = []
    for category, target, entries in sections:
        rows = ''.join(f'''<tr>
          <td width="32" valign="top" style="width:32px;padding:6px 0;font-size:12px;line-height:1.7;color:#81909d;">{number:02d}</td>
          <td style="padding:6px 0;font-size:14px;line-height:1.7;"><a href="#news-{number:02d}" style="color:#253e52;text-decoration:none;">{esc(title(it))}</a></td>
        </tr>''' for number, it, _formal in entries)
        groups.append(f'''<div style="margin:16px 0 4px;font-size:12px;font-weight:600;line-height:1.7;">
          <a href="#{target}" style="color:#276c8a;text-decoration:none;">{esc(category)} · {len(entries)}条</a>
        </div><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;">{rows}</table>''')
    return f'''<tr><td style="padding:20px 22px 18px;border-bottom:1px solid #e4e7eb;">
      {anchor('brief-toc')}
      <h2 style="font-size:18px;line-height:1.5;margin:0 0 5px;color:#253e52;">本期目录</h2>
      <div style="font-size:11px;line-height:1.8;color:#81909d;">点击标题查看正文；记住新闻编号，方便下次查找。</div>
      {''.join(groups)}
    </td></tr>'''


def render(items,meta):
    reading=meta.get('reading_links',[]);all_items=items+reading
    groups=reading_sections(items,reading)
    chips=''.join(f'<a href="#{target}" style="display:inline-block;margin:0 12px 6px 0;font-size:12px;line-height:1.8;color:#276c8a;text-decoration:underline;">{esc(category)} {len(entries)}</a>' for category,target,entries in groups)
    date=dt.date.fromisoformat(meta['today']);weekday='一二三四五六日'[date.weekday()]
    cutoff=meta['now_display'][11:16]
    sections=[]
    for category,target,entries in groups:
        first,last=entries[0][0],entries[-1][0]
        complete=sum(formal for _number,_it,formal in entries)
        subtitle=f'第 {first:02d}—{last:02d} 条 · 三要素齐全 {complete} 条 · 要素待核实 {len(entries)-complete} 条'
        rows=''.join(formal_card(it,number) if formal else reading_row(it,number) for number,it,formal in entries)
        sections.append(section(category,subtitle,rows,target))
    if not all_items:
        sections.append(section('今日暂无合格新消息','来源读取情况见下方；本期没有使用旧闻补齐。',''))
    health=meta.get('source_health',{})
    available=sum(v.get('status') in ('ok','empty') or ('status' not in v and v.get('ok')) for v in health.values())
    unavailable=[v['name'] for v in health.values() if not v.get('ok')]
    health_note=f'来源检查 {available}/{len(health)} 正常'
    if unavailable: health_note+='；受限或部分失败：'+'、'.join(unavailable)
    preview=' · '.join(title(it)[:50] for it in all_items[:3]) or '今日暂无合格新消息'
    digest_health=meta.get('digest_health',{})
    digest_note=''
    if digest_health.get('status')=='degraded':
        digest_note=f'<div style="margin-top:10px;font-size:12px;line-height:1.8;color:#876343;">中文整理生成 {digest_health.get("generated",0)} 条。'
        if digest_health.get('partial'):digest_note+=f'其中 {digest_health["partial"]} 条省略了未通过校验的片段。'
        if digest_health.get('excerpt') or digest_health.get('unavailable'):digest_note+='其余使用中文摘编或注明暂不可用。'
        if digest_health.get('reason')=='missing_key':digest_note+='云端接口密钥尚未配置。'
        digest_note+='</div>'
    sample_note=f'<div style="margin-top:10px;font-size:12px;line-height:1.8;color:#876343;">{esc(meta["sample_notice"])}</div>' if meta.get('sample_notice') else ''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="format-detection" content="telephone=no,date=no,address=no,email=no"><title>环球早报</title>
      <style>body{{margin:0!important}}td{{word-wrap:break-word;overflow-wrap:anywhere}}a[x-apple-data-detectors]{{color:inherit!important;text-decoration:none!important}}@media(max-width:420px){{.paper{{width:100%!important}}}}</style></head>
      <body style="margin:0;background:#f2f3f5;color:#172635;font-family:-apple-system,'PingFang SC','Microsoft YaHei',Arial,sans-serif;">
      <div style="display:none;font-size:1px;line-height:1px;color:#f2f3f5;max-height:0;max-width:0;opacity:0;overflow:hidden;">{esc(preview)}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f3f5;width:100%;table-layout:fixed;"><tr><td align="center" style="padding:12px 0;">
      <table class="paper" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#fff;table-layout:fixed;">
      <tr><td style="padding:25px 22px 18px;border-top:4px solid #253e52;border-bottom:1px solid #e4e7eb;">
        <div style="font-size:11px;letter-spacing:2px;color:#8d7347;margin-bottom:8px;">官方机构 · 权威媒体 · 每日精选</div>
        <h1 style="font-size:29px;line-height:1.3;letter-spacing:2px;margin:0 0 12px;color:#172635;font-family:'Songti SC','SimSun',serif;">环球早报</h1>
        <div style="font-size:12px;color:#6a7885;">{date.year}年{date.month}月{date.day}日 &nbsp;周{weekday} &nbsp;·&nbsp; 采集截至 {esc(cutoff)} 北京时间{' · 试刊' if meta['mode']=='dry-run' else ''}</div>
      </td></tr>
      <tr><td style="padding:16px 22px 2px;"><div style="font-size:13px;line-height:1.8;color:#253e52;margin-bottom:8px;">本期 {len(all_items)} 条 · 正式新闻 {len(items)} 条 · 最新报道链接 {len(reading)} 条</div>{chips}{sample_note}</td></tr>
      {contents(groups)}
      {''.join(sections)}
      <tr><td style="padding:26px 22px;font-size:11px;line-height:1.8;color:#87929d;">
        {digest_note}{esc(health_note)}<br>正文统一简体中文。报道事实与简短解读分开呈现；解读属于基于材料的推断，AI整理可能有误。缺失要素不补写，原文链接用于进一步核对。<br>
        云端每日定时采集 · 电脑关机也能运行 · 平台调度可能延迟
      </td></tr></table></td></tr></table></body></html>'''
