# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 渲染层（S2）

全部文案都做成模板，可在 AstrBot 插件配置里直接改，不需要动代码。
模板里用 {占位符}，渲染时替换；模板被改坏会自动回退，不会导致消息发不出去。

可用占位符
  赛前提醒 remind_template
    {platform}      平台显示名（如 Codeforces）
    {title}         比赛名称
    {datetime}      完整时间 2026-10-14 22:35
    {date}          仅日期   2026-10-14
    {time}          仅时间   22:35
    {minutes}       时长，单位【分钟】（如 120）
    {duration}      时长，人类可读（如 2h15m）
    {url}           比赛链接
    {minutes_ahead} 提前量（如 15）

  每日日报 daily_title / daily_section / daily_item / daily_empty
    {platform}      平台显示名        {items}  该平台下的条目（已按行拼好）
    {count}         该平台的比赛数量   {total}  今日比赛总场次
    {time}          比赛时间 22:35    {title}  比赛名称
    {url}           比赛链接          {date}   日报日期
    （daily_item 默认在比赛下一行附 `🔗{url}`；链接为空时该行自动消失）
    {quote}         今天的名言（已拼成一行，含出处，如「AC 一时爽 —— 算法竞赛圈」）
    {quote_text}    名言正文（不含出处）  {quote_by}  出处
    {quote_cat}     分类中文名（名家名言 / 圈内梗 / 竞赛鸡汤 / 自定义）

  名言彩蛋 daily_quote（1.4.0，默认 `〖{quote}〗`，追加在日报最末尾）
    * 内容来自 core/quotes.py 的内置库，按**北京时间当天**抽 —— 同一天刷几次都一样，
      换一天自动换一句（`quote_fixed_daily=False` 时改成每条都重抽）
    * `quote_blocked`（1.6.0）是在面板「名言库」里被屏蔽的 id，命中的句子不会出现
    * 留空 = 整块不显示；也可以把 {quote} 写进 daily_title / daily_empty 换个位置
    * daily_title / daily_section / daily_empty 里都能用上面那四个占位符
      （daily_item 不带 —— 每条比赛后面重复同一句名言没有意义）

  用户战绩 profile_template / profile_week_section / profile_week_item
    {handle}        昵称 / handle      {platform} 平台显示名
    {rating}        当前 Rating        {max_rating} 历史最高 Rating
    {solved}        总过题数           {rank_title} 段位称号（CF 有，牛客为空）
    {url}           个人主页链接       {week}    本周战绩区块（已拼好）
    {days}          统计天数           {week_label} 「本周」等标签

  战绩里的单场比赛 profile_week_item
    {name}          比赛名称           {solved}  过题数
    {total}         总题数             {rank}    排名
    {date} {time}   比赛日期 / 时间     {url}     比赛链接
    {rating_change} Rating 变化（带正负号）

  日报「未来 N 天」里的单场比赛（daily_upcoming_item 与 daily_item 同一套占位符）
    {date} {time}   日期 / 时间         {when}    今日/明日/后天/周六
    {platform}      平台名             {duration} 时长
    {title}         比赛名称           {url}     比赛链接
    {short_date}    10-17（月-日，赛程列表默认用它）

  /赛程 列表 upcoming_title / upcoming_item / upcoming_empty
    {days}          查了几天            {count}  实际列出几场
    {limit}         一次最多列几场       {index}  条目序号（从 1 开始）
    {emoji}         平台小图标          {max_days} 可查天数上限（夹紧提示用）
    其余占位符与 daily_item 完全相同（{title} {time} {date} {weekday} {duration} {url} …）

⚠️「未来 N 天」在**两个地方故意不一样**（用户 2026-10-16 明确要求「区分开」）：

  | 地方 | 起算日 | 天数配置 | 理由 |
  |---|---|---|---|
  | 日报末尾的「未来 N 天」区块 | **不含今天**，明天算第 1 天 | `lookahead_days` | 今天的比赛正文里已按平台列过，重复没意义 |
  | `/赛程 N` 指令 | **含今天剩余**，今天算第 1 天 | `upcoming_days` | 手动查赛程时最关心「今晚还有没有比赛」 |

  两者都是**自然日、左闭右开**窗口，实现在 `_daily_future_window()` /
  `_query_future_window()` 里；不要合并成一个函数，那正是被分开的原因。

提示：模板里的占位符同时支持**全角大括号**（｛rating｝）和**中文名**（｛最高Rating｝），
      直接照着手机上看到的格式抄进配置即可，不需要记英文名。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

try:  # 插件包内导入
    from . import quotes as _quotes
