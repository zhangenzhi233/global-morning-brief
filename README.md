# Global Morning Brief · 环球早报

将官方机构和权威媒体的近期报道整理为简体中文邮件，按主题组织，并把报道事实与编辑推断分开。通过 GitHub Actions 云端运行，电脑关机仍可接收早报。

适合个人阅读与工程学习。正式新闻须有事件时间、实际地点、人物或机构的来源证据；未确认齐全的报道单独列出。自动规则与模型整理都有局限，不声称独立事实核查或保证准点。

## 可以做什么

- 覆盖主要国家/地区与科技产业、经济金融、健康、环境、社会文化、政治安全六类主题。
- 多语言材料经用户自己的 DeepSeek API 整理为简体中文，保留核对入口；只有 RSS 摘要时明确注明依据。
- 官方域名白名单、首次发布时间检查、标题/地址去重、主题与地区配额。
- 移动端邮件版式、HTML/纯文本/JSON产物，采集失败和中文整理降级如实记录。
- 生成前检查当日发送记录；SMTP状态不明不盲重发，竞争claim保护跨运行重复投递。
- 请求数量与时间预算、无模型离线测试。模型密钥和邮箱授权码只通过环境变量/Secrets读取。

## 不用密钥也能先查看

打开 [虚构邮件样张](samples/brief-demo.html)。它只演示版式，不是真实新闻，也不是API实测输出。

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python briefing.py --dry-run
```

Python 3.11+。`--dry-run`不发送，但会访问配置的新闻来源；若已设置`NEWS_AI_API_KEY`，也可能调用付费模型接口。关闭真实模型调用：PowerShell运行`$env:NEWS_AI_LIVE='0'`，Bash运行`export NEWS_AI_LIVE=0`。离线测试不抓新闻、不调用模型、不发送邮件。

默认产物在`run-output/`：`brief.html`、`brief.txt`、`brief.json`和`report.json`。

## 在自己的GitHub仓库运行

1. 将此代码放入自己的仓库。公开源码无需填写或公开任何凭据。
2. Settings → Secrets and variables → Actions → Secrets：填入自己的`SMTP_USER`、`SMTP_PASSWORD`、`MAIL_TO`。QQ使用SMTP授权码，默认SSL465；其他邮箱可配置`SMTP_HOST`与`SMTP_PORT`（465/587）。
3. 要启用中文模型整理，再填`NEWS_AI_API_KEY`。它是模型平台密钥，与邮箱授权码不同。
4. Variables中可设置`SMTP_FROM_NAME`，默认“环球早报”。不要把密钥放入Variables。
5. Actions → Verify mail sender (no delivery)：输入预期邮箱，只核对SMTP登录，不发送邮件。
6. Actions → Morning Brief → Run workflow：默认保留`dry_run`勾选，只生成试刊；取消才发送。查看产物后再启用定时。
7. Variables中新建`ENABLE_SCHEDULE=true`才启用每日发送。公开展示仓库及新复制仓库默认不运行定时发送。自动CI只跑离线测试，不读取Secrets或调用模型。

配置字段对照见[.env.example](.env.example)；程序不自动读取该文件。GitHub运行自动提供`GITHUB_TOKEN`与`GITHUB_REPOSITORY`，用于默认分支的`.delivery/YYYY-MM-DD.json`持久记录；工作流因此需要contents写权限。不要删除已有sent/sending/uncertain记录来强行重发。

## 时间与费用

目标北京时间07:30。主要准备时刻07:17，另设轻量唤醒用于补偿调度延迟；实际运行只有在07:10–12:00前才生成正文。07:30前准备完只等待至目标时间，最长等待20分钟；跨日期拒发。GitHub调度可能延迟或丢失触发，补偿不是准点保证。

不需要单独租服务器，GitHub额度与计费取决于你的账号/仓库设置；免费不等于无限额度，可在账号预算设置中限制Actions付费用量。DeepSeek API按个人账户实际用量计费。当前只记录请求数，还没有精确token用量与金额统计。同一正文给多个收件人不会按人数重复调用模型，个性化生成则会增加用量。

`MAIL_TO`支持英文逗号分隔多个邮箱，当前To地址对收件人相互可见。它适合个人/小范围使用；面向订阅者的隐藏地址投递、同意与退订尚未实现。

## 来源与边界

20个配置入口包括政府网站、新华社、人民日报、联合国、NASA、美联储、BBC、DW、RFI、France 24和CNA。路透社/AFP/AP仅在公开刊载平台存在明确署名时标注，未购买或冒充其商业订阅。来源可用性会变化，实际成功情况以每期报告为准。

只用原文材料，不补造新闻要素。对方声明保留归属，解读独立标为推断。校验检查语言、材料编号、数字和结构，不能证明所有概括都正确；没有全文时不宣称阅读全文。详细规则见[SOURCE_POLICY.md](SOURCE_POLICY.md)。

## 项目结构

```text
briefing.py                采集编排、过滤、SMTP及持久发送记录
international_sources.py   国际媒体/机构来源解析
global_evidence.py         事件要素与地区识别
newsroom.py                主题、配额与报道附录
chinese_digest.py          简体中文整理及输出校验
email_layout.py            手机邮件排版
schedule_guard.py          实际时刻和当日发送记录入口检查
config.json                来源、主题及整理预算配置
tests/                     132项离线回归测试
.github/workflows/         只读CI、可选定时及SMTP认证检查
samples/                   明确标注虚构的展示样张
```

后续方向见[ROADMAP.md](ROADMAP.md)，当前验收边界见[docs/validation.md](docs/validation.md)。公开资料不包含邮箱配置、个人履历、内部MEMORY、发送记录或历史Actions日志。

## 贡献与许可

欢迎按[CONTRIBUTING.md](CONTRIBUTING.md)反馈问题。此候选展示版暂未添加LICENSE，许可证由维护者确定后补入；公开可查看不等于已授予任意商业复用许可。代码许可不替代新闻内容发布方的使用条款，本文不宣称自动取得商业新闻再分发权。
