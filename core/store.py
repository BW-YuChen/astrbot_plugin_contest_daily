# -*- coding: utf-8 -*-
"""
竞赛日报机器人 · 存储层

落盘文件（AstrBot 规范：持久化数据放 data/ 目录，不放插件目录）：
  contests_cache.json  赛程缓存（抓取层写，日报/提醒只读，不联网）
  subscriptions.json   群订阅表 {umo: {...}}   umo = AstrBot 会话唯一 ID
  sent_log.json        去重表（提醒幂等 + 日报当日幂等 + 日报时间指纹 dailyTime）
  profile_cache.json   用户战绩查询缓存（/cf /nk，含带 TTL 的单场 standings）
  group_aliases.json   会话备注映射表 {umo: "计科2201群"}（面板/指令自定义，方便选群）
  quotes.json          名言库的自定义条目与屏蔽名单（面板可视化管理，1.6.0）
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

SCHEMA_VERSION = 1
# 会话备注的长度上限：备注只是给人看的标签（面板里选择用），太长会把界面撑坏
ALIAS_MAX_LEN = 24

_lock = threading.Lock()


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default  # 坏文件按空处理，不抛异常炸插件


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子替换，断电不留半截文件


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.cache_file = os.path.join(data_dir, "contests_cache.json")
        self.subs_file = os.path.join(data_dir, "subscriptions.json")
        self.sent_file = os.path.join(data_dir, "sent_log.json")
        self.profile_file = os.path.join(data_dir, "profile_cache.json")
        self.alias_file = os.path.join(data_dir, "group_aliases.json")
        self.quote_file = os.path.join(data_dir, "quotes.json")

    # ---------- 赛程缓存 ----------

    def save_contests(self, contests: list[dict], meta: dict) -> None:
        with _lock:
            _write_json(
                self.cache_file,
                {"schemaVersion": SCHEMA_VERSION, **meta, "contests": contests},
            )

    def load_contests(self) -> tuple[list[dict], dict]:
        d = _read_json(self.cache_file, {})
        return d.get("contests", []), {k: v for k, v in d.items() if k != "contests"}

    # ---------- 订阅表 ----------

    def list_subscriptions(self) -> dict[str, dict]:
        return _read_json(self.subs_file, {})

    def subscribe(self, umo: str, info: dict | None = None) -> bool:
        """返回是否为新订阅（已订阅返回 False）。"""
        with _lock:
            subs = _read_json(self.subs_file, {})
            if umo in subs:
                return False
            subs[umo] = {"addedAt": info or {}, **(info or {})}
            _write_json(self.subs_file, subs)
            return True

    def unsubscribe(self, umo: str) -> bool:
        with _lock:
            subs = _read_json(self.subs_file, {})
            if umo not in subs:
                return False
            del subs[umo]
            _write_json(self.subs_file, subs)
            return True

    # ---------- 会话备注映射表 ----------
    #
    # 用户要求（1.3.0）：「将群 ID 给定一个映射表，可以自定义绑定备注，这样选择方便」。
    # 起因是 umo 长这样 —— `qq_official:GroupMessage:6B1F...`，在面板里一眼看不出是哪个群，
    # 想把「测试推送」只发给某一个群时根本没法选。
    #
    # 为什么**单独一个文件**而不是塞进 subscriptions.json：
    #   * 退订/重订阅不该把辛苦起的备注弄丢；
    #   * 面板和 `/群备注` 指令都要频繁改它，独立文件写盘小、互不干扰（订阅表另有用途）。
    # 结构：{"schemaVersion": 1, "aliases": {"<umo>": "计科2201群"}}

    def get_aliases(self) -> dict[str, str]:
        """返回 {umo: 备注}。坏文件 / 脏值一律跳过，绝不抛异常。"""
        d = _read_json(self.alias_file, {})
        raw = d.get("aliases") if isinstance(d, dict) else None
        if not isinstance(raw, dict):
            # 兼容「直接就是一张裸表」的手写文件
            raw = d if isinstance(d, dict) else {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k] = v.strip()
        return out

    def set_alias(self, umo: str, alias: str) -> str:
        """设置 / 清除某个会话的备注，返回**生效后**的备注（空串 = 已清除）。"""
        umo = str(umo or "").strip()
        if not umo:
            return ""
        text = " ".join(str(alias or "").split())[:ALIAS_MAX_LEN]
        with _lock:
            d = _read_json(self.alias_file, {})
            if not isinstance(d, dict):
                d = {}
            raw = d.get("aliases")
            if not isinstance(raw, dict):
                raw = {}
            if text:
                raw[umo] = text
            else:
                raw.pop(umo, None)
            d = {"schemaVersion": SCHEMA_VERSION, "aliases": raw}
            _write_json(self.alias_file, d)
        return text

    # ---------- 去重表 ----------

    def load_sent(self) -> dict:
        return _read_json(
            self.sent_file, {"schemaVersion": SCHEMA_VERSION, "reminded": {}, "dailySent": {}}
        )

    def mark_reminded(self, contest_id: str, umo: str, start_at: int) -> None:
        with _lock:
            d = self.load_sent()
            d["reminded"][f"{contest_id}|{umo}"] = start_at
            _write_json(self.sent_file, d)

    def has_reminded(self, contest_id: str, umo: str) -> bool:
        return f"{contest_id}|{umo}" in self.load_sent().get("reminded", {})

    def reminded_umos(self, contest_id: str) -> set[str]:
        """某场比赛已经提醒过哪些会话（提醒计划里显示「已提醒 2/3」用）。"""
        reminded = self.load_sent().get("reminded")
        if not isinstance(reminded, dict):
            return set()
        prefix = f"{contest_id}|"
        return {k[len(prefix):] for k in reminded if k.startswith(prefix)}

    def count_reminded(self, contest_id: str) -> int:
        return len(self.reminded_umos(contest_id))

    def mark_daily_sent(self, umo: str, date_str: str) -> None:
        with _lock:
            d = self.load_sent()
            daily = d.get("dailySent")
            if not isinstance(daily, dict):  # 老文件 / 手改坏了都兜一下，别 KeyError
                daily = {}
                d["dailySent"] = daily
            daily[umo] = date_str
            _write_json(self.sent_file, d)

    def daily_sent_on(self, umo: str, date_str: str) -> bool:
        daily = self.load_sent().get("dailySent")
        if not isinstance(daily, dict):
            return False
        return daily.get(umo) == date_str

    def count_daily_sent(self, date_str: str) -> int:
        """今天已成功收到日报的会话数（「推送测试」面板拿它显示进度）。"""
        daily = self.load_sent().get("dailySent")
        if not isinstance(daily, dict):
            return 0
        return sum(1 for v in daily.values() if v == date_str)

    def reset_daily_sent(self, date_str: str) -> int:
        """把「当天已推送」标记清零，让**所有**订阅会话都能再收一遍。返回清掉的条数。

        使用场景（1.2.17，用户明确要求）：
          * 在 AstrBot 里改了 `daily_time` —— 改完就该重新测一遍，而不是因为
            「今天已经发过」被静默跳过（他就是这么被坑的：改到 12:42 三个群全无反应）；
          * 点面板上的「重置今天的推送状态」按钮。

        直接**清空整张表**而不是只删 `date_str` 那几条：这张表的记录**只有等于当天才有意义**，
        其它日期全是陈旧垃圾，留着也永远不会再被匹配到，一起丢掉正好顺手做个 GC。
        """
        with _lock:
            d = self.load_sent()
            daily = d.get("dailySent")
            if not isinstance(daily, dict):
                if daily is not None:
                    d["dailySent"] = {}
                    _write_json(self.sent_file, d)
                return 0
            removed = sum(1 for v in daily.values() if v == date_str)
            if daily:
                d["dailySent"] = {}
                _write_json(self.sent_file, d)
            return removed

    # ---------- 日报时间指纹 ----------
    #
    # 存的是「上一次真正生效过的 (日期, daily_time)」。用途单一但关键：判断用户是不是
    # **刚改了 daily_time**。这个判断必须**跨重启有效** —— 在 AstrBot 里保存插件配置
    # 往往会重载插件，内存变量会被清空，只靠 self._last_daily_time 会漏判。

    def get_daily_time(self) -> dict:
        """返回 {"date": "YYYY-MM-DD", "time": "HH:MM"}；从没写过则返回 {}。"""
        v = self.load_sent().get("dailyTime")
        return v if isinstance(v, dict) else {}

    def set_daily_time(self, date_str: str, time_str: str) -> None:
        with _lock:
            d = self.load_sent()
            d["dailyTime"] = {"date": date_str, "time": time_str}
            _write_json(self.sent_file, d)

    def cleanup_sent(self, contests: list[dict], grace_seconds: int = 7200) -> int:
        """清掉「已结束超过 grace_seconds」的提醒记录，防止文件无限膨胀。返回清除条数。"""
        import time

        now = int(time.time())
        keep_ends = {c["id"]: (c.get("startAt") or 0) + (c.get("durationMinutes") or 0) * 60 for c in contests}
        with _lock:
            d = self.load_sent()
            removed = 0
            for key in list(d.get("reminded", {})):
                cid = key.split("|", 1)[0]
                end = keep_ends.get(cid)
                if end is not None and now - end > grace_seconds:
                    del d["reminded"][key]
                    removed += 1
            # 没有东西可清就不要写盘 —— 这个方法原先每 30 秒被调一次，
            # 无条件写盘等于每 30 秒白写一次文件
            if removed:
                _write_json(self.sent_file, d)
            return removed

    # ---------- 用户战绩查询缓存（/cf /nk）----------
    #
    # 结构：
    # {
    #   "schemaVersion": 1,
    #   "entries": {
    #     "cf:firefly_iv":                 {"expiresAt": <ts>, "data": {...}},  10 分钟
    #     "nk:886965097":                  {"expiresAt": <ts>, "data": {...}},  10 分钟
    #     "cfcs:2276:firefly_iv":          {"expiresAt": <ts>, "data": {...}},  24 小时
    #     "cfprob:counts":                 {"expiresAt": <ts>, "data": {...}}   24 小时 · 全局共享
    #   }
    # }
    #
    # 三个设计约束（用户明确要求「不要缓存太多，要定期清理」）：
    #   1. 只存**派生出来的小结果**（一个用户档案约 0.7KB、单场成绩约 30B、
    #      题库映射是 {比赛: 题目数} 约 17KB），绝不把 2.2MB 的题库原始响应留在盘上
    #   2. 有**分组上限 + 总量上限**，超了丢最早过期的（见 PREFIX_CAPS / cleanup_profile_cache）
    #   3. 主循环按 cache_cleanup_minutes 定期清理，且**无变化不写盘**

    MAX_ENTRIES = 300  # 硬上限兜底（put 时的自我保护），定期清理会按配置收得更紧
    # 全局共享的缓存键：**不参与条数裁剪**。丢了要重新下载 2.2MB 题库，
    # 而它自身只有一份、体积固定（约 17KB），没有增长风险。
    SHARED_KEYS = ("cfprob:counts",)
    # 各类缓存的分组上限（键前缀 -> 条数）。都是可重建的派生数据，超了就丢最早过期的。
    PREFIX_CAPS = {"cfcs:": 120, "cf:": 60, "nk:": 60}

    def _load_profile_cache(self) -> dict:
        d = _read_json(self.profile_file, {})
        if not isinstance(d, dict):
            return {"schemaVersion": SCHEMA_VERSION, "entries": {}}
        if not isinstance(d.get("entries"), dict):
            d["entries"] = {}
        return d

    def get_profile_cache(self, key: str) -> Any:
        """取未过期缓存，过期或不存在返回 None。"""
        e = self._load_profile_cache()["entries"].get(key)
        if not isinstance(e, dict):
            return None
        exp = e.get("expiresAt") or 0
        if exp and exp < time.time():
            return None
        return e.get("data")

    def put_profile_cache(self, key: str, data: Any, ttl: int) -> None:
        with _lock:
            d = self._load_profile_cache()
            entries = d["entries"]
            entries[key] = {"expiresAt": int(time.time()) + max(1, int(ttl)), "data": data}
            if len(entries) > self.MAX_ENTRIES:
                # 超限时先扔掉最早已过期的条目
                now = time.time()
                for k in [k for k, v in entries.items()
                          if isinstance(v, dict) and (v.get("expiresAt") or 0) < now]:
                    del entries[k]
                # 还超就按过期时间从头丢
                if len(entries) > self.MAX_ENTRIES:
                    for k, _ in sorted(
                        entries.items(), key=lambda kv: (kv[1] or {}).get("expiresAt", 0)
                    )[: len(entries) - self.MAX_ENTRIES]:
                        entries.pop(k, None)
            _write_json(self.profile_file, d)

    def delete_profile_cache(self, key: str) -> bool:
        """删除一条战绩缓存，返回是否真的删掉了（1.5.0）。

        用途：某次 /cf 查询**没拿到平台头像**时，把这份档案从缓存里丢掉
        （用户要求「查不到 CF 头像的战绩卡不要缓存」）。缓存里存的正是那份带着
        「当下取不到的头像地址」的档案，留着它下次还是同样的结果；丢掉之后
        下次查询会重新联网，图床一恢复就能自己好起来。档案是可重建的派生数据，
        丢掉没有任何副作用。
        """
        if not key:
            return False
        with _lock:
            d = self._load_profile_cache()
            entries = d["entries"]
            if key not in entries:
                return False
            del entries[key]
            _write_json(self.profile_file, d)
            return True

    def cleanup_profile_cache(self, max_entries: int = 0, prefix_caps: dict | None = None) -> int:
        """清过期 + 按条数上限裁剪，返回清除条数。**没有变化就不写盘。**

        两步：
          1. 过期条目直接删
          2. 超上限时优先丢「最早过期」的 —— 战绩缓存全是可重建的派生数据，
             丢了再查一次就好，绝不能让它无限增长
        全局共享键（题库映射）不参与裁剪，避免为了省几 KB 又去下 2.2MB。
        """
        now = time.time()
        caps = dict(self.PREFIX_CAPS)
        if prefix_caps:
            for k, v in prefix_caps.items():
                try:
                    if int(v) > 0:
                        caps[k] = int(v)
                except (TypeError, ValueError):
                    continue
        try:
            limit = int(max_entries) if max_entries and int(max_entries) > 0 else self.MAX_ENTRIES
        except (TypeError, ValueError):
            limit = self.MAX_ENTRIES
        removed = 0
        with _lock:
            d = self._load_profile_cache()
            entries = d["entries"]

            def _exp(key: str) -> float:
                v = entries.get(key)
                return (v or {}).get("expiresAt", 0) if isinstance(v, dict) else 0

            def _evict(keys: list, n: int) -> None:
                nonlocal removed
                for k in sorted(keys, key=_exp)[:n]:
                    if entries.pop(k, None) is not None:
                        removed += 1

            # ① 过期
            for k in [k for k in entries if _exp(k) < now]:
                del entries[k]
                removed += 1

            # ② 分组上限
            for prefix, cap in caps.items():
                keys = [k for k in entries if k.startswith(prefix) and k not in self.SHARED_KEYS]
                if len(keys) > cap:
                    _evict(keys, len(keys) - cap)

            # ③ 总量上限
            keys = [k for k in entries if k not in self.SHARED_KEYS]
            if len(keys) > limit:
                _evict(keys, len(keys) - limit)

            if removed:
                _write_json(self.profile_file, d)
            return removed

    # ---------- 战绩缓存的「按类清理」（1.6.0）----------
    #
    # 用户要求：「增加一键清除 cf/nk 查询缓存（分开）的指令，需要给出具体清除的数量，
    #           比赛缓存不要去动他」。
    #
    # 两个关键约束决定了实现方式：
    #   1. **只动 profile_cache.json**。赛程缓存是另一个文件（contests_cache.json，
    #      由抓取层写入），本节的任何方法都碰不到它 —— 清掉赛程意味着下一次日报得当
    #      场重新联网抓一遍，代价高且没必要。
    #   2. **按前缀分组**，而不是「清空整个文件」：同一个文件里还躺着题库映射
    #      （`cfprob:counts`，约 17KB，清掉要重下 2.2MB），以及别人自己的键。
    #
    # 前缀与键的对应关系（见上文 profile_cache 结构注释）：
    #   `cf:v2:<handle>`        单用户 CF 档案
    #   `cfcs:<cid>:<handle>`   单场 CF 成绩（24h）
    #   `nk:<uid>`              牛客档案
    #   `cfprob:counts`         CF 题库映射（全局共享，24h）
    PROFILE_KINDS = {
        "cf": ("cf:", "cfcs:"),
        "nk": ("nk:",),
        "problemset": ("cfprob:",),
    }
    # 面板要分行显示的细分口径（键前缀 -> 统计名）
    PROFILE_BUCKETS = (
        ("cf:", "profile"),
        ("cfcs:", "contest"),
        ("nk:", "nk"),
        ("cfprob:", "problemset"),
    )

    @staticmethod
    def _kind_prefixes(kind: str) -> tuple:
        """把 kind 归一成前缀元组；`all` = cf + nk（**不含**题库映射）。"""
        k = str(kind or "").strip().lower()
        if k in ("all", "全部", "所有", "*"):
            return ("cf:", "cfcs:", "nk:")
        return Store.PROFILE_KINDS.get(k, ())

    def profile_cache_stats(self) -> dict:
        """战绩缓存的分项条数，供指令/面板展示（只读，不写盘）。

        返回 `{"total", "profile", "contest", "nk", "problemset", "other", "expired"}`。
        `contests`（赛程缓存）刻意**不在**这里 —— 它压根不是这个文件里的东西。
        """
        now = time.time()
        out = {name: 0 for _, name in self.PROFILE_BUCKETS}
        out.update({"total": 0, "other": 0, "expired": 0})
        d = self._load_profile_cache()
        for key, entry in d["entries"].items():
            out["total"] += 1
            hit = False
            for prefix, name in self.PROFILE_BUCKETS:
                if key.startswith(prefix):
                    out[name] += 1
                    hit = True
                    break
            if not hit:
                out["other"] += 1
            exp = (entry or {}).get("expiresAt") or 0 if isinstance(entry, dict) else 0
            if exp and exp < now:
                out["expired"] += 1
        return out

    def clear_profile_cache(self, kind: str) -> dict:
        """按类清掉战绩查询缓存，返回 `{"ok", "removed": {统计名: 条数}, "total", "message"?}`。

        `kind` ∈ `cf`（CF 档案 + 单场成绩）/ `nk`（牛客档案）/ `problemset`（CF 题库映射）/
        `all`（cf + nk）。**不含过期过滤**：用户点「清」就是想清干净，过期的也一并带走。
        """
        prefixes = self._kind_prefixes(kind)
        if not prefixes:
            return {"ok": False, "removed": {}, "total": 0, "message": f"不认识的缓存类别：{kind}"}
        removed = {name: 0 for _, name in self.PROFILE_BUCKETS}
        total = 0
        with _lock:
            d = self._load_profile_cache()
            entries = d["entries"]
            for key in list(entries):
                if not any(key.startswith(p) for p in prefixes):
                    continue
                del entries[key]
                total += 1
                for prefix, name in self.PROFILE_BUCKETS:
                    if key.startswith(prefix):
                        removed[name] += 1
                        break
            if total:
                _write_json(self.profile_file, d)
        return {"ok": True, "removed": removed, "total": total}

    # ---------- 名言库（可视化管理，1.6.0）----------
    #
    # 用户要求：「名言库支持可视化管理，我要看到全部名言」。
    #
    # 内置的 60+ 条写在 core/quotes.py 里（代码的一部分，升级插件就会覆盖），所以
    # 「管理」不能靠改内置库，而是把用户的意志单独落一份：
    #   custom  面板里自己加的句子（与配置项 daily_quote_extra 合成同一个抽取池）
    #   blocked 屏蔽名单 —— 里面存的是 quotes.quote_id(正文)（`builtin:<sha1前10>`），
    #           只在抽取时过滤，不改代码。将来内置库增删句子也不会错位（见 quote_id 注释）。
    #
    # 为什么单独一个文件：它要被面板频繁读写、且与订阅/赛程/缓存都无关；
    # 塞进 sent_log.json 或 subscriptions.json 只会让「退订」「重置」这类操作更难解释。

    MAX_CUSTOM_QUOTES = 200
    MAX_BLOCKED_QUOTES = 500

    def get_quotes(self) -> dict:
        """返回 `{"custom": [{"text","by"}...], "blocked": [id...]}`。坏文件一律自愈成空。"""
        d = _read_json(self.quote_file, {})
        if not isinstance(d, dict):
            d = {}
        custom_raw = d.get("custom")
        if not isinstance(custom_raw, list):
            custom_raw = []
        custom = []
        for it in custom_raw:
            if isinstance(it, dict):
                text = str(it.get("text") or "").strip()
                if text:
                    custom.append({"text": text, "by": str(it.get("by") or "").strip()})
            elif isinstance(it, str) and it.strip():
                custom.append({"text": it.strip(), "by": ""})
        blocked_raw = d.get("blocked")
        if not isinstance(blocked_raw, list):
            blocked_raw = []
        blocked = [str(x).strip() for x in blocked_raw if str(x).strip()]
        return {"custom": custom, "blocked": blocked}

    def _write_quotes(self, data: dict) -> None:
        _write_json(
            self.quote_file,
            {
                "schemaVersion": SCHEMA_VERSION,
                "custom": data.get("custom") or [],
                "blocked": data.get("blocked") or [],
            },
        )

    def add_quote(self, text: str, by: str = "") -> tuple[bool, str]:
        """加一条自定义名言；返回 `(是否写入, 文案)`。

        调用方（main.py）已经用 `quotes.prepare_custom` 洗过并校验过，这里只做
        「去重 + 上限」这层存储纪律 —— 重复的句子留在池里会让它被抽中的概率翻倍，
        看起来就像抽签坏了。
        """
        text = str(text or "").strip()
        if not text:
            return False, "名言正文不能为空"
        by = str(by or "").strip()
        key = " ".join(text.split()).lower()
        with _lock:
            data = self.get_quotes()
            for it in data["custom"]:
                if " ".join(it["text"].split()).lower() == key:
                    return False, "这条名言已经在自定义库里了"
            if len(data["custom"]) >= self.MAX_CUSTOM_QUOTES:
                return False, (
                    f"自定义名言已达上限（{self.MAX_CUSTOM_QUOTES} 条），"
                    "先删掉几条再加"
                )
            data["custom"].append({"text": text, "by": by})
            self._write_quotes(data)
            return True, ""

    def remove_quote(self, text: str) -> bool:
        """按正文删掉一条自定义名言，返回是否真的删掉了。"""
        key = " ".join(str(text or "").split()).lower()
        if not key:
            return False
        with _lock:
            data = self.get_quotes()
            before = len(data["custom"])
            data["custom"] = [
                it for it in data["custom"] if " ".join(it["text"].split()).lower() != key
            ]
            if len(data["custom"]) == before:
                return False
            self._write_quotes(data)
            return True

    def set_quote_blocked(self, quote_id: str, blocked: bool) -> bool:
        """屏蔽 / 恢复一条名言（面板上点一下）。返回是否真的发生了变化。"""
        qid = str(quote_id or "").strip()
        if not qid:
            return False
        with _lock:
            data = self.get_quotes()
            ids = list(data["blocked"])
            if blocked:
                if qid in ids:
                    return False
                if len(ids) >= self.MAX_BLOCKED_QUOTES:
                    return False
                ids.append(qid)
            else:
                if qid not in ids:
                    return False
                ids = [x for x in ids if x != qid]
            data["blocked"] = ids
            self._write_quotes(data)
            return True
