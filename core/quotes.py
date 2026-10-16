# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 名言库（日报彩蛋）

给日报末尾那句「〖…〗」提供内容。三条要求决定了这里的实现方式：

  1. **同一天只出一条** —— 用「北京时间自然日」当随机种子，同一天无论渲染几次
     （定时推送、`/日报`、`/推送测试` 连点 10 次、多个群分别渲染）拿到的是**同一句**。
     否则同一个群里刷出不同名言、或者用户点两次测试看到两句不同的，会显得很随意。
  2. **归用户可管** —— 分类可关、可追加自己的句子（见 `pick_quote(cats=?, extra=?)`），
     内置库只当「默认值」用，不是硬编码的装饰。
  3. **纯文本、零依赖** —— 这里的句子会被塞进用户的日报模板里，所以每条都必须
     不含 `{` `}`（会与模板占位符打架）、不含换行、尽量短。

分类（cat）
  classic  名家名言：Knuth / Dijkstra / Wirth / Hamming / 古诗文 …（带出处）
  meme     圈内梗：顺口溜、自嘲、「被讲烂了但真的好用」的那些（基本不带出处）
  cheer    竞赛鸡汤：给正在掉分、正在补题的人

为什么 meme 里刻意**剔掉**了流传最广的那几首「顺口溜」——《暴力出奇迹》《骗分过样例》
《十年 OI 一场空》之类，只留下了正向的改写法：
日报是推给整个群的（学生、家长、指导老师都可能看到），一条鼓励「骗分」「面向样例编程」
的顺口溜放在标题下面，跟「认真补题」的调性正好相反。要那口原味，用户可以自己在
`daily_quote_extra` 里加 —— 内置库只负责「不出洋相」。

用法
  from . import quotes
  q = quotes.pick_quote(now_ts)            # 同一天稳定；now_ts 为 None 时真随机
  quotes.format_quote(q)                   # "你的 rating 会掉，你会的东西不会。"
  quotes.format_quote(q, with_source=False)
  quotes.search("补题")                     # 关键词搜索（/名言 指令用）
  quotes.counts()                          # {"classic": 20, "meme": 20, "cheer": 20}

**可视化管理（1.6.0）**
  用户要求「名言库支持可视化管理，我要看到全部名言」。内置库在代码里改不动，所以
  管理的方式是「**屏蔽 + 自定义**」两条腿：

    * `items(custom, blocked)`  —— 全库清单（**含**被屏蔽的），每项带 `id` / `source` /
      `blocked`，供面板把 62 条内置 + 用户自建的一条不落地列出来；
    * 屏蔽名单存 `data/quotes.json` 的 `blocked`（值就是 `quote_id(text)`），
      它只在抽取时起作用，**不改动代码里的内置库**，所以升级插件也不会丢；
    * 自定义名言也从同一个文件读，与配置项 `daily_quote_extra` 合成同一个池。

  抽取参数里 `blocked` 一路透传（`pick_quote(..., blocked=...)`），任何被屏蔽的句子
  都不会再出现在日报里；`id` 用正文的 sha1 前 10 位，正文不变则 id 不变 ——
  用下标当 id 的话，将来往库里插一句就会让所有屏蔽记录错位。