except ImportError:  # 单独 import 这个文件（无包上下文）时的兜底
    import quotes as _quotes  # type: ignore[no-redef]

TZ_CN = timezone(timedelta(hours=8))

PLATFORM_EMOJI = {"cf": "🔵", "nowcoder": "🟠", "atcoder": "🟣", "luogu": "🟢"}
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 全部默认文案。AstrBot 插件配置里的同名项会覆盖这里的值。
DEFAULT_TEMPLATES = {
    "platform_emoji": dict(PLATFORM_EMOJI),
    "platform_names": {
        "cf": "Codeforces",
        "nowcoder": "牛客竞赛",
        "atcoder": "AtCoder",
        "luogu": "洛谷",
    },
    # 赛前提醒：整条消息的模板
    "remind_template": (
        "⏰ {platform} 比赛即将开始\n"
        "🏷 {title}\n"
        "🕐 {datetime}（北京时间）\n"
        "⌛️时长{minutes}分钟\n"
        "🔗 {url}\n"
        "🚩大家加油哦"
    ),
    # 每日日报（每条比赛后面换行附上链接，和赛前提醒保持一致的观感）
    "daily_title": "🌅 今日比赛日报",
    "daily_section": "— {platform} —\n{items}",
    "daily_item": "⏰{time} {title}\n🔗{url}",
    "daily_empty": "今日没有Codeforces与牛客的竞赛，但依旧要坚持学习哦(*^-^)ﾉ🎉",
    # 可选：要不要在日报末尾追加「未来 N 天」。留空 = 不显示（默认）
    "daily_upcoming_section": "",
    "daily_upcoming_item": "{date} {time} {platform} {title}\n🔗{url}",
    # 日报名言彩蛋（1.4.0）：默认追加在日报最末尾，形如 〖AC 一时爽，一直 AC 一直爽〗
    # 留空 = 整块不显示；想换位置就把 {quote} 直接写进 daily_title / daily_empty / daily_section
    "daily_quote": "〖{quote}〗",
    # ---- /赛程 指令（未来 N 天列表）----
    "upcoming_title": "📅 未来 {days} 天 · 共 {count} 场",
    "upcoming_item": "{short_date} {time} {weekday}  {emoji} {title}  {duration}\n🔗{url}",
    "upcoming_empty": "📅 未来 {days} 天没有找到比赛",
    "upcoming_nodata": "📭 还没有赛程数据，正在抓取，请稍后再试",
    "upcoming_capped_note": "\n\nℹ️ 最多只能查看 {max_days} 天内的赛程，已按 {days} 天显示",
    # ---- 用户战绩查询（/cf、/nk）----
    "profile_template": (
        "💡{handle}\n"
        "🎈Rating：{rating}\n"
        "☁️最高Rating：{max_rating}\n"
        "🔺总过题：{solved}\n"
        "{week}"
    ),
    "profile_week_section": "— 本周比赛战绩 —\n{items}",
    "profile_week_item": "🔸{name}\n✅{solved}/{total}🚩排名：{rank}",
    # 留空 = 本周没比赛时不显示整块（默认）
    "profile_week_empty": "",
    # 可选：底部小字（如数据来源、更新时间）。留空 = 不显示（默认）
    "profile_note": "",
}

# 留空「有意义」的模板项 —— 合并配置时不回退成默认值
EMPTY_MEANINGFUL = {
    "daily_upcoming_section",
    "daily_quote",
    "profile_week_empty",
    "profile_note",
    "upcoming_capped_note",
}

# 全部标准占位符名。模板里写这些名字（任意大小写）时原样保留，不做别名转换。
STANDARD_KEYS = {
    "handle", "uid", "platform", "source", "rating", "max_rating", "solved",
    "rank_title", "url", "days", "week_label", "week", "items", "count", "date",
    "title", "time", "datetime", "minutes", "duration", "minutes_ahead", "total",
    "when", "weekday", "short_date", "emoji", "index", "max_days", "limit",
    "name", "rank", "rating_change",
    # 日报名言彩蛋（daily_quote / daily_title 等）
    "quote", "quote_text", "quote_by", "quote_cat",
    # 权限拒绝文案（deny_template）用
    "cmd", "user", "user_id", "level", "need",
}

