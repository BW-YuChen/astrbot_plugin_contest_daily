# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 用户战绩查询层（S6）

/CF <handle>   查 Codeforces 用户
/NK <uid>      查牛客用户

== 数据来源与取数思路 ==

Codeforces（调用间隔 >=1.0s 符合官方限流；互不依赖的请求错峰并发，见下）
  四类数据各司其职：
    ① 用户信息   -> handle / rating / 最高 rating / 段位 / 最高段位
    ② 提交记录   -> 「总过题数」（去重后的 AC）
                  + 各场比赛的「过题数」（按提交时间戳切场次）
                  + 本批提交里最早的提交时间（判断窗口够不够旧）
    ③ 比赛记录   -> 本周比赛列表（比赛名 / 名次 / 时间）
    ④ 全量题库   -> {比赛: 题目数}，即各场「总题数」
         ✔ 2.2 MB 一次拉全量，覆盖所有比赛，长缓存后所有用户共用
  另有「单场按人取提交」的兜底路径（2.6 KB）：
    只在提交记录的窗口够不着历史时才用（活跃用户的老比赛）

  ⚠️ 已废弃的做法：整场拉榜单（实测 5.8 MB / 4.5 s，且偶发 60s 级抖动）。
     它**不允许带任何筛选参数**，所以没法按人过滤，只能整场拉回来自己筛。
     现在改用上面两条轻量路径，单场成本从 5.8 MB 降到 2.6 KB。
     副作用：「排名」现在取 CF 官方 rating 更新时的名次，
     与榜单页名次可能差几十名（榜单含非正式参赛者）。

  ⚠️ 坑：判断「某场过了几题」不能用提交的绝对时间直接卡区间，
     要用「距本场开赛的秒数」这个字段：<0 是赛前提前做的，
     0~比赛时长 是正赛，很大是赛后补题。取 0 ~ CF_MAX_CONTEST_HOURS 这个
     偏宽松的上界，既能排除赛前/赛后，又不必另外查赛制时长。

牛客（无官方 API，走站点接口，实测可用）
  * 基础档案    -> 昵称 / rating / 名次 / 学校
  * 历史 rating -> 用于算最高 rating
  * 参赛历史    -> 每场：「过题数 / 总题数 / 名次 / 比赛名 / 开始时间」
        ⚠️ 坑：分页参数里有个「只取已结束场次」的开关是**必须**的，漏掉会返回空列表
  * 练习页（HTML）-> 「题已通过」= 总过题数

== 限流与缓存（四道闸）==
  1. 错峰并发：同平台相邻请求的**发出时刻**至少间隔 CF 1.0s / 牛客 0.6s，
     但请求本身可以重叠（_throttle 只锁「出发时刻」，不锁整个 HTTP 调用），
     所以 3 个互不依赖的请求总耗时 ≈ 2×间隔 + 最慢那个，而不是三者相加
  2. 结果缓存：用户档案默认 10 分钟；单场成绩默认 24 小时；
     题库映射默认 24 小时（全局共用，与具体用户无关）
  3. 用户级冷却：同一发送者默认 15 秒内只能查一次（在 main.py 里做）
  4. 全局互斥：同一时刻只处理一个查询（在 main.py 里做）