"""

from __future__ import annotations

import hashlib
import random

# 分类代号 -> 中文名（面板 / 指令 / 文档都读这里，避免三处各写一份）
CATEGORIES: dict = {
    "classic": "名家名言",
    "meme": "圈内梗",
    "cheer": "竞赛鸡汤",
}

# 默认启用全部分类（顺序即展示顺序）
DEFAULT_CATS: list = ["classic", "meme", "cheer"]

# 京东方自然日的秒偏移：算「今天」的种子用
_TZ_OFFSET = 8 * 3600
# 打散用的大奇数（Knuth 乘法散列常数），避免相邻日期抽到相邻下标
_HASH_MUL = 2654435761
_U32 = 0xFFFFFFFF

# 单条自定义名言的长度上限（跟群备注一个量级，防呆）
MAX_QUOTE_LEN = 60
# 出处（by）的长度上限
MAX_BY_LEN = 30
# 面板一次最多列出多少条（内置 62 + 自建 200 都在这之内，纯属防呆）
MAX_LIST_ITEMS = 400


def _q(text: str, cat: str, by: str = "") -> dict:
    """构造一条名言。text 里绝不能出现 `{` `}`（见模块头注释）。"""
    return {"text": text, "by": by, "cat": cat}


# ============================ 内置库 ============================

QUOTES: list = [
    # ---------------- 名家名言 ----------------
    _q("计算的目的在于洞察，而不在于数字。", "classic", "R. W. Hamming"),
    _q("过早的优化是万恶之源。", "classic", "Donald Knuth"),
    _q("小心上面那段代码里的 bug：我只证明了它是对的，没跑过它。", "classic", "Donald Knuth"),
    _q("简单性是可靠性的前提。", "classic", "Edsger W. Dijkstra"),
    _q("测试能证明 bug 的存在，却永远无法证明 bug 的不存在。", "classic", "Edsger W. Dijkstra"),
    _q("程序 = 数据结构 + 算法。", "classic", "Niklaus Wirth"),
    _q("空谈无益，把代码拿给我看。", "classic", "Linus Torvalds"),
    _q("只要盯代码的眼睛足够多，所有 bug 都是浅的。", "classic", "Linus 定律"),
    _q("先让它跑起来，再让它对，最后让它快。", "classic", "软件工程谚语"),
    _q("如果你没法把它讲清楚，说明你还没真的弄懂它。", "classic", "费曼学习法"),
    _q("纸上得来终觉浅，绝知此事要躬行。", "classic", "陆游《冬夜读书示子聿》"),
    _q("不积跬步，无以至千里。", "classic", "《荀子·劝学》"),
    _q("锲而不舍，金石可镂。", "classic", "《荀子·劝学》"),
    _q("学而不思则罔，思而不学则殆。", "classic", "《论语·为政》"),
    _q("敏而好学，不耻下问。", "classic", "《论语·公冶长》"),
    _q("工欲善其事，必先利其器。", "classic", "《论语·卫灵公》"),
    _q("博学之，审问之，慎思之，明辨之，笃行之。", "classic", "《礼记·中庸》"),
    _q("问渠那得清如许？为有源头活水来。", "classic", "朱熹《观书有感》"),
    _q("长风破浪会有时，直挂云帆济沧海。", "classic", "李白《行路难》"),
    _q("会当凌绝顶，一览众山小。", "classic", "杜甫《望岳》"),
    _q("路漫漫其修远兮，吾将上下而求索。", "classic", "屈原《离骚》"),
    _q("宝剑锋从磨砺出，梅花香自苦寒来。", "classic", "《警世贤文·勤奋篇》"),
    # ---------------- 圈内梗 ----------------
    _q("AC 一时爽，一直 AC 一直爽。", "meme", "算法竞赛圈"),
    _q("WA 是常态，AC 是惊喜，补题才是实力。", "meme", "算法竞赛圈"),
    _q("每一次 TLE 都在提醒你：你离正解只差一个复杂度。", "meme", "算法竞赛圈"),
    _q("样例过了不等于能过，但至少说明你没写反下标。", "meme", "算法竞赛圈"),
    _q("十年竞赛一场空，不开 long long 见祖宗。", "meme", "OI 圈老话"),
    _q("今天被题虐千百遍，明天再见它如初见。", "meme", "算法竞赛圈"),
    _q("一个人卡住的题，三个人吵五分钟就通了。", "meme", "算法竞赛圈"),
    _q("对拍五分钟，调试两小时——但真的划算。", "meme", "算法竞赛圈"),
    _q("签到题也是题，A 掉它才有点开下一题的底气。", "meme", "算法竞赛圈"),
    _q("评测机是不会说谎的老师，只是有时不给你留面子。", "meme", "算法竞赛圈"),
    _q("坐到比赛结束的耐心，比手速稀缺得多。", "meme", "算法竞赛圈"),
    _q("你掉的每一分 rating，都会变成补题列表上的一道题。", "meme", "算法竞赛圈"),
    _q("不会就换角度想，正解总在下一次提交里。", "meme", "算法竞赛圈"),
    _q("别急着翻题解，先和这道题单独相处十分钟。", "meme", "算法竞赛圈"),
    _q("赛后那半小时补题，比比赛的两小时值钱得多。", "meme", "算法竞赛圈"),
    _q("榜一是别人的，AC 掉这道题的快乐是你自己的。", "meme", "算法竞赛圈"),
    _q("打表能过的题，也要知道它为什么能过。", "meme", "算法竞赛圈"),
    _q("数组开小、取模写反、文件名打错——这三届届都在应验。", "meme", "算法竞赛圈"),
    _q("组队赛里最值钱的不是手速快的人，是讲得明白的人。", "meme", "算法竞赛圈"),
    _q("代码不会骗你，它只是严格地执行了你写下的东西。", "meme", "调试界名言"),
    # ---------------- 竞赛鸡汤 ----------------
    _q("你刷过的每一道题，都算数。", "cheer"),
    _q("你的 rating 会掉，你会的东西不会。", "cheer"),
    _q("慢就是快：真正弄懂一道题，比刷十道水题有用。", "cheer"),
    _q("你不需要一开始就很强，只需要今天比昨天强一点。", "cheer"),
    _q("不会的题是礼物，会做的题是奖励。", "cheer"),
    _q("掉分不要紧，掉分之后不补题才要紧。", "cheer"),
    _q("坚持到比赛结束的人，已经赢过了半途退出的自己。", "cheer"),
    _q("你现在觉得难的题，三个月后会觉得简单——前提是别停下。", "cheer"),
    _q("从 0 到 1 最难，而你已经走在路上了。", "cheer"),
    _q("每一次提交都需要勇气，每一次 AC 都值得鼓掌。", "cheer"),
    _q("缺的不是天赋，是把一道题想到底的那半小时。", "cheer"),
    _q("慢慢来，比较快。", "cheer"),
    _q("你的对手不是榜上第一名，是昨天的自己。", "cheer"),
    _q("今天只弄懂一个结论，也是往前挪了一步。", "cheer"),
    _q("别怕做不出来，怕的是连题都没读完。", "cheer"),
    _q("讲一遍思路给别人听，你就真的会了。", "cheer"),
    _q("训练的意义不是变强的那天，而是没变弱的每一天。", "cheer"),
    _q("把大目标拆成下一道题，剩下的交给时间。", "cheer"),
    _q("每一次 WA，都在帮你缩小正确答案的范围。", "cheer"),
    _q("认真打完今天这场，未来的你会谢谢现在没放弃的自己。", "cheer"),
]


# ============================ 工具 ============================


def _clean_text(s) -> str:
    """把一条自定义名言洗成「能安全塞进模板」的样子。

    用户可能在配置框里粘进来带换行、带花括号、带首尾空格的句子：
      * 换行 -> 空格（日报模板里换行会撑开版面）
      * `{` `}` -> 全角 ｛｝（半角会跟模板占位符打架，轻则这句被吞、重则整条日报渲染出错）
    这两步不做的话，出问题的地方在渲染层，排查起来会很难看 —— 在这里掐掉最省事。
    """
    t = "" if s is None else str(s)
    t = t.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = t.replace("{", "｛").replace("}", "｝")
    t = " ".join(t.split())
    if len(t) > MAX_QUOTE_LEN:
        t = t[:MAX_QUOTE_LEN].rstrip() + "…"
    return t


def clean_text(s) -> str:
    """`_clean_text` 的公开别名（面板/指令层要拿它做去重键，别再各处重写一遍）。"""
    return _clean_text(s)


def quote_id(text, source: str = "builtin") -> str:
    """一条名言的稳定 id：`<来源>:<正文 sha1 前 10 位>`。

    为什么不用「第几条」当下标：屏蔽名单要长期有效，而内置库将来会增删句子 ——
    一旦插入一条，所有下标就整体错位，用户的屏蔽记录会莫名作用到别的句子上。
    用正文散列则「正文不变、id 不变」，增删互不影响。
    """
    h = hashlib.sha1(str(text or "").strip().encode("utf-8")).hexdigest()[:10]
    return f"{(source or 'builtin').strip()}:{h}"


def _norm_blocked(blocked) -> set:
    """把屏蔽名单归一成 set（None / 脏值一律安全跳过）。"""
    if not blocked:
        return set()
    if isinstance(blocked, str):
        blocked = [blocked]
    try:
        return {str(x).strip() for x in blocked if str(x).strip()}
    except TypeError:
        return set()


def _decorate(q: dict, source: str, blocked_ids: set) -> dict:
    """给一条名言加上 id / 来源 / 是否被屏蔽（面板列表用）。"""
    text = str(q.get("text") or "")
    d = {
        "text": text,
        "by": str(q.get("by") or ""),
        "cat": q.get("cat") or "custom",
        "source": source,
        "id": quote_id(text, source),
    }
    d["blocked"] = d["id"] in blocked_ids
    return d


def items(extra=None, blocked=None, include_blocked: bool = True) -> list:
    """**全库清单**（内置 + 自定义），每项带 `id` / `source` / `blocked`。

    面板「名言库」用它把每一条都列出来（用户要求「我要看到全部名言」）。
    `include_blocked=False` 时只返回还能抽中的那些 —— 抽取池就是这么取的。
    """
    came = _norm_blocked(blocked)
    out = [_decorate(q, "builtin", came) for q in QUOTES]
    out += [_decorate(q, "custom", came) for q in _norm_extra(extra)]
    if not include_blocked:
        out = [d for d in out if not d["blocked"]]
    return out


def prepare_custom(text, by="") -> tuple[dict | None, str | None]:
    """校验并清洗一条「新增」的名言，返回 `(条目, 错误文案)`。

    面板里加句子是**人直接打的**，所以在这里一次性把关：
      * 空 / 只有空白 -> 拒（否则日报会出现一对空〖〗）
      * 超长 -> 拒（而不是偷偷截断：用户看不到自己写的被砍了一半，最气人）
      * 花括号 / 换行 -> 按 `_clean_text` 的规则洗成安全形态
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return None, "名言正文不能为空"
    if len(raw) > MAX_QUOTE_LEN:
        return None, f"太长了：{len(raw)} 字，上限 {MAX_QUOTE_LEN} 字"
    cleaned = _clean_text(raw)
    if not cleaned:
        return None, "名言正文不能为空"
    return {
        "text": cleaned,
        "by": _clean_text(by)[:MAX_BY_LEN],
        "cat": "custom",
    }, None


