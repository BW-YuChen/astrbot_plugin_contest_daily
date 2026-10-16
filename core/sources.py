# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 数据源抓取层（S1）

数据源（均为 2026-10-15 本机实测结论）：
  cf       Codeforces 官方赛程接口
           - 「即将开始」对应的是 phase 字段值为 BEFORE（不是 UPCOMING，官方文档坑）
           - 开赛时间字段单位是【秒】
           - 本机必须走 Python urllib（curl 会 SSL connect error）
           - 直连与走代理均可达，**直连优先、系统代理兜底**（见 _openers：本机代理对
             部分站点回 502/000，直连更稳；但强制直连在「必须走代理才能上外网」的
             机器上会一条都抓不到，所以连接失败时再走一次代理）
  nowcoder 牛客赛事页
           - 无公开赛程 API，解析 HTML 里的 <div data-id="..." data-json="...">
           - data-json 属性经【两次】html.unescape 后是完整 JSON
           - 开赛时间字段单位是【毫秒】，归一化时必须 /1000
           - 页面按**顶级分类**分页（2026-10-16 实测页面顶部三个大 tab）：
               13 = 牛客系列赛（练习赛/周赛/挑战赛/小白月赛/集训营…）
               14 = 高校校赛（各校校赛 + 同步赛，如「XX大学程序设计竞赛(同步赛)」）
               15 = 自主创建赛（任何人自建，99+ 场）—— **默认不收录**
             data-json 里带分类 id 字段，可直接用来兜底过滤（不依赖页面参数）

设计约束：
  - 纯标准库、同步阻塞；AstrBot 插件侧用 asyncio.to_thread() 调用
  - 单源失败不中断整体，错误记入 meta.warnings，用上一次缓存兜底
  - 限速沿用会长助理 net.py 参数：串行 + 每次请求间隔 ≥2.0s
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 25
REQ_INTERVAL = 2.0  # 源之间的最小间隔（秒），限速合规

CF_API = "https://codeforces.com/api/contest.list?gym=false"
NK_INDEX = "https://ac.nowcoder.com/acm/contest/vip-index"

# 牛客「顶级分类」id（2026-10-16 从页面顶部三个大 tab 实测到的）
NK_CAT_SERIES = 13   # 牛客系列赛
NK_CAT_SCHOOL = 14   # 高校校赛（含各校同步赛）
NK_CAT_USER = 15     # 自主创建赛（任何人自建，数量极多）
# 默认收录：系列赛 + 高校校赛；自主创建赛不要（用户 2026-10-16 明确要求）
NK_CATEGORIES_DEFAULT = (NK_CAT_SERIES, NK_CAT_SCHOOL)

# 统一比赛模型字段：id / source / title / startAt(UTC秒) / durationMinutes / url / status / raw


def _now() -> int:
    return int(time.time())


def _make_opener(use_proxy: bool) -> urllib.request.OpenerDirector:
    """构造 opener。use_proxy=False 强制直连；True 读系统代理（环境变量 / 注册表）。

    直连的理由（原始注释保留）：本机代理对部分站点会回 502/000，实测直连更稳。
    """
    handler = urllib.request.ProxyHandler() if use_proxy else urllib.request.ProxyHandler({})
    op = urllib.request.build_opener(handler)
    op.addheaders = [
        ("User-Agent", UA),
        ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
    ]
    return op


def _openers() -> tuple:
    """抓取通道：**直连优先、系统代理兜底**。

    直连放第一条是因为本机代理对部分站点回 502/000，直连更稳；
    但强制直连在「必须走代理才能上外网」的机器上会**全军覆没**（赛程一条都抓不到），
    所以连接层失败时再走一次代理。机器没配代理时两条通道等价，只留一条。
    """
    direct = _make_opener(False)
    if urllib.request.getproxies():
        return (direct, _make_opener(True))
    return (direct,)


_OPENERS = _openers()


def _http_get(url: str, retries: int = 1) -> tuple[bool, str]:
    """抓取文本，失败重试 retries 次（每条通道各重试）。返回 (ok, text_or_err)。"""
    last = None
    for op in _OPENERS:
        for _ in range(retries + 1):
            try:
                req = urllib.request.Request(url)
                with op.open(req, timeout=TIMEOUT) as r:
                    return True, r.read().decode("utf-8", "ignore")
            except Exception as e:  # noqa: BLE001 网络异常统一降级
                last = f"{type(e).__name__}: {e}"
    return False, last or "unknown"