# 占位符别名：**只收中文名和明确变体**，标准英文名一律不列（避免 {time} 被误映射）。
# 这样用户可以直接把手机里看到的 ｛最高Rating｝ 原样抄进配置。
PLACEHOLDER_ALIASES = {
    "handle": ("昵称", "用户名", "姓名", "用户", "选手"),
    "rating": ("分数", "当前分数", "当前rating", "rating值"),
    "max_rating": ("maxrating", "最高rating", "最高分", "历史最高"),
    "solved": ("problems", "过题数", "总过题", "通过数", "已通过", "ac数", "解题数"),
    "total": ("总题数", "题目总数", "problemcount", "题目数"),
    "rank": ("排名", "名次", "ranking", "rating排名"),
    "name": ("比赛名称", "赛事名称", "比赛名", "contest", "contestname", "比赛"),
    "date": ("日期", "day"),
    "platform": ("平台", "站点"),
    "rank_title": ("段位", "称号", "头衔"),
    "url": ("链接", "主页", "地址"),
    "week": ("战绩", "比赛战绩", "本周战绩"),
    "week_label": ("标签",),
    "days": ("天数",),
    "items": ("条目", "列表"),
    "count": ("数量", "场次"),
    "rating_change": ("分数变化", "rating变化", "变化"),
    "duration": ("时长",),
    "minutes": ("分钟数",),
    "datetime": ("完整时间",),
    "when": ("是哪天", "相对日期"),
    "weekday": ("星期", "周几"),
    "minutes_ahead": ("提前量",),
    # 日报名言彩蛋
    "quote": ("名言", "金句", "句子"),
    "quote_text": ("名言正文", "金句正文", "名言内容"),
    "quote_by": ("名言出处", "出处", "作者"),
    "quote_cat": ("名言分类",),
    # 权限拒绝文案（deny_template）
    "cmd": ("指令", "命令", "指令名"),
    "user_id": ("用户id", "qq号", "id"),
    "user": ("用户名",),
    "level": ("权限等级",),
    "need": ("所需权限", "权限要求"),
}

_ALIAS_TO_STD: dict = {}
for _std, _names in PLACEHOLDER_ALIASES.items():
    for _n in _names:
        _ALIAS_TO_STD[_n.lower()] = _std

_FULLWIDTH_BRACES = str.maketrans({"｛": "{", "｝": "}"})
_RE_ANY_PLACEHOLDER = re.compile(r"\{([^{}]{1,24})\}")

# 字面量转义序列 -> 真字符。只处理这三个，绝不动其他反斜杠
# （用户文案里可能有正常反斜杠，比如路径或颜文字）。
_UNESCAPES = {"\\n": "\n", "\\r": "\r", "\\t": "\t"}
# 双层转义优先匹配（WebUI 会把用户写的 \n 再转义一层变成 \\n），
# 交替分支保证 \\n 一次换干净，不会残留孤立的反斜杠
_RE_ESCAPE = re.compile(r"\\\\[nrt]|\\[nrt]")
_ESCAPE_MAX_PASSES = 3  # 少数 WebUI 会把反斜杠再转义一层（\\n），多跑两遍兜住


def _unescape_newlines(text: str) -> str:
    r"""把配置里写死的**字面量** `\n` 转成真换行。

    为什么需要：AstrBot 的配置框存的是原样字符串 —— 用户在里面打 `\n` 得到的是
    两个字符（反斜杠 + n），不是换行符。直接渲染就会在群里外显成一行里的 `\n`
    （实测已出现：`— 本周比赛战绩 —\n这周还没参加比赛哦`）。

    处理范围很窄，只认 \n \r \t（含双层转义的 \\n）；其他反斜杠一律原样保留。
    """
    if not isinstance(text, str) or "\\" not in text:
        return text
    for _ in range(_ESCAPE_MAX_PASSES):
        new = _RE_ESCAPE.sub(lambda m: _UNESCAPES["\\" + m.group(0)[-1]], text)
        if new == text:
            break
        text = new
    return text


def _normalize_tpl(tpl: str) -> str:
    """把模板归一化（用户怎么写都能正确渲染）：

    - 字面量 `\n` / `\r` / `\t` -> 真换行/制表符（配置框里打 `\n` 不会外显）
    - 全角括号 ｛｝ -> 半角 {}（用户从手机格式里直接抄过来也能用）
    - 中文名 / 变体 -> 标准占位符名（｛最高Rating｝ -> {max_rating}、{problems} -> {solved}）
    - 标准名本身优先，原样保留（保证 {time} 不会被当成「日期」）
    - 认不出的占位符原样保留，交给 _safe_format 的降级分支处理
    """
    if not isinstance(tpl, str) or not tpl:
        return tpl
    normalized = _unescape_newlines(tpl).translate(_FULLWIDTH_BRACES)

    def _sub(m: re.Match) -> str:
        raw = m.group(1).strip()
        if not raw:
            return m.group(0)
        low = raw.lower()
        if raw in STANDARD_KEYS:
            return "{" + raw + "}"
        if low in STANDARD_KEYS:
            return "{" + low + "}"
        std = _ALIAS_TO_STD.get(low)
        if std is None:
            return "{" + raw + "}"
        return "{" + std + "}"

    return _RE_ANY_PLACEHOLDER.sub(_sub, normalized)


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ_CN)