def _norm_extra(extra) -> list:
    """把用户配置的自定义名言归一化成与内置库同构的条目。

    宽容收三种写法：`["句子", ...]`、`[{"text": ..., "by": ...}, ...]`、
    或者整个配置项被写成了一整段带换行的文本（按行拆）。
    """
    if not extra:
        return []
    items_ = extra
    if isinstance(items_, str):
        items_ = items_.split("\n")
    if isinstance(items_, dict):
        items_ = [items_]
    if not isinstance(items_, (list, tuple)):
        return []
    out = []
    for it in items_:
        by = ""
        if isinstance(it, dict):
            by = _clean_text(it.get("by") or it.get("source") or "")
            it = it.get("text") or it.get("quote") or ""
        text = _clean_text(it)
        if text:
            out.append({"text": text, "by": by, "cat": "custom"})
    return out


def _wanted(cats) -> set | None:
    """把配置里的分类项归一化成小写代号集合；非法/为空 -> None（= 不筛）。"""
    if not cats:
        return None
    if isinstance(cats, str):
        cats = [cats]
    want = set()
    for c in cats or []:
        key = str(c or "").strip().lower()
        if key in CATEGORIES:
            want.add(key)
        elif key in ("custom", "自定义"):
            want.add("custom")
    return want or None


