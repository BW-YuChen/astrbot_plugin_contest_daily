# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 战绩卡片图渲染层（S7）

/cf、/nk 查询结果渲染成一张卡片图（模仿常见 OJ 战绩卡版式，配色按平台
Rating 段位色走）。设计要点：

  * 主体：Rating / 最高 Rating / 本周战绩
    每场显示「日期 · 排名」+「过题数/总题数」（对齐文本模板的
    `✅7/9🚩排名：12`），**不显示 Rating 变化**（掉分/涨分一律不画）
  * 配色 = 用户当前 Rating 对应的平台段位色
      Codeforces: 官方段位色（灰/绿/青/蓝/紫/橙/红）
      牛客:       colorLevel 1~7+ 映射到同一套段位色（拿不到时按 rating 映射）
  * 头像（1.7.2 起简化）：CF 大图优先、小图兜底；牛客取平台头像地址
    （改图片尺寸参数拿更大更清晰的版本）；
    **所有候选并行取、谁先成用谁**（见 `_fetch_best`），失败候选不再拖累整张卡；
    平台头像**全部拿不到**（图床 503 / 用户没上传过头像 / 接口没给 / 超时 / 解码失败）
    时回退「首字母圆角块」，绝不影响出图。
    🚫 1.7.2（用户要求）**去掉了 QQ 头像兜底**：QQ 官方机器人的 openid 头像接口
    对任何参数都回 200 + 一张 40×40 默认灰图，
    既拿不到真头像、又要额外认灰图、还多两条配置项 —— 收益为零，直接删掉，
    回到「取不到就用首字母块」这一版行为（更简单、更可预期）。
  * 1.7.0（用户第 3 次报「CF 头像还是查不到」）：
    CF 的**大图头像是源站长期 503**（实测 4/8 可下，换 UA / Referer /
    出口 / 换 CDN 缓存键统统还是 503，响应头证明确已回源），
    而**小图 8/8 可用**。1.5.0 的「穿缓存 + 串行」把预算耗在必挂的
    大图上，让能用的小图没机会试 —— 1.7.0 改成并行 + 删掉穿缓存。
    顺带想明白一件事：「看着稳」的方案往往不是能拿到图，而是**失败时不出声**
    （长期落库 + 失败静默降级 + 永不重试）。我们能落地的是**头像拿到就长期留着**、
    **拿不到也照样出卡**。
    渲染完通过 `info["avatar_source"]` 上抛「头像来自哪里」
    （1.7.2 起只剩 `platform` / `placeholder` 两态）。
  * 依赖 Pillow：没装时 render_profile_card 返回 None，调用方回退纯文本模板，
    所以本模块属于「锦上添花」，炸了不能影响主流程 —— 全函数无 raise
  * 输出文件按 平台+用户名 固定命名（同名覆盖），不会无限堆积
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

# ------------------------------------------------------------------ 平台配色

# Codeforces 官方段位色（按当前 rating 取档）
_CF_TIERS = (
    (1200, "#808080"),  # newbie        灰
    (1400, "#008000"),  # pupil         绿
    (1600, "#03A89E"),  # specialist    青
    (1900, "#0000FF"),  # expert        蓝
    (2100, "#AA00AA"),  # candidate     紫
    (2400, "#FF8C00"),  # master/IM     橙
    (10**9, "#FF0000"),  # GM 及以上     红
)

# 牛客 colorLevel -> 同一套段位色（1 灰 2 绿 3 青 4 蓝 5 紫 6 橙 7+ 红）
_NK_LEVEL_COLORS = {
    1: "#808080",
    2: "#008000",
    3: "#03A89E",
    4: "#0000FF",
    5: "#AA00AA",
    6: "#FF8C00",
}

PLATFORM_WORDMARK = {"cf": "Codeforces", "nowcoder": "牛客竞赛"}
PLATFORM_HOST = {"cf": "codeforces.com", "nowcoder": "ac.nowcoder.com"}


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def _rgb(c) -> tuple:
    return c if isinstance(c, tuple) else _hex(c)