def _fmt_duration(minutes) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h{m:02d}m"
    if h:
        return f"{h}h"
    return f"{m}m"


_RE_PLACEHOLDER = _RE_ANY_PLACEHOLDER  # 兼容中文占位符名

_RE_MULTI_NL = re.compile(r"\n{3,}")


def _fmt_num(v, dash: str = "—") -> str:
    """数字显示：None 显示占位符，浮点整数去掉 .0（牛客 rating 是 4017.0）。"""
    if v is None or v == "":
        return dash
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:g}"
    return str(v)


def _tidy(text: str) -> str:
    """可选区块（如「本周战绩」）关闭时，模板里会残留空行，这里压掉。"""
    return _RE_MULTI_NL.sub("\n\n", (text or "").replace("\r\n", "\n")).strip()


def _safe_format(tpl: str, **kw) -> str:
    """安全格式化：模板被改坏（多写了 `{` 之类）也不抛异常。

    先用 str.format；失败则退化为正则替换，缺失的占位符替换成空串。
    """
    if not isinstance(tpl, str):
        return ""
    try:
        return tpl.format(**kw)
    except Exception:  # noqa: BLE001 模板写坏属于用户输入问题，不能让它炸掉消息
        return _RE_PLACEHOLDER.sub(lambda m: str(kw.get(m.group(1).strip(), "")), tpl)


def merge_templates(user_tpl: dict | None) -> dict:
    """合并用户配置与默认模板，并把占位符归一化。

    空串 / 空 dict 视为「没填」，回退默认值。
    例外见 EMPTY_MEANINGFUL —— 那些项留空是**有意义**的（表示关闭该区块）。
    """
    t = dict(DEFAULT_TEMPLATES)
    if user_tpl:
        for k, v in user_tpl.items():
            if v is None:
                continue
            if k in EMPTY_MEANINGFUL:
                t[k] = v if isinstance(v, str) else ""
                continue
            if isinstance(v, str) and not v.strip():
                continue
            if isinstance(v, dict) and not v:
                continue
            t[k] = v
    for k, v in list(t.items()):
        if isinstance(v, str) and v:
            t[k] = _normalize_tpl(v)
    return t


def render_text(tpl: str, **kw) -> str:
    """渲染一条**自定义文案**（非赛程/战绩对象），供权限拒绝提示这类模板使用。

    与消息模板同样宽容：字面量 `\\n` 会变成真换行、全角括号 ｛｝ 可用、
    中文占位符（｛指令｝｛用户ID｝）会自动映射到标准名；模板写坏也不抛异常。
    """
    if not isinstance(tpl, str) or not tpl:
        return ""
    return _safe_format(_normalize_tpl(tpl) or "", **kw)


def render_config_text(tpl: dict | None, key: str, **kw) -> str:
    """按配置项名渲染一条独立文案（如 /赛程 的「夹紧提示」）。

    为什么要单独一个入口：main.py 拿到的 `tpl` 是**未合并**的原始配置
    （没填的项是 None，合并发生在各 render_* 内部）。直接 `tpl.get(key)` 会拿到
    None，配置里没填的文案就永远不显示 —— 这里统一先与默认值合并再渲染。
    留空（EMPTY_MEANINGFUL 里的项）依然表示「不显示」。
    """
    t = merge_templates(tpl)
    return render_text(t.get(key) or "", **kw)


def _platform_name(t: dict, source: str) -> str:
    names = t.get("platform_names") or {}
    try:
        return names.get(source) or source
    except AttributeError:
        return source


# 「区块模板里必须有 {items}」的兜底：认英文名与常见中文别名
_RE_ITEMS_KEY = re.compile(r"\{\s*(items|entry|列表|条目|条目列表|赛程|比赛列表|内容)\s*\}")