# ======================= Codeforces =======================

def parse_cf(payload: str, now_ts: int | None = None) -> list[dict]:
    """解析 CF contest.list 响应文本 -> 统一模型列表。纯函数，便于离线测试。"""
    now_ts = now_ts or _now()
    data = json.loads(payload)
    if data.get("status") != "OK":
        raise ValueError(f"cf status={data.get('status')}")

    out: list[dict] = []
    for c in data.get("result", []):
        if c.get("phase") != "BEFORE":  # 注意：不是 UPCOMING
            continue
        ts = c.get("startTimeSeconds")
        if not ts:
            continue
        dur = c.get("durationSeconds") or 0
        out.append(
            {
                "id": f"cf:{c.get('id')}",
                "source": "cf",
                "title": c.get("name", "").strip(),
                "startAt": int(ts),  # CF 本身就是秒
                "durationMinutes": round(dur / 60) if dur else None,
                "url": f"https://codeforces.com/contest/{c.get('id')}",
                "status": "upcoming" if ts > now_ts else "running",
                "raw": {"type": c.get("type"), "relativeTimeSeconds": c.get("relativeTimeSeconds")},
            }
        )
    out.sort(key=lambda x: x["startAt"])
    return out


def fetch_cf() -> tuple[list[dict], str | None]:
    ok, text = _http_get(CF_API)
    if not ok:
        return [], f"cf: {text}"
    try:
        return parse_cf(text), None
    except Exception as e:  # noqa: BLE001
        return [], f"cf: 解析失败 {e}"


# ======================= 牛客 =======================

RE_NK_ITEM = re.compile(r'data-id="(\d+)"[^>]*class="platform-item[^"]*"[^>]*data-json="([^"]*)"')


def _deep_unescape(s: str) -> str:
    """属性值被双重 HTML 转义（&amp;quot; -> &quot; -> "），循环解到稳定为止。"""
    prev = s
    for _ in range(3):
        cur = html.unescape(prev)
        if cur == prev:
            break
        prev = cur
    return prev


def parse_nowcoder(page_html: str, now_ts: int | None = None,
                   categories: tuple[int, ...] | None = None) -> list[dict]:
    """解析牛客 vip-index 页面 -> 统一模型列表。纯函数，便于离线测试。

    categories：只收录这些顶级分类（默认系列赛 13 + 高校校赛 14）。
    页面里没有 `topCategoryId` 字段时（老结构）一律放行，不做误杀。
    """
    return _parse_nk_items(page_html, now_ts, categories)[0]


def _parse_nk_items(
    page_html: str, now_ts: int | None, categories: tuple[int, ...] | None
) -> tuple[list[dict], int]:
    """返回 (收录的比赛, 正则命中条数)。

    命中条数是「页面结构有没有变」的判据 —— 收录结果可能因为比赛都结束了而为 0，
    但页面本身一定列着历史场次，所以命中 0 才说明解析规则该跟着页面改了。
    """
    now_ts = now_ts or _now()
    cats = tuple(categories) if categories else NK_CATEGORIES_DEFAULT
    out: list[dict] = []
    hits = 0
    for m in RE_NK_ITEM.finditer(page_html):
        hits += 1
        data_id, attrs = m.group(1), m.group(2)
        try:
            d = json.loads(_deep_unescape(attrs))
        except json.JSONDecodeError:
            continue  # 单条坏数据不中断
        # 顶级分类过滤：自主创建赛(15) 之类直接丢掉
        top_cat = d.get("topCategoryId")
        if top_cat is not None:
            try:
                if int(top_cat) not in cats:
                    continue
            except (TypeError, ValueError):
                pass
        start_ms = d.get("contestStartTime")
        end_ms = d.get("contestEndTime")
        if not start_ms:
            continue
        start_at = int(start_ms / 1000)  # 毫秒 -> 秒（关键归一化）
        end_at = int(end_ms / 1000) if end_ms else None
        dur_ms = d.get("contestDuration")
        if not dur_ms and end_at:
            dur_ms = (end_at - start_at) * 1000
        if start_at + (dur_ms or 0) / 1000 < now_ts - 3600:
            continue  # 结束超过 1 小时的不再收录
        status = "upcoming" if start_at > now_ts else "running"
        extra = d.get("settingInfo") or {}
        title = (d.get("contestName") or "").strip() or f"牛客比赛{data_id}"
        out.append(
            {
                "id": f"nowcoder:{data_id}",
                "source": "nowcoder",
                "title": title,
                "startAt": start_at,
                "durationMinutes": round(dur_ms / 60000) if dur_ms else None,
                "url": f"https://ac.nowcoder.com/acm/contest/{data_id}",
                "status": status,
                "raw": {
                    "type": d.get("type"),
                    "category": int(top_cat) if top_cat is not None else None,
                    "rated": extra.get("ratingStatus"),
                    "ratingUpperLimit": extra.get("ratingUpperLimit"),
                    "needCharge": extra.get("needCharge"),
                    "organizer": d.get("creatorName"),
                },
            }
        )
    out.sort(key=lambda x: x["startAt"])
    return out, hits