def _pool(cats=None, extra=None, blocked=None) -> list:
    """取出候选池：分类筛「未被屏蔽的」内置库，再接上用户自定义。

    自定义是**加法**，不像内置三类那样会被分类开关关掉：用户自己打的句子被静默丢弃
    才是更糟的意外（1.4.0 的配置说明就是这么写的，但当时只有把 `custom` 也写进
    `daily_quote_cats` 才真的生效，等于默认不生效 —— 1.6.0 修正为「恒进池」；
    不想要某一句就在面板里删掉它）。

    兜底纪律（1.6.0 起更严）：分类筛空 / 某个分类全被屏蔽时，退回**未被屏蔽的全库**，
    而不是含屏蔽项的全库 —— 用户特意点过「屏蔽」的句子绝不偷偷回来。
    极端情况（全库都被屏蔽）返回空池，日报那一段自然不显示，符合预期。
    """
    want = _wanted(cats)
    allow_custom = want is None or "custom" in want or bool(want & set(CATEGORIES))
    picked = [
        d
        for d in items(extra, blocked, include_blocked=False)
        if (d["cat"] == "custom" and allow_custom)
        or (d["cat"] != "custom" and (want is None or d["cat"] in want))
    ]
    if not picked:
        picked = items(extra, blocked, include_blocked=False)
    return [{"text": d["text"], "by": d["by"], "cat": d["cat"]} for d in picked]