"""

from __future__ import annotations

import gzip
import html as _html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

TZ_CN = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 40  # 常规请求超时
BIG_TIMEOUT = 90  # 大响应（全量题库约 2.2MB）单独放宽，实测偶发 60s 级抖动
CF_MIN_INTERVAL = 1.0   # CF 官方建议每秒不超过 1 次（也是并发请求之间的错峰间隔）
NK_MIN_INTERVAL = 0.6   # 牛客站点，保守一点
CF_MAX_CONTEST_HOURS = 8  # 单场比赛时长上界：用它把「赛前提前做」和「赛后补题」排除在战绩外
# 判断「这批提交能不能覆盖某场历史比赛」时，留出的保守余量。
# 提交窗口是「最近 N 条」，只能保证覆盖 [最早提交时间, 现在]；比赛开赛时刻
# 一定早于该场 rating 更新时间 CF_MAX_CONTEST_HOURS 以上，所以退到这么久以前才敢下结论。
CF_COVER_MARGIN = CF_MAX_CONTEST_HOURS * 3600

CF_API = "https://codeforces.com/api/"
NK_BASE = "https://ac.nowcoder.com"

# 战绩缓存的键前缀版本号。缓存是**落盘**的（profile_cache.json），改了取数口径后
# 旧条目不会自动失效 —— 加版本号强制重拉一次，避免「代码修好了但用户还看到旧结果」。
# 1.2.18：CF 负分选手（rating ≤ 0）的 rating 改由比赛记录兜底，旧缓存里存的是 None。
CF_CACHE_VER = "v2"
CF_CACHE_KEY_PREFIX = f"cf:{CF_CACHE_VER}:"

# 线程锁：同一平台的请求串行化 + 限速（profile 查询走线程池，不能用 asyncio.Lock）
_CF_LOCK = threading.Lock()
_NK_LOCK = threading.Lock()
_CF_LAST = [0.0]
_NK_LAST = [0.0]


class InvalidInput(str):
    """标记「用户输入本身无效」的错误（无此用户 / 参数格式不对）。

    为什么要单独标出来：群主要求**无效的指令**（缺参数、参数格式错、查不到该用户）
    与**无权限**回完全同一句文案，而「超时 / 网络故障」仍要回可重试的提示 ——
    两者都是 `(profile, err)` 里的 err 字符串，只有类型能区分开。
    继承 str 是为了不破坏既有的 `(dict | None, str | None)` 契约与所有字符串断言。
    """

    pass


# ======================= 网络层 =======================

def _openers() -> tuple:
    """取数通道：**直连优先、系统代理兜底**。

    为什么直连优先：CF API 与牛客直连更快（实测 0.7s / 0.1s，走代理 1.3s / 0.2s），
    而且本机代理对部分站点会回 502/000（原始注释见 sources.py）。

    为什么还要留一条代理兜底：以前这里写死 `ProxyHandler({})`（强制直连、不读系统
    代理），在「必须走代理才能上外网」的机器上 /cf、/nk 会**一个都查不出来**。
    CF 的头像 CDN 就是这么被挡住的（见 card.py 的实测表）——同一个隐患不该留两次。
    机器**没配**代理时两条通道完全等价，这时只留一条，免得把同一件事做两遍。
    """
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if urllib.request.getproxies():
        return (direct, urllib.request.build_opener(urllib.request.ProxyHandler()))
    return (direct,)


_OPENERS = _openers()


def _http_text(
    url: str, headers: dict | None = None, retries: int = 1, timeout: int = 0
) -> tuple[bool, str]:
    """GET 文本，自动处理 gzip。返回 (ok, text_or_err)。

    timeout 传 0 用默认 TIMEOUT；standings 这类大响应会传更大的值。
    通道按 `_OPENERS` 顺序试（直连 -> 代理），每条通道各重试 retries 次。
    """
    hdrs = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        hdrs.update(headers)
    to = timeout or TIMEOUT
    last = "unknown"
    for op in _OPENERS:
        for _ in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with op.open(req, timeout=to) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    enc = r.headers.get_content_charset() or "utf-8"
                    return True, raw.decode(enc, "replace")
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    body = ""
                # 服务器已经明确答复（含 CF 的 "User with handle xxx not found"）
                # -> 换通道不会给出别的答案，直接返回，省掉一次白等的请求
                return False, f"HTTP {e.code} {body[:200]}"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
    return False, last


def _throttle(platform: str) -> None:
    """平台级限速：保证相邻两次请求的**发出时刻**间隔不小于阈值。

    注意这里只锁「出发时刻」，调用方拿到返回值后的 HTTP 请求并不持锁，
    所以多个线程可以错峰出发、同时在途 —— 既守住了对站点的请求节奏，
    又让网络等待重叠起来（这是战绩查询提速的关键之一）。
    """
    lock, last, gap = (
        (_CF_LOCK, _CF_LAST, CF_MIN_INTERVAL)
        if platform == "cf"
        else (_NK_LOCK, _NK_LAST, NK_MIN_INTERVAL)
    )
    with lock:
        wait = gap - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time()


def _get_cf_json(endpoint: str, timeout: int = 0) -> tuple[dict | None, str | None]:
    _throttle("cf")
    ok, text = _http_text(CF_API + endpoint, timeout=timeout)
    if not ok:
        return None, f"CF 请求失败: {text}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"CF 返回非 JSON: {e}"
    if data.get("status") != "OK":
        return None, f"CF 错误: {data.get('comment') or data.get('status')}"
    return data, None


def _get_nk_json(path: str, referer: str = "") -> tuple[dict | None, str | None]:
    _throttle("nk")
    hdrs = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        hdrs["Referer"] = referer
    ok, text = _http_text(NK_BASE + path, headers=hdrs)
    if not ok:
        return None, f"牛客请求失败: {text}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "牛客返回的不是 JSON（接口可能变更，需要更新）"
    if data.get("code") not in (0, None):
        return None, f"牛客错误: {data.get('msg')}"
    return data, None


# ======================= 纯解析函数（便于离线测试）=======================

def _as_obj(payload):
    """把「JSON 字符串」或「已经解析好的 dict」统一成 dict。

    为什么需要：ProfileFetcher 拿到的是 `_get_cf_json` 解析好的 dict，而离线测试
    用的是样本 JSON 文件里的字符串。以前在取数侧写 `parse_x(json.dumps(data))`
    —— 那等于把 2.2 MB 的题库、123 KB 的提交记录**再序列化一份字符串、再解析
    回来**，多一次内存副本（2 GB 小内存机器上这是最没必要的开销）和一次 CPU。
    现在两边都直接吃 dict，字符串只在测试/离线场景出现。
    """
    if isinstance(payload, (dict, list)):
        return payload
    return json.loads(payload)


class _NoSuchUser(ValueError):
    """CF 回了 `status=OK` 但 `result` 为空 —— 这个 handle 在 CF 上不存在。

    为什么要单独一个类型：CF 对「被它过滤成空串」的 handle（例如纯 emoji）**既不报错
    也不返回用户**，只给一个空的 result。以前这里抛裸 `ValueError`，会被上游当成
    「解析异常 / 网络故障」，用户看到的是「❌ 查询出错」而不是「没有这个用户」——
    2026-10-16 用户报的「非字母数字英文的 handle 查不了」就是这条。
    继承 ValueError 是为了不破坏既有的 `except ValueError` 与字符串断言契约。
    """


# CF handle 允许的字符。**这不是我们施加的限制，而是 CF 自己的口径** ——
# 2026-10-16 逐个字符实测：除 `;`（多 handle 分隔符）外，输入里**任何**其它字符
# CF 都会悄悄删掉再拿去查：
#   `tourist+X` `tourist%X` … `tourist’X`（含空格、中文 `中`、emoji `😀`）
#   -> 一律变成查 `touristX`；
#   `游客123` -> 查 `123`；`😀tourist` -> 查 `tourist`；
#   全是非法字符时（`😀`）-> `{"status":"OK","result":[]}`，一个用户都不给。
_CF_HANDLE_CHAR = re.compile(r"[A-Za-z0-9_.\-]")


def _cf_handle_guard(handle: str) -> str | None:
    """handle 含 CF 不接受的字符时返回提示文案，合法则返回 None。

    🔑 为什么**拦下**而不是「照原样查、让 CF 去删」：CF 删掉非法字符后拿到的
    往往**是另一个真实用户**（`游客123` -> 用户 `123`、`😀tourist` -> 用户 `tourist`）。
    那会返回一张看起来完全正常的卡片，用户根本分不清查的是谁 —— 比直接报错危险。
    AstrBot 的 CommandFilter 只把**第一个 token** 传进来（内部先 `re.sub(r"\\s+", " ")`
    再 `split(" ")`），所以正常情况下不会有空格；这里仍留一条可读分支以防万一。
    """
    illegal = "".join(dict.fromkeys(c for c in handle if not _CF_HANDLE_CHAR.match(c)))
    if not illegal:
        return None
    shown = "".join("空格" if c.isspace() else c for c in illegal[:8])
    legal = "".join(c for c in handle if _CF_HANDLE_CHAR.match(c))
    msg = (
        f"Codeforces 的 handle 只允许字母 / 数字 / 下划线 / 短横线 / 点。"
        f"你输入里的「{shown}」这类字符 CF 会先删掉再拿去查"
        f"（例如 游客123 会被它当成 123、😀tourist 当成 tourist），"
        f"查到的是谁就说不准了。"
    )
    if legal:
        msg += f"\n去掉这些字符后是「{legal}」，确认没错就发 `/cf {legal}`。"
    else:
        msg += "\n去掉之后什么都不剩了 —— 这不是一个有效的 Codeforces handle。"
    return msg


def _cf_no_such_user(handle: str) -> str:
    """「CF 上没有这个用户」的提示（条数按 InvalidInput 走，与无权限同一句）。"""
    return f"Codeforces 上没有名为「{handle}」的用户，请检查拼写"


def parse_cf_info(payload) -> dict:
    """用户信息 -> 基础档案。payload 可以是 JSON 字符串或已解析的 dict。"""
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf user.info failed")
    arr = data.get("result") or []
    if not arr:
        # status 是 OK 但没有用户：handle 不存在（常见于被 CF 过滤成空串的输入）
        raise _NoSuchUser("empty")
    u = arr[0]
    return {
        "handle": u.get("handle", ""),
        "rating": u.get("rating"),
        "max_rating": u.get("maxRating"),
        "rank_title": u.get("rank") or "",
        "max_rank_title": u.get("maxRank") or "",
        "country": u.get("country") or "",
        "organization": u.get("organization") or "",
        "registered_at": u.get("registrationTimeSeconds"),
        # 头像：大图约 300px（画质好但 CF 图床约一半会 503），
        # 小图约 76px（基本都能下）。两个都给，卡片层「先大后小」兜底。
        "avatar": u.get("titlePhoto") or u.get("avatar") or "",
        "avatar_fallback": u.get("avatar") or "",
    }


def _in_contest_window(s: dict, limit_seconds: int) -> bool:
    """这条提交是否落在「比赛时段内」。

    提交里「距本场开赛的秒数」这个字段：
      < 0        赛前（提前用题目链接写的）
      0 ~ 时长   正赛
      很大       赛后补题

    取 0 ~ limit 这个偏宽松的上界：既能排除赛前/赛后，又不必另外查赛制时长。
    老数据里没有该字段时，退化成「本场开赛时间 + 提交绝对时间」的区间判断。
    """
    rts = s.get("relativeTimeSeconds")
    if isinstance(rts, (int, float)):
        return 0 <= rts <= limit_seconds
    start = (s.get("author") or {}).get("startTimeSeconds")
    ts = s.get("creationTimeSeconds")
    if start and ts:
        return start <= ts <= start + limit_seconds
    return False


def parse_cf_status(payload, max_contest_hours: int = CF_MAX_CONTEST_HOURS) -> dict:
    """提交记录 -> 一次拿到三样东西。

    返回 {solved_total, by_contest, oldest_ts, submissions}：
      solved_total  总过题数（不同题目的 AC 去重，口径与旧版一致）
      by_contest    {contestId: 该场「比赛时段内」AC 的不同题目数}
      oldest_ts     本批提交里最早的提交时间（判断窗口够不够旧）
      submissions   本批提交条数（排障用）

    一份 123 KB 的响应就把「总过题数」和「本周每场过题数」都解决了，
    原先这两样要分别靠再拉一次提交与 5.8 MB 的整场榜单。
    """
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf user.status failed")
    limit = max(1, int(max_contest_hours)) * 3600
    rows = data.get("result") or []
    seen = set()
    in_contest: dict = {}
    oldest = None
    for s in rows:
        ts = s.get("creationTimeSeconds") or 0
        if ts and (oldest is None or ts < oldest):
            oldest = ts
        p = s.get("problem") or {}
        idx = p.get("index")
        if idx is None or s.get("verdict") != "OK":
            continue
        cid = p.get("contestId")
        seen.add((cid, p.get("problemsetName") or "contest", idx))
        if cid is not None and _in_contest_window(s, limit):
            in_contest.setdefault(cid, set()).add(idx)
    return {
        "solved_total": len(seen),
        "by_contest": {k: len(v) for k, v in in_contest.items()},
        "oldest_ts": oldest,
        "submissions": len(rows),
    }


def parse_cf_solved(payload) -> int:
    """提交记录 -> 总过题数（不同题目的 AC 去重）。"""
    return parse_cf_status(payload)["solved_total"]


def parse_cf_contest_solved(payload, max_contest_hours: int = CF_MAX_CONTEST_HOURS) -> int:
    """单场按人取提交 -> 该场「比赛时段内」AC 的不同题目数。

    2.6 KB，替代 5.8 MB 的整场榜单。实测与榜单口径一致
    （Firefly_IV @ 2276：两边都是 4 题）。
    """
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf contest.status failed")
    limit = max(1, int(max_contest_hours)) * 3600
    idxs = set()
    for s in data.get("result") or []:
        if s.get("verdict") != "OK":
            continue
        if not _in_contest_window(s, limit):
            continue
        idx = (s.get("problem") or {}).get("index")
        if idx:
            idxs.add(idx)
    return len(idxs)


def parse_cf_problem_counts(payload) -> dict:
    """全量题库 -> {contestId: 题目数}，即各场比赛的「总题数」。

    全量题库一次 2.2 MB，覆盖约 2000 场，长缓存后所有用户共用；
    比每场拉一份 5.8 MB 榜单便宜得多。实测第 2276 场得 7 题，
    与整场榜单 problems 长度一致。
    """
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf problemset.problems failed")
    res = data.get("result") or {}
    counts: dict = {}
    for p in res.get("problems") or []:
        cid = p.get("contestId")
        if cid is not None:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def parse_cf_history(payload, week_days: int = 7, now_ts: int | None = None) -> list[dict]:
    """比赛记录 -> 最近 week_days 天内的比赛（含 contestId / 名称 / 排名 / 时间）。

    只有标了 rated、且真的参与了计分的场次才会出现在这里（无 rating 变化的场次查不到）。
    rank 是 CF 官方 rating 更新的名次；「过题数/总题数」由提交记录与题库映射补齐。
    """
    now_ts = now_ts or int(time.time())
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf user.rating failed")
    since = now_ts - week_days * 86400
    out = []
    for r in data.get("result") or []:
        ts = r.get("ratingUpdateTimeSeconds")
        if not ts or ts < since or ts > now_ts:
            continue
        out.append(
            {
                "contest_id": str(r.get("contestId")),
                "name": r.get("contestName", ""),
                "rank": r.get("rank"),
                "start_at": int(ts),
                "rating_change": (r.get("newRating", 0) - r.get("oldRating", 0)),
            }
        )
    out.sort(key=lambda x: x["start_at"])
    return out


# CF 官方段位分段（与官方返回的段位字段命名一致，从高到低匹配）。
# 用途：用户信息接口对 rating ≤ 0 的用户**不返回段位字段**，得自己按分段推。
CF_RANK_TIERS = (
    (3000, "legendary grandmaster"),
    (2600, "international grandmaster"),
    (2400, "grandmaster"),
    (2300, "international master"),
    (2100, "master"),
    (1900, "candidate master"),
    (1600, "expert"),
    (1400, "specialist"),
    (1200, "pupil"),
)


def cf_rank_title(rating) -> str:
    """把 rating 换成 CF 官方称号；负数 / 0 及 1200 以下都是 newbie。

    只在 CF 没给称号时（rating ≤ 0 的用户）当兜底用，非数字一律返回空串。
    """
    if not isinstance(rating, (int, float)) or isinstance(rating, bool):
        return ""
    for lo, name in CF_RANK_TIERS:
        if rating >= lo:
            return name
    return "newbie"


def parse_cf_rating_summary(payload) -> dict | None:
    """比赛记录（全历史）-> 当前 rating / 历史最高 / 参赛场次；没打过计分赛返回 None。

    🔑 为什么必须有这个兜底：**CF 的用户信息接口对 rating ≤ 0 的用户会把 rating /
    最高 rating / 段位三个字段整个省掉**（不是给 0，是压根没有这个 key）。
    实测 handle `_I_AM_THE_LOWEST_`：
      * 用户信息接口 -> 只有 handle / country / 头像…，**没有 rating**（`.get()` 得到 None）
      * 比赛记录     -> 26 场完整记录，当前 **-35**、历史最高 **775**
    只认用户信息接口的话，这种「负分选手」会被当成未定级用户，卡片直接显示「暂无」。
    反正比赛记录本来就为「本周战绩」拉过了，顺手取全量汇总即可，零额外请求。
    """
    data = _as_obj(payload)
    if data.get("status") != "OK":
        raise ValueError(data.get("comment") or "cf user.rating failed")
    items = [r for r in (data.get("result") or []) if isinstance(r, dict)]
    vals = [
        r.get("newRating")
        for r in items
        if isinstance(r.get("newRating"), (int, float)) and not isinstance(r.get("newRating"), bool)
    ]
    if not vals:
        return None
    # result 本身按时间升序，但别依赖它：显式按更新时间取最后一场，历史最高取全量最大。
    last = max(items, key=lambda r: r.get("ratingUpdateTimeSeconds") or 0)
    latest = last.get("newRating")
    if not isinstance(latest, (int, float)) or isinstance(latest, bool):
        latest = vals[-1]
    return {
        "rating": int(latest),
        "max_rating": int(max(vals)),
        "contests": len(items),
        "last_contest": last.get("contestName") or "",
        "updated_at": int(last.get("ratingUpdateTimeSeconds") or 0),
    }


def parse_nk_basic(payload) -> dict:
    """牛客基础档案 -> 昵称 / rating / 排名 / 学校。"""
    data = _as_obj(payload)
    d = data.get("data") or {}
    return {
        "handle": d.get("nickname") or "",
        "rating": d.get("rating"),
        "rank": d.get("rank"),
        "has_rating": bool(d.get("hasRating")),
        "contest_count": d.get("contestCount"),
        "rated_count": d.get("ratedCount"),
        "school": d.get("school") or "",
        "color_level": d.get("colorLevel"),
        "avatar": d.get("tinnyHeaderUrl") or d.get("headerUrl") or "",
    }


def parse_nk_max_rating(payload) -> int | None:
    """牛客历史 rating -> 历史最高 rating。"""
    data = _as_obj(payload)
    items = data.get("data") or []
    vals = [i.get("rating") for i in items if isinstance(i.get("rating"), (int, float))]
    return int(max(vals)) if vals else None


def parse_nk_joined(payload, week_days: int = 7, now_ts: int | None = None) -> list[dict]:
    """牛客参赛历史 -> 最近 week_days 天的比赛战绩（含过题数/总题数/排名）。"""
    now_ts = now_ts or int(time.time())
    data = _as_obj(payload)
    inner = data.get("data") or {}
    since_ms = (now_ts - week_days * 86400) * 1000
    out = []
    for c in inner.get("dataList") or []:
        start_ms = c.get("startTime")
        if not start_ms or start_ms < since_ms:
            continue
        out.append(
            {
                "contest_id": str(c.get("contestId")),
                "name": c.get("contestName", ""),
                "solved": c.get("acceptedCount"),
                "total": c.get("problemCount"),
                "rank": c.get("rank"),
                "start_at": int(start_ms / 1000),
                "rating_change": c.get("changeValue"),
                "user_count": c.get("userCount"),
            }
        )
    out.sort(key=lambda x: x["start_at"])
    return out


_RE_NK_SOLVED = re.compile(r'<div class="state-num">(\d+)</div>\s*<span>题已通过</span>')


def parse_nk_solved_total(page_html: str) -> int | None:
    """牛客练习页 -> 「题已通过」数字。"""
    m = _RE_NK_SOLVED.search(page_html)
    if m:
        return int(m.group(1))
    # 宽松兜底：span 在前的情况
    m2 = re.search(r"题已通过[\s\S]{0,80}?(\d+)", page_html)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"(\d+)[\s\S]{0,40}?题已通过", page_html)
    return int(m3.group(1)) if m3 else None


# ======================= 查询器 =======================

class ProfileFetcher:
    """用户战绩查询器。

    cache 需实现 get_profile_cache(key) / put_profile_cache(key, data, ttl)，
    传 None 表示不使用缓存（测试用）。
    """

    def __init__(
        self,
        cache=None,
        week_days: int = 7,
        cache_minutes: int = 10,
        result_ttl_hours: int = 24,
        problemset_ttl_hours: int = 24,
        status_count: int = 10000,
        max_workers: int = 3,
    ):
        self.cache = cache
        self.week_days = max(1, int(week_days))
        self.cache_ttl = max(1, int(cache_minutes)) * 60
        self.result_ttl = max(1, int(result_ttl_hours)) * 3600
        self.problemset_ttl = max(1, int(problemset_ttl_hours)) * 3600
        self.status_count = max(1, int(status_count))
        self.max_workers = max(1, int(max_workers))

    # ---------- 缓存包装 ----------

    def _cget(self, key: str):
        if not self.cache:
            return None
        try:
            return self.cache.get_profile_cache(key)
        except Exception:  # noqa: BLE001
            return None

    def _cput(self, key: str, data, ttl: int) -> None:
        if not self.cache:
            return
        try:
            self.cache.put_profile_cache(key, data, ttl)
        except Exception:  # noqa: BLE001
            pass

    def _cdel(self, key: str) -> bool:
        fn = getattr(self.cache, "delete_profile_cache", None) if self.cache else None
        if not fn:
            return False
        try:
            return bool(fn(key))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def cache_key(platform: str, ident: str) -> str:
        """档案缓存键。**与 cf() / nk() 内部保持同一套构造**，别在两处各写一遍。"""
        pf = (platform or "cf").strip().lower()
        if pf == "nowcoder":
            return "nk:" + str(ident or "")
        return CF_CACHE_KEY_PREFIX + str(ident or "").lower().strip()

    def forget(self, profile: dict) -> bool:
        """把这份档案从缓存里删掉，返回是否删掉了（1.5.0）。

        什么时候用：战绩卡片**没取到平台头像**时（main.py 判定 avatar_source != platform）。
        用户的要求是「查不到 CF 头像的战绩卡不要缓存」—— 缓存里那份档案带着一个当下
        取不到的头像地址，留着它下次查询仍然看不到头像；删掉后下次会重新联网，
        图床一恢复就能自己好。档案本身是可重建的派生数据，删了没有副作用。
        """
        p = profile or {}
        pf = p.get("platform") or "cf"
        ident = p.get("handle") or p.get("uid") or ""
        if not ident:
            return False
        return self._cdel(self.cache_key(pf, ident))

    # ---------- 错峰并发 ----------

    def _parallel(self, jobs: dict) -> dict:
        """错峰并发执行多个互不依赖的取数动作，返回 {名字: 结果}。

        为什么并发也不会把站点惹毛：_throttle 只锁「出发时刻」，保证相邻请求
        至少间隔 CF_MIN_INTERVAL / NK_MIN_INTERVAL，所以是**错峰出发、同时在途**，
        而不是打一梭子。收益来自把各自的网络等待重叠起来 —— 实测 CF 单请求
        1~8 秒抖动很大，串行三个就是三者相加，错峰并发则是「N×间隔 + 最慢的那个」。
        """
        out: dict = {}
        if not jobs:
            return out
        workers = max(1, min(self.max_workers, len(jobs)))
        if workers == 1:
            for name, fn in jobs.items():
                out[name] = fn()
            return out
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="profq") as pool:
            futures = {pool.submit(fn): name for name, fn in jobs.items()}
            for fut in as_completed(futures):
                out[futures[fut]] = fut.result()
        return out

    # ---------- 题库映射（各场「总题数」）----------

    def _problemset_map(self) -> tuple[dict, str | None]:
        """{contestId: 题目数}，全量题库映射，长缓存（默认 24 小时，全局共用）。

        一次 2.2 MB 换到所有比赛的总题数；换来的收益是单场成本从
        「每场每次 5.8 MB」降到「所有比赛合计一次 2.2 MB」。

        内存：解析出的原始 payload（约 9000 道题的 dict 列表）是**本模块最大的一笔
        临时内存**，所以这里解析完立刻 `del` 掉、只留下 ~2500 项的 {contestId: 题数}
        小字典（几十 KB）进缓存 —— 2 GB 内存的小机器上这一步很关键。
        """
        key = "cfprob:counts"
        hit = self._cget(key)
        if isinstance(hit, dict) and hit:
            return hit, None
        data, err = _get_cf_json("problemset.problems", timeout=BIG_TIMEOUT)
        if err:
            return {}, err
        try:
            counts = parse_cf_problem_counts(data)
        except Exception as e:  # noqa: BLE001
            return {}, f"解析题库失败：{e}"
        finally:
            del data  # 尽快释放大对象，别让它挂到函数返回（GC 前峰值会高一截）
        if counts:
            self._cput(key, counts, self.problemset_ttl)
        return counts, None

    def warm(self) -> bool:
        """预热题库映射（供插件启动时后台调用）。

        预热过之后，用户第一次 /cf 查询就不用等那 2.2 MB 下载了。
        缓存还在有效期内时几乎零成本（只读一次本地缓存）。
        """
        try:
            _, err = self._problemset_map()
            return not err
        except Exception:  # noqa: BLE001
            return False

    def _contest_solved(self, contest_id, handle: str) -> int | None:
        """单场「按人」拉成绩（2.6 KB）。只在该用户提交窗口够不着历史时才调用。"""
        skey = f"cfcs:{contest_id}:{handle.lower()}"
        hit = self._cget(skey)
        if isinstance(hit, dict):
            return hit.get("solved")
        if isinstance(hit, int):
            return hit
        data, err = _get_cf_json(
            f"contest.status?contestId={contest_id}&handle={urllib.parse.quote(handle)}"
        )
        if err:
            return None
        try:
            solved = parse_cf_contest_solved(data)
        except Exception:  # noqa: BLE001
            return None
        self._cput(skey, {"solved": solved}, self.result_ttl)
        return solved

    # ---------- Codeforces ----------

    def cf(self, handle: str) -> tuple[dict | None, str | None]:
        """返回 (profile, error)。命中缓存时 seconds 会标 cached=True。"""
        handle = (handle or "").strip()
        if not handle:
            return None, InvalidInput("请给出 Codeforces 的 handle，例如：/cf tourist")
        # 含 CF 不接受的字符时先拦下：CF 会删掉它们再去查**别人**（详见 _cf_handle_guard）
        bad = _cf_handle_guard(handle)
        if bad:
            return None, InvalidInput(bad)

        key = self.cache_key("cf", handle)
        hit = self._cget(key)
        if hit:
            return {**hit, "cached": True}, None

        now = int(time.time())
        q = urllib.parse.quote(handle)

        # ① 三个互不依赖的请求一次性错峰并发发出（详见 _parallel）
        got = self._parallel(
            {
                "info": lambda: _get_cf_json(f"user.info?handles={q}"),
                "rating": lambda: _get_cf_json(f"user.rating?handle={q}"),
                "status": lambda: _get_cf_json(
                    f"user.status?handle={q}&from=1&count={self.status_count}"
                ),
            }
        )

        data, err = got.pop("info", (None, "请求未执行"))
        if err:
            # CF 对不存在的 handle 会明确回 "User with handle xxx not found" ->
            # 算「输入无效」；其余（超时 / 5xx / 被墙）算网络问题，两者回复不同
            if "not found" in err.lower():
                return None, InvalidInput(_cf_no_such_user(handle))
            return None, f"查询失败：{err}"
        try:
            info = parse_cf_info(data)
        except _NoSuchUser:
            # status=OK 但没返回用户 —— 同样是「没这个人」，不是网络故障
            return None, InvalidInput(_cf_no_such_user(handle))
        except Exception as e:  # noqa: BLE001
            return None, f"解析用户信息失败：{e}"

        # ② 一份提交记录同时拿到：总过题数 / 各场过题数 / 提交窗口下界（失败不致命）
        solved_total = None
        by_contest: dict = {}
        oldest_ts = None
        data, err = got.pop("status", (None, "请求未执行"))
        if not err:
            try:
                st = parse_cf_status(data)
                solved_total = st["solved_total"]
                by_contest = st["by_contest"]
                oldest_ts = st["oldest_ts"]
            except Exception:  # noqa: BLE001
                pass

        # ③ 比赛历史 -> 本周场次（rank 即 CF 官方 rating 名次）
        #    同一份数据再算一个**全历史汇总**：CF 对 rating ≤ 0 的用户不返回 rating 字段，
        #    后面用它兜底（详见 parse_cf_rating_summary 的注释）。
        week = []
        hist = None
        data, err = got.pop("rating", (None, "请求未执行"))
        if not err:
            try:
                week = parse_cf_history(data, self.week_days, now)
            except Exception:  # noqa: BLE001
                week = []
            try:
                hist = parse_cf_rating_summary(data)
            except Exception:  # noqa: BLE001
                hist = None

        # ④ 补齐每场的「过题数 / 总题数」
        if week:
            counts, _perr = self._problemset_map()
            fallback: list = []
            for item in week:
                cid_s = item.get("contest_id")
                try:
                    cid = int(cid_s)
                except (TypeError, ValueError):
                    cid = None
                item["total"] = counts.get(cid) if cid is not None else None

                if cid in by_contest:
                    # 提交窗口覆盖到这场，直接用（零额外请求）
                    item["solved"] = by_contest[cid]
                elif oldest_ts is not None and oldest_ts <= (
                    item.get("start_at") or 0
                ) - CF_COVER_MARGIN:
                    # 窗口已经老过这场开赛 -> 这场的提交一定都在本批提交记录里，
                    # 没统计到就是没过题，不必再发请求
                    item["solved"] = 0
                else:
                    fallback.append((cid_s, item))

            # 提交窗口够不着的（通常是刷题量极大的活跃用户的老比赛）：逐场按人拉，每场 2.6 KB
            for cid_s, item in fallback:
                solved = self._contest_solved(cid_s, handle)
                if solved is not None:
                    item["solved"] = solved

        # ⑤ rating 兜底（rating ≤ 0 的「负分选手」）
        #    CF 的用户信息接口会**整个省掉** rating / 最高 rating / 段位字段，只认它就会显示
        #    「暂无」。实测 _I_AM_THE_LOWEST_：用户信息里三个字段一个没有，但比赛记录
        #    有 26 场 —— 当前 -35、历史最高 775。这里用全历史汇总补齐。
        rating = info["rating"]
        max_rating = info["max_rating"]
        rank_title = info["rank_title"]
        max_rank_title = info["max_rank_title"]
        rating_derived = False
        if hist is not None:
            if rating is None:
                rating = hist["rating"]
                rating_derived = True
            if max_rating is None:
                max_rating = hist["max_rating"]
        # 称号同理：CF 不给就自己按分段推（负数 -> newbie），别让徽章位空着
        if not rank_title:
            rank_title = cf_rank_title(rating)
        if not max_rank_title:
            max_rank_title = cf_rank_title(max_rating)

        profile = {
            "platform": "cf",
            "uid": info["handle"],
            "handle": info["handle"],
            "rating": rating,
            "max_rating": max_rating,
            "rank_title": rank_title,
            "solved": solved_total,
            "week": week,
            "week_days": self.week_days,
            "avatar": info.get("avatar") or "",
            "avatar_fallback": info.get("avatar_fallback") or "",
            "extra": {
                "country": info.get("country"),
                "organization": info.get("organization"),
                "max_rank_title": max_rank_title,
                # 供文案区分「真实 rating」与「从战绩历史推出来的」；UI 暂不强制展示
                "rating_derived": rating_derived,
                "rated_contests": (hist or {}).get("contests"),
            },
            "url": f"https://codeforces.com/profile/{urllib.parse.quote(info['handle'])}",
            "fetched_at": now,
            "cached": False,
        }
        self._cput(key, profile, self.cache_ttl)
        return profile, None

    # ---------- 牛客 ----------

    def nk(self, uid: str) -> tuple[dict | None, str | None]:
        uid = (uid or "").strip()
        if not uid:
            return None, InvalidInput("请给出牛客用户 ID（主页链接里的那串数字），例如：/nk 886965097")
        if not uid.isdigit():
            return None, InvalidInput(
                "牛客只支持用数字 ID 查询（昵称搜索需要登录，暂不支持）。\n"
                "获取方式：打开 https://ac.nowcoder.com/acm/contest/rating-index ，\n"
                "点开自己的头像进入个人主页，地址栏 /profile/ 后面那串数字就是你的 ID。"
            )

        key = self.cache_key("nowcoder", uid)
        hit = self._cget(key)
        if hit:
            return {**hit, "cached": True}, None

        now = int(time.time())
        ref = f"{NK_BASE}/acm/contest/profile/{uid}"

        def _practice() -> tuple[bool, str]:
            _throttle("nk")
            return _http_text(
                f"{NK_BASE}/acm/contest/profile/{uid}/practice-coding",
                headers={"Accept": "text/html,*/*", "Referer": ref},
            )

        # 四个请求互不依赖，一次性错峰并发发出（详见 _parallel）
        got = self._parallel(
            {
                "basic": lambda: _get_nk_json(
                    f"/acm/contest/rating-basic?uid={uid}", referer=ref
                ),
                "history": lambda: _get_nk_json(
                    f"/acm/contest/rating-history?uid={uid}", referer=ref
                ),
                "joined": lambda: _get_nk_json(
                    f"/acm-heavy/acm/contest/profile/contest-joined-history?uid={uid}&page=1"
                    f"&onlyJoinedFilter=true&searchContestName=&onlyRatingFilter=false"
                    f"&contestEndFilter=true",
                    referer=ref,
                ),
                "practice": _practice,
            }
        )

        # ① 基础档案（拿不到就不用往下走了）
        data, err = got.pop("basic", (None, "请求未执行"))
        if err:
            return None, f"查不到该牛客用户：{err}"
        basic = parse_nk_basic(data)
        if not basic.get("handle") and basic.get("rating") is None:
            return None, InvalidInput(f"牛客上没有 ID 为 {uid} 的用户（或该用户未公开战绩）")

        # ② 最高 Rating
        max_rating = None
        data, err = got.pop("history", (None, "请求未执行"))
        if not err:
            try:
                max_rating = parse_nk_max_rating(data)
            except Exception:  # noqa: BLE001
                max_rating = None

        # ③ 总过题数（练习页 HTML）
        solved_total = None
        ok, text = got.pop("practice", (False, ""))
        if ok:
            try:
                solved_total = parse_nk_solved_total(text)
            except Exception:  # noqa: BLE001
                solved_total = None

        # ④ 本周战绩（含过题数/总题数/排名）
        week = []
        data, err = got.pop("joined", (None, "请求未执行"))
        if not err:
            try:
                week = parse_nk_joined(data, self.week_days, now)
            except Exception:  # noqa: BLE001
                week = []

        profile = {
            "platform": "nowcoder",
            "uid": uid,
            "handle": basic["handle"] or uid,
            "rating": basic["rating"],
            "max_rating": max_rating if max_rating is not None else basic["rating"],
            "rank_title": "",
            "rank": basic.get("rank"),
            "solved": solved_total,
            "week": week,
            "week_days": self.week_days,
            "avatar": basic.get("avatar") or "",
            "extra": {
                "school": basic.get("school"),
                "contest_count": basic.get("contest_count"),
                "color_level": basic.get("color_level"),
            },
            "url": ref,
            "fetched_at": now,
            "cached": False,
        }
        self._cput(key, profile, self.cache_ttl)
        return profile, None