def _with_items(tpl: str) -> str:
    """区块模板漏写 {items} 时自动补上，避免「只有标题、一条内容都没有」。

    实测踩坑（2026-10-16 用户反馈「设置了明日赛事但回答里没有」）：
    `daily_upcoming_section` 填成 `— 明日赛事 Tomorrow —`，漏了 {items}，
    渲染出来就只有一个标题，比赛一条都不显示 —— 数据其实是好的，找半天找不到原因。
    这类「看着像坏了、其实少个占位符」的坑不值得让用户自己排查，直接兜住：
    模板非空但没有 {items}（或中文别名）时，在末尾补一行 {items}。
    """
    tpl = tpl or ""
    if not tpl.strip() or _RE_ITEMS_KEY.search(tpl):
        return tpl
    return tpl.rstrip() + "\n{items}"


# 「只有标签没有值」的残行：链接为空时 `🔗{url}` 会剩下一行光秃秃的 🔗。
# 这类行直接删掉，比让用户去写条件模板省事（默认日报模板末尾就是 🔗{url}）。
_RE_LINK_ONLY_LINE = re.compile(r"^[ \t]*(?:🔗|🔗️|🖇|链接|link)[:：]?[ \t]*$", re.IGNORECASE)

# 「条目模板里有没有链接占位符」的判定（中文别名在归一化阶段已转成 url）
_RE_URL_KEY = re.compile(r"\{\s*url\s*\}")


def _with_url(tpl: str, attach: bool = True) -> str:
    """条目模板漏写 `{url}` 时自动补一行 `🔗{url}`。

    为什么要兜：老版本安装的配置里 `daily_item` 存着旧默认值 `⏰{time} {title}`，
    升级后不会自动变成新的默认值，用户会觉得「说好的链接呢」——和当初
    `daily_upcoming_section` 漏写 {items} 是同一类坑，直接兜住最省事。

    `daily_item_attach_url` 设为 false 时完全不插手，模板怎么写就怎么输出。
    """
    tpl = tpl or ""
    if not attach or not tpl.strip() or _RE_URL_KEY.search(tpl):
        return tpl
    return tpl.rstrip() + "\n🔗{url}"


def _clean_item(text: str) -> str:
    """清掉比赛条目里的空链接残行，压掉首尾空行。"""
    if not text:
        return ""
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if not _RE_LINK_ONLY_LINE.match(ln)]
    return "\n".join(lines).strip()


def _render_item(t: dict, key: str, c: dict, now_ts: int | None = None) -> str:
    """渲染一条比赛条目：套模板 + 自动补链接 + 清掉空链接残行。"""
    tpl = _with_url(t.get(key) or "", bool(t.get("item_attach_url", True)))
    return _clean_item(_safe_format(tpl, **_item_kwargs(c, t, now_ts)))


def _item_kwargs(c: dict, t: dict, now_ts: int | None = None) -> dict:
    """渲染单条比赛时可用的占位符。now_ts 作为 {when} 的相对日期基准。"""
    dt = _dt(c["startAt"]) if c.get("startAt") else None
    minutes = c.get("durationMinutes")
    return {
        "title": c.get("title", ""),
        "time": dt.strftime("%H:%M") if dt else "--:--",
        "date": dt.strftime("%Y-%m-%d") if dt else "--",
        "short_date": dt.strftime("%m-%d") if dt else "--",
        "datetime": dt.strftime("%Y-%m-%d %H:%M") if dt else "--",
        "weekday": WEEKDAY_CN[dt.weekday()] if dt else "--",
        "when": _when_label(dt, now_ts) if dt else "--",
        "minutes": minutes if minutes else "未知",
        "duration": _fmt_duration(minutes),
        "url": c.get("url", ""),
        "platform": _platform_name(t, c.get("source", "")),
        "source": c.get("source", ""),
    }


def _when_label(dt, now_ts: int | None = None) -> str:
    """相对日期标签：今日 / 明日 / 后天 / 周六（更远的就报星期）。

    「未来 N 天」区块里每条都带一下日期归属，用户一眼知道是哪天，
    不用去看 {date} 的具体数字。

    基准**必须**用渲染时传进来的 now_ts，不能用真实时钟：日报是按某个时间点
    渲染的，两者在跨天边界会不一致（实测：15:03 的条目被判成「明日」）。
    """
    if dt is None:
        return "--"
    base = _dt(now_ts) if now_ts else datetime.now(TZ_CN)
    delta = (dt.date() - base.date()).days
    if delta == 0:
        return "今日"
    if delta == 1:
        return "明日"
    if delta == 2:
        return "后天"
    return WEEKDAY_CN[dt.weekday()]