def fetch_nowcoder(
    categories: tuple[int, ...] | None = None
) -> tuple[list[dict], str | None]:
    """抓牛客：每个顶级分类一个请求（分类页之间同样限速 2s）。

    单分类失败不影响其他分类；「命中 0 条」才当成页面结构变化的错误上报。
    """
    cats = tuple(categories) if categories else NK_CATEGORIES_DEFAULT
    out: list[dict] = []
    errs: list[str] = []
    for i, cat in enumerate(cats):
        if i:
            time.sleep(REQ_INTERVAL)  # 同一站点的连续请求，保持限速
        ok, text = _http_get(f"{NK_INDEX}?topCategoryFilter={cat}")
        if not ok:
            errs.append(f"nowcoder[{cat}]: {text}")
            continue
        try:
            items, hits = _parse_nk_items(text, None, cats)
        except Exception as e:  # noqa: BLE001
            errs.append(f"nowcoder[{cat}]: 解析失败 {e}")
            continue
        if hits == 0:
            errs.append(
                f"nowcoder[{cat}]: 解析到 0 场（页面结构可能变化，请检查 samples 对比）"
            )
            continue
        out += items

    # 一场比赛不会同时属于两个分类，保险起见仍按 id 去重
    seen: set[str] = set()
    uniq: list[dict] = []
    for c in out:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    uniq.sort(key=lambda x: x["startAt"])
    return uniq, ("; ".join(errs) or None)


# ======================= 汇总 =======================

def fetch_all(
    enabled: tuple[str, ...] = ("cf", "nowcoder"),
    nk_categories: tuple[int, ...] | None = None,
) -> tuple[list[dict], dict]:
    """抓所有启用的源。返回 (contests_sorted, meta)。单源失败不中断。

    nk_categories：牛客收录的顶级分类（默认系列赛 13 + 高校校赛 14，不含自主创建赛 15）。
    """

    def _nk() -> tuple[list[dict], str | None]:
        return fetch_nowcoder(nk_categories)

    fetchers = {"cf": fetch_cf, "nowcoder": _nk}
    all_items: list[dict] = []
    sources_meta: dict = {}
    warnings: list[str] = []

    for name in enabled:
        fn = fetchers.get(name)
        if fn is None:
            warnings.append(f"未知数据源 {name}")
            continue
        t0 = time.time()
        try:
            items, err = fn()
        except Exception as e:  # noqa: BLE001 兜底：不让单源炸整体
            items, err = [], f"{name}: {type(e).__name__}: {e}"
        sources_meta[name] = {
            "ok": err is None,
            "count": len(items),
            "elapsed": round(time.time() - t0, 1),
            "lastSuccess": datetime.now(TZ_CN).isoformat(timespec="seconds") if err is None else None,
        }
        if err:
            warnings.append(err)
        all_items += items
        if name != enabled[-1]:
            time.sleep(REQ_INTERVAL)  # 源间限速

    # 全局去重（同 id 保留先到的）+ 排序
    seen: set[str] = set()
    uniq: list[dict] = []
    for c in all_items:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    uniq.sort(key=lambda x: (x["startAt"] is None, x["startAt"] or 0, x["id"]))

    meta = {
        "generatedAt": datetime.now(TZ_CN).isoformat(timespec="seconds"),
        "sources": sources_meta,
        "warnings": warnings,
    }
    return uniq, meta