def _mix(c, other: str, t: float) -> tuple:
    """c 向 other 混合 t（0=c 原色，1=other）。"""
    r, g, b = _rgb(c)
    or_, og, ob = _hex(other)
    return (int(r + (or_ - r) * t), int(g + (og - g) * t), int(b + (ob - b) * t))


def _hexify(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*_rgb(c))


def _tier_color(rating) -> str:
    if rating is None:
        return "#808080"
    try:
        r = int(float(rating))
    except (TypeError, ValueError):
        return "#808080"
    for up, color in _CF_TIERS:
        if r < up:
            return color
    return "#FF0000"


def theme_color(profile: dict) -> str:
    """按平台决定主题色（用户 Rating 段位色）。"""
    if profile.get("platform") == "nowcoder":
        lv = (profile.get("extra") or {}).get("color_level")
        try:
            lv = int(lv) if lv is not None else None
        except (TypeError, ValueError):
            lv = None
        if lv is not None:
            return _NK_LEVEL_COLORS.get(lv, "#FF0000" if lv >= 7 else "#808080")
    return _tier_color(profile.get("rating"))


def rating_change_color(v) -> tuple:
    """Rating 变化配色：涨绿跌红（Codeforces 口径）。

    卡片图里已经不画 Rating 变化（按用户要求只显示过题/总题数），
    这个函数留给「以后想画回来」或外部复用，保留成本极低。
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return _hex("#8a94a6")
    return _hex("#00a152") if v > 0 else (_hex("#e5484d") if v < 0 else _hex("#8a94a6"))


# ------------------------------------------------------------------ 字体

_FONT_CACHE: dict = {}
# 字体实例是有内存代价的（实测 msyh.ttc 每个 (字号,粗体) 约 0.3 MB），而
# _fit_font 会按 2 的步长摸出一堆字号，不加限制在长期运行里能攒到几十个
# （= 十几 MB）。封顶后按「最早创建的先淘汰」，2 GB 内存的机器上更稳。
_FONT_CACHE_MAX = 24
_FONT_DIRS = [
    r"C:\Windows\Fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
]

_CANDIDATES = {
    # (bold, weight_score)：越靠前越优先
    True: (
        "msyhbd.ttc",
        "msyhbd.ttf",
        "Dengb.ttf",
        "simhei.ttf",
        "NotoSansCJK-Bold.ttc",
        "NotoSansCJKtc-Bold.otf",
        "SourceHanSansCN-Bold.otf",
        "wqy-microhei.ttc",
        "PingFang.ttc",
    ),
    False: (
        "msyh.ttc",
        "msyhl.ttc",
        "Deng.ttf",
        "simhei.ttf",
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJKtc-Regular.otf",
        "SourceHanSansCN-Regular.otf",
        "wqy-microhei.ttc",
        "PingFang.ttc",
    ),
}


def _find_font_file(bold: bool) -> str | None:
    try:
        from glob import glob

        for name in _CANDIDATES[bold]:
            for d in _FONT_DIRS:
                hits = sorted(glob(os.path.join(d, "**", name), recursive=True))
                if hits:
                    return hits[0]
        # 兜底：字体目录里随便找一个能用的中文字体
        for d in _FONT_DIRS:
            for pat in ("*.tt[cf]", "*.otf"):
                for hit in sorted(glob(os.path.join(d, "**", pat), recursive=True)):
                    if re.search(r"(cjk|han|hei|song|wqy|noto|pingfang|deng|msyh)", hit, re.I):
                        return hit
    except Exception:  # noqa: BLE001
        pass
    return None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = _find_font_file(bold)
    f = None
    if path:
        try:
            f = ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            f = None
    if f is None:
        try:
            f = ImageFont.load_default()
        except Exception:  # noqa: BLE001
            f = None
    if len(_FONT_CACHE) >= _FONT_CACHE_MAX:
        # dict 保持插入顺序 -> 弹掉最早创建的那个（缓存上限内无感）
        _FONT_CACHE.pop(next(iter(_FONT_CACHE)), None)
    _FONT_CACHE[key] = f
    return f


# ------------------------------------------------------------------ 头像

_AVATAR_TTL = 7 * 86400  # 下载成功后缓存 7 天
# 候选全失败时，多久之内不再重试（别反复捶 CDN）。
# 15 分钟：图床故障恢复后，用户最多等这么久就能自动拿回头像；再短就是把
# 「上游 503、只能等它自己好」变成反复空跑。
_AVATAR_FAIL_TTL = 900

# ---- 1.7.0 重设：并发取图，超时按「候选」算而不是按「整卡」算 -----------------
#
# 实测（2026-10-16，用户第 3 次报「CF 头像还是查不到」）：
#
#   | CF 候选      | 尺寸   | 成功率 | 说明                           |
#   |--------------|--------|--------|--------------------------------|
#   | 大图地址     | ~300px | 4/8    | 源站大面积 503，且长期不恢复   |
#   | 小图地址     | 76px   | **8/8**| 稳定可用                       |
#
# 病根不在「候选不够」，而在**串行取图 + 失败的候选把预算吃光**：大图先
# 失败（1.1s）、再打一枪穿缓存（2.5s），整卡 5s 预算就没了 —— 后面**本来一定
# 能拿到的小图根本没机会试**。1.7.0 改成并行取所有候选、谁快用谁。
_AVATAR_TIMEOUT = 6.0  # 单个候选单次请求的超时（经代理取 CF 头像实测 1~3s）
_AVATAR_FAST_WINDOW = 2.0  # 高优先级候选的「让位窗口」，见 _fetch_best
_AVATAR_TOTAL_BUDGET = 8.0  # 整卡所有候选的总预算（并行发起，不是各候选叠加）
_AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB 足够，再大就是坏数据

_AVATAR_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _avatar_openers() -> tuple:
    """头像下载的候选通道，**代理优先、直连兜底**。

    🔑 为什么必须两通道（2026-10-16 实测本机，用户报「爬不到 CF 头像了」）：

      | 目标                     | 系统代理      | 直连        |
      |--------------------------|---------------|-------------|
      | CF 公开接口              | 1.3s OK       | 0.7s OK     |
      | **CF 头像 CDN**          | **6.4s OK**   | **9.2s 失败** |
      | 牛客站点                 | 0.2s OK       | 0.1s OK     |

    CF 的头像 CDN 挂在 Cloudflare 上，**「必须走代理才能上外网」的机器上直连必定
    超时**。而这里以前写死 `ProxyHandler({})`（强制直连、不读系统代理），
    在那个环境里就等于永远拿不到 CF 头像 —— 用户看到的就是卡片上的灰色占位图。
    机器**没配**代理时 `ProxyHandler()`（默认）与直连等价，所以代理放第一条不会
    拖慢国内直连可达的目标。反过来 CF API / 牛客直连更快，那两个模块就不用换序。
    """
    if urllib.request.getproxies():
        return (
            urllib.request.build_opener(urllib.request.ProxyHandler()),
            urllib.request.build_opener(urllib.request.ProxyHandler({})),
        )
    # 没配代理时两条通道完全等价，只留一条 —— 别把同一件事做两遍（失败时要等两倍超时）
    return (urllib.request.build_opener(urllib.request.ProxyHandler({})),)


_AVATAR_OPENERS = _avatar_openers()


def _normalize_url(u) -> str:
    """补协议、去空白。CF 部分数据里给的是 `//userpic...` 这种协议相对地址。"""
    u = str(u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u


def _nk_bigger(u: str) -> str:
    """牛客头像挂在阿里云 OSS 上，改 `x-oss-process` 的尺寸参数就能直接拿到
    更大的图（默认接口给的是 100×100，改成 240×240 仍是 22 KB）。"""
    if "images.nowcoder.com" not in u:
        return ""
    return u.split("?", 1)[0] + "?x-oss-process=image/resize,m_mfit,h_240,w_240"


def avatar_candidates(profile: dict) -> list:
    """按优先级给出头像候选地址（先大后小）。

    ⚠️ CF 头像为什么「时有时无」（2026-10-16 两轮实测，8 个真实账号结论一致）：

      * **大图地址**（约 300px，画质好）只有 **4/8** 能下 —— 它是 CF 图床上
        **长期挂着的那个服务**，不是我们这边的网络问题：换 UA、加 Referer、
        换代理出口、加 `?v=` 换缓存键（响应头显示确实回源了，证明
        确实回源了）**统统还是 503**。所以 1.5.0 那套「CDN 缓存了陈旧 503、
        穿一次就能拿到」的判断不成立，已在 1.7.0 删掉。
      * **小图地址**（约 76px，小，实测 69×80）**8/8** 全部可下 —— 这是唯一可靠的
        CF 真头像源。卡片头像显示 128px，放大后略有软化，但**先有再说**。
      * 另外 CF 图床的其它尺寸路径实测全是 503，别试。

    所以两个地址都给、先大后小，由 `_fetch_best` **并行**去取：谁先成用谁，
    大图挂掉完全不会拖累小图。牛客同理，只是它是换图片尺寸参数。
    """
    platform = profile.get("platform") or "cf"
    urls: list = []
    for key in ("avatar", "avatar_fallback"):
        u = _normalize_url(profile.get(key) or "")
        if not u:
            continue
        if platform == "nowcoder":
            big = _nk_bigger(u)
            if big and big != u:
                urls.append(big)
        urls.append(u)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _read_once(opener, url: str, timeout: float) -> tuple[bytes, int]:
    """单次请求，返回 (字节, HTTP 状态码)。状态码 0 表示超时/连接被拒这类软失败。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _AVATAR_UA})
        with opener.open(req, timeout=timeout) as r:
            return r.read(_AVATAR_MAX_BYTES), 200
    except urllib.error.HTTPError as e:
        return b"", int(getattr(e, "code", 0) or 0)
    except Exception:  # noqa: BLE001
        return b"", 0