def date_seed(ts) -> int:
    """把一个时间戳换算成「北京时间的第几天」—— 同一天同一个数。"""
    try:
        return int((float(ts) + _TZ_OFFSET) // 86400)
    except (TypeError, ValueError):
        return 0


def pick_quote(seed=None, cats=None, extra=None, blocked=None) -> dict | None:
    """抽一条名言。

    `seed` 传时间戳时按「当天」抽（同一天稳定）；传 None 表示真随机。
    `blocked` 是屏蔽名单（`quote_id` 的集合），命中者不参与抽取。
    """
    pool = _pool(cats, extra, blocked)
    if not pool:
        return None
    if seed is None:
        return dict(random.choice(pool))
    n = len(pool)
    # 先乘法散列打散（否则「同一天 = 同一下标」在 pool 变长时会前后扎堆），
    # 再用 MT 取下标：python 的 Random(seed) 跨版本/跨平台输出稳定，可写进测试。
    hashed = (int(seed) * _HASH_MUL) & _U32
    return dict(pool[random.Random(hashed).randrange(n)])


def format_quote(q: dict | None, with_source: bool = True) -> str:
    """渲染成一行文本；`with_source` 为真且有条目出处时附上「—— 出处」。"""
    if not q:
        return ""
    text = str(q.get("text") or "").strip()
    by = str(q.get("by") or "").strip()
    if with_source and by:
        return f"{text} —— {by}"
    return text


def search(keyword: str, limit: int = 10, extra=None, blocked=None) -> list:
    """按关键词（不区分大小写）搜正文与出处，`/名言 <词>` 用。

    只搜「还能抽中的」那些 —— 已经被屏蔽的句子搜出来也没法用，反而让人以为屏蔽没生效。
    """
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    hits = [
        q for q in all_quotes(extra, blocked)
        if kw in q["text"].lower() or kw in str(q["by"]).lower()
    ]
    return hits[: max(1, int(limit or 10))]


def all_quotes(extra=None, blocked=None) -> list:
    """内置库 + 自定义（**已剔除屏蔽项**）的副本，外部改动不会污染模块状态。"""
    return [
        {"text": d["text"], "by": d["by"], "cat": d["cat"]}
        for d in items(extra, blocked, include_blocked=False)
    ]


def counts(cats=None, extra=None, blocked=None) -> dict:
    """候选池里各分类各有多少条（面板/文档展示用，含用户自定义、不含被屏蔽的）。"""
    pool = _pool(cats, extra, blocked)
    out = {k: 0 for k in CATEGORIES}
    out["custom"] = 0
    for q in pool:
        out[q.get("cat") or "custom"] = out.get(q.get("cat") or "custom", 0) + 1
    return out


def stats(cats=None, extra=None, blocked=None) -> dict:
    """名言库体检：总数 / 被屏蔽数 / 真能抽中的数 / 分类分布（面板顶部摘要用）。

    这里刻意把「被屏蔽」与「分类没启用」分开统计：面板要能一眼看出
    「这条为什么没被推送」到底是屏蔽了，还是它的分类被关了。
    """
    all_items = items(extra, blocked)
    want = _wanted(cats)
    by_cat = {k: {"total": 0, "blocked": 0, "enabled": 0} for k in list(CATEGORIES) + ["custom"]}
    for d in all_items:
        bucket = by_cat.setdefault(d["cat"], {"total": 0, "blocked": 0, "enabled": 0})
        bucket["total"] += 1
        if d["blocked"]:
            bucket["blocked"] += 1
    eligible = 0
    for q in _pool(cats, extra, blocked):
        bucket = by_cat.setdefault(q.get("cat") or "custom", {"total": 0, "blocked": 0, "enabled": 0})
        bucket["enabled"] += 1
        eligible += 1
    return {
        "total": len(all_items),
        "blocked": sum(1 for d in all_items if d["blocked"]),
        "custom": sum(1 for d in all_items if d["source"] == "custom"),
        "eligible": eligible,
        "byCat": by_cat,
        # 保持展示顺序（不是 sorted，否则面板上分类会跳来跳去）
        "enabledCats": [c for c in list(CATEGORIES) + ["custom"] if want is None or c in want],
    }


def cat_label(cat: str) -> str:
    """分类代号 -> 中文名（未知分类原样返回）。"""
    return CATEGORIES.get(str(cat or "").lower(), "自定义")


def cat_choices() -> list:
    """面板筛选下拉用：`[["", "全部分类"], ["classic", "名家名言"], ...]`（含自定义）。"""
    out = [["", "全部分类"]]
    out += [[k, v] for k, v in CATEGORIES.items()]
    out.append(["custom", "自定义"])
    return out
