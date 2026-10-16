# -*- coding: utf-8 -*-
"""
竞赛日报 QQ 机器人 · AstrBot 插件主入口

功能
  1. 自动抓取 Codeforces + 牛客赛程（每 30 分钟；牛客收录「系列赛 + 高校校赛/同步赛」，
     不含「自主创建赛」，见配置 nowcoder_categories）
  2. 每天定时把日报推到所有订阅群
  3. 每场比赛开赛前 15 分钟推送提醒（分钟级扫描 + 幂等去重）
     ⚠️ 定时推送（2/3）走的是**主动消息**。若机器人尚未上线（或平台未放开该权限），
        会收到 40034102「主动消息失败，无权限」——注意它和 40034100「超过频控限制」
        是两码事：频控等额度恢复即可，无权限与条数无关。**换 API / 换推送插件都绕不过去**：
        所有插件最终都走同一个 qq_official 适配器出口，而该适配器发群消息时固定不带
        msg_id（= 主动消息）。插件不重试、不刷屏：平台性拒绝每天只提示一次并给出排查顺序
        （先确认机器人是否已上线 → 上线后额度很充足 → 上线前用被动通道 / 私聊订阅，
        见 PUSH_PERM_HINT），临时故障最多重试 REMIND_FAIL_MAX_TRIES 次后放弃。
        群成员 @机器人 发指令（被动回复）一直不受影响。
  4. 用户战绩查询：/cf <handle>、/nk <uid>（结果渲染成战绩卡片图，
     配色按平台 Rating 段位色；渲染失败或关闭开关时回退纯文本模板）

指令
  /日报            立即推送一次日报到当前会话（被动回复，永远不受主动消息限制）
  /推送测试 [关键词…]  推一轮日报测试，验证主动消息权限（仅授权用户）。
                  带关键词 = 只推给备注/群号匹配的会话，避免打扰其它群
  /提醒测试 [关键词…]  推一轮**赛前提醒**测试（任选一场比赛渲染；不写幂等，不影响正式提醒）
  /群备注 [关键词] [备注]  给群绑自定义备注（群 ID 映射表），方便面板里选择；不填参数则查看
  /赛程 [天数]      查询未来 N 天比赛（默认取配置 upcoming_days，**最多 7 天**；
                  **含今天剩余场次**，今天算第 1 天——手动查时最关心今晚有没有比赛）
                  与日报末尾「未来 N 天」区块的口径**不同**：那个不含今天（明天算第 1 天），
                  因为今天的比赛日报正文里已经列过了。
  /cf <handle>     查 Codeforces 用户战绩（Rating / 最高 Rating / 本周比赛，卡片图）
  /nk <uid>        查牛客用户战绩（同上，uid 是主页链接里的数字）
  /订阅日报         把当前会话加入订阅表
  /退订日报         移除订阅
  /订阅列表         查看已订阅的会话（含自定义备注）
  /刷新赛程         立即抓取一次（仅 Bot 管理员）
  /名言            随机来一句竞赛名言；/名言 库 看库、/名言 补题 搜一句
  /清缓存 [cf|nk|全部|题库]  一键清掉战绩查询缓存，回复清掉的具体条数
  /whoami          查看自己的身份 ID 与各指令当前权限

日报名言彩蛋（1.4.0）
  日报末尾默认追加一句 `〖…〗`（模板项 daily_quote，默认值 `〖{quote}〗`），
  内容取自 core/quotes.py 的内置库（名家名言 22 + 圈内梗 20 + 竞赛鸡汤 20）：
    * 按**北京时间当天**抽 —— 同一天里定时推送、/日报、面板连点「推送测试」、
      多个群分别渲染，拿到的都是同一句；换一天自动换一句。
      想让每条消息都重抽，把配置 `daily_quote_random_each_time` 打开。
    * 分类可关（`daily_quote_cats`）、可追加自己的句子（`daily_quote_extra`，与内置库
      混在同一个池里抽）、出处可隐（`daily_quote_with_source`）。
    * 模板留空 = 不显示；想把名言挪到标题下面，把 {quote} 写进 daily_title 即可
      （daily_title / daily_section / daily_empty 都能用 {quote} {quote_text}
      {quote_by} {quote_cat} 四个占位符）。
    * 池为空时整块跳过，不会留下一个空的 `〖〗` 看着像插件坏了。
  1.6.0：名言库**可视化管理**（用户要求「名言库支持可视化管理，我要看到全部名言」）——
  面板「名言库」区把每一条都列出来（内置 60+ 条 + 自建的），可以按分类/关键词筛、
  逐条「屏蔽」（不合适的句子不再被抽中）或「恢复」，也能直接新增/删除自定义句子。
  屏蔽名单与自定义句子都存在 data/quotes.json（**不改动代码里的内置库**，升级不丢）；
  指令侧也能用 /名言 库 看全库概要（已屏蔽的会标 🚫）。

权限模型（配置里可逐条自定义）
  每条指令都有自己的权限等级，在插件配置的「各指令权限等级」（command_permissions）里改：
    all    所有人可用（默认：日报 / 赛程 / cf / nk / whoami / 名言）
    admin  授权用户可用（AstrBot 管理员 / 白名单 / 群主·群管理员；默认：订阅日报等管理类）
    strict 仅 AstrBot 管理员与白名单（**忽略**群主/群管理员角色）
    off    关闭该指令
  旧配置项 admin_only_commands 仍兼容（只对未在 command_permissions 里出现的指令生效）。
  被拦住时回复「无权限文案」（deny_template，默认「前方的功能以后再来探索吧！趁这时间多多刷题哦！」），
  文案里可用 {cmd} {user} {user_id} {level} {need} 占位符。

回复口径（「指令没生效」的四种情况）
  * 无权限          -> deny_template
  * 无效的指令      -> 同一句（缺参数 / 参数格式错 / 查不到该用户；invalid_reply_like_deny）
  * 未识别的指令    -> 同一句（@ 机器人 + `/xxx` 且没有任何插件注册过；unknown_command_reply）
  * 超时 / 网络故障 / 异常 -> **静默不回复**，只写日志（silent_on_error，默认开）
  前三种的**具体原因只写日志**，日志里能查到「拒绝 / 无效指令 / 未识别的指令」三类标记；
  第四类日志会标注「-> 静默不回复」。

可视化视图「推送测试」（1.2.17 起，1.3.0 扩展）
  AstrBot 的插件配置表单（_conf_schema.json）只支持输入控件、**没有按钮类型**，
  所以「立即推送测试」按钮做成了插件可视化视图：
    * 页面：views/panel/index.html（+ app.js），Dashboard 以受限 iframe 载入
    * 前端：(await window.AstrBotPluginView.ready()).apiGet/apiPost(...)
    * 后端：__init__ 里 context.register_web_api("/{插件名}/...") 注册，
      handler 就是下面的 page_status / page_push_test / page_remind_test /
      page_alias / page_reset_sent / page_clear_cache / page_quote
  1.3.0 面板新增两块（用户要求「把赛前提醒也加进去」+「测试要选择推送的群，以免打扰」）：
    * 「目标会话」—— 带复选框的会话列表，可**给每个群绑自定义备注**（群 ID 映射表，
      存 data/group_aliases.json），于是「群 ID」不再是天书，勾选即可；
      **测试默认一个都不勾**，点按钮前必须选，避免一按就打扰所有群。
    * 「赛前提醒」—— 列出「哪场比赛、几点提醒、已经提醒过几个群」，并可选中一场
      作为「测试赛前提醒」的内容。测试提醒**不写幂等记录**，不会顶掉当天的正式提醒。
  老版本 AstrBot 没有 register_web_api / astrbot.api.web 时**只打一条 info 日志**，
  插件照常工作 ——     面板用不了还有 /推送测试、/提醒测试、/群备注 指令兜底。
  1.6.0 面板再加两块：
    * 「名言库」—— 列出**全部**名言（含被屏蔽的），逐条屏蔽/恢复/删除/新增（见上文）；
    * 「缓存维护」—— 分三行显示 CF 档案 / 牛客档案 / 题库映射各有几条，各配一个
      「清除」按钮；等价指令是 /清缓存 cf|nk|全部|题库。**赛程缓存不在这里**，
      它由抓取层维护，清掉意味着下一次日报要当场重新联网抓一遍。

战绩卡片的头像（1.7.2 定稿：只有「平台头像」一拨，取不到就用首字母块）
  平台头像来源：CF = 大图地址 + 小图地址；牛客 = 平台头像地址（改图片尺寸拿更清晰版）。
    * **并行取所有候选 + 2s 让位窗口**（core/card.py `_fetch_best`）：CF 的**大图**
      是**源站长期 503**（实测 8 个真实账号只 4 个能下，换 UA / Referer / 出口 /
      换 CDN 缓存键统统还是 503，响应头证明确已回源），而**小图** 8/8 可用。
      1.5.0 的「串行 + 穿缓存」把整卡预算耗在必挂的大图上，
      让能用的小图根本没机会试 —— 1.7.0 改成并行后实测 4/4 成功、1.1~1.2s。
    * **取不到就画首字母圆角块**（1.7.2，用户要求「查不到头像就降级为名字首字母，
      像前面某一版那样」）：QQ 头像兜底整条链路（头像地址拼装、
      `avatar_fallback_qq` / `qq_avatar_url_template` / `profile_avatar_source`
      三个配置项、默认灰图识别）**全部删除** —— 官方机器人的 openid 头像接口
      对任何参数都回 200 + 一张 40×40 默认灰图，拿不到真头像，收益为零。
    * **没取到平台头像**的那次查询：档案不进缓存（core/profile.py `forget`）、
      卡片图发完即删（main.py `_profile_output`），下次查询重新联网自愈。
      开关：`ephemeral_card_without_avatar`（默认开）。
  另注：36 个抽样账号里有 16 个用的是 CF **全局共享**的占位图
  （他们从没上传过头像）—— 这类地址只有一个缓存键，本地缓存
  命中后基本零成本。

日报「改时间即重测」（1.2.17）
  改 `daily_time` 时会把 sent_log.json 里当天的 dailySent 记录**清零**，
  于是所有订阅群都会重新收一遍（不再因为「今天已发过」被静默跳过）。
  这个判断基于**存盘的**时间指纹（date + time），跨插件重载依然有效；
  不想要这个行为可把 `daily_reset_on_time_change` 关掉。

调度设计（重要）
  使用 asyncio 自维护循环，而不是 cron_manager。理由：
    * 官方文档明确支持在 __init__ 里 asyncio.create_task() 注册后台任务
    * 行为完全可控，不依赖 cron_manager 的接口签名（未在实机验证过）
    * 插件自包含，用户无需在 WebUI 另外配置定时任务
  三条时间线互不阻塞，且「日报」与「提醒」只读缓存、不联网，
  避免网络抖动导致漏提醒（联网只发生在抓取时间线里）。

战绩查询的限流与提速（重要性仅次于功能本身）
  查询会打第三方接口，必须限流，否则轻则被风控、重则 IP 被封：
    * 错峰并发：同平台相邻请求的「出发时刻」至少间隔 CF 1.0s（官方要求 <=1 次/秒）、
      牛客 0.6s；但请求本身可以重叠，所以是「错峰出发、同时在途」，
      3~4 个无依赖的请求总耗时 ≈ N×间隔 + 最慢那个，而不是逐条相加
    * 结果缓存：档案默认 10 分钟；单场成绩默认 24 小时（成绩不会变）；
      题库映射（各场总题数）默认 24 小时且全局共用
    * 用户冷却：同一发送者默认 15 秒内只能查一次
    * 全局互斥：同一时刻只处理一个战绩查询，其余直接提示稍后再试
  另外插件启动时会**后台预热题库映射**，让第一次 /cf 不必等那 2.2MB 下载。

导入方式（改前必读）
  AstrBot 是用 __import__("data.plugins.<插件目录名>.main") 把插件当**包**加载的
  （见 astrbot/core/star/star_manager.py 的 load()）。所以：
    * ✅ 必须用相对导入 `from .core import render`
    * ❌ 不要 `sys.path.insert(0, 插件目录)` 后再 `from core import render`
      那样会往 sys.modules 里塞一个**通用名** `core`。别的插件只要也有 core/ 包，
      谁先加载谁赢，本插件的 core 就会被静默替换（典型症状：/cf 报
      `'Store' object has no attribute 'get_profile_cache'`）；
      而且这个全局 core 不在 AstrBot 的重载清理范围内，热重载后仍是旧代码。
  回归测试见 tests/test_import_isolation.py。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone

# 仅用于数据目录回退；不要把通用名称 core 注入全局导入路径。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 插件名 —— 注册插件可视化视图的后端路由时**必须**带上它当前缀：
#   后端注册   /{PLUGIN_NAME}/push-test
#   视图端调用 bridge.apiPost("push-test")   ← 不带插件名
PLUGIN_NAME = "astrbot_plugin_contest_daily"

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

# 插件可视化视图（views/panel）用到的 Web 助手。老版本 AstrBot / 离线冒烟测试的桩环境
# 里没有 astrbot.api.web —— 这里**必须**降级而不是让插件加载失败：视图打不开不影响
# 机器人本体，指令通道（/推送测试）照样能用。
try:  # noqa: SIM105
    from astrbot.api.web import error_response as _web_error_response
    from astrbot.api.web import json_response as _web_json_response
except Exception:  # noqa: BLE001
    _web_json_response = None  # type: ignore[assignment]
    _web_error_response = None  # type: ignore[assignment]

# 当前 Web 请求的上下文代理（官方文档推荐用法：from astrbot.api.web import request，
# 之后在 handler 里用 request.query / await request.json() 读参数）。
# 它只在「插件 Web API handler 正在执行」期间有效，所以这里只拿引用、绝不在别处调用。
try:  # noqa: SIM105
    from astrbot.api.web import request as _web_request
except Exception:  # noqa: BLE001
    _web_request = None  # type: ignore[assignment]

# 指令的「贪婪参数」标记（AstrBot 官方写法）：handler 签名里写
#   `extra: GreedyStr = GreedyStr`
# AstrBot 会把指令后面**剩余的所有词**合并成一个字符串传进来（等价于 shell 的 "$@"）。
# ⚠️ 不能写 `*extra: str`：AstrBot 的 CommandFilter.init_handler_md 只看签名里
# 「有名有姓」的参数，可变参数 `*extra` 会被当成一个**无默认值的必填参数**，
# 结果就是 `/清缓存`（带不带参数都一样）报「必要参数缺失： kind(str)=,extra(str)」
# —— 1.7.1 实机截图踩的坑。用类本身当默认值，是为了让 AstrBot 的参数校验
# 把它识别成贪婪参数（它判断的是 `param.default is GreedyStr`），同时直接函数调用
# （离线测试）不传参也不会 TypeError；用 _greedy() 把类对象洗回空串。
try:  # noqa: SIM105
    from astrbot.core.star.filter.command import GreedyStr
except Exception:  # noqa: BLE001  老版本 AstrBot / 桩测试环境没有这个类

    class GreedyStr(str):  # type: ignore[no-redef]
        """兜底占位：老版本 AstrBot 没有 GreedyStr，退化成普通字符串参数。"""

from .core import render
from .core import quotes as quote_lib
from .core.profile import InvalidInput, ProfileFetcher
from .core.sources import NK_CATEGORIES_DEFAULT, fetch_all
from .core.store import Store

TZ_CN = timezone(timedelta(hours=8))
TICK_SECONDS = 30  # 主循环心跳：每 30 秒判断一次各任务是否到点
PROFILE_TIMEOUT = 120  # 单次战绩查询的硬超时（秒），防止 CF 抖动时把指令挂死
MAX_LOOKAHEAD_DAYS = 7  # 「赛程 / 未来 N 天」的硬上限：配置和指令参数都不许超过它

# 缓存清理的「礼貌让路」参数（用户要求：清理不得干扰日报主体工作，也不得影响正在跑的指令）
CLEANUP_MIN_FILE_AGE = 300  # 只删「最后写入超过 5 分钟」的缓存文件：刚生成/可能正在读取的一律不动
CLEANUP_FIRST_DELAY = 120   # 启动后先等 2 分钟再清，避开插件加载 / 首次抓取的高峰
# 「手动推送测试」一轮的硬超时保护：即便某个群把发送接口挂死，也别让面板一直转圈
MANUAL_PUSH_NOTE_LIMIT = 20  # 面板最多回显多少个会话的明细（多了没必要）
PANEL_TARGET_LIMIT = 60      # 面板「目标会话」勾选列表最多列几个（可选列表，比明细放宽些）
# 面板「赛前提醒计划」：只看未来多久内开赛的比赛，最多列几条（1.3.0）
REMIND_PLAN_HOURS = 72
REMIND_PLAN_LIMIT = 30
# 测试提醒消息末尾追加的说明：一眼区分「测试」与「正式提醒」，
# 免得群里同学被一条假提醒带着去蹲一场不存在的比赛。
REMIND_TEST_NOTE = "\n\n（本条为测试消息，可忽略～）"


def _jresp(obj, status_code: int = 200):
    """统一的 Web 响应：有 astrbot.api.web 就用官方 helper，没有就直接返回 dict。

    直接返回 dict 在 AstrBot 的 Quart 兼容层里同样合法（官方文档明确保留），
    所以桩环境 / 老版本下这个降级是安全的，测试也就能直接断言返回值。
    """
    if _web_json_response is not None:
        try:
            return _web_json_response(obj, status_code=status_code)
        except TypeError:  # 老签名不接受 status_code
            return _web_json_response(obj)
    return obj


def _eresp(message: str, status_code: int = 400):
    if _web_error_response is not None:
        try:
            return _web_error_response(message, status_code=status_code)
        except TypeError:
            return _web_error_response(message)
    return {"ok": False, "message": message, "_status": status_code}


# ---------------------------------------------------------------- 推送失败治理
# 非平台性故障（网络抖动等）的失败日志去重间隔：同一类别 30 分钟内只打一条
PUSH_FAIL_LOG_INTERVAL = 1800
# 赛前提醒遇到临时故障最多重试几次就放弃（提醒窗口只有十几分钟，一直刷没意义）
REMIND_FAIL_MAX_TRIES = 3

# 「机器人没有主动消息权限」这类**永久性**平台拒绝：重试永远无效，必须立刻放弃。
# 触发场景：QQ 官方机器人返回 40034102「主动消息失败，无权限」。
# 被动回复（用户 @ 机器人后的 5 分钟窗口内）不受影响，所以群里的指令查询照常可用。
_RE_NO_ACTIVE_PERM = re.compile(
    r"主动消息|无权限|40034102|no\s*permission|permission\s*denied", re.IGNORECASE
)
# ⚠️「超过频控」是**另一回事**：额度恢复了就能发，不是权限问题。
# 官方错误码表里两者分得很清楚——40034100 = 主动消息发送超过频控限制；
# 40034105 / 40034102 = 主动消息发送失败，无权限（排查建议：检查机器人权限设置）。
# 麻烦的是频控文案「主动消息发送超过频控限制」里**也含「主动消息」四个字**，
# 所以必须在 _is_perm_denied 里先排除，否则频控会被误判成永久拒绝、还打印错误的排查提示。
_RE_RATE_LIMITED = re.compile(r"频控|频率限制|40034100|too\s+many\s+requests", re.IGNORECASE)
PUSH_PERM_HINT = (
    "这是平台侧的权限问题，不是插件故障：定时日报 / 赛前提醒走的是「主动消息」，"
    "平台返回 40034102（主动消息失败，无权限）。按发生概率排查："
    "① 群里的开关 —— 这是「有的群能收到、有的群收不到」最常见的原因："
    "手机 QQ 打开那个群 → 群设置 → 机器人 → 选中本机器人 → 打开「机器人主动在群聊内发言」"
    "（建议同时把「机器人可获取的群聊消息范围」设为「获取群内全部消息」）。"
    "⚠️ 这个开关是「每个群单独设置」的，不在 QQ 开放平台里 —— 所以会出现"
    "「开放平台那边权限配置一模一样，两个群能收到、第三个收不到」；"
    "② 机器人是否已上线：官方要求「提交审核，审核通过并手动上线后」才能加到群 / 单聊，"
    "未上线（沙箱期）主动消息通常发不出去；"
    "③ 额度不用操心：群聊主动消息频控是个人认证 60/qpm、未认证 30/qpm，"
    "单群每天最多接收 1000 条 —— 日报一天 1 条远够用。"
    "（顺带区分：40034100 是「超过频控」，与权限无关，等额度恢复即可。）"
    "④ 上面的都排完了还不行，就先回到被动通道：群成员 @机器人 发 /日报、/赛程 手动获取。"
    "（本提示每台机器人每天只打印一次，之后降为 debug，不会刷屏。）"
)


def _is_perm_denied(err_text: str) -> bool:
    """判断推送失败是不是「机器人没有主动消息权限」这类永久性平台拒绝。

    频控超限（40034100）要排除：那种情况等额度恢复就能发，不能当成权限问题
    立刻放弃、更不能贴「无权限」的排查提示。
    """
    text = err_text or ""
    if _RE_RATE_LIMITED.search(text):
        return False
    return bool(_RE_NO_ACTIVE_PERM.search(text))


UPCOMING_LIMIT = 15     # /赛程 一次最多列几条（防止把消息撑爆）


def _cn_now() -> datetime:
    return datetime.now(TZ_CN)


def _safe_sid(event) -> str:
    """安全取发送者 ID，任何适配器异常都不让它冒泡。"""
    try:
        return str(event.get_sender_id() or "?")
    except Exception:  # noqa: BLE001
        return "?"


def _scope_name(event) -> str:
    """当前会话该怎么称呼：群里叫「本群」，私聊叫「本会话（私聊）」。

    官方机器人的私聊没有 group_id（取到空串/None），据此区分即可。
    订阅/退订的回复文案以前一律写死「本群」，在私聊里发指令时会很别扭，
    而私聊正是「主动消息」限制的备选出路（见 PUSH_PERM_HINT ②）。
    """
    try:
        return "本群" if event.get_group_id() else "本会话（私聊）"
    except Exception:  # noqa: BLE001 任何适配器异常都不影响指令本身
        return "本会话"


# ---------------------------------------------------------------- 会话（群）识别与选择
#
# 用户要求（1.3.0）：「测试提醒全部要选择推送的群，以免打扰；将群 ID 给定一个映射表，
# 可以自定义绑定备注，这样选择方便」。
# 起因：umo 形如 `qq_official:GroupMessage:6B1F3C...`，在面板里完全看不出是哪个群，
# 「只想发给自己那个群」根本无从选择；而且以前点一下按钮就全群开推，很打扰。

def _umo_parts(umo: str) -> dict:
    """拆 AstrBot 的会话唯一 ID：`<平台>:<类型>:<会话ID>`。

    例：qq_official:GroupMessage:123456 -> {platform: "qq_official", kind: "group", sid: "123456"}
        aiocqhttp:group:AAA             -> 同上（部分适配器用小写 group）
    类型判断用「包含」而不是精确等于：官方适配器给的是 GroupMessage / FriendMessage，
    协议适配器给的是 group / private，还有 C2C_MESSAGE_CREATE 这类变体。
    """
    parts = str(umo or "").split(":")
    platform = parts[0] if parts else ""
    mtype = parts[1] if len(parts) > 1 else ""
    sid = ":".join(parts[2:]) if len(parts) > 2 else ""
    low = mtype.lower()
    if "group" in low:
        kind = "group"
    elif "friend" in low or "private" in low or "c2c" in low or "direct" in low:
        kind = "private"
    else:
        kind = "other"
    return {"platform": platform, "type": mtype, "sid": sid, "kind": kind}


def _umo_label(umo: str, alias: str = "") -> str:
    """会话的显示名：有备注用备注；没有就按「群 123456 / 私聊 abc」生成。

    这是「映射表」的兜底价值：即使一个备注都没配，面板里也不会只看到一串 umo。
    """
    text = " ".join(str(alias or "").split())
    if text:
        return text
    p = _umo_parts(umo)
    name = {"group": "群", "private": "私聊"}.get(p["kind"], "会话")
    return f"{name} {p['sid']}" if p["sid"] else (str(umo) or "未知会话")


def _match_targets(keywords, umos, aliases) -> tuple[list[str], list[str]]:
    """按关键词挑会话：命中备注（包含匹配、忽略大小写）或 umo 片段。

    给指令通道用（面板是勾选，不需要关键词）：
        /推送测试 计科2201      -> 只推备注含「计科2201」的那个群
        /提醒测试 123456        -> 匹配 umo 里含 123456 的会话
    返回 (命中的 umo 列表（去重保序）, 没匹配上的关键词列表)。
    """
    hits: list[str] = []
    missed: list[str] = []
    for kw in keywords:
        k = str(kw or "").strip().lower()
        if not k:
            continue
        found = [
            u
            for u in umos
            if k in str(aliases.get(u, "")).lower() or k in str(u).lower()
        ]
        if found:
            hits.extend(found)
        else:
            missed.append(str(kw).strip())
    seen: set[str] = set()
    out: list[str] = []
    for u in hits:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, missed


def _greedy(v) -> str:
    """把 GreedyStr 参数洗成普通字符串（见文件头 GreedyStr 导入处的说明）。

    AstrBot 运行时传的是「剩余参数合并后的字符串」；直接函数调用（离线测试）
    没传参时拿到的是签名默认值 —— 即 GreedyStr **类本身**，这里洗成空串。
    """
    if isinstance(v, type):  # 默认值是 GreedyStr 类（不是实例）
        return ""
    return str(v or "")


def _join_args(*parts) -> str:
    """把指令的几个参数拼回一句，用空格分隔、丢掉空段。

    `/推送测试 计科2201 软工2301`、`/群备注 2301 软件工程 2301班` 这种多词写法
    之所以能自然工作，靠的是签名里的 GreedyStr 贪婪参数 + 这里把几段拼回去。
    """
    out = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            out.extend(str(x or "").strip() for x in p)
        else:
            out.append(_greedy(p).strip())
    return " ".join(p for p in out if p)


def _contest_start(c: dict) -> float:
    """比赛开始时间戳，脏值一律当 0（排序 / 过滤都不能被一条坏数据炸掉）。"""
    try:
        return float((c or {}).get("startAt") or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_short_date(ts: float) -> str:
    """把时间戳格式化成「MM-DD HH:MM」（北京时间），面板列表里够用且不啰嗦。"""
    try:
        return datetime.fromtimestamp(float(ts), TZ_CN).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "—"


def _fmt_full_date(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts), TZ_CN).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "—"


def _prune_dir(path: str, max_age_seconds: float, max_files: int) -> int:
    """按「超龄 + 超量」清理一个目录里的**文件**（不递归删子目录）。

    清理策略：先删超过 max_age_seconds 没动过的；再按 mtime 从旧到新删，
    直到文件数不超过 max_files。任何异常都吞掉（缓存清理不该影响主流程）。
    目录不存在时直接返回 0。
    """
    removed = 0
    try:
        if not os.path.isdir(path):
            return 0
        now = time.time()
        entries = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, e.path))
        keep = []
        for mtime, p in entries:
            if now - mtime > max_age_seconds:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    keep.append((mtime, p))
            else:
                keep.append((mtime, p))
        if len(keep) > max_files:
            keep.sort()  # mtime 从旧到新
            for _, p in keep[: len(keep) - max_files]:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    except Exception:  # noqa: BLE001
        pass
    return removed


def _dir_size_bytes(path: str) -> int:
    """递归统计目录内所有文件的字节数（含子目录）。目录不存在/读不了时返回 0。"""
    total = 0
    try:
        if not os.path.isdir(path):
            return 0
        for root, _dirs, files in os.walk(path):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    continue
    except Exception:  # noqa: BLE001
        pass
    return total


def _disk_free_bytes(path: str) -> int:
    """`path` 所在卷的剩余可用字节。取不到时返回 -1（调用方须当「未知」处理）。

    `path` 可能还没建出来（首次运行），所以一路往上找第一个存在的父目录再探。
    """
    try:
        probe = os.path.abspath(path)
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                return -1
            probe = parent
        return int(shutil.disk_usage(probe).free)
    except Exception:  # noqa: BLE001
        return -1


def _prune_dir_by_size(
    path: str,
    max_bytes: int,
    target_ratio: float = 0.8,
    min_age_seconds: float = 0.0,
) -> tuple[int, int]:
    """按**总体积**清目录（递归子目录）：超上限就从「最早写入」的开始删，直到降回水位。

    这是磁盘缓存的**最后一道保险**：`_prune_dir` 已经按「保留天数 + 文件数」清过一轮，
    但它管不住「天数没到、文件却特别大」的情况 —— 头像一张几百 KB、卡片图几十 KB，
    攒上几千个就是几 GB。本函数不看年龄只看总量，把最旧的先丢。

    细节：
      * 排序键用 mtime、从旧到新删。缓存文件写完就不再改动，「最早写入」即「最早创建」；
        而且这样跨平台语义一致（Linux 上没有真正的「创建时间」，st_ctime 是 inode 变更时间）。
      * 目标水位 = max_bytes * target_ratio（默认 80%）—— 留一段缓冲，免得删到刚好压在上限上、
        下一次清理立刻又被触发（抖动）。
      * `min_age_seconds > 0` 时**跳过太新的文件**：刚写出来、可能正被读取或即将发送的缓存
        一律不动（删了会害得正在跑的 `/cf` 拿不到图）。它们计入总体积、但不参与删除。
      * 删不掉的（被其它线程占着等）直接跳过继续，不中断整轮清理。
      * 全程吞异常：缓存清理绝不能影响主流程。

    返回 (删除文件数, 释放字节数)。
    """
    removed = 0
    freed = 0
    try:
        if max_bytes <= 0 or not os.path.isdir(path):
            return 0, 0
        now = time.time()
        total = 0
        files: list[tuple[float, int, str]] = []
        for root, _dirs, names in os.walk(path):
            for fn in names:
                p = os.path.join(root, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                total += st.st_size
                if min_age_seconds > 0 and now - st.st_mtime < min_age_seconds:
                    continue  # 太新：算进体积，但本轮不碰
                files.append((st.st_mtime, st.st_size, p))
        if total <= max_bytes:
            return 0, 0  # 没超上限：一个都不动
        target = int(max_bytes * max(0.1, min(1.0, float(target_ratio))))
        files.sort(key=lambda t: t[0])  # 最早写入的排前面
        for _mt, sz, p in files:
            if total <= target:
                break
            try:
                os.remove(p)
            except OSError:
                continue  # 正被占用等，跳过留待下轮
            removed += 1
            freed += sz
            total -= sz
    except Exception:  # noqa: BLE001
        pass
    return removed, freed


class ContestDailyPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}

        self.store = Store(self._resolve_data_dir())

        self._last_refresh = 0.0          # 上次抓取时间戳
        self._last_daily_key = None       # 上次日报的 日期字符串
        self._last_daily_time = None      # 上次日报用的 daily_time（改了配置要允许再跑一轮）
        self._refresh_lock = asyncio.Lock()  # 防并发抓取
        self._profile_lock = asyncio.Lock()  # 战绩查询全局互斥（保护第三方接口）
        self._profile_last: dict = {}        # {sender_id: 上次查询时间} 用户级冷却
        self._perm_warned: set = set()       # 已提示过的非法权限等级（避免日志刷屏）
        self._appid_cache = None             # QQ 官方机器人 AppID（拼头像地址用，只找一次）
        # 推送失败治理（日志刷屏 & 无效重试）
        self._push_hint_day = ""             # 「无主动消息权限」提示已打印的日期（每天一次）
        self._push_fail_log: dict = {}       # {失败类别: 上次打日志的时间戳}
        self._push_fail_count: dict = {}     # {失败类别: 累计次数}
        self._remind_fail_count: dict = {}   # {"比赛id|umo": 连续失败次数}
        # 手动推送测试（面板按钮 / /推送测试 指令）：全局互斥，防止连点把群刷爆
        self._test_push_lock = asyncio.Lock()
        self._last_push_result: dict | None = None  # 最近一次「推送一轮」的结果（面板展示用）
        # 面板接口读参数用的「当前请求」替身（离线测试注入用；None = 走 astrbot.api.web.request）
        self._web_req_stub = None
        self._task = asyncio.create_task(self._main_loop())
        # 缓存清理跑在**独立 task**：清理再慢也不影响日报/提醒的判定与发送
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._register_web_apis()            # 插件可视化视图的后端接口（失败不影响本体）

        logger.info(
            "[contest_daily] 插件已加载，数据目录: %s，日报时间: %s，提前量: %s 分钟"
            % (self.store.data_dir, self._cfg("daily_time", "09:00"), self._cfg("remind_minutes", 15))
        )

    # ---------------------------------------------------------------- 可视化视图后端
    #
    # AstrBot 的插件配置表单（_conf_schema.json）**只支持输入控件，没有按钮类型**，
    # 所以「立即推送测试」这个按钮只能做成插件可视化视图（views/panel）：
    # 页面由 Dashboard 以受限 iframe 加载，通过 window.AstrBotPluginView 这个 bridge
    # 回调到这里注册的 Web API。全部注册逻辑都包在 try 里 —— 老版本 AstrBot 没有
    # register_web_api 时只打一条 info 日志，插件照常工作（还有 /推送测试 指令兜底）。

    def _register_web_apis(self) -> None:
        reg = getattr(self.context, "register_web_api", None)
        if not callable(reg) or _web_json_response is None:
            logger.info(
                "[contest_daily] 当前 AstrBot 不支持插件可视化视图，"
                "「推送测试」面板不可用；请在群里 @机器人 发 /推送测试 代替"
            )
            return
        routes = (
            (f"/{PLUGIN_NAME}/status", self.page_status, ["GET"], "竞赛日报运行状态"),
            (f"/{PLUGIN_NAME}/push-test", self.page_push_test, ["POST"], "立即推送测试"),
            (f"/{PLUGIN_NAME}/remind-test", self.page_remind_test, ["POST"], "测试赛前提醒"),
            (f"/{PLUGIN_NAME}/alias", self.page_alias, ["POST"], "保存会话备注"),
            (f"/{PLUGIN_NAME}/reset-sent", self.page_reset_sent, ["POST"], "重置今天的推送状态"),
            (f"/{PLUGIN_NAME}/clear-cache", self.page_clear_cache, ["POST"], "清除战绩查询缓存"),
            (f"/{PLUGIN_NAME}/quote", self.page_quote, ["POST"], "名言库管理（增删/屏蔽）"),
        )
        for route, handler, methods, desc in routes:
            try:
                reg(route, handler, methods, desc)
            except Exception as e:  # noqa: BLE001 单个接口注册失败不该拖垮其它接口
                logger.warning(f"[contest_daily] 注册视图接口 {route} 失败: {type(e).__name__}: {e}")
        logger.info("[contest_daily] 「推送测试」面板接口已注册（打开插件详情页 → 推送测试）")

    # ---------------------------------------------------------------- 基础设施

    def _nk_categories(self) -> tuple[int, ...]:
        """牛客要抓哪些「顶级分类」。

        13 = 牛客系列赛、14 = 高校校赛（含各校同步赛）、15 = 自主创建赛（默认不要）。
        配置里允许写成 [13, 14]、["13", "14"] 或 "13,14" 三种形式，脏值直接丢掉；
        全被丢光时回落到默认值（宁可少配，也别把牛客整个抓空）。
        """
        raw = self._cfg("nowcoder_categories", None)
        cats: list[int] = []
        if isinstance(raw, (list, tuple, set)):
            parts = list(raw)
        elif isinstance(raw, str):
            parts = re.split(r"[,，\s]+", raw.strip())
        else:
            parts = []
        for p in parts:
            try:
                cats.append(int(str(p).strip()))
            except (TypeError, ValueError):
                continue
        return tuple(cats) or NK_CATEGORIES_DEFAULT

    def _cfg(self, key: str, default=None):
        """读配置，AstrBot 的 config 可能是 dict 或对象，两种都兼容。"""
        try:
            if hasattr(self.config, "get"):
                v = self.config.get(key, default)
            else:
                v = getattr(self.config, key, default)
            return default if v is None else v
        except Exception:  # noqa: BLE001
            return default

    # ---------------------------------------------------------------- Web 请求参数
    #
    # 官方文档的读法：from astrbot.api.web import request，然后
    #   request.query.get("k", default) / await request.json(default={})
    # 但这个 `request` 是**上下文代理**，只在 handler 执行期间有效，而且离线冒烟测试里
    # 根本不存在（_web_request 为 None）。所以统一收敛到下面两个方法：
    #   * 运行时 —— 用官方代理；
    #   * 测试时 —— self._web_req_stub 注入一个假的（带 .query + await .json()）。
    # 参数一律「缺省即退回安全默认值」，绝不让读参数失败把接口打成 500。

    _REQ_PARAM_KEYS = (
        "scope", "targets", "contestId", "umo", "alias", "keyword",
        "kind", "action", "id", "text", "by",
    )

    def _req(self):
        """当前请求对象（可能是官方代理、注入的替身，或 None）。"""
        stub = getattr(self, "_web_req_stub", None)
        return stub if stub is not None else _web_request

    async def _params(self) -> dict:
        """把 query 与 POST body 合并成参数字典（body 优先）。读不到就返回 {}。"""
        out: dict = {}
        r = self._req()
        if r is None:
            return out
        try:
            q = getattr(r, "query", None)
            if q is not None:
                for k in self._REQ_PARAM_KEYS:
                    v = q.get(k, None)
                    if v not in (None, ""):
                        out[k] = v
        except Exception:  # noqa: BLE001 query 形态因 AstrBot 版本而异，读不到就算了
            pass
        try:
            body = await r.json(default={})
        except Exception:  # noqa: BLE001
            body = {}
        if isinstance(body, dict):
            for k, v in body.items():
                if v not in (None, ""):
                    out[k] = v
        return out

    def _resolve_scope(self, params: dict, subs: dict) -> tuple[list[str] | None, str | None]:
        """决定这一轮「发给谁」，返回 (目标 umo 列表 或 None 表示全部, 错误文案)。

        三种情况：
          * `scope=selected`（面板勾选）：**只**发勾选的会话。一个都没勾就明确报错 ——
            绝不能悄悄退化成「全推」：面板的按钮就在那儿，一按全群收到消息，正是用户
            要找我们避免的「打扰」。
          * 传了 targets（未标 scope）：按 targets 发（指令通道用）。
          * 什么都没传：全部订阅会话（兼容老前端与指令通道的旧行为）。
        """
        scope = str(params.get("scope") or "").strip().lower()
        raw = params.get("targets")
        if raw is None:
            if scope == "selected":
                return None, (
                    "还没有勾选任何会话 —— 先在「目标会话」里勾上要接收测试消息的群再点，"
                    "免得打扰其它群"
                )
            return None, None
        targets: list[str] = []
        if isinstance(raw, str):
            parts = re.split(r"[,\n;，、]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            parts = [str(x) for x in raw]
        else:
            parts = []
        seen: set[str] = set()
        for p in parts:
            p = str(p).strip()
            if p and p not in seen:
                seen.add(p)
                targets.append(p)
        if not targets:
            if scope == "selected":
                return None, (
                    "还没有勾选任何会话 —— 先在「目标会话」里勾上要接收测试消息的群再点，"
                    "免得打扰其它群"
                )
            return None, None
        if scope == "selected":
            # 勾选模式下只认订阅表里的会话：防止面板/前端把脏数据写进来乱发消息
            known = [u for u in targets if u in subs]
            if not known:
                return None, "勾选的会话都不在订阅表里了，点「刷新状态」重新拉一次列表"
            return known, None
        return targets, None

    # 需要从配置读取的文案模板键（全部可在 AstrBot 插件配置里改）
    TEMPLATE_KEYS = (
        "platform_names",
        "platform_emoji",
        "remind_template",
        "daily_title",
        "daily_section",
        "daily_item",
        "daily_empty",
        "daily_upcoming_section",
        "daily_upcoming_item",
        "upcoming_title",
        "upcoming_item",
        "upcoming_empty",
        "upcoming_nodata",
        "upcoming_capped_note",
        "profile_template",
        "profile_week_section",
        "profile_week_item",
        "profile_week_empty",
        "profile_note",
        "daily_quote",
    )

    def _templates(self) -> dict:
        """收集文案模板配置交给渲染层（渲染层负责与默认值合并，空值自动回退）。"""
        t = {k: self._cfg(k, None) for k in self.TEMPLATE_KEYS}
        # 条目自动补链接开关（日报 + 赛程；渲染层用 item_attach_url 这个名字读）
        try:
            t["item_attach_url"] = bool(self._cfg("daily_item_attach_url", True))
        except Exception:  # noqa: BLE001 配置读不出来就按默认开
            t["item_attach_url"] = True
        return t

    # ---------------------------------------------------------------- 日报名言彩蛋（1.4.0）
    #
    # 1.6.0 起自定义名言有**两个来源**，合成同一个抽取池：
    #   * `daily_quote_extra`（插件配置里粘的，老用法，一行一条）
    #   * `data/quotes.json` 的 `custom`（面板「名言库」里加的，可视化管理）
    # 以及一份**屏蔽名单** `blocked`（面板里点「屏蔽」的内置句子）。

    def _quote_store(self) -> dict:
        """面板托管的自定义名言与屏蔽名单；读不到（老版本数据目录 / 文件坏了）就给空。"""
        empty = {"custom": [], "blocked": []}
        try:
            d = self.store.get_quotes()
        except Exception as e:  # noqa: BLE001 存储异常不该让日报整体失败
            logger.debug(f"[contest_daily] 读 quotes.json 失败: {type(e).__name__}: {e}")
            return empty
        if not isinstance(d, dict):
            return empty
        return {
            "custom": list(d.get("custom") or []),
            "blocked": list(d.get("blocked") or []),
        }

    def _quote_pool(self) -> list:
        """进抽取池的自定义名言 = 配置里粘的 + 面板里加的（按洗过的正文去重）。

        去重是必要的：同一句在两处各存一份，它在池里就占两个名额，被抽中的概率翻倍
        —— 用户会以为随机坏了。按 `_clean_text` 洗过再比，避免「多了个空格」就判为不同。
        返回的仍是**原样的条目**（字符串 / dict 都保留），归一化交给 core.quotes。
        """
        try:
            raw = self._cfg("daily_quote_extra", []) or []
        except Exception:  # noqa: BLE001
            raw = []
        cfg_items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        items: list = []
        seen: set = set()
        for it in list(self._quote_store()["custom"]) + cfg_items:
            key = quote_lib.clean_text(it.get("text") if isinstance(it, dict) else it).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(it)
        return items

    def _quote_blocked(self) -> list:
        """被屏蔽的名言 id 列表（给渲染层过滤用）。"""
        return [str(x) for x in self._quote_store()["blocked"] if str(x).strip()]

    def _quote_cats(self) -> list:
        """启用哪些名言分类；配置写坏了就回退成「全开」。"""
        try:
            v = self._cfg("daily_quote_cats", list(quote_lib.DEFAULT_CATS)) or []
        except Exception:  # noqa: BLE001
            return list(quote_lib.DEFAULT_CATS)
        return list(v) if isinstance(v, (list, tuple)) else [v]

    def _quote_with_source(self) -> bool:
        """名言后面要不要附「—— 出处」。"""
        try:
            return bool(self._cfg("daily_quote_with_source", True))
        except Exception:  # noqa: BLE001
            return True

    def _quote_opts(self) -> dict:
        """日报渲染的名言参数（定时推送与 /日报 两个调用点共用，别再各写一遍）。

        `daily_quote_random_each_time` 关着（默认）时是**按天**抽：同一天无论是定时推送、
        `/日报`、面板连点「推送测试」，还是多个群分别渲染，拿到的都是同一句 ——
        这才是「每天换一句」；开着则每条消息都重抽（同一个群可能一天看到好几句）。
        """
        try:
            random_each = bool(self._cfg("daily_quote_random_each_time", False))
        except Exception:  # noqa: BLE001
            random_each = False
        return {
            "quote_pool": self._quote_pool(),
            "quote_cats": self._quote_cats(),
            "quote_blocked": self._quote_blocked(),
            "quote_source": self._quote_with_source(),
            "quote_fixed_daily": not random_each,
        }

    def _panel_quotes(self) -> dict:
        """面板「名言库」区要的整份数据：**全部**名言（含被屏蔽的）+ 分类摘要。

        用户的原话是「我要看到全部名言」——所以这里不折叠、不省略（内置 60+ 条 + 自建
        最多 200 条都在 `MAX_LIST_ITEMS` 之内），每条都带 id / 来源 / 是否被屏蔽 /
        分类是否启用，面板据此渲染按钮与状态标签。
        """
        pool = self._quote_pool()
        blocked = self._quote_blocked()
        cats = self._quote_cats()
        catalog: list = []
        try:
            rows = quote_lib.items(pool, blocked)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 生成名言清单失败: {type(e).__name__}: {e}")
            rows = []
        # 「分类没启用」与「被屏蔽」要分开显示：面板得能回答「这条为什么没被推送」
        valid_cats = {str(c or "").strip().lower() for c in cats}
        valid_cats = {c for c in valid_cats if c in quote_lib.CATEGORIES or c == "custom"}
        all_on = not valid_cats  # 配置留空 / 写了个不存在的分类 -> 与 core.quotes 一致按全开处理
        for d in rows[: quote_lib.MAX_LIST_ITEMS]:
            cat = str(d.get("cat") or "custom")
            catalog.append(
                {
                    "id": d["id"],
                    "text": d["text"],
                    "by": d["by"],
                    "cat": cat,
                    "catLabel": quote_lib.cat_label(cat),
                    "source": d["source"],
                    "blocked": bool(d["blocked"]),
                    # 自定义恒进池（见 core.quotes._pool 的说明），所以它永远是「启用」的
                    "catOn": True if cat == "custom" else (all_on or cat in valid_cats),
                }
            )
        try:
            st = quote_lib.stats(cats, pool, blocked)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 统计名言库失败: {type(e).__name__}: {e}")
            st = {}
        return {
            "items": catalog,
            "truncated": len(rows) > len(catalog),
            "stats": st,
            "cats": quote_lib.cat_choices(),
            "enabledCats": self._quote_cats(),
            "maxLength": quote_lib.MAX_QUOTE_LEN,
            "maxCustom": getattr(self.store, "MAX_CUSTOM_QUOTES", 200),
            "configExtra": len(self._quote_pool_raw()),
        }

    def _quote_pool_raw(self) -> list:
        """配置项 `daily_quote_extra` 的原始条数（面板提示「配置里还粘着 N 条」用）。"""
        try:
            raw = self._cfg("daily_quote_extra", []) or []
        except Exception:  # noqa: BLE001
            return []
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        return [x for x in items if quote_lib.clean_text(x.get("text") if isinstance(x, dict) else x)]

    # ---------------------------------------------------------------- QQ 官方机器人探测

    def _qq_appid(self) -> str:
        """QQ 官方机器人的 AppID。读不到返回空串，且只找一次。

        唯一用途：判断当前接的是不是 QQ 官方机器人适配器 —— 是的话启动预检会提醒
        「主动消息无权限」（见 `_warn_if_push_unavailable`）。1.7.2 起与头像无关
        （QQ 头像兜底已按用户要求删除）。
        """
        cached = getattr(self, "_appid_cache", None)
        if cached is not None:
            return cached
        appid = ""
        try:
            cfg = self.context.get_config()
            plats = cfg.get("platform") if hasattr(cfg, "get") else None
            for p in plats or []:
                if not hasattr(p, "get"):
                    continue
                if str(p.get("type") or "").startswith("qq_official") and p.get("appid"):
                    appid = str(p["appid"]).strip()
                    break
        except Exception:  # noqa: BLE001 读全局配置失败不影响查询
            appid = ""
        self._appid_cache = appid
        return appid

    @staticmethod
    def _card_log(msg: str) -> None:
        """卡片层的日志回调：card.py 刻意不依赖 astrbot，日志靠回调传出来。"""
        logger.info(f"[contest_daily] {msg}")

    # ---------------------------------------------------------------- 战绩查询

    def _fetcher(self) -> ProfileFetcher:
        """按当前配置构造查询器（很轻，每次现建即可，改配置能立刻生效）。"""
        return ProfileFetcher(
            cache=self.store,
            week_days=int(self._cfg("profile_week_days", 7)),
            cache_minutes=int(self._cfg("profile_cache_minutes", 10)),
            result_ttl_hours=int(self._cfg("profile_result_cache_hours", 24)),
            problemset_ttl_hours=int(self._cfg("profile_problemset_ttl_hours", 24)),
            status_count=int(self._cfg("profile_status_count", 10000)),
        )

    def _profile_cooldown_left(self, sid: str) -> int:
        """距离下次可查询还剩几秒（0 = 可以查）。首次查询时 _profile_last 无记录 -> 返回 0。"""
        try:
            cd = int(self._cfg("profile_cooldown_seconds", 15))
        except Exception:  # noqa: BLE001
            cd = 15
        if cd <= 0:
            return 0
        elapsed = time.time() - self._profile_last.get(sid, 0.0)
        if elapsed >= cd:
            return 0
        return int(cd - elapsed) + 1

    def _profile_mark(self, sid: str) -> None:
        self._profile_last[sid] = time.time()
        # 顺手清掉很老的记录，防止字典无限膨胀
        if len(self._profile_last) > 500:
            cutoff = time.time() - 3600
            for k in [k for k, v in self._profile_last.items() if v < cutoff]:
                self._profile_last.pop(k, None)

    def _resolve_data_dir(self) -> str:
        """按 AstrBot 规范把数据放 data/ 目录；逐级回退，保证任何版本都能跑。"""
        # 1) 新版 StarTools
        try:
            from astrbot.api.star import StarTools  # type: ignore

            d = StarTools.get_data_dir()
            path = os.path.join(str(d), "astrbot_plugin_contest_daily")
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:  # noqa: BLE001
            pass
        # 2) 通用 data 路径
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # type: ignore

            path = os.path.join(str(get_astrbot_data_path()), "plugin_data", "astrbot_plugin_contest_daily")
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:  # noqa: BLE001
            pass
        # 3) 兜底：插件目录旁（不理想但不会崩）
        path = os.path.join(_PLUGIN_DIR, "data")
        os.makedirs(path, exist_ok=True)
        return path

    async def _push(self, umo: str, text: str) -> tuple[bool, str]:
        """主动推送一条文本消息到指定会话。失败只记日志，不抛异常。

        返回 `(是否成功, 失败类别)`，失败类别取值为：

        * `""`     —— 成功
        * `"perm"` —— **平台永久性拒绝**（机器人没有「主动消息」权限，错误码 40034102）。
          重试永远无效，调用方应立刻放弃并记账，别每 30 秒刷一次；
        * `"other"` —— 网络抖动等临时故障，调用方可以有限重试。
        """
        try:
            await self.context.send_message(umo, MessageChain().message(text))
            return True, ""
        except Exception as e:  # noqa: BLE001
            kind = "perm" if _is_perm_denied(str(e)) else "other"
            self._log_push_failure(umo, kind, e)
            return False, kind

    def _log_push_failure(self, umo: str, kind: str, err: Exception) -> None:
        """推送失败日志：平台限制每天提示一次，其余按类别去重，避免刷屏。

        之前是每个会话一条 `logger.error`：3 个群 = 3 条 ERROR，还会被误当成插件坏了。
        现在平台性拒绝降为 `warning` 且每天只讲一次道理，之后降为 debug。
        """
        n = self._push_fail_count.get(kind, 0) + 1
        self._push_fail_count[kind] = n

        if kind == "perm":
            today = datetime.now(TZ_CN).strftime("%Y-%m-%d")
            if self._push_hint_day == today:
                logger.debug(
                    f"[contest_daily] 推送到 {umo} 仍被平台拒绝（今天累计 {n} 次），"
                    f"不再重复提示"
                )
                return
            self._push_hint_day = today
            logger.warning(f"[contest_daily] 推送到 {umo} 被平台拒绝：{err}\n[contest_daily] {PUSH_PERM_HINT}")
            return

        now = time.time()
        if now - self._push_fail_log.get(kind, 0.0) < PUSH_FAIL_LOG_INTERVAL:
            return  # 同类故障刚报过，安静一会儿
        self._push_fail_log[kind] = now
        logger.warning(
            f"[contest_daily] 推送到 {umo} 失败（累计 {n} 次；"
            f"同类日志 {PUSH_FAIL_LOG_INTERVAL // 60} 分钟内只打一条）: "
            f"{type(err).__name__}: {err}"
        )

    # ---------------------------------------------------------------- 权限控制

    # 默认需要授权的指令（查询类指令不受限，管理类受限）
    # ⚠️ 已被配置项 command_permissions 取代，保留仅为兼容老配置（未在
    #    command_permissions 里出现的指令，仍按这份名单判「授权才能用」）。
    DEFAULT_RESTRICTED = (
        "订阅日报",
        "退订日报",
        "订阅列表",
        "刷新赛程",
        "推送测试",
        "提醒测试",
        "群备注",
    )

    # 逐指令权限等级（可在 AstrBot 插件配置的「各指令权限等级」里逐条改）
    #   all    所有人可用
    #   admin  授权用户可用（AstrBot 管理员 / 白名单 / 群主·群管理员）
    #   strict 仅 AstrBot 管理员与白名单可用（**忽略**群主/群管理员角色）
    #   off    关闭该指令（被调用时回复拒绝文案）
    PERM_LEVELS = ("all", "admin", "strict", "off")
    PERM_LABELS = {
        "all": "所有人",
        "admin": "授权用户",
        "strict": "白名单用户",
        "off": "已关闭",
        "invalid": "正确的用法",  # 不是权限档，仅用于「无效指令」的文案占位符 {need}
    }
    DEFAULT_PERMISSIONS = {
        "日报": "all",
        "赛程": "all",
        "cf": "all",
        "nk": "all",
        "whoami": "all",
        "名言": "all",
        "订阅日报": "admin",
        "退订日报": "admin",
        "订阅列表": "admin",
        "刷新赛程": "admin",
        "推送测试": "admin",
        "提醒测试": "admin",
        "群备注": "admin",
        "清缓存": "admin",
    }
    # 无权限时的默认回复（可在配置里改，见 deny_template）
    DEFAULT_DENY_TEMPLATE = "前方的功能以后再来探索吧！趁这时间多多刷题哦！"

    def _admin_users(self) -> set:
        """配置里的授权用户 ID 白名单。兼容列表与「逗号/换行分隔的字符串」两种写法。"""
        v = self._cfg("admin_users", [])
        if isinstance(v, str):
            v = v.replace("，", ",").replace(",", "\n").splitlines()
        try:
            return {str(x).strip() for x in v if str(x).strip()}
        except TypeError:
            return set()

    def _legacy_restricted(self) -> list:
        """旧配置 admin_only_commands 归一成列表（兼容老用户）。"""
        v = self._cfg("admin_only_commands", list(self.DEFAULT_RESTRICTED))
        if isinstance(v, str):
            v = v.replace("，", ",").replace(",", "\n").splitlines()
        try:
            return [str(x).strip() for x in v if str(x).strip()]
        except TypeError:
            return list(self.DEFAULT_RESTRICTED)

    def _is_restricted(self, cmd_name: str) -> bool:
        """（兼容旧接口）该指令是否在「需要授权」名单里。"""
        return cmd_name in self._legacy_restricted()

    def _perm_map(self) -> dict:
        """解析配置里的逐指令等级 -> {指令名: 等级}。

        * 等级值**写错**时（如 "default"、"admn"）：回落到**该指令自己的默认等级**
          （查询类 all、管理类 admin），只影响写错的这一条 —— 绝不因为一个笔误
          把整条指令锁死。2026-10-15 实测翻车案例：配置里出现了不存在的 "default"，
          旧逻辑一律按 admin 从严，结果默认对所有人开放的 /cf 也被拦，群里查战绩
          全部「没有回应」。
        * command_permissions 里没提到的指令：看旧的 admin_only_commands 名单，
          再没有就用 DEFAULT_PERMISSIONS
        """
        out: dict = {}
        raw = self._cfg("command_permissions", None)
        if isinstance(raw, str) and raw.strip():
            # 个别 WebUI 会把 dict 配置存成 JSON 字符串，尽力解析回来
            try:
                raw = json.loads(raw)
            except Exception:  # noqa: BLE001
                raw = None
        if isinstance(raw, dict):
            for k, v in raw.items():
                key = str(k or "").strip()
                lv = str(v or "").strip().lower()
                if not key or not lv:
                    continue
                if lv not in self.PERM_LEVELS:
                    fallback = self.DEFAULT_PERMISSIONS.get(key, "all")
                    if key not in self._perm_warned:
                        self._perm_warned.add(key)
                        logger.warning(
                            f"[contest_daily] 配置「各指令权限等级」里 {key}={v!r} 不是合法等级，"
                            f"已回落到该指令的默认等级 {fallback}（可选：{' / '.join(self.PERM_LEVELS)}）"
                        )
                    lv = fallback
                out[key] = lv
        for name in self._legacy_restricted():
            out.setdefault(name, "admin")
        return out

    def _cmd_level(self, cmd_name: str) -> str:
        """某条指令当前生效的权限等级。"""
        try:
            return self._perm_map().get(cmd_name) or self.DEFAULT_PERMISSIONS.get(cmd_name, "all")
        except Exception:  # noqa: BLE001 配置写坏也不能拦住指令
            return self.DEFAULT_PERMISSIONS.get(cmd_name, "all")

    def _is_authorized(self, event, allow_group_role: bool | None = None) -> tuple:
        """判断发送者能否执行受限指令。三层判断，任意一层通过即放行。

        ① AstrBot 全局管理员（WebUI 里的管理员 ID 配置）
        ② 插件配置的「授权用户 ID」白名单  ← 最可靠，官方机器人下建议用这层
        ③ 群内角色为群主 / 管理员           ← 仅在适配器能提供角色信息时生效

        allow_group_role=False 时跳过第 ③ 层（对应权限等级 strict：只认白名单与全局管理员）。

        背景：QQ 官方机器人的「获取群成员角色」接口目前是内邀制（错误码 11253），
        普通机器人拿不到 member_role，因此第 ③ 层在官方适配器下大概率不触发，
        请以第 ① / ② 层为准。而协议的适配器（aiocqhttp / NapCat）能正常返回 role。
        """
        # ① AstrBot 全局管理员
        try:
            if event.is_admin():
                return True, "astrbot_admin"
        except Exception:  # noqa: BLE001
            pass

        # ② 白名单
        try:
            sid = str(event.get_sender_id() or "").strip()
        except Exception:  # noqa: BLE001
            sid = ""
        if sid and sid in self._admin_users():
            return True, "whitelist"

        # ③ 群内角色
        if allow_group_role is None:
            allow_group_role = bool(self._cfg("allow_group_admin", True))
        if allow_group_role:
            role = str(getattr(event, "role", "") or "").lower()
            if role in ("owner", "admin"):
                return True, "group_" + role

        return False, ""

    def _deny_text(self, event, cmd_name: str, level: str = "admin") -> str:
        """拒绝提示。文案可在配置里改（deny_template），默认只回一句鼓励语。

        可用占位符（`\\n` 可写换行、全角括号 ｛｝ 与中文名都认）：
          {cmd} 指令名   {user} 昵称   {user_id} 用户 ID
          {level} 权限等级英文值   {need} 权限等级中文说明

        想让用户能自助申请授权，就把 {user_id} 写进模板，例如：
          前方的功能以后再来探索吧！趁这时间多多刷题哦！\\n（你的 ID：{user_id}）
        """
        def _safe(fn, default="未知"):
            try:
                v = fn()
                return str(v) if v not in (None, "") else default
            except Exception:  # noqa: BLE001
                return default

        kw = {
            "cmd": cmd_name,
            "user": _safe(event.get_sender_name, "群成员"),
            "user_id": _safe(event.get_sender_id, "获取失败"),
            "level": level,
            "need": self.PERM_LABELS.get(level, "授权用户"),
        }
        tpl = str(self._cfg("deny_template", "") or "").strip() or self.DEFAULT_DENY_TEMPLATE
        try:
            out = render.render_text(tpl, **kw)
        except Exception as e:  # noqa: BLE001 模板写坏也不能炸消息
            logger.warning(f"[contest_daily] 拒绝文案模板渲染失败，用默认文案: {type(e).__name__}: {e}")
            out = self.DEFAULT_DENY_TEMPLATE
        return out or self.DEFAULT_DENY_TEMPLATE

    def _allowed_for(self, event, level: str) -> bool:
        """当前发送者在该权限等级下是否放行（用于准入检查与 /whoami 展示）。"""
        if level == "all":
            return True
        if level == "off":
            return False
        # strict 忽略群角色；admin 是否认群角色仍由 allow_group_admin 配置决定
        return self._is_authorized(
            event, allow_group_role=(False if level == "strict" else None)
        )[0]

    def _error_reply(self, fallback: str) -> str | None:
        """超时 / 网络错误 / 异常的出口：默认**不回复**（只写日志）。

        按群主要求：这类「不是用户输入问题」的失败不暴露到群里，静默即可；
        关闭 `silent_on_error` 则恢复可重试的提示。
        返回 None = 不要回复。
        """
        if bool(self._cfg("silent_on_error", True)):
            return None
        return fallback

    def _invalid_reply(self, event, cmd_name: str) -> str:
        """「指令没生效（输入无效）」的统一回复 —— **与无权限完全同一句**。

        按群主要求：缺参数 / 参数格式不对 / 查不到该用户，都与无权限用同一条文案
        （仍然读 deny_template，所以改文案时两处一起变），具体原因只写 AstrBot 日志。
        """
        return self._deny_text(event, cmd_name, "invalid")

    def _invalid_or(self, event, cmd_name: str, reason: str, fallback: str) -> str:
        """无效指令的统一出口。

        * `invalid_reply_like_deny = true`（默认，群主要求）：**与无权限回同一条文案**，
          具体原因（缺参数 / 参数格式错 / 查不到该用户）只写日志
        * `false`：保持原有的信息量（用法说明 / 失败原因照原样回给用户）

        注意：网络故障、超时**不属于**无效指令，调用方不会走这里（见 _profile_flow）。
        """
        if bool(self._cfg("invalid_reply_like_deny", True)):
            logger.info(f"[contest_daily] 无效指令 /{cmd_name}（{reason}）-> 按无权限文案回复")
            return self._invalid_reply(event, cmd_name)
        logger.info(f"[contest_daily] 无效指令 /{cmd_name}（{reason}）-> 回详细信息")
        return fallback

    def _wake_prefixes(self) -> list:
        """唤醒前缀。优先读 AstrBot 全局配置的 wake_prefix，读不到就用 '/'。"""
        try:
            cfg = self.context.get_config()
            wp = cfg.get("wake_prefix") if hasattr(cfg, "get") else None
            if isinstance(wp, str) and wp.strip():
                return [wp.strip()]
            if isinstance(wp, (list, tuple)):
                got = [str(x).strip() for x in wp if str(x).strip()]
                if got:
                    return got
        except Exception:  # noqa: BLE001
            pass
        return ["/"]

    def _known_command_tokens(self) -> set:
        """所有已注册指令的**首词**（含其他插件注册的指令），用于避免抢答。

        直接读 AstrBot 的全局 handler 注册表，所以别的插件装/卸、改指令名都能实时跟上。
        读不到注册表时返回空集 —— 调用方据此**放弃回复**（宁可沉默，也不能抢答别人）。
        """
        try:
            from astrbot.core.star.filter.command import CommandFilter  # noqa: F401
            from astrbot.core.star.star_handler import (
                EventType,
                star_handlers_registry,
            )
        except Exception:  # noqa: BLE001
            return set()
        tokens: set = set()
        try:
            for md in star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent
            ):
                for flt in getattr(md, "event_filters", None) or []:
                    getter = getattr(flt, "get_complete_command_names", None)
                    names = getter() if callable(getter) else None
                    if not names:
                        cn = getattr(flt, "command_name", None)
                        names = [cn] if cn else []
                    for full in names:
                        parts = str(full or "").split()
                        if parts:
                            tokens.add(parts[0])
        except Exception:  # noqa: BLE001
            return set()
        return tokens

    def _deny_if_restricted(self, event, cmd_name: str) -> str | None:
        """指令准入检查。返回 None = 放行；否则返回应当回复的拒绝文本。

        等级取自配置（command_permissions），默认查询类 all、管理类 admin。
        """
        level = self._cmd_level(cmd_name)
        if level == "all":
            return None
        if level == "off":
            logger.info(f"[contest_daily] 指令 {cmd_name} 已在配置里关闭，拒绝 {_safe_sid(event)}")
            return self._deny_text(event, cmd_name, level)
        if self._allowed_for(event, level):
            return None
        logger.info(
            f"[contest_daily] 拒绝 {cmd_name}（等级={level}）：sender={_safe_sid(event)} 不在授权范围"
        )
        return self._deny_text(event, cmd_name, level)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def on_unmatched_command(self, event: AstrMessageEvent):
        """未被任何插件识别的指令 -> 回同一句「无权限」文案。

        触发条件（三条同时满足，宁可沉默也不误伤）：
          ① 机器人被 @ 或唤醒（官方机器人群里只有 @ 才收得到消息，噪声天然很低）
          ② 消息以唤醒前缀开头（默认 `/`）—— 纯聊天不受影响，仍走 LLM
          ③ 首词不在**所有已注册指令名**里（注册表实时读，含其他插件的指令）

        `priority=-100`：AstrBot 按 priority 从大到小执行 handler，所以这个
        兜底会**最后跑**，前面被其他指令接走的消息已经 stop_event，直接跳过。
        """
        try:
            if not bool(self._cfg("unknown_command_reply", True)):
                return
            if not getattr(event, "is_at_or_wake_command", False):
                return
            if event.is_stopped() or getattr(event, "_has_send_oper", False):
                return
            text = str(event.get_message_str() or "").strip()
            head = None
            for prefix in self._wake_prefixes():
                if prefix and text.startswith(prefix):
                    head = text[len(prefix) :].strip()
                    break
            if not head:  # 不是指令形态（没带唤醒前缀），交给后面的流程
                return
            parts = head.split()
            token = parts[0] if parts else ""
            if not token:
                return
            known = self._known_command_tokens()
            if not known:
                # 读不到注册表 -> 无法判断是否属于别的插件，放弃回复（绝不抢答）
                logger.debug("[contest_daily] 无法读取指令注册表，跳过未识别指令兜底")
                return
            if token in known:
                return
            logger.info(
                f"[contest_daily] 未识别的指令「{token}」：sender={_safe_sid(event)}，"
                f"按无权限文案回复"
            )
            yield event.plain_result(self._invalid_reply(event, token))
            event.stop_event()  # 顺带拦住后续 LLM，避免同一句话被回两遍
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[contest_daily] 未识别指令兜底异常（忽略）: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 调度

    async def _main_loop(self):
        """主循环：每 30 秒醒来一次，按各自的节奏触发三条时间线。"""
        await asyncio.sleep(5)  # 启动后稍等，避开 AstrBot 初始化高峰
        logger.info("[contest_daily] 调度循环已启动")
        levels = ", ".join(f"/{k}={self._cmd_level(k)}" for k in self.DEFAULT_PERMISSIONS)
        logger.info(f"[contest_daily] 各指令权限等级：{levels}（可在配置「各指令权限等级」里改）")
        await self._warn_if_push_unavailable()
        while True:
            try:
                now = time.time()
                # ① 抓取（联网）
                if now - self._last_refresh >= max(5, int(self._cfg("refresh_minutes", 30))) * 60:
                    await self._job_refresh()
                # ② 日报（只读缓存）
                await self._job_daily_if_due(now)
                # ③ 赛前提醒（只读缓存）
                await self._job_remind(now)
                # 缓存清理**不在这里**：用户要求「清理不得干扰日报主体工作」，所以它跑在
                # 自己的 task 上（见 _cleanup_loop），清理耗时再长也拖不慢这里的 tick。
            except asyncio.CancelledError:
                logger.info("[contest_daily] 调度循环被取消")
                raise
            except Exception as e:  # noqa: BLE001 绝不让循环因异常退出
                logger.error(f"[contest_daily] 调度循环异常: {type(e).__name__}: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def _cleanup_loop(self) -> None:
        """独立的缓存清理时间线（与抓取 / 日报 / 提醒**分开跑**）。

        用户明确要求「定期清理要预留余量，但清理操作不得干扰日报主体工作，不得影响当前
        任务或当前指令」，所以清理从主循环里摘了出来：

          * 跑在**自己的 task** 上 —— 清几千个文件花十几秒也只拖慢自己，不会推迟日报 /
            提醒的判定时刻；
          * 只删**可重建的缓存文件**，从不碰 subscriptions.json / sent_log.json，
            也不推任何消息 —— 清理再激进也不会让日报少发一条；
          * 真删之前还有两道礼貌让路（见 _job_cleanup / _prune_cards_by_size）：
            有战绩查询在跑就跳过本轮 cards/ 清理；最后写入 5 分钟内的文件一律不碰。

        任何异常都吞掉，绝不让这个 task 死掉（死了就再也没人清缓存了）。
        """
        await asyncio.sleep(CLEANUP_FIRST_DELAY)  # 先歇一会儿，避开插件加载 / 首次抓取高峰
        logger.info("[contest_daily] 缓存清理时间线已启动（独立于日报与提醒）")
        while True:
            try:
                await self._job_cleanup()
            except asyncio.CancelledError:
                logger.info("[contest_daily] 缓存清理时间线被取消")
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[contest_daily] 缓存清理失败（不影响日报/提醒）: {type(e).__name__}: {e}"
                )
            try:
                gap = max(5, int(self._cfg("cache_cleanup_minutes", 30))) * 60
            except (TypeError, ValueError):
                gap = 1800
            await asyncio.sleep(gap)

    async def _warn_if_push_unavailable(self) -> None:
        """启动预检：QQ 官方机器人默认没有主动消息权限，定时推送**必然**失败。

        与其等用户在群里等日报、再盯着满屏 ERROR 找原因，不如启动时就说清楚。
        只在「确实已有订阅」时才唠叨——没用定时推送的人不该看到这条警告。
        预检本身绝不影响启动（任何异常都吞掉）。
        """
        try:
            if not self._qq_appid():
                return  # 不是 QQ 官方机器人适配器（OneBot 个人号等），主动推送正常
            subs = await asyncio.to_thread(self.store.list_subscriptions)
            if not subs:
                return  # 还没有人订阅，用不上定时推送
            self._push_hint_day = datetime.now(TZ_CN).strftime("%Y-%m-%d")  # 今天不再重复提示
            logger.warning(
                f"[contest_daily] 当前平台是 QQ 官方机器人，且已有 {len(subs)} 个订阅会话。"
                f"\n[contest_daily] {PUSH_PERM_HINT}"
            )
        except Exception as e:  # noqa: BLE001 预检失败绝不影响启动
            logger.debug(f"[contest_daily] 推送权限预检跳过: {type(e).__name__}: {e}")

    async def terminate(self):
        """插件卸载/停用时取消后台任务（调度循环 + 缓存清理各一个）。"""
        for task in (self._task, getattr(self, "_cleanup_task", None)):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        logger.info("[contest_daily] 插件已卸载")

    # ---------------------------------------------------------------- 三条时间线

    async def _job_refresh(self) -> bool:
        """抓取层：联网拉两源 -> 写缓存。串行 + 限速，避免触发风控。"""
        if self._refresh_lock.locked():
            return False
        async with self._refresh_lock:
            self._last_refresh = time.time()
            sources = tuple(self._cfg("sources", ["cf", "nowcoder"]))
            try:
                # fetch_all 是同步阻塞的（urllib），丢到线程池里跑，别卡住事件循环
                contests, meta = await asyncio.to_thread(
                    fetch_all, sources, self._nk_categories()
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[contest_daily] 抓取失败: {type(e).__name__}: {e}")
                return False

            if not contests:
                logger.warning(f"[contest_daily] 抓取到 0 场，保留旧缓存。warnings={meta.get('warnings')}")
                return False

            await asyncio.to_thread(self.store.save_contests, contests, meta)
            detail = " ".join(f"{k}={v['count']}" for k, v in meta.get("sources", {}).items())
            logger.info(f"[contest_daily] 抓取完成: {len(contests)} 场（{detail}）")
            for w in meta.get("warnings", []):
                logger.warning(f"[contest_daily] {w}")
            # 顺手预热战绩用的题库映射（缓存有效期内只是读一次本地文件，几乎零成本）
            await self._warm_profile_cache()
            return True

    async def _job_cleanup(self) -> None:
        """定期清理各类缓存（默认每 cache_cleanup_minutes 分钟一次）。

        这是**独立于日报 / 提醒**的一条时间线（见 _cleanup_loop）：只删可重建的缓存文件，
        从不碰订阅表，也不发任何消息 —— 它慢一点、卡一下都不会影响日报主体工作。

        清这几样，都只做「删」，不做重算：
          * profile_cache：过期条目 + 超过条数上限的条目
            （题库映射这类全局共享键不参与裁剪，见 Store.SHARED_KEYS）
          * sent_log：已结束超过 2 小时的提醒记录
          * cards/（卡片图 + 头像）：先按「保留天数 + 每目录 500 个文件」清，
            再做一道**体积兜底**（见下）
          * contests_cache 不裁剪：它只存当前赛程，体量由数据源决定，天然有界

        体积兜底有**两个触发条件**，取先到者：
          ① 缓存自己涨太多：cards/ 总占用 > card_cache_max_mb（默认 512 MB）
          ② 磁盘快满了：所在卷剩余空间 < card_cache_min_free_mb（默认 300 MB）——
             这时即使缓存没到自己上限，也要先腾出「缺的那部分」给 AstrBot / Python /
             日志留出余量（小盘上这条比 ① 更容易先触发）
        按 1.5 GB 的盘算：缓存最多 512 MB + 随时预留 300 MB，剩下约 700 MB 留给系统。
        删除一律按「最早写入优先」，并**跳过最后写入 5 分钟内的文件**：刚生成、可能正在
        被读取或马上要发出去的一律不碰，免得害了正在跑的 /cf。

        还有一道礼貌让路：有战绩查询在跑时，整个 cards/ 部分跳过本轮（下轮再清）。

        注意：只有真的删掉了东西才写盘，避免为了「什么都没清」而反复写文件。
        """
        try:
            cap = int(self._cfg("profile_cache_max_entries", 200))
        except (TypeError, ValueError):
            cap = 200
        try:
            removed = await asyncio.to_thread(self.store.cleanup_profile_cache, cap)
        except Exception as e:  # noqa: BLE001
            removed = 0
            logger.warning(f"[contest_daily] 战绩缓存清理失败: {type(e).__name__}: {e}")
        sent_removed = 0
        try:
            contests, _ = await asyncio.to_thread(self.store.load_contests)
            if contests:
                sent_removed = await asyncio.to_thread(self.store.cleanup_sent, contests)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 提醒记录清理失败: {type(e).__name__}: {e}")
        # 卡片图与头像的磁盘缓存：按名覆盖的卡片图不会无限长，但头像缓存是 md5 命名、
        # 只增不减（含失败标记），所以定期按「保留天数 + 总量上限」清一次。
        # 纯磁盘操作，不占内存；失败只记日志。
        files_removed = 0
        size_freed = 0
        cards_dir = os.path.join(self.store.data_dir, "cards")
        # 让路：正在渲染战绩卡片时，cards/ 里可能就有它马上要读的文件，整块跳过本轮。
        # （上面的 profile_cache / sent_log 是 json，与渲染无关，照常清。）
        if self._profile_lock.locked():
            logger.debug("[contest_daily] 有战绩查询在跑，卡片缓存清理本轮让路")
        else:
            try:
                keep_days = max(1, int(self._cfg("card_cache_keep_days", 30)))
                files_removed = await asyncio.to_thread(
                    _prune_dir, cards_dir, keep_days * 86400, 500
                )
                avatar_dir = os.path.join(cards_dir, "avatar_cache")
                files_removed += await asyncio.to_thread(
                    _prune_dir, avatar_dir, keep_days * 86400, 500
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[contest_daily] 卡片/头像缓存清理失败: {type(e).__name__}: {e}")

            # 体积兜底（最后一道保险）：按「天数 + 文件数」清完，再看**总占用**和**磁盘余量**。
            # 上面那套是按年龄/数量算的，管不住「天数没到、文件却特别大」和小盘被吃满。
            n_by_size, size_freed = await self._prune_cards_by_size(cards_dir)
            files_removed += n_by_size

        if removed or sent_removed or files_removed:
            extra = f"（其中体积兜底释放 {size_freed / 1048576:.0f} MB）" if size_freed else ""
            logger.info(
                f"[contest_daily] 缓存清理：战绩 {removed} 条 / 提醒记录 {sent_removed} 条 / "
                f"卡片图与头像 {files_removed} 个文件{extra}"
            )

    async def _prune_cards_by_size(self, cards_dir: str) -> tuple[int, int]:
        """体积兜底：按「自身上限 + 磁盘余量」决定腾多少，删最早写入的。

        两个触发条件取先到者（详见 _job_cleanup 的说明）：
          ① cards/ 总占用 > card_cache_max_mb
          ② 所在卷剩余 < card_cache_min_free_mb（小盘场景的主力）
        触发 ② 时预算会被压到「当前体积 − 需要腾出的空间」，必要时把缓存清空，
        优先保住磁盘余量 —— 缓存都是可重建的，磁盘满了才是真事故。

        删除时跳过最后写入 5 分钟内的文件（CLEANUP_MIN_FILE_AGE），不碰可能正在被读的。
        纯 to_thread 阻塞操作，不占事件循环。返回 (删除文件数, 释放字节数)。
        """
        try:
            max_mb = int(self._cfg("card_cache_max_mb", 512))
        except (TypeError, ValueError):
            max_mb = 512
        if max_mb <= 0:
            return 0, 0  # 0 = 用户明确关掉了体积兜底
        try:
            min_free_mb = int(self._cfg("card_cache_min_free_mb", 300))
        except (TypeError, ValueError):
            min_free_mb = 300
        try:
            budget = max_mb * 1048576
            before = await asyncio.to_thread(_dir_size_bytes, cards_dir)
            free = await asyncio.to_thread(_disk_free_bytes, cards_dir)

            reasons: list[str] = []
            if before > budget:
                reasons.append(f"缓存 {before / 1048576:.0f} MB 超过上限 {max_mb} MB")
            if min_free_mb > 0 and 0 <= free < min_free_mb * 1048576:
                need = min_free_mb * 1048576 - free
                reasons.append(f"磁盘只剩 {free / 1048576:.0f} MB，低于预留 {min_free_mb} MB")
                budget = min(budget, before - need)  # 腾出缺的那部分
            if not reasons:
                return 0, 0

            n_del, freed = await asyncio.to_thread(
                _prune_dir_by_size, cards_dir, max(1, budget), 0.8, CLEANUP_MIN_FILE_AGE
            )
            after_free = await asyncio.to_thread(_disk_free_bytes, cards_dir)
            tail = f"，磁盘剩余 {after_free / 1048576:.0f} MB" if after_free >= 0 else ""
            logger.info(
                "[contest_daily] 缓存体积兜底（%s）：已按「最早写入」删除 %d 个文件、"
                "释放 %.0f MB，现约 %.0f MB%s"
                % (
                    "；".join(reasons),
                    n_del,
                    freed / 1048576,
                    (before - freed) / 1048576,
                    tail,
                )
            )
            if after_free >= 0 and min_free_mb > 0 and after_free < min_free_mb * 1048576:
                logger.warning(
                    f"[contest_daily] 磁盘剩余仍只有 {after_free / 1048576:.0f} MB（没到预留的 "
                    f"{min_free_mb} MB）—— 图片缓存已经清光了，空间是被别的文件占着的，"
                    f"请手动清理磁盘"
                )
            return n_del, freed
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 缓存体积清理失败: {type(e).__name__}: {e}")
            return 0, 0

    async def _warm_profile_cache(self) -> None:
        """后台预热 CF 题库映射（约 2.2MB，全局共用）。

        目的：用户第一次 /cf 查询时不用再等这 2.2MB 下载。
        失败不影响任何功能，只记一条 debug 日志（下次刷新还会再试）。
        """
        try:
            ok = await asyncio.to_thread(self._fetcher().warm)
            if ok:
                logger.debug("[contest_daily] 战绩题库映射已预热")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[contest_daily] 预热题库映射失败（不影响功能）: {type(e).__name__}: {e}")

    async def _sync_daily_time_change(self, today: str, target: str) -> bool:
        """检测 `daily_time` 是不是刚被改过；是的话把「今天已推送」记录清零。

        用户 1.2.17 明确要求：「时间一旦更改后，刷新今天的推送状态到 0，其他已经发过的群
        也要重新测试」。起因是他调完群权限想验证效果，把时间改到 12:42 却发现**三个群全没反应**
        —— 因为其中两个群当天已经成功收到过日报，被 `dailySent` 静默跳过了。

        返回是否真的重置了（调用方据此**临时放行一轮**，不受内存节流标记阻挡）。

        三个细节：
          * 判据是**存盘的** `dailyTime` 指纹（date + time），不是内存变量 —— 在 AstrBot 里
            保存插件配置往往会重载插件，内存变量会被清空，只靠内存会漏判；
          * 只在「指纹日期 == 今天」时才算「改了时间」。指纹是昨天的说明今天还没跑过任何一轮，
            当前根本不存在「今天的已推送记录」，无需清零；
          * 重置后**立刻**把新时间写回指纹，保证这次变更只清算一次（否则每个 30 秒的 tick
            都会再清一遍）。
        """
        if not bool(self._cfg("daily_reset_on_time_change", True)):
            return False
        try:
            prev = await asyncio.to_thread(self.store.get_daily_time)
        except Exception as e:  # noqa: BLE001 读不到指纹不该影响日报本身
            logger.debug(f"[contest_daily] 读取日报时间指纹失败: {type(e).__name__}: {e}")
            return False
        if prev.get("date") != today or prev.get("time") == target:
            return False
        try:
            n = await asyncio.to_thread(self.store.reset_daily_sent, today)
            await asyncio.to_thread(self.store.set_daily_time, today, target)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 重置当天推送状态失败: {type(e).__name__}: {e}")
            return False
        logger.info(
            f"[contest_daily] 检测到日报时间从 {prev.get('time')} 改为 {target}，"
            f"已把「{today} 已推送」的 {n} 条记录清零 —— 所有订阅会话都会重新收一遍日报"
        )
        return True

    async def _push_daily_to_subscribers(
        self,
        today: str,
        now: float,
        *,
        force: bool = False,
        reason: str = "",
        targets: list[str] | None = None,
    ) -> dict:
        """把今日日报推给订阅会话，返回结果字典，并打印一行汇总日志。

        `force=True` 是**手动测试**语义：无视「今天已发过」的记录，每个目标会话都推一遍
        —— 用户点「立即推送测试」要的就是这个：让每个群都蹦出一条，好一眼看出哪个群还没配好。

        `targets` 给定时**只**推给这几个会话（1.3.0 用户要求「测试要选择推送的群，以免打扰」）；
        为 None 才是全部订阅会话（定时那轮走的永远是全部）。

        汇总日志里必须点名「谁没收到」：以前只给一个数字，而 perm 的详细提示每天只打印一次，
        当天第二轮之后失败就查不出是哪个会话了（用户实测踩到过）。
        """
        subs = await asyncio.to_thread(self.store.list_subscriptions)
        recipients = list(targets) if targets is not None else list(subs)
        res: dict = {
            "ok": True,
            "reason": reason,
            "kind": "daily",
            "total": len(recipients),
            "sent": 0,
            "skipped": 0,
            "denied": 0,
            "failed": 0,
            "sessions": [],
            # 面板用它算「今天还没收到的会话数」
            "today": today,
            # 本轮是不是「只发给勾选的会话」
            "selectedOnly": targets is not None,
        }
        contests, meta = await asyncio.to_thread(self.store.load_contests)
        if not contests:
            logger.warning("[contest_daily] 缓存为空，跳过日报（等待抓取）")
            res["ok"] = False
            res["message"] = "赛程缓存为空，稍等抓取完成后再试（或发 /刷新赛程）"
            return res

        text = render.render_daily(
            contests,
            int(now),
            meta.get("generatedAt", ""),
            tpl=self._templates(),
            platform_order=list(self._cfg("sources", ["cf", "nowcoder"])),
            lookahead_days=self._clamp_days(self._cfg("lookahead_days", 7))[0],
            **self._quote_opts(),
        )
        sent = 0
        skipped = 0
        denied: list[str] = []  # 被平台拒绝的会话（无权限等，属永久性）
        failed: list[str] = []  # 临时故障的会话（网络抖动等）
        for umo in recipients:
            if not force and self.store.daily_sent_on(umo, today):
                # 该会话今天已成功发过（存盘记录，**重启插件也仍然生效**）。
                # 注意：被平台拒绝的会话**不记账**，所以重载插件 / 改 daily_time 后，
                # 没成功的那些会被自动补试。
                skipped += 1
                res["sessions"].append({"umo": umo, "status": "skipped", "label": "今天已发过，跳过"})
                continue
            ok, kind = await self._push(umo, text)
            if ok:
                self.store.mark_daily_sent(umo, today)
                sent += 1
                res["sessions"].append({"umo": umo, "status": "sent", "label": "已送达"})
            elif kind == "perm":
                denied.append(umo)
                res["sessions"].append(
                    {"umo": umo, "status": "denied", "label": "被平台拒绝（无「主动消息」权限）"}
                )
            else:
                failed.append(umo)
                res["sessions"].append({"umo": umo, "status": "failed", "label": "临时故障（网络等）"})

        parts = []
        if denied:
            parts.append(f"{len(denied)} 个被平台拒绝主动消息（详见上面的提示）")
        if failed:
            parts.append(f"{len(failed)} 个临时故障")
        if skipped:
            parts.append(f"{skipped} 个今天已发过、跳过")
        tail = "，" + "、".join(parts) if parts else ""
        miss = denied + failed
        if miss:
            shown = "、".join(miss[:3]) + (f" 等 {len(miss)} 个" if len(miss) > 3 else "")
            tail += f"；未送出：{shown}"
        scope_txt = f"（只发给勾选的 {len(recipients)} 个会话）" if targets is not None else ""
        logger.info(
            f"[contest_daily] 日报已推送 {sent}/{len(recipients)} 个会话{scope_txt}{tail}"
        )

        res.update({"sent": sent, "skipped": skipped, "denied": len(denied), "failed": len(failed)})
        res["message"] = f"送达 {sent}/{len(recipients)} 个会话{scope_txt}{tail}"
        self._last_push_result = res
        return res

    async def _run_manual_push(self, params: dict | None = None) -> dict:
        """手动推一轮日报（面板按钮 / `/推送测试` 指令共用）。全局互斥，防连点刷屏。

        `params` 支持 `targets`（只发这些会话）与 `scope=selected`（面板勾选语义）。

        ⚠️ 「查锁 -> 占锁」之间**不能有 await**：连点两下时两个协程会先后跑到 here，
        中间一旦让出事件循环（比如为了读订阅表去 to_thread），两边都会看到「锁是空的」
        于是各推一轮、群被刷两条。所以订阅表在读锁**之后**、持锁期间再取。
        """
        params = params or {}
        if self._test_push_lock.locked():
            return {"ok": False, "busy": True, "message": "上一轮推送测试还没跑完，稍等一下再来"}
        async with self._test_push_lock:
            subs = await asyncio.to_thread(self.store.list_subscriptions)
            targets, err = self._resolve_scope(params, subs)
            if err:
                return {"ok": False, "needSelection": True, "message": err}
            res = await self._push_daily_to_subscribers(
                _cn_now().strftime("%Y-%m-%d"),
                time.time(),
                force=True,
                reason="手动测试",
                targets=targets,
            )
        return res

    # ---------------------------------------------------------------- 赛前提醒：计划 + 测试

    def _remind_plan(
        self, now: float, limit: int = REMIND_PLAN_LIMIT, hours: int = REMIND_PLAN_HOURS
    ) -> tuple[list[dict], dict]:
        """列出「接下来会在什么时候提醒哪几场比赛」—— 面板「赛前提醒」区块的数据源。

        只列**还没开赛**、且在 hours 小时内开赛的比赛，按提醒时刻升序。
        每条都带上「提醒时刻」本身（= 开赛时间 − remind_minutes），因为用户真正关心的是
        「几点会响」，而不是几点开赛。
        """
        contests, _meta = self.store.load_contests()
        subs = self.store.list_subscriptions()
        total = len(subs)
        try:
            lead = max(0, int(self._cfg("remind_minutes", 15) or 0))
        except Exception:  # noqa: BLE001
            lead = 15
        try:
            tpl = render.merge_templates(self._templates())
            names = tpl.get("platform_names") or {}
            emojis = tpl.get("platform_emoji") or {}
        except Exception:  # noqa: BLE001 模板坏掉也不能让面板空白
            names, emojis = {}, {}

        horizon = now + max(1, int(hours)) * 3600
        items: list[dict] = []
        for c in contests:
            start = _contest_start(c)
            if start <= now or start > horizon:
                continue  # 已开赛的不列（提醒窗口早过了）；太远的也不列，免得刷屏
            remind_at = start - lead * 60
            if lead <= 0:
                phase = "off"  # 提醒被关掉了
            elif now >= remind_at:
                phase = "window"  # 已进入提醒窗口：本 tick 就会发（或已经发过）
            else:
                phase = "scheduled"
            src = str(c.get("source") or "")
            items.append(
                {
                    "id": str(c.get("id") or ""),
                    "title": str(c.get("title") or "（无标题）"),
                    "url": str(c.get("url") or ""),
                    "platform": src,
                    "platformName": str(names.get(src) or src or "未知平台"),
                    "emoji": str(emojis.get(src) or ""),
                    "startAt": int(start),
                    "startText": _fmt_short_date(start),
                    "startFull": _fmt_full_date(start),
                    "remindAt": int(remind_at),
                    "remindText": _fmt_short_date(remind_at),
                    "minutesToStart": int((start - now) // 60),
                    "phase": phase,
                    "remindedCount": self.store.count_reminded(str(c.get("id") or "")),
                    "subs": total,
                }
            )
        items.sort(key=lambda x: x["remindAt"])
        truncated = len(items) > limit
        plan_meta = {
            "lead": lead,
            "subs": total,
            "hours": int(hours),
            "count": len(items),
            "truncated": truncated,
            "enabled": lead > 0,
        }
        return items[:limit], plan_meta

    def _pick_contest(self, contests: list[dict], now: float, contest_id: str = ""):
        """给测试提醒挑一场比赛来渲染文案。返回 (比赛, 错误文案, 是不是「已开赛的兜底选择」)。"""
        cid = str(contest_id or "").strip()
        if cid:
            hit = next((c for c in contests if str(c.get("id") or "") == cid), None)
            if hit is None:
                return None, "选中的那场比赛已经不在缓存里了（可能被刷新掉了），点「刷新状态」后重选一场", False
            return hit, "", _contest_start(hit) <= now
        future = sorted((c for c in contests if _contest_start(c) > now), key=_contest_start)
        if future:
            return future[0], "", False
        # 缓存里一场未来的比赛都没有（假期常见）—— 退回最近的一场，让用户照样能测通道
        latest = max(contests, key=_contest_start, default=None)
        return latest, "", True

    async def _push_remind_once(self, params: dict) -> dict:
        """测试提醒的实际执行体。

        ⚠️ 与定时提醒的关键区别：**绝不写幂等记录**（不调 mark_reminded）。
        否则「测完这场比赛」之后，真正的赛前提醒到点就被当成「已提醒过」跳过了
        —— 这是最阴的坑：用户以为测通了，结果当天群里一条真提醒都没收到。
        """
        now = time.time()
        contests, _meta = await asyncio.to_thread(self.store.load_contests)
        subs = await asyncio.to_thread(self.store.list_subscriptions)
        targets, err = self._resolve_scope(params, subs)
        if err:
            return {"ok": False, "needSelection": True, "kind": "remind", "message": err}
        if not contests:
            return {
                "ok": False,
                "kind": "remind",
                "message": "赛程缓存为空，没有可以拿来渲染的比赛；稍等抓取完成或发 /刷新赛程 后再试",
            }
        contest, merr, stale = self._pick_contest(contests, now, params.get("contestId", ""))
        if contest is None:
            return {"ok": False, "kind": "remind", "message": merr or "没挑到可以测试的比赛"}

        try:
            lead = int(self._cfg("remind_minutes", 15) or 15)
            lead = lead if lead > 0 else 15  # 关掉提醒时也得给个数字渲染出文案
        except Exception:  # noqa: BLE001
            lead = 15
        text = render.render_reminder(contest, int(now), lead, tpl=self._templates()) + REMIND_TEST_NOTE

        recipients = list(targets) if targets is not None else list(subs)
        res: dict = {
            "ok": True,
            "kind": "remind",
            "reason": "手动测试",
            "total": len(recipients),
            "sent": 0,
            "skipped": 0,
            "denied": 0,
            "failed": 0,
            "sessions": [],
            "selectedOnly": targets is not None,
            "contestId": str(contest.get("id") or ""),
            "contestTitle": str(contest.get("title") or ""),
            "contestStart": _fmt_full_date(contest.get("startAt") or 0),
            "staleContest": bool(stale),
            "lead": lead,
            "noIdempotent": True,
        }
        sent = 0
        denied: list[str] = []
        failed: list[str] = []
        for umo in recipients:
            ok, kind = await self._push(umo, text)
            if ok:
                sent += 1
                res["sessions"].append({"umo": umo, "status": "sent", "label": "已送达"})
            elif kind == "perm":
                denied.append(umo)
                res["sessions"].append(
                    {"umo": umo, "status": "denied", "label": "被平台拒绝（无「主动消息」权限）"}
                )
            else:
                failed.append(umo)
                res["sessions"].append({"umo": umo, "status": "failed", "label": "临时故障（网络等）"})

        tail = ""
        if denied:
            tail += f"，{len(denied)} 个被平台拒绝主动消息"
        if failed:
            tail += f"，{len(failed)} 个临时故障"
        miss = denied + failed
        if miss:
            shown = "、".join(miss[:3]) + (f" 等 {len(miss)} 个" if len(miss) > 3 else "")
            tail += f"；未送出：{shown}"
        logger.info(
            f"[contest_daily] 赛前提醒测试（{res['contestTitle']}）已推送 "
            f"{sent}/{len(recipients)} 个会话{tail}（不记入「已提醒」，不影响正式提醒）"
        )
        res.update({"sent": sent, "denied": len(denied), "failed": len(failed)})
        res["message"] = f"送达 {sent}/{len(recipients)} 个会话{tail}"
        return res

    async def _run_manual_remind(self, params: dict | None = None) -> dict:
        """手动推一轮赛前提醒（面板按钮 / `/提醒测试` 指令共用）。与日报测试共用一把锁。"""
        params = params or {}
        if self._test_push_lock.locked():
            return {
                "ok": False,
                "busy": True,
                "kind": "remind",
                "message": "上一轮测试还没跑完，稍等一下再来",
            }
        async with self._test_push_lock:
            return await self._push_remind_once(params)

    # ---------------------------------------------------------------- 面板接口实现

    async def page_status(self):
        """GET status —— 「推送测试」面板一进来就拉的状态快照。

        1.3.0 起还带两份「给按钮用」的清单：
          * `targets`   —— 可以当作测试目标的会话（带自定义备注/群号/今天是否已收到）
          * `reminders` —— 接下来的赛前提醒计划（哪场比赛、什么时候提醒）
        """
        today = _cn_now().strftime("%Y-%m-%d")
        try:
            subs = await asyncio.to_thread(self.store.list_subscriptions)
            contests, meta = await asyncio.to_thread(self.store.load_contests)
            fingerprint = await asyncio.to_thread(self.store.get_daily_time)
            aliases = await asyncio.to_thread(self.store.get_aliases)
            reminders, plan_meta = await asyncio.to_thread(self._remind_plan, time.time())
        except Exception as e:  # noqa: BLE001 面板读不到数据不该 500 到看不见原因
            return _jresp({"ok": False, "message": f"读取本地数据失败：{type(e).__name__}: {e}"})
        umos = list(subs.keys())
        # 「今天已收到日报」的口径 = **当前订阅表中**的会话（1.7.3 修复）。
        # sent_log.json 是**只增不清**的幂等账本：退订不清记录（这样当天退订又重新订阅
        # 也不会重收一遍），于是直接 count 整个账本会出现「订阅会话 3/2、待发 -1 个」
        # 这种账目错乱 —— 实机截图踩的坑。只数订阅表里的人，分子永远不会超过分母。
        sent_today = 0
        for umo in umos:
            try:
                if self.store.daily_sent_on(umo, today):
                    sent_today += 1
            except Exception:  # noqa: BLE001 单个会话读失败不影响面板
                continue
        sessions = []
        targets = []
        for idx, umo in enumerate(umos[:PANEL_TARGET_LIMIT]):
            try:
                done = self.store.daily_sent_on(umo, today)
            except Exception:  # noqa: BLE001
                done = False
            if idx < MANUAL_PUSH_NOTE_LIMIT:
                sessions.append({"umo": umo, "sentToday": done})
            p = _umo_parts(umo)
            raw_info = subs.get(umo)
            info = raw_info if isinstance(raw_info, dict) else {}
            targets.append(
                {
                    "umo": umo,
                    "alias": str(aliases.get(umo, "")),
                    "label": _umo_label(umo, aliases.get(umo, "")),
                    "kind": p["kind"],
                    "sid": p["sid"],
                    "platform": p["platform"],
                    "by": str((info or {}).get("by") or ""),
                    "sentToday": done,
                }
            )
        return _jresp(
            {
                "ok": True,
                "today": today,
                "dailyTime": str(self._cfg("daily_time", "09:00")),
                "resetOnTimeChange": bool(self._cfg("daily_reset_on_time_change", True)),
                "remindMinutes": int(self._cfg("remind_minutes", 15) or 0),
                "subs": len(umos),
                "sentToday": sent_today,
                "contestCount": len(contests),
                "generatedAt": str(meta.get("generatedAt") or ""),
                # 存盘指纹 == 「上一次真正生效过的时间」。和 dailyTime 不一致说明刚改过、还没跑。
                "appliedDate": str(fingerprint.get("date") or ""),
                "appliedTime": str(fingerprint.get("time") or ""),
                "sessions": sessions,
                "sessionsTruncated": len(umos) > MANUAL_PUSH_NOTE_LIMIT,
                "lastResult": self._last_push_result,
                # 1.3.0 新增：目标会话清单（带备注）+ 赛前提醒计划
                "targets": targets,
                "targetsTruncated": len(umos) > PANEL_TARGET_LIMIT,
                "aliases": {k: v for k, v in aliases.items() if k in set(umos)},
                "reminders": reminders,
                "plan": plan_meta,
                # 1.6.0 新增：战绩缓存分项条数 + 名言库全量清单
                "cache": self._cache_stats_safe(),
                "quotes": self._panel_quotes(),
            }
        )

    async def page_push_test(self):
        """POST push-test —— 「立即推送日报测试」。

        body 可带 `{"targets": [...], "scope": "selected"}` 只推给勾选的会话；
        什么都不带（老前端 / 指令通道）则是全部订阅会话。
        """
        params = await self._params()
        res = await self._run_manual_push(params)
        return _jresp(res)

    async def page_remind_test(self):
        """POST remind-test —— 「测试赛前提醒」。

        body：`{"targets": [...], "scope": "selected", "contestId": "cf:2276"}`。
        不写幂等记录（改不到真实提醒），也不动 `dailySent`（跟日报是两码事）。
        """
        params = await self._params()
        res = await self._run_manual_remind(params)
        return _jresp(res)

    async def page_alias(self):
        """POST alias —— 给某个会话绑定 / 清除自定义备注（群 ID -> 备注 的映射表）。

        body：`{"umo": "qq_official:GroupMessage:1234", "alias": "计科2201群"}`；
        `alias` 传空串即为清除备注。
        """
        params = await self._params()
        umo = str(params.get("umo") or "").strip()
        if not umo:
            return _jresp({"ok": False, "message": "缺少会话 ID（umo），无法保存备注"})
        try:
            saved = await asyncio.to_thread(self.store.set_alias, umo, str(params.get("alias") or ""))
        except Exception as e:  # noqa: BLE001
            return _jresp({"ok": False, "message": f"保存备注失败：{type(e).__name__}: {e}"})
        label = _umo_label(umo, saved)
        return _jresp(
            {
                "ok": True,
                "umo": umo,
                "alias": saved,
                "label": label,
                "message": (
                    f"已把「{_umo_parts(umo)['sid'] or umo}」的备注改成「{saved}」"
                    if saved
                    else "已清除这条备注（面板里将按群号显示）"
                ),
            }
        )

    async def page_reset_sent(self):
        """POST reset-sent —— 把今天的「已推送」记录清零，让所有群重新进入待推送状态。"""
        today = _cn_now().strftime("%Y-%m-%d")
        try:
            n = await asyncio.to_thread(self.store.reset_daily_sent, today)
            await asyncio.to_thread(self.store.set_daily_time, today, str(self._cfg("daily_time", "09:00")))
        except Exception as e:  # noqa: BLE001
            return _jresp({"ok": False, "message": f"重置失败：{type(e).__name__}: {e}"})
        # 顺手把内存节流标记清掉：下一次 tick 若已过点，会自动完整补跑一轮
        self._last_daily_key = None
        self._last_daily_time = None
        logger.info(f"[contest_daily] 手动重置「{today} 已推送」记录 {n} 条（面板操作）")
        return _jresp(
            {
                "ok": True,
                "removed": n,
                "today": today,
                "message": f"已把 {today} 的 {n} 条「已推送」记录清零，所有订阅会话重新进入待推送状态",
            }
        )

    async def page_clear_cache(self):
        """POST clear-cache —— 清掉 `profile_cache.json` 里的某一类战绩查询缓存。

        body：`{"kind": "cf" | "nk" | "problemset" | "all"}`。
        返回具体清掉了几条（分项 + 合计），**不动赛程缓存**（那是另一个文件，
        清掉意味着下一次日报要当场重新联网抓一遍）。
        """
        params = await self._params()
        kind = str(params.get("kind") or "").strip().lower()
        if not kind:
            return _jresp({"ok": False, "message": "缺少参数 kind（cf / nk / problemset / all）"})
        if kind in ("all", "全部", "所有", "*"):
            kinds = ("cf", "nk")
        elif kind in ("problemset", "题库", "cfprob"):
            kinds = ("problemset",)
        elif kind in ("cf", "codeforces", "nk", "nowcoder", "牛客"):
            kinds = (("nk",) if kind in ("nk", "nowcoder", "牛客") else ("cf",))
        else:
            return _jresp({"ok": False, "message": f"不认识的缓存类别：{kind}", "cache": self._cache_stats_safe()})
        try:
            res = await asyncio.to_thread(self._clear_profile_cache, kinds)
        except Exception as e:  # noqa: BLE001
            return _jresp({"ok": False, "message": f"清理失败：{type(e).__name__}: {e}"})
        logger.info(f"[contest_daily] 面板清缓存 {kinds}：{res}")
        return _jresp(
            {
                "ok": True,
                "kind": kind,
                "removed": res,
                "cache": self._cache_stats_safe(),
                "message": self._cache_result_text(kinds, res),
            }
        )

    async def page_quote(self):
        """POST quote —— 名言库的可视化管理（新增 / 删除 / 屏蔽 / 恢复）。

        body：`{"action": "add"|"del"|"block"|"unblock", "text": ..., "by": ..., "id": ...}`。

        * `add` / `del` 只作用于**自定义**名言（内置库在代码里，删不掉 —— 想让它别出现
          就点「屏蔽」，屏蔽名单存在 data/quotes.json，升级插件也不会丢）。
        * 动作与面板 id 都由前端给，但**服务端一律以 id 回查清单**再决定动哪一条：
          不信任前端传来的正文，免得改错句子。
        返回体带上刷新后的整份名言库数据，前端据此重渲染（省一次往返）。
        """
        params = await self._params()
        action = str(params.get("action") or "").strip().lower()
        qid = str(params.get("id") or "").strip()
        text = str(params.get("text") or "")
        by = str(params.get("by") or "")

        if action in ("add", "新增", "添加"):
            item, err = quote_lib.prepare_custom(text, by)
            if err:
                return _jresp({"ok": False, "message": err, "quotes": self._panel_quotes()})
            # 与**整个库**（内置 + 自定义）比一遍：重复的句子留在池里会让它被抽中的概率翻倍
            key = item["text"].lower()
            for d in quote_lib.items(self._quote_pool(), self._quote_blocked()):
                if d["text"].lower() == key:
                    return _jresp(
                        {
                            "ok": False,
                            "message": f"这句已经在库里了（{quote_lib.cat_label(d['cat'])}），不用再加一遍",
                            "quotes": self._panel_quotes(),
                        }
                    )
            try:
                ok, why = await asyncio.to_thread(self.store.add_quote, item["text"], item["by"])
            except Exception as e:  # noqa: BLE001
                return _jresp({"ok": False, "message": f"保存失败：{type(e).__name__}: {e}"})
            if not ok:
                return _jresp({"ok": False, "message": why, "quotes": self._panel_quotes()})
            logger.info(f"[contest_daily] 面板新增自定义名言：{item['text'][:20]}")
            return _jresp(
                {
                    "ok": True,
                    "message": f"已加入自定义名言：〖{item['text']}〗（下一次抽签就会参与）",
                    "quotes": self._panel_quotes(),
                }
            )

        if action in ("del", "delete", "删除"):
            target = self._find_quote_row(qid)
            if target is None:
                return _jresp({"ok": False, "message": "没找到这条名言，列表可能已经刷新了", "quotes": self._panel_quotes()})
            if target["source"] != "custom":
                return _jresp(
                    {
                        "ok": False,
                        "message": "内置名言改不动（它在插件代码里）—— 点「屏蔽」就能让它不再出现",
                        "quotes": self._panel_quotes(),
                    }
                )
            try:
                done = await asyncio.to_thread(self.store.remove_quote, target["text"])
            except Exception as e:  # noqa: BLE001
                return _jresp({"ok": False, "message": f"删除失败：{type(e).__name__}: {e}"})
            return _jresp(
                {
                    "ok": bool(done),
                    "message": "已删除这条自定义名言" if done else "这条已经不在了",
                    "quotes": self._panel_quotes(),
                }
            )

        if action in ("block", "屏蔽", "unblock", "恢复", "取消屏蔽"):
            want_block = action in ("block", "屏蔽")
            target = self._find_quote_row(qid)
            if target is None:
                return _jresp({"ok": False, "message": "没找到这条名言，列表可能已经刷新了", "quotes": self._panel_quotes()})
            if target["blocked"] == want_block:
                return _jresp(
                    {
                        "ok": True,
                        "message": "这条的状态没变（列表已经刷新过了）",
                        "quotes": self._panel_quotes(),
                    }
                )
            try:
                changed = await asyncio.to_thread(self.store.set_quote_blocked, target["id"], want_block)
            except Exception as e:  # noqa: BLE001
                return _jresp({"ok": False, "message": f"保存屏蔽名单失败：{type(e).__name__}: {e}"})
            if not changed:
                return _jresp(
                    {
                        "ok": False,
                        "message": "屏蔽名单已达上限或状态没变，没有写入",
                        "quotes": self._panel_quotes(),
                    }
                )
            verb = "已屏蔽" if want_block else "已恢复"
            tail = "，它不会再出现在日报里" if want_block else "，它重新进入抽取池"
            logger.info(f"[contest_daily] 面板{verb}名言：{target['text'][:20]}")
            return _jresp(
                {
                    "ok": True,
                    "message": f"{verb}〖{target['text']}〗{tail}",
                    "quotes": self._panel_quotes(),
                }
            )

        return _jresp(
            {
                "ok": False,
                "message": f"不认识的动作：{action or '(空)'}（可选 add / del / block / unblock）",
                "quotes": self._panel_quotes(),
            }
        )

    def _find_quote_row(self, qid: str) -> dict | None:
        """按面板 id 回查一条名言（不信前端传来的正文，只认 id）。"""
        if not qid:
            return None
        try:
            for d in quote_lib.items(self._quote_pool(), self._quote_blocked()):
                if d["id"] == qid:
                    return d
        except Exception:  # noqa: BLE001
            return None
        return None

    # ---------------------------------------------------------------- 缓存清理

    def _cache_stats_safe(self) -> dict:
        """读各类战绩缓存条数；读失败也不该让接口 500。"""
        try:
            return self.store.profile_cache_stats()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[contest_daily] 读缓存统计失败: {type(e).__name__}: {e}")
            return {"total": 0, "profile": 0, "contest": 0, "nk": 0, "problemset": 0, "other": 0, "expired": 0}

    def _clear_profile_cache(self, kinds) -> dict:
        """逐个类别清缓存并汇总（`kind` 已归一成元组）。"""
        total = 0
        per: dict = {}
        for k in kinds:
            res = self.store.clear_profile_cache(k)
            total += int(res.get("total") or 0)
            for name, n in (res.get("removed") or {}).items():
                if n:
                    per[name] = per.get(name, 0) + int(n)
        return {"total": total, "detail": per}

    @staticmethod
    def _cache_kind_label(kinds) -> str:
        names = {"cf": "CF 查询缓存", "nk": "牛客查询缓存", "problemset": "CF 题库映射"}
        return " + ".join(names.get(k, k) for k in kinds)

    def _cache_result_text(self, kinds, res: dict) -> str:
        """清缓存后的结果文案：**必须给出具体条数**（用户明确要求），并讲清没动什么。"""
        detail = res.get("detail") or {}
        total = int(res.get("total") or 0)
        label = self._cache_kind_label(kinds)
        if not total:
            return f"{label}本来就是空的，没有需要清理的条目。"
        lines = [f"已清除 {label}：共 {total} 条"]
        if "profile" in detail:
            lines.append(f"· CF 用户档案 {detail['profile']} 条")
        if "contest" in detail:
            lines.append(f"· CF 单场成绩 {detail['contest']} 条")
        if "nk" in detail:
            lines.append(f"· 牛客档案 {detail['nk']} 条")
        if "problemset" in detail:
            lines.append(f"· CF 题库映射 {detail['problemset']} 条（下次查询会重新下载题库）")
        lines.append("🗓 赛程缓存（比赛数据）没有动，订阅表 / 提醒记录 / 已推送记录也没动。")
        return "\n".join(lines)

    async def _job_daily_if_due(self, now: float) -> int:
        """日报层：到了配置时间且今天还没发过 -> 推给所有订阅会话。"""
        target = str(self._cfg("daily_time", "09:00"))
        try:
            hh, mm = (int(x) for x in target.split(":"))
        except Exception:  # noqa: BLE001
            hh, mm = 9, 0
        now_cn = datetime.fromtimestamp(now, TZ_CN)
        today = now_cn.strftime("%Y-%m-%d")

        # ① 用户把 daily_time 改了 -> 先把「今天已推送」清零，让**所有**群重新来一遍
        #    （1.2.17 用户明确要求；关掉 daily_reset_on_time_change 可恢复旧行为）
        reset_done = await self._sync_daily_time_change(today, target)

        # ② 「整轮还要不要再跑」由内存标记节流，否则失败会话会每 30 秒重试一次
        #    （1.2.13 修的就是这个刷屏）。刚重置过就放行一轮，让改时间这件事立刻可见。
        if not reset_done and self._last_daily_key == today and self._last_daily_time == target:
            return 0
        if (now_cn.hour, now_cn.minute) < (hh, mm):
            return 0  # 还没到点

        self._last_daily_key = today  # 先置位，防止同一 tick 内重复触发
        self._last_daily_time = target
        try:
            await asyncio.to_thread(self.store.set_daily_time, today, target)
        except Exception as e:  # noqa: BLE001 指纹写失败只影响「改时间检测」，不该挡住日报
            logger.debug(f"[contest_daily] 写日报时间指纹失败: {type(e).__name__}: {e}")

        res = await self._push_daily_to_subscribers(today, now, reason="定时")
        return res["sent"]

    async def _job_remind(self, now: float) -> int:
        """提醒层：分钟级扫描。命中即推，靠 sent_log 保证幂等。"""
        lead = int(self._cfg("remind_minutes", 15))
        if lead <= 0:
            return 0
        contests, _ = await asyncio.to_thread(self.store.load_contests)
        if not contests:
            return 0
        subs = await asyncio.to_thread(self.store.list_subscriptions)
        if not subs:
            return 0

        sent = 0
        skipped = 0
        for c in contests:
            start = c.get("startAt")
            if not start:
                continue
            remain = start - now
            # 命中窗口：还没开始，且剩余时间已进入提醒区间
            if not (0 < remain <= lead * 60):
                continue
            for umo in subs:
                key = f"{c['id']}|{umo}"
                if self.store.has_reminded(c["id"], umo):
                    self._remind_fail_count.pop(key, None)
                    continue
                ok, kind = await self._push(
                    umo, render.render_reminder(c, int(now), lead, tpl=self._templates())
                )
                if ok:
                    self.store.mark_reminded(c["id"], umo, int(start))
                    self._remind_fail_count.pop(key, None)
                    sent += 1
                    continue
                # 失败记账：平台永久拒绝立刻放弃；临时故障容忍 REMIND_FAIL_MAX_TRIES
                # 次后也放弃。否则每 30 秒一个 tick 会一直重试同一场
                # （15 分钟窗口 = 最多 30 次 × 每场每群），日志被刷爆而成功率不会变高。
                tries = self._remind_fail_count.get(key, 0) + 1
                self._remind_fail_count[key] = tries
                if kind == "perm" or tries >= REMIND_FAIL_MAX_TRIES:
                    self.store.mark_reminded(c["id"], umo, int(start))
                    self._remind_fail_count.pop(key, None)
                    skipped += 1
        if sent:
            logger.info(f"[contest_daily] 赛前提醒已推送 {sent} 条")
        if skipped:
            logger.info(
                f"[contest_daily] 有 {skipped} 条提醒因推送始终失败已跳过（同一条不再重试）"
            )
        # 陈旧提醒记录的清理统一交给 _job_cleanup（默认每 30 分钟一次）。
        # 这里原来每 30 秒调一次 cleanup_sent，等于每 30 秒读写一遍 sent_log.json。
        return sent

    # ---------------------------------------------------------------- 指令

    @filter.command("日报", alias={"竞赛日报", "contest"})
    async def cmd_daily(self, event: AstrMessageEvent):
        """立即推送一次日报到当前会话"""
        deny = self._deny_if_restricted(event, "日报")
        if deny:
            yield event.plain_result(deny)
            return
        contests, meta = await asyncio.to_thread(self.store.load_contests)
        if not contests:
            yield event.plain_result("📭 还没有赛程数据，正在抓取，请稍后再试（或发「/刷新赛程」）")
            asyncio.create_task(self._job_refresh())
            return
        text = render.render_daily(
            contests,
            int(time.time()),
            meta.get("generatedAt", ""),
            tpl=self._templates(),
            platform_order=list(self._cfg("sources", ["cf", "nowcoder"])),
            lookahead_days=self._clamp_days(self._cfg("lookahead_days", 7))[0],
            **self._quote_opts(),
        )
        yield event.plain_result(text)

    @filter.command("赛程")
    async def cmd_upcoming(self, event: AstrMessageEvent, days: int = 0):
        """查询未来 N 天比赛（含今天剩余场次），例如 /赛程 5（N 最大 7）"""
        deny = self._deny_if_restricted(event, "赛程")
        if deny:
            yield event.plain_result(deny)
            return
        contests, _ = await asyncio.to_thread(self.store.load_contests)
        tpl = self._templates()
        if not contests:
            yield event.plain_result(
                render.render_config_text(tpl, "upcoming_nodata", days=0)
            )
            asyncio.create_task(self._job_refresh())
            return
        # 天数上限 7：参数与配置都统一走 _clamp_days() 夹紧
        # 天数配置与日报区块**互相独立**：这里是 upcoming_days（/赛程 专用，含今天剩余），
        # 日报区块用 lookahead_days（不含今天）—— 见 render 层两个窗口函数
        d, capped = self._clamp_days(days or self._cfg("upcoming_days", 7))
        text = render.render_upcoming(
            contests, int(time.time()), d, limit=UPCOMING_LIMIT, tpl=tpl
        )
        if capped:
            text += render.render_config_text(
                tpl,
                "upcoming_capped_note",
                days=d,
                max_days=MAX_LOOKAHEAD_DAYS,
                limit=UPCOMING_LIMIT,
            )
        yield event.plain_result(text)

    @staticmethod
    def _clamp_days(days) -> tuple[int, bool]:
        """把「查几天」夹到 [1, MAX_LOOKAHEAD_DAYS]，返回 (天数, 是否被夹过)。"""
        try:
            raw = int(days)
        except (TypeError, ValueError):
            raw = MAX_LOOKAHEAD_DAYS
        d = max(1, min(raw, MAX_LOOKAHEAD_DAYS))
        return d, d != raw

    # ---------------------------------------------------------------- 战绩查询指令

    @staticmethod
    def _profile_usage(kind: str) -> str:
        if kind == "cf":
            return (
                "🔍 用法：/cf <handle>\n"
                "\n"
                "例如：/cf tourist\n"
                "\n"
                "· handle 就是 Codeforces 个人主页地址末尾的用户名\n"
                "· https://codeforces.com/profile/tourist → 填 tourist\n"
                "· 只能含字母 / 数字 / 下划线 / 短横线 / 点；emoji、中文、空格等\n"
                "  CF 自己会删掉再查（比如 游客123 会被当成 123），所以本插件直接拦下\n"
                "· 查的是 Rating / 最高 Rating / 本周比赛战绩（渲染成卡片图）"
            )
        return (
            "🔍 用法：/nk <用户ID>\n"
            "\n"
            "例如：/nk 886965097\n"
            "\n"
            "· 牛客 ID 是个人主页地址末尾的那串数字\n"
            "· 打开 https://ac.nowcoder.com/acm/contest/rating-index ，\n"
            "  点自己的头像进主页，地址栏 /profile/ 后面那串数字就是 ID\n"
            "· 暂不支持按昵称搜索（牛客搜索接口需要登录）\n"
            "· 查的是 Rating / 最高 Rating / 本周比赛战绩（渲染成卡片图）"
        )

    async def _profile_flow(self, event, kind: str, arg: str):
        """战绩查询的 async generator，成功时只 yield 最终的战绩卡片。

        只回一条：查得慢时先说「正在查询…」的做法已去掉 —— 群聊里那条占位消息
        会被其他人夹在中间，反而更乱；现在提速后通常几秒内直接出结果
        （题库映射已在启动时预热，命中缓存则是 0 秒）。
        """
        arg = (arg or "").strip()

        # ① 参数缺失 -> 无效指令（默认与无权限同一句；可配置成回用法说明）
        if not arg:
            logger.info(f"[contest_daily] {kind} 未带参数，按无效指令处理")
            yield self._invalid_or(event, kind, "缺少参数", self._profile_usage(kind))
            return

        # ② 用户级冷却
        sid = _safe_sid(event)
        left = self._profile_cooldown_left(sid)
        if left > 0:
            yield f"⏳ 查询太频繁啦，请 {left} 秒后再试（防止把第三方接口打挂）"
            return

        # ③ 全局互斥：同一时刻只处理一个查询
        if self._profile_lock.locked():
            yield "⏳ 正在处理其他同学的查询，请过几秒再试"
            return

        async with self._profile_lock:
            self._profile_mark(sid)
            fetcher = self._fetcher()
            fn = fetcher.cf if kind == "cf" else fetcher.nk
            t0 = time.time()
            logger.info(f"[contest_daily] {kind} 查询开始：{arg} （sender={sid}）")
            try:
                profile, err = await asyncio.wait_for(
                    asyncio.to_thread(fn, arg), timeout=PROFILE_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[contest_daily] {kind} 查询超时（>{PROFILE_TIMEOUT}s）: {arg} -> 静默不回复"
                )
                msg = self._error_reply(
                    "⌛️ 查询超时了（对方比赛记录较多或网络较慢），请稍后再试"
                )
                if msg:
                    yield msg
                return
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"[contest_daily] {kind} 查询异常: {type(e).__name__}: {e} -> 静默不回复"
                )
                msg = self._error_reply(
                    f"❌ 查询出错：{type(e).__name__}，详情见 AstrBot 日志"
                )
                if msg:
                    yield msg
                return
            if err or not profile:
                # 失败原因可能是多行（例如 handle 含非法字符时的完整解释）——
                # 压成一行写日志，免得一条日志占好几行、grep 时看着乱
                logger.info(
                    f"[contest_daily] {kind} 查询失败（{time.time() - t0:.1f}s）："
                    f"{str(err).replace(chr(10), ' / ')}"
                )
            else:
                logger.info(
                    f"[contest_daily] {kind} 查询完成（{time.time() - t0:.1f}s）："
                    f"{profile.get('handle')} rating={profile.get('rating')} "
                    f"本周 {len(profile.get('week') or [])} 场"
                )

        if err or not profile:
            if isinstance(err, InvalidInput):
                # 输入无效（无此用户 / 参数格式不对）-> 与无权限同一句
                yield self._invalid_or(event, kind, str(err), f"❌ {err}")
            else:
                # 网络故障 / 解析异常：**静默**（不暴露到群里，只写日志）
                logger.warning(f"[contest_daily] {kind} 查询失败: {err} -> 静默不回复")
                msg = self._error_reply(f"❌ {err or '查询失败'}")
                if msg:
                    yield msg
            return

        text = render.render_profile(profile, tpl=self._templates())

        # 图片卡片优先（配置可关）；渲染失败回退纯文本，绝不因出图失败吞掉结果
        if not bool(self._cfg("profile_card_enabled", True)):
            yield text
            return
        path = None
        card_info: dict = {}
        try:
            from .core import card

            out_dir = os.path.join(self.store.data_dir, "cards")
            # 头像只有「平台头像」一拨：CF 用大图 + 小图并行取，
            # 取不到就画首字母块（1.7.2 起去掉了 QQ 头像兜底，用户要求）
            path = await asyncio.to_thread(
                card.render_profile_card,
                profile,
                out_dir,
                self._card_log,
                card_info,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[contest_daily] 战绩卡片渲染失败，回退文本: {type(e).__name__}: {e}")
        # 1.5.0（用户要求）：连头像都没取到的卡片算次品 —— 档案不进缓存、文件发完即删
        ephemeral = bool(path) and self._ephemeral_no_avatar(card_info)
        if ephemeral:
            await asyncio.to_thread(self._forget_profile_cache, profile)
        if path:
            yield ("image", path, text, ephemeral)
        else:
            yield text

    def _ephemeral_no_avatar(self, card_info: dict) -> bool:
        """这张卡是不是「连头像都没拿到」的次品？

        用户原话（1.5.0）：「未查询到的 cf 头像的战绩卡不要缓存，发完就删」。
        满足条件时这次查询**不留在档案缓存里**、卡片图**发完就删**。

        判定口径（1.7.2 起只有两态）：渲染层上报 `avatar_source`，`platform` = 拿到了
        平台头像，`placeholder` = 只剩一个首字母方块。**只有 `placeholder` 算次品** ——
        QQ 头像那一档已按用户要求删除，卡片上要么是真头像、要么是首字母块。

        开关 `ephemeral_card_without_avatar` 关掉即回到老行为（一视同仁地留在
        cards/ 里等定期清理，档案也照常缓存）。
        """
        if not bool(self._cfg("ephemeral_card_without_avatar", True)):
            return False
        src = str((card_info or {}).get("avatar_source") or "")
        # 拿不到 avatar_source（渲染在中途抛异常）时不当成次品 —— 没有依据就别乱删
        return src == "placeholder"

    def _forget_profile_cache(self, profile: dict) -> None:
        """把「没取到平台头像」的那份档案从缓存里删掉（1.5.0）。

        缓存里存的正是那份**带着当下取不到的头像地址**的档案，留着它下次查询
        还是同样的结果；删掉之后下次会重新联网，CF 图床一恢复就能自己好起来
        （详见 core/profile.py 里 `ProfileFetcher.forget`）。
        档案是可重建的派生数据，删掉没有任何副作用；出错也只写 debug 日志。
        """
        p = profile or {}
        try:
            if self._fetcher().forget(p):
                logger.info(
                    "[contest_daily] 未取到平台头像，已清掉档案缓存，下次查询会重试：%s / %s",
                    p.get("platform") or "cf", p.get("handle") or p.get("uid") or "?",
                )
        except Exception as e:  # noqa: BLE001 清缓存失败绝不能影响这次回复
            logger.debug(f"[contest_daily] 清理档案缓存失败: {type(e).__name__}: {e}")

    def _drop_ephemeral_card(self, path) -> None:
        """删掉「没取到平台头像」的那张卡片图（1.5.0，用户要求「发完就删」）。

        这类卡是次品：下次查询几乎一定会重新渲染（档案缓存也已经清了），
        留着只是白占磁盘。删不掉也只写 debug —— 绝不能影响这次回复。
        """
        try:
            p = str(path or "")
            if p and os.path.exists(p):
                os.remove(p)
                logger.info(
                    f"[contest_daily] 未取到平台头像的卡片已即时清理：{os.path.basename(p)}"
                )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[contest_daily] 清理无头像卡片失败: {type(e).__name__}: {e}")

    async def _profile_output(self, event, item):
        """把 _profile_flow 的产出发出去；返回 None 表示已经自己发完了。

        * str  -> 纯文本（交给调用方 yield）
        * tuple("image", 路径, 兜底文案) -> **自己 await event.send() 发图**，
          失败时回退发兜底文案。

        为什么不像别处那样直接 yield 图片链：yield 出去的结果由 AstrBot 管线发送，
        发送阶段抛的异常插件**捕获不到**。一旦发图失败（QQ 官方机器人上传富媒体
        失败、适配器不支持本地图片等），群里就彻底没有回应——2026-10-15 实测：
        纯文本的 /赛程、/订阅日报 回复正常，而 /cf、/nk 完全没有回应，问题就出在这里。
        自己发送就能兜住：发图失败立刻改发文本，保证「一定有回应」。

        1.5.0：第 4 个元素是「这张卡是不是没取到平台头像的次品」。是的话**发完就删**
        文件（用户要求），无论发图成功还是回退文本 —— 反正它下次也会重新渲染。
        """
        if isinstance(item, str):
            return event.plain_result(item)
        try:
            parts = list(item)
            path, caption = parts[1], parts[2]
            ephemeral = bool(parts[3]) if len(parts) > 3 else False
        except Exception:  # noqa: BLE001
            return event.plain_result(str(item))
        try:
            chain = MessageChain()
            if not hasattr(chain, "file_image"):
                raise RuntimeError("当前 AstrBot 的 MessageChain 不支持 file_image")
            await event.send(chain.file_image(path))
            logger.info(f"[contest_daily] 战绩卡片图已发送：{os.path.basename(str(path))}")
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[contest_daily] 战绩卡片图发送失败，回退纯文本：{type(e).__name__}: {e}"
            )
            return event.plain_result(caption)
        finally:
            # 发送（上传）已经完成，文件可以安全删除；异常路径同样删 —— 它已经没用了
            if ephemeral:
                self._drop_ephemeral_card(path)

    @filter.command("cf", alias={"CF", "Codeforces", "codeforces"})
    async def cmd_cf(self, event: AstrMessageEvent, handle: str = ""):
        """查询 Codeforces 用户战绩，例如 /cf tourist（所有人可用）"""
        deny = self._deny_if_restricted(event, "cf")
        if deny:
            yield event.plain_result(deny)
            return
        try:
            async for item in self._profile_flow(event, "cf", handle):
                res = await self._profile_output(event, item)
                if res is not None:
                    yield res
        except Exception as e:  # noqa: BLE001
            # 兜底：意外异常也**不暴露**给群里，只写日志（群主要求静默）
            logger.error(f"[contest_daily] /cf 处理异常: {type(e).__name__}: {e}", exc_info=True)
            msg = self._error_reply("❌ 查询出错啦，详情见 AstrBot 日志；稍后再试试~")
            if msg:
                yield event.plain_result(msg)

    @filter.command("nk", alias={"NK", "nowcoder", "牛客", "牛客竞赛"})
    async def cmd_nk(self, event: AstrMessageEvent, uid: str = ""):
        """查询牛客用户战绩，例如 /nk 886965097（所有人可用）"""
        deny = self._deny_if_restricted(event, "nk")
        if deny:
            yield event.plain_result(deny)
            return
        try:
            async for item in self._profile_flow(event, "nk", uid):
                res = await self._profile_output(event, item)
                if res is not None:
                    yield res
        except Exception as e:  # noqa: BLE001
            # 兜底：意外异常也不暴露给群里，只写日志（群主要求静默）
            logger.error(f"[contest_daily] /nk 处理异常: {type(e).__name__}: {e}", exc_info=True)
            msg = self._error_reply("❌ 查询出错啦，详情见 AstrBot 日志；稍后再试试~")
            if msg:
                yield event.plain_result(msg)

    @filter.command("订阅日报")
    async def cmd_subscribe(self, event: AstrMessageEvent):
        """把当前会话加入订阅表（需管理员权限）"""
        deny = self._deny_if_restricted(event, "订阅日报")
        if deny:
            yield event.plain_result(deny)
            return
        umo = event.unified_msg_origin
        scope = _scope_name(event)
        if await asyncio.to_thread(self.store.subscribe, umo, {"by": event.get_sender_name()}):
            yield event.plain_result(
                f"✅ {scope}已订阅竞赛日报\n"
                f"· 每天 {self._cfg('daily_time', '09:00')} 推送当日与未来赛程\n"
                f"· 每场比赛开赛前 {self._cfg('remind_minutes', 15)} 分钟提醒\n"
                "· 发送「/退订日报」可取消"
            )
        else:
            yield event.plain_result(f"ℹ️ {scope}已订阅，无需重复订阅")

    @filter.command("退订日报", alias={"取消订阅"})
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        """移除当前会话的订阅（需管理员权限）"""
        deny = self._deny_if_restricted(event, "退订日报")
        if deny:
            yield event.plain_result(deny)
            return
        scope = _scope_name(event)
        if await asyncio.to_thread(self.store.unsubscribe, event.unified_msg_origin):
            yield event.plain_result(f"✅ 已退订，{scope}不再收到日报与赛前提醒")
        else:
            yield event.plain_result(f"ℹ️ {scope}尚未订阅")

    @filter.command("订阅列表")
    async def cmd_list(self, event: AstrMessageEvent):
        """查看已订阅的会话（需管理员权限）"""
        deny = self._deny_if_restricted(event, "订阅列表")
        if deny:
            yield event.plain_result(deny)
            return
        subs = await asyncio.to_thread(self.store.list_subscriptions)
        if not subs:
            yield event.plain_result("📭 当前没有任何订阅")
            return
        aliases = await asyncio.to_thread(self.store.get_aliases)
        lines = ["📋 已订阅会话：", ""]
        for i, umo in enumerate(subs, 1):
            lines.append(f"{i}. {_umo_label(umo, aliases.get(umo, ''))}")
            lines.append(f"     {umo}")
        lines.append("")
        lines.append(f"共 {len(subs)} 个")
        lines.append("改备注：/群备注 <群号或关键词> <新备注>（方便在「推送测试」面板里勾选）")
        yield event.plain_result("\n".join(lines))

    @filter.command("刷新赛程")
    async def cmd_refresh(self, event: AstrMessageEvent):
        """立即抓取一次赛程（需管理员权限）"""
        deny = self._deny_if_restricted(event, "刷新赛程")
        if deny:
            yield event.plain_result(deny)
            return
        yield event.plain_result("🔄 正在抓取 CF 与牛客赛程，请稍候…")
        ok = await self._job_refresh()
        contests, meta = await asyncio.to_thread(self.store.load_contests)
        if ok:
            detail = " ".join(f"{k} {v['count']} 场" for k, v in meta.get("sources", {}).items())
            yield event.plain_result(f"✅ 抓取完成：{detail}\n缓存共 {len(contests)} 场")
        else:
            yield event.plain_result("❌ 抓取失败，详情见 AstrBot 日志（网络或数据源结构可能变化）")

    @filter.command("推送测试", alias={"测试推送", "pushtest"})
    async def cmd_push_test(
        self, event: AstrMessageEvent, keyword: str = "", extra: GreedyStr = GreedyStr
    ):
        """立即给订阅会话推一轮日报（需管理员权限）。

        用途：验证定时日报用的「主动消息」权限到底配好没有 —— 调完群设置后不用干等到点。
        它也是插件可视化视图（Dashboard 插件详情页 → 推送测试）的降级通道：
        老版本 AstrBot 没有 register_web_api，面板出不来时用这条指令一样能测。

        1.3.0 起支持「只推给某些群」：`/推送测试 计科2201` 只推给备注里含「计科2201」的会话
        （也可以直接填群号片段），避免测试时惊动所有群。不填关键词才是全部订阅会话。
        （`extra: GreedyStr` 收下空格分隔的多个关键词；不能用 `*extra`，见文件头说明。）
        """
        deny = self._deny_if_restricted(event, "推送测试")
        if deny:
            yield event.plain_result(deny)
            return
        keyword = _join_args(keyword, extra)
        params, note = await asyncio.to_thread(self._cmd_targets, keyword)
        if note is not None:
            yield event.plain_result(note)
            return
        scope = f"（只发给 {keyword.strip()} 匹配到的会话）" if params else ""
        yield event.plain_result(f"🔄 正在推送一轮日报测试{scope}，请稍候…")
        res = await self._run_manual_push(params)
        if not res.get("ok"):
            yield event.plain_result(f"❌ {res.get('message') or '推送失败'}")
            return
        yield event.plain_result(self._push_result_text(res, "推送测试"))

    @filter.command("提醒测试", alias={"测试提醒", "remindtest"})
    async def cmd_remind_test(
        self, event: AstrMessageEvent, keyword: str = "", extra: GreedyStr = GreedyStr
    ):
        """立即推一轮「赛前提醒」测试（需管理员权限）。

        和 /推送测试 的区别：推的是**提醒**模板（任选一场比赛渲染），并且**不写幂等记录**
        —— 测完不会把当天的正式提醒顶掉（这正是最容易踩的坑）。

        不填关键词 = 全部订阅会话；填「计科2201」= 只推给备注匹配的那个群。
        """
        deny = self._deny_if_restricted(event, "提醒测试")
        if deny:
            yield event.plain_result(deny)
            return
        keyword = _join_args(keyword, extra)
        params, note = await asyncio.to_thread(self._cmd_targets, keyword)
        if note is not None:
            yield event.plain_result(note)
            return
        scope = f"（只发给 {keyword.strip()} 匹配到的会话）" if params else ""
        yield event.plain_result(f"🔄 正在推送一轮赛前提醒测试{scope}，请稍候…")
        res = await self._run_manual_remind(params)
        if not res.get("ok"):
            yield event.plain_result(f"❌ {res.get('message') or '提醒测试失败'}")
            return
        lines = [
            f"⏰ 提醒测试完成：{res['message']}",
            f"· 用的是这场比赛：{res.get('contestTitle')}（{res.get('contestStart')} 开赛）",
            f"· 提前量：{res.get('lead')} 分钟",
        ]
        if res.get("staleContest"):
            lines.append("  ⚠️ 缓存里没有即将开始的比赛，这条是用最近一场（已开赛）的文案渲染的")
        lines += self._push_result_lines(res)
        lines.append("")
        lines.append("ℹ️ 测试提醒**不记入**「已提醒」，不会影响当天真正的赛前提醒。")
        yield event.plain_result("\n".join(lines))

    @filter.command("群备注", alias={"设置备注", "群别名", "备注"})
    async def cmd_alias(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        alias: str = "",
        extra: GreedyStr = GreedyStr,
    ):
        """给某个群（会话）绑一个备注，方便在「推送测试」面板里选择（需管理员权限）。

        用法：
          /群备注                       查看当前映射表
          /群备注 123456789 计科2201群   按群号片段匹配并写备注
          /群备注 计科2201               只写一个参数 = 把匹配到的会话备注清掉
        备注存在 data/group_aliases.json，与订阅表分开（退订重订阅不会丢）。
        """
        deny = self._deny_if_restricted(event, "群备注")
        if deny:
            yield event.plain_result(deny)
            return
        alias = _join_args(alias, extra)  # 备注里带空格也能写（/群备注 2301 软件工程 2301班）
        subs = await asyncio.to_thread(self.store.list_subscriptions)
        aliases = await asyncio.to_thread(self.store.get_aliases)

        if not str(keyword).strip():
            if not subs:
                yield event.plain_result("📭 当前没有任何订阅会话，先发 /订阅日报 再加入")
                return
            lines = ["🗂 会话备注映射表：", ""]
            for i, umo in enumerate(subs, 1):
                lines.append(f"{i}. {_umo_label(umo, aliases.get(umo, ''))}")
                lines.append(f"     {umo}")
            lines.append("")
            lines.append("改备注：/群备注 <群号或备注关键词> <新备注>")
            yield event.plain_result("\n".join(lines))
            return

        keywords = re.split(r"[\s,，]+", str(keyword).strip())
        hits, missed = _match_targets(keywords, list(subs), aliases)
        if not hits:
            yield event.plain_result(
                f"❓ 没找到匹配「{keyword.strip()}」的订阅会话。\n"
                "用 /群备注（不带参数）看一下有哪些群，拿群号或现有备注来匹配。"
            )
            return
        if missed:
            yield event.plain_result(
                f"❓ 这些关键词没匹配到会话：{'、'.join(missed)}\n"
                "一次只改一个是安全的，重发一次只带能匹配上的关键词即可。"
            )
            return
        if len(hits) > 1:
            lines = [f"⚠️ 「{keyword.strip()}」匹配到 {len(hits)} 个会话，为避免改错请用更精确的关键词：", ""]
            for umo in hits:
                lines.append(f"· {_umo_label(umo, aliases.get(umo, ''))} —— {umo}")
            yield event.plain_result("\n".join(lines))
            return

        umo = hits[0]
        if not str(alias).strip():
            await asyncio.to_thread(self.store.set_alias, umo, "")
            yield event.plain_result(
                f"✅ 已清除备注 —— {umo} 在面板里将按群号显示\n"
                "（想设置备注：/群备注 <群号或关键词> <新备注>）"
            )
            return
        saved = await asyncio.to_thread(self.store.set_alias, umo, str(alias))
        yield event.plain_result(
            f"✅ 已把会话备注设为「{saved}」\n"
            f"· 会话：{umo}\n"
            "打开 Dashboard 的「推送测试」面板即可按备注勾选这个群。"
        )

    def _cmd_targets(self, keyword: str) -> tuple[dict, str | None]:
        """把指令里的关键词解析成「只推这些会话」的参数；返回 (params, 错误文案)。

        面板是勾选，指令是打字，所以这里用「备注/群号模糊匹配」。没给关键词就返回 {}（= 全部）。
        匹配不上时给出明确提示而不是默默全推 —— 用户明确说过「以免打扰」。
        """
        text = str(keyword or "").strip()
        if not text:
            return {}, None
        subs = self.store.list_subscriptions()
        aliases = self.store.get_aliases()
        found, missed = _match_targets(re.split(r"[\s,，]+", text), list(subs), aliases)
        if not found:
            return {}, (
                f"❓ 没找到匹配「{text}」的订阅会话，本轮没有发出任何消息。\n"
                "用 /群备注（不带参数）可以查看有哪些群，或先给群绑个备注：\n"
                "  /群备注 <群号片段> <备注>\n"
                "想给**全部**订阅会话测试，就不带关键词直接发指令。"
            )
        if missed:
            return {}, (
                f"❓ 这些关键词没匹配到会话：{'、'.join(missed)}\n"
                "为免误发，本轮没有发出任何消息；请用能全部匹配上的关键词重试。"
            )
        return {"targets": found}, None

    def _push_result_lines(self, res: dict) -> list[str]:
        """把一轮推送结果渲染成指令回复的明细行（/推送测试 与 /提醒测试 共用）。"""
        lines = [
            "",
            f"· 目标会话：{res.get('total', 0)} 个",
            f"· 已送达：{res.get('sent', 0)} 个",
        ]
        if res.get("denied"):
            lines.append(f"· 被平台拒绝（无「主动消息」权限）：{res['denied']} 个")
        if res.get("failed"):
            lines.append(f"· 临时故障：{res['failed']} 个")
        aliases = self.store.get_aliases()
        missed = [s for s in res.get("sessions", []) if s.get("status") in ("denied", "failed")]
        if missed:
            lines.append("")
            lines.append("未送出的会话：")
            for s in missed[:5]:
                lines.append(f"  ✗ {_umo_label(s['umo'], aliases.get(s['umo'], ''))} —— {s['label']}")
            if len(missed) > 5:
                lines.append(f"  …… 另有 {len(missed) - 5} 个")
            lines.append("")
            lines.append("排查顺序见日志里的提示；群里 @机器人 发 /日报 走的被动通道不受影响。")
        return lines

    def _push_result_text(self, res: dict, title: str) -> str:
        lines = [f"📣 {title}完成：{res.get('message', '')}"]
        lines += self._push_result_lines(res)
        if res.get("sent"):
            lines.append("")
            lines.append("ℹ️ 本轮已把送达的会话记为「今天已发」，今天到点后不再重复推送。")
        return "\n".join(lines)


    @filter.command("名言", alias={"金句", "每日一句", "quote"})
    async def cmd_quote(
        self, event: AstrMessageEvent, keyword: str = "", extra: GreedyStr = GreedyStr
    ):
        """随机来一句算法竞赛名言；也支持搜库与看库概览（默认所有人可用）。

        用法：
          /名言            随机来一句（真随机，和日报用的是同一个库）
          /名言 补题        搜含「补题」的名言（正文与出处都搜）
          /名言 库          看各分类的条目数与示例

        日报里那句是**按天**抽的（同一天稳定、跨天自动换），这里不看日期 ——
        想换掉今天日报里那句，用 `/名言 库` 找到喜欢的，填进配置「名言库·自定义名言」。
        """
        deny = self._deny_if_restricted(event, "名言")
        if deny:
            yield event.plain_result(deny)
            return
        kw = _join_args(keyword, extra).strip()
        with_source = self._quote_with_source()
        custom = self._quote_pool()
        blocked = self._quote_blocked()

        if not kw:
            q = quote_lib.pick_quote(None, extra=custom, blocked=blocked)
            line = quote_lib.format_quote(q, with_source) or "（名言库是空的）"
            yield event.plain_result(
                f"〖{line}〗\n\n"
                "💡 日报每天会自动换一句（同一天内多个群看到的是同一句）。\n"
                "想看整个库：/名言 库　搜一句：/名言 补题"
            )
            return

        if kw in ("库", "列表", "全部", "所有", "list"):
            rows = quote_lib.items(custom, blocked)
            st = quote_lib.stats(self._quote_cats(), custom, blocked)
            lines = [
                f"📚 名言库：共 {st.get('total', len(rows))} 条"
                f"（可抽取 {st.get('eligible', 0)} 条"
                + (f"，已屏蔽 {st['blocked']} 条" if st.get("blocked") else "")
                + "）",
                "",
            ]
            for cat, label in quote_lib.CATEGORIES.items():
                hits = [d for d in rows if d["cat"] == cat]
                if not hits:
                    continue
                lines.append(f"· {label}（{len(hits)} 条）")
                for d in hits[:2]:
                    lines.append(f"  〖{d['text']}〗")
                if len(hits) > 2:
                    lines.append(f"  …… 还有 {len(hits) - 2} 条")
            custom_rows = [d for d in rows if d["cat"] == "custom"]
            if custom_rows:
                lines.append(f"· 自定义（{len(custom_rows)} 条）")
                for d in custom_rows[:2]:
                    lines.append(f"  〖{d['text']}〗")
            shown = [d for d in rows if d["blocked"]][:2]
            if shown:
                lines.append("· 已屏蔽（不参与抽取）")
                for d in shown:
                    lines.append(f"  🚫 〖{d['text']}〗")
            lines += [
                "",
                "日报每天自动抽一句；只有想要的分类时，改配置「名言库·名言分类」。",
                "想逐条过一遍、屏蔽不合适的句子：打开插件面板 →「名言库」（全部名言都在那里）。",
                "搜句子：/名言 关键词",
            ]
            yield event.plain_result("\n".join(lines))
            return

        hits = quote_lib.search(kw, limit=8, extra=custom, blocked=blocked)
        if not hits:
            yield event.plain_result(
                f"🔍 没找到含「{kw}」的名言。\n"
                "内置库的句子也能在面板「名言库」里逐条看；"
                "想加自己的句子，去面板「名言库」点添加，或在配置「名言库·自定义名言」里写一句。"
            )
            return
        lines = [f"🔍 含「{kw}」的名言（{len(hits)} 条）：", ""]
        for i, q in enumerate(hits, 1):
            lines.append(f"{i}. 〖{quote_lib.format_quote(q, with_source)}〗")
        lines += ["", "看全部：/名言 库"]
        yield event.plain_result("\n".join(lines))

    # ---------------------------------------------------------------- 清缓存（1.6.0）

    # 指令里能写的类别别名 -> 内部类别（cf / nk 各自独立，all = 两者）
    CACHE_ALIASES = {
        "cf": "cf",
        "codeforces": "cf",
        "codeforce": "cf",
        "nk": "nk",
        "nowcoder": "nk",
        "牛客": "nk",
        "牛客竞赛": "nk",
        "题库": "problemset",
        "题单": "problemset",
        "cfprob": "problemset",
        "all": "all",
        "全部": "all",
        "所有": "all",
        "both": "all",
    }

    @filter.command("清缓存", alias={"清除缓存", "清理缓存", "缓存清理", "clearcache"})
    async def cmd_clear_cache(
        self, event: AstrMessageEvent, kind: str = "", extra: GreedyStr = GreedyStr
    ):
        """一键清掉 /cf、/nk 的战绩查询缓存（需管理员权限）。

        用户要求（1.6.0）：「增加一键清除 cf/nk 查询缓存（分开）的指令，需要给出具体清除的
        数量（不用返回具体内容），比赛缓存不要去动他」。

        用法：
          /清缓存           看各类缓存现在有多少条 + 用法
          /清缓存 cf        清 CF（用户档案 + 单场成绩）
          /清缓存 nk        清牛客（用户档案）
          /清缓存 全部      两个都清
          /清缓存 题库      只清 CF 题库映射（清完下次查询要重下 2.2MB，一般不用碰）

        为什么值得有这个指令：战绩缓存是**可选内容**（头像、rating 曲线、单场成绩）的
        快照。第三方接口抽风/改版时，缓存里那半截数据会一直显示到 TTL 到期，看着就像
        「插件坏了」；清一次立刻重查，是最快的自救手段。

        **不动的东西**（用户明确划线）：赛程缓存（另一个文件，清掉要当场重新抓）、
        订阅表、提醒记录、已推送记录、群备注、名言库。
        """
        deny = self._deny_if_restricted(event, "清缓存")
        if deny:
            yield event.plain_result(deny)
            return
        arg = _join_args(kind, extra).strip().lower()
        stats = self._cache_stats_safe()

        def _usage() -> str:
            return (
                "🧹 战绩查询缓存清理\n"
                "\n"
                f"· CF 用户档案：{stats.get('profile', 0)} 条\n"
                f"· CF 单场成绩：{stats.get('contest', 0)} 条\n"
                f"· 牛客档案：{stats.get('nk', 0)} 条\n"
                f"· CF 题库映射：{stats.get('problemset', 0)} 条（全局共享，24 小时）\n"
                f"（合计 {stats.get('total', 0)} 条，其中已过期 {stats.get('expired', 0)} 条）\n"
                "\n"
                "用法：\n"
                "  /清缓存 cf     清 CF（档案 + 单场成绩）\n"
                "  /清缓存 nk     清牛客（档案）\n"
                "  /清缓存 全部   两个都清\n"
                "  /清缓存 题库   只清 CF 题库映射\n"
                "\n"
                "🗓 赛程缓存（比赛数据）不受影响；订阅、提醒、已推送记录也都不动。\n"
                "清掉之后下一次 /cf、/nk 会重新联网取数，接口恢复正常时可立刻看到最新结果。"
            )

        if not arg:
            yield event.plain_result(_usage())
            return

        target = self.CACHE_ALIASES.get(arg)
        if not target:
            yield event.plain_result(
                f"❓ 不认识的缓存类别「{arg}」。可填 cf / nk / 全部 / 题库，"
                "不带参数则查看各类条数。\n\n" + _usage()
            )
            return
        kinds = ("cf", "nk") if target == "all" else (target,)
        try:
            res = await asyncio.to_thread(self._clear_profile_cache, kinds)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[contest_daily] /清缓存 失败: {type(e).__name__}: {e}")
            yield event.plain_result(f"❌ 清理失败：{type(e).__name__}: {e}")
            return
        after = self._cache_stats_safe()
        lines = ["🧹 " + self._cache_result_text(kinds, res)]
        if target != "problemset":
            lines.append(
                f"📦 现在还剩：CF 档案 {after.get('profile', 0)} · CF 单场成绩 "
                f"{after.get('contest', 0)} · 牛客档案 {after.get('nk', 0)}"
            )
        logger.info(f"[contest_daily] /清缓存 {target} 完成：{res}")
        yield event.plain_result("\n".join(lines))

    @filter.command("whoami", alias={"我的ID", "我的id", "身份"})
    async def cmd_whoami(self, event: AstrMessageEvent):
        """查看自己的身份信息（用于申请指令权限，默认所有人可用）"""
        deny = self._deny_if_restricted(event, "whoami")
        if deny:
            yield event.plain_result(deny)
            return
        try:
            is_admin = bool(event.is_admin())
        except Exception:  # noqa: BLE001
            is_admin = False
        sid = _safe_sid(event)

        def _safe(fn, default="未知"):
            try:
                v = fn()
                return str(v) if v not in (None, "") else default
            except Exception:  # noqa: BLE001
                return default

        role = str(getattr(event, "role", "") or "member")
        role_cn = {"owner": "群主", "admin": "管理员", "member": "普通成员"}.get(role, role)
        in_wl = sid in self._admin_users()
        authorized = self._is_authorized(event)

        lines = [
            "🔍 你的身份信息",
            "",
            f"· 用户 ID：{sid}",
            f"· 昵称：{_safe(event.get_sender_name)}",
            f"· 群 ID：{_safe(event.get_group_id, '（私聊）')}",
            f"· 群内角色：{role_cn}",
            f"· AstrBot 管理员：{'是' if is_admin else '否'}",
            f"· 在授权白名单：{'是' if in_wl else '否'}",
            "",
            f"→ 受限指令权限：{'✅ 可以使用' if authorized[0] else '❌ 无权限'}",
        ]

        # 各指令当前生效的权限等级（配置里可逐条改），方便用户自查「我为啥不能用 X」
        lines += ["", "📋 各指令权限（可在插件配置里改）："]
        for name in self.DEFAULT_PERMISSIONS:
            level = self._cmd_level(name)
            mark = "✅" if self._allowed_for(event, level) else "❌"
            lines.append(f"{mark} /{name} — {self.PERM_LABELS.get(level, level)}")

        if not authorized[0]:
            lines += [
                "",
                "如需使用 /订阅日报、/刷新赛程 等管理指令，",
                "把上面的「用户 ID」发给协会管理员，加进插件配置即可。",
            ]
        yield event.plain_result("\n".join(lines))