def _fetch_image_bytes(url: str, budget: float | None = None) -> bytes:
    """取一张头像。失败返回 b""。

    按 `_AVATAR_OPENERS` 的顺序换通道试（代理 -> 直连）：

    * 超时 / 连接被拒（软失败）**换通道** —— 这正是「直连被墙、代理能通」的典型表现；
    * HTTP 403/404 这类硬失败不换通道 —— 地址本身不可用，换出口只是白等；
    * 503 不再做任何「穿缓存重试」：1.7.0 实测给 503 的地址加 `?v=` 后响应头显示
      确实回源了，**源站照样回 503**。那一枪纯粹白等，
      还会把整卡预算吃光 —— 直接认输，把时间让给别的候选才是对的。

    budget 是本张图的时间预算（秒）。每个通道只拿**自己那一份**：旧实现让慢通道
    （实测经代理取 CF 头像 1~3s，糟糕时 6s+）独占全部预算直到超时，后面那条能通的
    通道根本没机会跑 —— 这是 1.5.0 另一个隐性坑。
    """
    limit = _AVATAR_TIMEOUT if budget is None else max(1.0, min(float(budget), _AVATAR_TIMEOUT))
    deadline = time.monotonic() + limit
    total = max(1, len(_AVATAR_OPENERS))
    for idx, opener in enumerate(_AVATAR_OPENERS):
        remain = deadline - time.monotonic()
        if remain <= 0.5:
            break
        share = remain if idx == total - 1 else max(1.0, remain / (total - idx))
        data, code = _read_once(opener, url, min(remain, share))
        if data:
            return data
        if code:  # 有 HTTP 状态码（含 503/404）：地址本身的问题，换通道没意义
            return b""
        # code == 0：超时 / 连不上这类软失败 -> 换下一个通道（代理 <-> 直连）再试
    return b""