def _midnight(ts: int, plus_days: int = 0) -> int:
    """ts 所在自然日的零点（北京时区），再往后推 plus_days 天。"""
    d = _dt(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((d + timedelta(days=plus_days)).timestamp())


def _daily_future_window(now_ts: int, days: int) -> tuple[int, int]:
    """**日报「未来 N 天」区块**的窗口：`[明天 00:00, 明天 00:00 + N 天)`。

    不含今天，明天算第 1 天（用户口径）。今天的比赛日报正文里已经按平台列过了，
    这里再出现一次就是重复，所以从明天零点起算。

    窗口按自然日切、左闭右开；不能从当前时刻往后推 N×24 小时——那样最后一天
    会被截断（23:00 查「未来 1 天」，明天 23:59 的比赛反而看不到）。
    """
    lo = _midnight(now_ts, 1)
    return lo, lo + max(0, int(days)) * 86400


def _query_future_window(now_ts: int, days: int) -> tuple[int, int]:
    """**`/赛程 N` 指令**的窗口：`[现在, 明天 00:00 + (N-1) 天)`。

    与日报区块**故意区别对待**（用户 2026-10-16 要求「区分开」）：
    `/赛程` 是用户当场手动查的，最关心的就是**今晚**那场，所以含今天剩余场次。
    计数方式 = 从今天起共 N 个自然日，今天只算还没开赛的
      * N=1 -> 今天剩下的（到今晚 24:00 为止）
      * N=2 -> 今天剩下的 + 明天一整天
      * N=7 -> 今天剩下的 + 明天起 6 天
    已开赛/已结束的自然被 `now_ts` 挡在外面。
    """
    hi = _midnight(now_ts, 1) + max(0, int(days) - 1) * 86400
    return now_ts, hi


# ======================= 赛前提醒 =======================

def render_reminder(c: dict, now_ts: int, lead_minutes: int = 15, tpl: dict | None = None) -> str:
    """赛前提醒。默认格式：

        ⏰ Codeforces 比赛即将开始
        🏷 Educational Codeforces Round 195 (Rated for Div. 2)
        🕐 2026-10-14 22:35（北京时间）
        ⌛️时长120分钟
        🔗 https://codeforces.com/contest/2276
        🚩大家加油哦
    """
    t = merge_templates(tpl)
    kw = _item_kwargs(c, t, now_ts)
    kw["minutes_ahead"] = lead_minutes
    return _safe_format(t["remind_template"], **kw)


# ======================= 每日日报 =======================


def _quote_kwargs(
    now_ts: int,
    *,
    quote=None,
    pool=None,
    cats=None,
    blocked=None,
    with_source: bool = True,
    fixed_daily: bool = True,
) -> dict:
    """日报名言彩蛋的四个占位符。

    * `quote` 显式给了字符串就直接用它（用户指定 / 测试用固定值）。
    * 否则按**北京时间自然日**抽一条 —— 同一天稳定：定时推送、`/日报`、
      `/推送测试` 连点、多个群分别渲染，看到的都是同一句；换一天自动换。
      `fixed_daily=False` 时改成真正的「每次渲染都重抽」（用户要求「每条都不同」时用）。
    * `blocked` 是用户在面板里屏蔽掉的名言 id（1.6.0）—— 被屏蔽的句子不参与抽取。
    * 抽不到时四个键都给空串（不是不给键）—— 模板里写了 `{quote}` 却拿不到键，
      `str.format` 会抛 KeyError，虽然 `_safe_format` 能兜住，但那条路会顺手把
      其它缺失占位符也吞成空串，代价太高，不如这里保证键始终存在。
    """
    if isinstance(quote, str) and quote.strip():
        q = {"text": quote.strip(), "by": "", "cat": "custom"}
    else:
        seed = _quotes.date_seed(now_ts) if fixed_daily else None
        q = _quotes.pick_quote(seed, cats=cats, extra=pool, blocked=blocked)
    if not q:
        return {"quote": "", "quote_text": "", "quote_by": "", "quote_cat": ""}
    return {
        "quote": _quotes.format_quote(q, with_source),
        "quote_text": q.get("text") or "",
        "quote_by": q.get("by") or "",
        "quote_cat": _quotes.cat_label(q.get("cat")),
    }


def render_daily(
    contests: list[dict],
    now_ts: int,
    generated_at: str = "",
    tpl: dict | None = None,
    platform_order: list | None = None,
    lookahead_days: int = 7,
    quote: str | None = None,
    quote_pool: list | None = None,
    quote_cats: list | None = None,
    quote_blocked: list | None = None,
    quote_source: bool = True,
    quote_fixed_daily: bool = True,
) -> str:
    """每日日报。按平台分组，**没有比赛的平台不显示**。

        🌅 今日比赛日报
        — Codeforces —
        ⏰22:35 Educational Codeforces Round 195 (Rated for Div. 2)
        🔗https://codeforces.com/contest/2276

        — 牛客竞赛 —
        ⏰19:00 牛客周赛165
        🔗https://ac.nowcoder.com/acm/contest/90001

        〖你的 rating 会掉，你会的东西不会。〗

    今天完全没有比赛时，只输出 daily_empty 那一段（名言照常追加）。
    平台顺序遵循配置的 sources 顺序（默认 cf -> nowcoder）。
    """
    t = merge_templates(tpl)
    order = list(platform_order or ["cf", "nowcoder"])
    today = _dt(now_ts).date()
    date_cn = f"{today.month}月{today.day}日"
    qk = _quote_kwargs(
        now_ts,
        quote=quote,
        pool=quote_pool,
        cats=quote_cats,
        blocked=quote_blocked,
        with_source=quote_source,
        fixed_daily=quote_fixed_daily,
    )

    # 今日的比赛（含正在进行中的）
    todays = [c for c in contests if c.get("startAt") and _dt(c["startAt"]).date() == today]
    todays.sort(key=lambda x: x["startAt"])

    head = _safe_format(t["daily_title"], date=date_cn, total=len(todays), **qk)

    if not todays:
        out = head + "\n" + _safe_format(t["daily_empty"], date=date_cn, total=0, **qk)
        return _append_quote(out, t, qk, now_ts)

    # 按平台分组；平台顺序遵循配置，未登记的源追加在后面
    groups: dict = {}
    for c in todays:
        groups.setdefault(c.get("source", ""), []).append(c)
    ordered = [s for s in order if s in groups] + [s for s in groups if s not in order]

    sections = []
    for src in ordered:
        items = groups[src]
        item_text = "\n".join(_render_item(t, "daily_item", c, now_ts) for c in items)
        sections.append(
            _safe_format(
                _with_items(t["daily_section"]),
                platform=_platform_name(t, src),
                source=src,
                count=len(items),
                items=item_text,
                **qk,
            )
        )

    out = head + "\n" + "\n\n".join(sections)

    # 可选：追加「未来 N 天」（默认模板留空 -> 不渲染）
    # 口径：不含今天，明天算第 1 天；今天的比赛上面已经列过了，不重复
    up_tpl = t.get("daily_upcoming_section") or ""
    if up_tpl.strip() and lookahead_days > 0:
        lo, hi = _daily_future_window(now_ts, lookahead_days)
        futures = [
            c for c in contests if c.get("startAt") and lo <= c["startAt"] < hi
        ]
        futures.sort(key=lambda x: x["startAt"])
        if futures:
            rows = [
                _render_item(t, "daily_upcoming_item", c, now_ts)
                for c in futures
            ]
            out += "\n\n" + _safe_format(
                _with_items(up_tpl),
                platform="",
                count=len(futures),
                items="\n".join(rows),
                days=lookahead_days,
                **qk,
            )

    return _append_quote(out, t, qk, now_ts)


def _append_quote(out: str, t: dict, qk: dict, now_ts: int) -> str:
    """把 daily_quote 那一段追加到日报末尾。

    两个「不显示」的判据：模板留空（用户主动关闭），或者名言本身为空
    （比如分类被筛空了）。后者尤其重要 —— 否则会留下一个空的 `〖〗`，
    看着像插件坏了。
    """
    tpl = (t.get("daily_quote") or "").strip()
    if not tpl or not qk.get("quote"):
        return out
    block = _safe_format(tpl, date=_dt(now_ts).strftime("%Y-%m-%d"), **qk)
    return out + "\n\n" + block



# ======================= 手动查询（/赛程） =======================

def render_upcoming(
    contests: list[dict],
    now_ts: int,
    days: int = 7,
    limit: int = 15,
    tpl: dict | None = None,
) -> str:
    """ /赛程 N：未来 N 天全部未开赛比赛（**含今天剩余场次**，今天算第 1 天）。

    与日报的「未来 N 天」区块**故意不同**（用户 2026-10-16 要求区分开）：
    手动查询时最有用的是「今晚还有没有比赛」，所以不从明天起算。
    详见 `_query_future_window()`。

    全部文案可在配置里改（upcoming_title / upcoming_item / upcoming_empty）。
    条目之间自动空一行；每条比赛默认在下一行附 🔗链接（与日报一致的兜底规则）。
    """
    t = merge_templates(tpl)
    lo, hi = _query_future_window(now_ts, days)
    items = [c for c in contests if c.get("startAt") and lo <= c["startAt"] < hi]
    items.sort(key=lambda x: x["startAt"])
    items = items[:limit]
    if not items:
        return _safe_format(t["upcoming_empty"], days=days, count=0, limit=limit)

    emoji_map = t.get("platform_emoji") or PLATFORM_EMOJI
    attach = bool(t.get("item_attach_url", True))
    item_tpl = _with_url(t.get("upcoming_item") or "", attach)
    rows = []
    for i, c in enumerate(items, 1):
        kw = _item_kwargs(c, t, now_ts)
        kw["emoji"] = emoji_map.get(c.get("source", ""), "⚪")
        kw["index"] = i
        rows.append(_clean_item(_safe_format(item_tpl, **kw)))
    head = _safe_format(
        t["upcoming_title"], days=days, count=len(items), total=len(items), limit=limit
    )
    return _tidy(head + "\n\n" + "\n\n".join(rows))


# ======================= 用户战绩（/cf、/nk）=======================

def _profile_kwargs(p: dict, t: dict) -> dict:
    """渲染用户档案时可用的占位符。"""
    now = datetime.now(TZ_CN)
    return {
        "handle": p.get("handle") or p.get("uid") or "",
        "uid": p.get("uid") or "",
        "platform": _platform_name(t, p.get("platform", "")),
        "source": p.get("platform", ""),
        "rating": _fmt_num(p.get("rating"), "未定级"),
        "max_rating": _fmt_num(p.get("max_rating")),
        "solved": _fmt_num(p.get("solved")),
        "rank_title": p.get("rank_title") or "",
        "url": p.get("url") or "",
        "days": p.get("week_days", 7),
        "week_label": "本周",
        "date": now.strftime("%Y-%m-%d"),
        "week": "",
        "items": "",
        "count": len(p.get("week") or []),
        "total": len(p.get("week") or []),
    }


def _week_item_kwargs(c: dict, platform: str, t: dict) -> dict:
    """渲染战绩里单场比赛时可用的占位符。"""
    start = c.get("start_at")
    dt = _dt(start) if start else None
    cid = c.get("contest_id") or ""
    if platform == "cf":
        url = f"https://codeforces.com/contest/{cid}" if cid else ""
    else:
        url = f"https://ac.nowcoder.com/acm/contest/{cid}" if cid else ""
    chg = c.get("rating_change")
    if chg is None:
        chg_str = ""
    else:
        chg_str = f"{int(chg):+d}" if float(chg).is_integer() else f"{chg:+g}"
    return {
        "name": c.get("name", ""),
        "solved": _fmt_num(c.get("solved")),
        "total": _fmt_num(c.get("total")),
        "rank": _fmt_num(c.get("rank")),
        "date": dt.strftime("%m-%d") if dt else "--",
        "time": dt.strftime("%H:%M") if dt else "--",
        "datetime": dt.strftime("%Y-%m-%d %H:%M") if dt else "--",
        "url": url,
        "platform": _platform_name(t, platform),
        "rating_change": chg_str,
    }


def render_profile(profile: dict, tpl: dict | None = None) -> str:
    """用户战绩卡片。默认格式：

        💡tourist
        🎈Rating：3343
        ☁️最高Rating：4009
        🔺总过题：1630
        — 本周比赛战绩 —
        🔸DeepInfra Round 1 (Codeforces Round 1126, Div. 1 + Div. 2)
        ✅5/9🚩排名：12

    本周没有比赛时，`profile_week_empty` 默认留空 -> 整块不显示，
    只留上面四行（尾部空行会被压掉）。
    """
    t = merge_templates(tpl)
    kw = _profile_kwargs(profile, t)
    platform = profile.get("platform", "")
    week = profile.get("week") or []

    if week:
        rows = [
            _safe_format(t["profile_week_item"], **_week_item_kwargs(c, platform, t))
            for c in week
        ]
        item_text = "\n".join(rows)
        section = t.get("profile_week_section") or ""
        if section.strip():
            week_text = _safe_format(
                _with_items(section),
                items=item_text,
                count=len(week),
                week_label=kw["week_label"],
                days=kw["days"],
            )
        else:
            # 区块模板被清空，但用户仍想看到战绩 -> 直接输出条目
            week_text = item_text
    else:
        week_text = _safe_format(t.get("profile_week_empty") or "", **kw)

    kw["week"] = week_text
    kw["items"] = week_text
    out = _safe_format(t["profile_template"], **kw)

    note = t.get("profile_note") or ""
    if note.strip():
        out += "\n" + _safe_format(note, **kw)
    return _tidy(out)