def _looks_like_markup(data: bytes) -> bool:
    """响应体像 HTML/JSON 而不是图片。

    CF 图床故障时**状态码也可能是 200** 却回一页错误 HTML；直接丢给 Pillow 会因为
    「cannot identify image file」落到同一处兜底，但判别一下能少一次解码尝试、
    日志里也能说清是「图床回了网页」而不是「图片坏了」。
    """
    head = bytes(data[:32]).lstrip().lower()
    return head.startswith(
        (b"<!doctype", b"<html", b"<head", b"<?xml", b"{\"", b"{\n", b"{ ")
    )


def _first_ok(futs) -> bytes:
    """按**优先级**（futs 的顺序）取第一个已完成且内容可用的候选。"""
    for f in futs:
        if not f.done():
            continue
        try:
            d = f.result()
        except Exception:  # noqa: BLE001 单个候选炸了不影响别的候选
            continue
        if d and not _looks_like_markup(d):
            return d
    return b""


def _fetch_best(urls: list) -> bytes:
    """**并行**取所有候选，返回优先级最高的那张可用图；都失败返回 b""。

    两段等待（1.7.0，配合 `_AVATAR_FAST_WINDOW` / `_AVATAR_TOTAL_BUDGET`）：

      ① 先等一个「让位窗口」（2s）：窗口内完成的候选里按优先级挑 —— CF 的
         大图只要能下（约 1.1s）就会赢，拿到画质最好的那张；
      ② 窗口内一张都没拿到，再等到总预算（8s），仍然是按优先级挑已完成的。

    于是「大图成功」与「大图 503、小图顶上」两种情况的耗时都
    只由**最快的候选**决定（实测都约 1.2s），失败的候选不再拖垮整张卡 ——
    这正是 1.5.0 串行版做不到的事（那几个必挂的候选把 5s 预算吃光了）。
    """
    from concurrent.futures import ThreadPoolExecutor, wait

    pool = ThreadPoolExecutor(max_workers=min(len(urls), 4))
    try:
        t0 = time.monotonic()
        futs = [pool.submit(_fetch_image_bytes, u) for u in urls]
        _done, pending = wait(futs, timeout=_AVATAR_FAST_WINDOW)
        pic = _first_ok(futs)
        if pic is None and pending:
            left = _AVATAR_TOTAL_BUDGET - (time.monotonic() - t0)
            if left > 0:
                wait(pending, timeout=left)
            pic = _first_ok(futs)
        return pic or b""
    finally:
        pool.shutdown(wait=False)


def _load_avatar(urls, cache_dir: str):
    """下载头像并返回 RGB Image；任何失败返回 None。

    urls 可以是单个地址，也可以是按优先级排列的候选列表（见 avatar_candidates）。
    磁盘缓存：成功存 `<key>.img`（7 天）；候选全失败则写一个空的 `<key>.miss`
    标记（15 分钟内不再尝试 —— 图床恢复后自动复原）。key = 全部候选地址的哈希，
    所以换了头像地址会自动重新下载，不会沿用旧图。

    1.7.0：取图交给 `_fetch_best` **并行**做，这里不再自己串行循环（原因见该函数）。
    """
    if isinstance(urls, str):
        urls = [urls]
    urls = [u for u in (urls or []) if u]
    if not urls:
        return None
    try:
        from PIL import Image, ImageOps

        os.makedirs(cache_dir, exist_ok=True)
        key = hashlib.md5("|".join(urls).encode()).hexdigest()
        good = os.path.join(cache_dir, key + ".img")
        miss = os.path.join(cache_dir, key + ".miss")

        if os.path.exists(good) and time.time() - os.path.getmtime(good) < _AVATAR_TTL:
            with open(good, "rb") as fh:
                data = fh.read()
        elif os.path.exists(miss) and time.time() - os.path.getmtime(miss) < _AVATAR_FAIL_TTL:
            return None
        else:
            data = _fetch_best(urls)
            if not data:
                with open(miss, "wb") as fh:  # 空标记，只看 mtime
                    fh.write(b"")
                return None
            with open(good, "wb") as fh:
                fh.write(data)
            try:
                os.remove(miss)  # 之前失败过、这次成了：清掉失败标记
            except OSError:
                pass

        img = Image.open(io.BytesIO(data)).convert("RGB")
        resample = getattr(Image, "Resampling", Image).LANCZOS
        return ImageOps.fit(img, (256, 256), resample)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 绘图工具

def _rrect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _ellipsis(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _fit_font(draw, text: str, max_w: int, size: int, bold: bool, min_size: int = 20):
    size = int(size)
    while size > min_size and draw.textlength(text, font=_font(size, bold)) > max_w:
        size -= 2
    return _font(size, bold)


def _check_mark(draw, x: float, y: float, size: int, color, width: int = 3) -> None:
    """画一个矢量对勾（≈ 文本里的 ✅，但不依赖字体是否含 U+2713 字形）。

    字形缺失时 Pillow 会画出「豆腐块」，所以这里用两条线段自己画，
    跨 Windows / Linux 字体都稳。
    """
    draw.line(
        [(x, y + size * 0.50), (x + size * 0.36, y + size * 0.88)], fill=color, width=width
    )
    draw.line(
        [(x + size * 0.36, y + size * 0.88), (x + size, y + size * 0.08)], fill=color, width=width
    )


def _solved_ratio(item: dict) -> str:
    """单场比赛的「过题数/总题数」，如 `3/7`。

    两个数都有 -> `3/7`；只有过题数 -> `3 题`；都拿不到 -> 空串（这一栏不画）。
    过题数/总题数的口径见 profile.py：分别是该场「比赛时段内 AC 的不同题目数」
    与题库映射出的该场题目总数（牛客那边直接给这两个数）。
    """
    def _n(v):
        if v is None:
            return None
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else f"{f:g}"
        except (TypeError, ValueError):
            return None

    s, t = _n(item.get("solved")), _n(item.get("total"))
    if s is not None and t is not None:
        return f"{s}/{t}"
    if s is not None:
        return f"{s} 题"
    return ""


# ------------------------------------------------------------------ 主渲染

def render_profile_card(
    profile: dict,
    out_dir: str,
    log=None,
    info: dict | None = None,
) -> str | None:
    """渲染战绩卡片图，返回图片路径；失败/未装 Pillow 返回 None（绝不抛异常）。

    profile 需要 main.py 里 ProfileFetcher 产出的字段：
      platform / handle / rating / max_rating / rank_title / week / week_days
      avatar（主头像，CF=大图地址 / 牛客=平台头像地址）
      avatar_fallback（备用头像，CF=avatar 小图；可缺省）
      每条 week：name / start_at / rank / solved / total（rating_change 不再画）

    平台头像取不到时**直接用首字母圆角块**（1.7.2 起不再有 QQ 头像那一拨，
    用户要求「查不到头像就降级为名字首字母，像前面某一版那样」）。
    log：可选的日志回调（card.py 不依赖 astrbot，日志靠回调传出去）。
    info：可选的 dict，渲染完会写入 `avatar_source`，取值
      `platform`（拿到了平台头像）/ `placeholder`（只剩首字母块）——
      调用方据此判断这张卡是不是次品（见 main.py 的 `_ephemeral_no_avatar`）。
    """
    try:
        return _render(profile, out_dir, log, info)
    except Exception as e:  # noqa: BLE001
        # 吞异常是为了「出图失败也不能影响主流程」，但**必须留下日志** ——
        # 不然调试时只看到「返回 None」，今天就被这个坑掉一次。
        _log(log, f"卡片渲染失败（已回退文本）：{type(e).__name__}: {e}")
        return None


def _log(log, msg: str) -> None:
    if log is None:
        return
    try:
        log(msg)
    except Exception:  # noqa: BLE001
        pass


def _render(
    profile: dict,
    out_dir: str,
    log=None,
    info: dict | None = None,
) -> str | None:
    from PIL import Image, ImageDraw

    platform = profile.get("platform") or "cf"
    theme = _hex(theme_color(profile))
    dark = _mix(theme, "#000000", 0.25)
    handle = profile.get("handle") or profile.get("uid") or "?"
    week = profile.get("week") or []
    week_days = profile.get("week_days", 7)
    rating = profile.get("rating")
    max_rating = profile.get("max_rating")
    rank_title = (profile.get("rank_title") or "").strip()
    extra = profile.get("extra") or {}

    W = 640
    PAD = 32

    def _num(v):
        if v is None:
            return None
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else f"{f:g}"
        except (TypeError, ValueError):
            return str(v)

    rating_s = _num(rating) or "暂无"
    max_s = _num(max_rating)

    # ---- 版式尺寸计算 ----
    wordmark_h = 30
    header_y = 78
    header_h = 40 + 128 + 14 + 46 + 16 + 42 + 26  # 头像上探 + 头像 + 间距 + 昵称 + 间距 + 徽章 + 底距
    header_y1 = header_y + header_h
    body_y = header_y1 + 18
    rows = week[:4]
    row_h = 56
    body_pad = 26
    body_w = W - PAD * 2
    left_w = 206
    title_h = 40
    if rows:
        content_h = title_h + len(rows) * row_h + 10
    else:
        content_h = title_h + 52
    body_h = max(212, body_pad + content_h + body_pad)
    body_y1 = body_y + body_h
    footer_y = body_y1 + 16
    H = footer_y + 24 + 22

    # ---- 背景渐变（主题色的深浅两档）----
    top = _mix(theme, "#ffffff", 0.30)
    bot = _mix(theme, "#ffffff", 0.58)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / max(1, H - 1)
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)),
        )
    # 左上一枚装饰大圆（更浅的主题色，柔和一点）
    deco = _mix(theme, "#ffffff", 0.72)
    d.ellipse((-90, -110, 170, 170), fill=deco)

    # ---- 顶部右侧平台字标 ----
    wm = PLATFORM_WORDMARK.get(platform, platform)
    d.text((W - PAD, 30), wm, font=_font(26, True), fill=dark, anchor="rm")

    # ---- 头部主题色卡片 ----
    _rrect(d, (PAD, header_y, W - PAD, header_y1), 30, theme)

    # 头像（上探出卡片顶边）—— 平台头像取不到就画首字母块（1.7.2 起没有 QQ 兜底）
    av_size = 128
    av_x = (W - av_size) // 2
    av_y = header_y - 40
    avatar_img = _load_avatar(
        avatar_candidates(profile), os.path.join(out_dir, "avatar_cache")
    )
    if info is not None:
        # 交给调用方决定这张卡的去留（1.5.0 用户要求：没拿到平台头像的卡不缓存、发完即删）
        info["avatar_source"] = "platform" if avatar_img is not None else "placeholder"
        info["platform"] = profile.get("platform") or "cf"
        info["handle"] = str(profile.get("handle") or profile.get("uid") or "")
    if avatar_img is not None:
        mask = Image.new("L", (av_size * 4, av_size * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, av_size * 4, av_size * 4), radius=28 * 4, fill=255)
        mask = mask.resize((av_size, av_size))
        # 白描边
        d.rounded_rectangle(
            (av_x - 4, av_y - 4, av_x + av_size + 4, av_y + av_size + 4),
            radius=30,
            fill=_mix(theme, "#ffffff", 0.85),
        )
        img.paste(avatar_img.resize((av_size, av_size)), (av_x, av_y), mask)
        d.rounded_rectangle(
            (av_x, av_y, av_x + av_size, av_y + av_size), radius=28, outline=(255, 255, 255), width=2
        )
    else:
        init = handle[:1].upper()
        f_init = _fit_font(d, init, 60, 56, True, 30)
        d.rounded_rectangle((av_x, av_y, av_x + av_size, av_y + av_size), radius=28,
                            fill=_mix(theme, "#ffffff", 0.22))
        d.text((av_x + av_size // 2, av_y + av_size // 2), init, font=f_init,
               fill=(255, 255, 255), anchor="mm")

    # 昵称（自适应缩字号）
    name_y = av_y + av_size + 26
    f_name = _fit_font(d, handle, W - PAD * 2 - 60, 38, True, 22)
    d.text((W // 2, name_y + 22), handle, font=f_name, fill=(255, 255, 255), anchor="mm")

    # 徽章行：MaxRating + 段位/学校
    badges = []
    if max_s:
        badges.append(f"MaxRating {max_s}")
    badge2 = rank_title or (extra.get("school") if isinstance(extra, dict) else None) or ""
    if badge2:
        badges.append(badge2)
    if badges:
        pill_h = 42
        gap = 16
        widths = []
        f_pill = _font(21, True)
        for b in badges:
            widths.append(d.textlength(b, font=f_pill) + 36)
        total = sum(widths) + gap * (len(badges) - 1)
        bx = (W - total) / 2
        by = name_y + 58
        pill_bg = _mix(theme, "#ffffff", 0.22)
        for b, bw in zip(badges, widths):
            _rrect(d, (bx, by, bx + bw, by + pill_h), pill_h // 2, pill_bg)
            d.text((bx + bw / 2, by + pill_h / 2), b, font=f_pill, fill=(255, 255, 255), anchor="mm")
            bx += bw + gap

    # ---- 主体白色卡片 ----
    _rrect(d, (PAD, body_y, W - PAD, body_y1), 26, (255, 255, 255))

    # 左列：Rating 大数字
    lx = PAD + 30
    d.text((lx, body_y + body_pad + 4), "Rating", font=_font(22, False), fill=_hex("#8a94a6"),
           anchor="la")
    f_rating = _fit_font(d, rating_s, left_w - 30, 58, True, 34)
    d.text((lx, body_y + body_pad + 46), rating_s, font=f_rating, fill=theme, anchor="la")
    sub_y = body_y + body_pad + 46 + 72
    d.text((lx, sub_y), "最高", font=_font(19, False), fill=_hex("#8a94a6"), anchor="la")
    d.text((lx + 46, sub_y), max_s or "—", font=_font(22, True), fill=dark, anchor="la")

    # 分隔线
    div_x = PAD + 30 + left_w
    d.line([(div_x, body_y + body_pad), (div_x, body_y1 - body_pad)], fill=_hex("#e8ecf2"), width=2)

    # 右列：本周战绩
    rx = div_x + 26
    rw = W - PAD - 24 - rx
    d.text(
        (rx, body_y + body_pad),
        f"本周战绩 · 近 {week_days} 天",
        font=_font(22, True),
        fill=dark,
        anchor="la",
    )

    if rows:
        ry = body_y + body_pad + title_h
        f_nm = _font(21, True)
        f_sub = _font(18, False)
        f_num = _font(20, True)
        for it in rows:
            name = str(it.get("name") or "未命名比赛")
            start = it.get("start_at")
            date_s = datetime.fromtimestamp(int(start), TZ).strftime("%m-%d") if start else "--"
            rank = it.get("rank")
            # 第一行：比赛名（截断）
            d.text((rx, ry), _ellipsis(d, name, f_nm, rw), font=f_nm, fill=_hex("#333b47"),
                   anchor="la")
            # 第二行左：日期 · 排名
            d.text((rx, ry + 28), f"{date_s} · 排名 {_num(rank) or '—'}", font=f_sub,
                   fill=_hex("#8a94a6"), anchor="la")
            # 第二行右：过题数 / 总题数（如 3/7）—— 对勾是矢量画的，不依赖字体字形
            solved_s = _solved_ratio(it)
            if solved_s:
                tw = d.textlength(solved_s, font=f_num)
                ck = 15
                x0 = rx + rw - (tw + ck + 7)
                d.text((rx + rw, ry + 27), solved_s, font=f_num, fill=theme, anchor="ra")
                _check_mark(d, x0, ry + 28, ck, theme)
            ry += row_h
        if len(week) > 4:
            d.text((rx, ry + 2), f"… 还有 {len(week) - 4} 场更早的", font=_font(17, False),
                   fill=_hex("#8a94a6"), anchor="la")
    else:
        d.text(
            (rx + rw / 2, body_y + body_pad + title_h + 18),
            "本周还没有打比赛，保持手感哦~",
            font=_font(19, False),
            fill=_hex("#8a94a6"),
            anchor="mm",
        )

    # ---- 底部小字 ----
    now = datetime.now(TZ)
    host = PLATFORM_HOST.get(platform, "")
    d.text(
        (W // 2, footer_y + 10),
        f"数据来自 {host} · {now.strftime('%m-%d %H:%M')} 生成",
        font=_font(16, False),
        fill=_mix(dark, "#ffffff", 0.35),
        anchor="mm",
    )

    # ---- 保存 ----
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", str(profile.get("handle") or profile.get("uid") or "user"))[:32]
    path = os.path.join(out_dir, f"{platform}_{safe or 'user'}.png")
    img.save(path, "PNG")
    return path
