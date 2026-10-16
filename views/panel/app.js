/*
 * 竞赛日报 · 「推送测试」面板前端（1.3.0 起，1.6.0 增「名言库」「缓存维护」）
 *
 * 运行在 Dashboard 的受限 iframe 里，只能通过 window.AstrBotPluginView 这个 bridge
 * 和宿主通信（拿不到 cookies / localStorage / 父页面 DOM）。后端接口在 main.py 里用
 * context.register_web_api() 注册，endpoint 是**相对插件**的，不带插件名前缀：
 *
 *   apiGet("status")                                             运行状态 + 目标会话 + 提醒计划 + 名言库 + 缓存
 *   apiPost("push-test",   {scope,targets})                      只给勾选的会话推日报测试
 *   apiPost("remind-test", {scope,targets,contestId})            只给勾选的会话推赛前提醒测试
 *   apiPost("alias",       {umo, alias})                         给会话绑自定义备注
 *   apiPost("reset-sent",  {})                                   把当天「已推送」清零
 *   apiPost("clear-cache", {kind:"cf"|"nk"|"problemset"|"all"})  清战绩查询缓存
 *   apiPost("quote",       {action, id?, text?, by?})            名言库增/删/屏蔽/恢复
 *
 * 勾选状态**默认全空**：用户要求「测试要选择推送的群，以免打扰」。
 * 所以没勾任何会话时两个测试按钮是禁用的，绝不会悄悄退化成「全群开推」。
 *
 * 名言库区（1.6.0）：用户要求「名言库支持可视化管理，我要看到全部名言」——
 * 所以这里把**每一条**都渲染出来（含被屏蔽的，只是显示为灰色 + 标「已屏蔽」），
 * 并支持按分类 / 关键词 / 只看已屏蔽 三种筛选。屏蔽与新增都写服务端，成功后直接用
 * 接口返回的整份名言库重渲染（不再多发一次 status 请求）。
 *
 * 降级考虑：bridge 还没注入（或页面被直接打开）时不要抛异常，给出可读提示即可 ——
 * 插件本体和 /推送测试、/提醒测试 指令都不受影响。
 */

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;

const $ = (id) => document.getElementById(id);
const els = {
  time: $("c-time"),
  subs: $("c-subs"),
  sent: $("c-sent"),
  cacheCard: $("c-cache"),   // 顶部状态卡「赛程缓存」
  cacheBox: $("cache"),      // ⑤ 缓存维护区的容器（别和上面那张卡混了）
  hint: $("hint-line"),
  banner: $("banner"),
  sessions: $("sessions"),
  sessionsTitle: $("sessions-title"),
  targets: $("targets"),
  targetsHint: $("targets-hint"),
  plan: $("plan"),
  planHint: $("plan-hint"),
  btnPush: $("btn-push"),
  btnRemind: $("btn-remind"),
  btnReset: $("btn-reset"),
  btnRefresh: $("btn-refresh"),
  btnAll: $("btn-all"),
  btnNone: $("btn-none"),
  btnUnread: $("btn-unread"),
  // 名言库
  quotes: $("quotes"),
  quotesHint: $("quotes-hint"),
  quoteCat: $("quote-cat"),
  quoteSearch: $("quote-search"),
  quoteNewText: $("quote-new-text"),
  quoteNewBy: $("quote-new-by"),
  btnQuoteAdd: $("btn-quote-add"),
  btnQuoteReset: $("btn-quote-reset"),
  btnOnlyBlocked: $("btn-only-blocked"),
  quoteBanner: $("quote-banner"),
  // 缓存维护
  cacheHint: $("cache-hint"),
  cacheBanner: $("cache-banner"),
};

/** 面板的本地状态：会话清单、勾选集合、提醒计划、本轮选中的比赛、名言库、缓存 */
const state = {
  targets: [],
  selected: new Set(),
  reminders: [],
  plan: {},
  picked: "",
  quotes: { items: [], stats: {}, cats: [], truncated: false, enabledCats: [] },
  qFilter: { cat: "", kw: "", onlyBlocked: false },
  cacheStats: {},
};

/** 后端若返回 {status:"ok", data:{...}}，bridge 会自动拆包；这里对两种形态都兼容。 */
function unwrap(res) {
  if (res && typeof res === "object" && res.status === "ok" && "data" in res) {
    return res.data;
  }
  return res;
}

const errText = (e) => (e && e.message ? e.message : e);

/** 每个区自带一条提示条（日报测试在一块、名言库在一块、缓存维护在一块），别互相盖掉。 */
function bannerIn(node, kind, text) {
  if (!node) return;
  node.className = "banner show " + kind;
  node.textContent = text;
}

function clearBannerIn(node) {
  if (!node) return;
  node.className = "banner";
  node.textContent = "";
}

function banner(kind, text) {
  bannerIn(els.banner, kind, text);
}

function clearBanner() {
  clearBannerIn(els.banner);
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function statusTag(status, label, cls) {
  const span = document.createElement("span");
  span.className = "tag " + (cls || status || "skipped");
  span.textContent = label || status || "未知";
  return span;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

/** umo -> 面板上显示的名字（优先备注，其次「群 1234」） */
function nameOf(umo) {
  const t = state.targets.find((x) => x.umo === umo);
  return t ? t.label : umo;
}

// ---------------------------------------------------------------- 目标会话

function syncButtons() {
  const n = state.selected.size;
  els.btnPush.textContent = n ? `立即推送日报测试（${n} 个会话）` : "立即推送日报测试";
  els.btnRemind.textContent = n ? `测试赛前提醒（${n} 个会话）` : "测试赛前提醒";
  els.btnPush.disabled = n === 0;
  els.btnRemind.disabled = n === 0;
  els.targetsHint.textContent = state.targets.length
    ? `${n} / ${state.targets.length} 已勾选`
    : "";
}

function setBusy(on) {
  [els.btnPush, els.btnRemind, els.btnReset, els.btnRefresh,
   els.btnAll, els.btnNone, els.btnUnread].forEach((b) => {
    if (b) b.disabled = on;
  });
  if (!on) syncButtons();
}

async function saveAlias(umo, value, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const data = unwrap(await bridge.apiPost("alias", { umo, alias: value }));
    if (!data || data.ok === false) {
      banner("err", (data && data.message) || "备注保存失败");
    } else {
      banner("ok", data.message);
      await loadStatus({ keepBanner: true, keepDetail: true });
    }
  } catch (e) {
    banner("err", "备注保存失败：" + errText(e));
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

function renderTargets() {
  els.targets.innerHTML = "";
  if (!state.targets.length) {
    els.targets.className = "empty";
    els.targets.textContent = "还没有任何订阅会话 —— 在群里 @机器人 发 /订阅日报 即可加入。";
    syncButtons();
    return;
  }
  els.targets.className = "";
  const ul = el("ul", "targets");
  state.targets.forEach((t) => {
    const li = el("li");

    const line = el("label", "pickline");
    const cb = el("input", "pick");
    cb.type = "checkbox";
    cb.value = t.umo;
    cb.checked = state.selected.has(t.umo);
    cb.addEventListener("change", () => {
      if (cb.checked) state.selected.add(t.umo);
      else state.selected.delete(t.umo);
      syncButtons();
    });
    const name = el("span", "name" + (t.alias ? "" : " unnamed"), t.label);
    line.append(cb, name, el("span", "umo", t.umo));
    li.appendChild(line);

    li.appendChild(
      statusTag(t.sentToday ? "sent" : "skipped", t.sentToday ? "今天已收到日报" : "今天还没发")
    );
    if (t.by) li.appendChild(el("span", "label", "由 " + t.by + " 订阅"));

    const box = el("span", "aliasbox");
    const input = el("input", "alias-input");
    input.type = "text";
    input.maxLength = 24;
    input.placeholder = t.kind === "group" ? "备注，如 计科2201群" : "备注";
    input.value = t.alias || "";
    const btn = el("button", "mini", "保存备注");
    btn.type = "button";
    btn.addEventListener("click", () => saveAlias(t.umo, input.value, btn));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveAlias(t.umo, input.value, btn);
      }
    });
    box.append(input, btn);
    li.appendChild(box);

    ul.appendChild(li);
  });
  els.targets.appendChild(ul);
  syncButtons();
}

// ---------------------------------------------------------------- 提醒计划

function renderPlan() {
  els.plan.innerHTML = "";
  const items = state.reminders || [];
  const meta = state.plan || {};
  const lead = meta.lead === undefined ? "—" : meta.lead;

  if (meta.enabled === false) {
    els.planHint.textContent = "提醒已关闭";
  } else {
    els.planHint.textContent = `开赛前 ${lead} 分钟提醒 · 未来 ${meta.hours || "?"} 小时内 ${meta.count || 0} 场`;
  }

  if (!items.length) {
    els.plan.className = "empty";
    els.plan.textContent = meta.enabled === false
      ? "赛前提醒已关闭（把「赛前提醒提前量」设为大于 0 即可开启）。"
      : `未来 ${meta.hours || "?"} 小时内没有比赛（或赛程缓存还没抓到）。` +
        "点「测试赛前提醒」时会自动改用最近的一场比赛来渲染文案。";
    return;
  }

  els.plan.className = "";
  const ul = el("ul", "plan");
  items.forEach((it, i) => {
    const li = el("li");
    const line = el("label");

    const radio = el("input");
    radio.type = "radio";
    radio.name = "plan";
    radio.value = it.id;
    radio.checked = state.picked ? state.picked === it.id : i === 0;
    radio.addEventListener("change", () => {
      state.picked = it.id;
    });

    const title = el("span", "ptitle", `${it.emoji || ""}${it.platformName} · ${it.title}`.trim());
    const time = el(
      "span",
      "ptime",
      `⏰ ${it.remindText} 提醒 · ${it.startText} 开赛（还有 ${it.minutesToStart} 分钟）`
    );
    line.append(radio, title, time);
    li.appendChild(line);

    if (it.phase === "window") li.appendChild(statusTag("failed", "正在提醒窗口内"));
    else if (it.phase === "off") li.appendChild(statusTag("skipped", "提醒已关闭"));
    else li.appendChild(statusTag("info", "待提醒"));

    if (it.remindedCount > 0) {
      li.appendChild(
        statusTag("sent", `已提醒 ${it.remindedCount}/${it.subs || 0}`, "sent")
      );
    }
    ul.appendChild(li);
  });
  els.plan.appendChild(ul);
}

// ---------------------------------------------------------------- 名言库（1.6.0）

/** 服务端返回的整份名言库数据直接接管（增删屏蔽都会返回刷新后的版本，省一次往返） */
function applyQuotes(data) {
  if (!data || typeof data !== "object") return;
  state.quotes = {
    items: data.items || [],
    stats: data.stats || {},
    cats: data.cats || [],
    truncated: !!data.truncated,
    enabledCats: data.enabledCats || [],
    maxLength: data.maxLength || 60,
    maxCustom: data.maxCustom || 200,
  };
  if (state.quotes.maxLength) {
    els.quoteNewText.maxLength = state.quotes.maxLength;
  }
  fillQuoteCats();
  renderQuotes();
}

function fillQuoteCats() {
  const cats = state.quotes.cats || [];
  const cur = state.qFilter.cat;
  els.quoteCat.innerHTML = "";
  cats.forEach((c) => {
    const value = Array.isArray(c) ? c[0] : c.id;
    const label = Array.isArray(c) ? c[1] : c.label;
    const opt = el("option", "", label);
    opt.value = value === undefined || value === null ? "" : String(value);
    els.quoteCat.appendChild(opt);
  });
  els.quoteCat.value = cur;
}

function filteredQuotes() {
  const f = state.qFilter;
  const kw = f.kw.trim().toLowerCase();
  return (state.quotes.items || []).filter((q) => {
    if (f.onlyBlocked && !q.blocked) return false;
    if (f.cat && q.cat !== f.cat) return false;
    if (kw) {
      const hit = String(q.text || "").toLowerCase().indexOf(kw) >= 0;
      const hitBy = String(q.by || "").toLowerCase().indexOf(kw) >= 0;
      if (!hit && !hitBy) return false;
    }
    return true;
  });
}

function quoteStateText(q) {
  if (q.blocked) return "已屏蔽（不会被抽到）";
  if (!q.catOn) return "分类未启用";
  return "可用";
}

function quoteRow(q) {
  const li = el("li", q.blocked ? "off" : "");
  li.appendChild(
    statusTag(
      q.blocked ? "denied" : q.catOn ? "sent" : "skipped",
      quoteStateText(q),
      q.blocked ? "denied" : q.catOn ? "sent" : "skipped"
    )
  );
  // 用 textContent 而不是 innerHTML：句子来自配置/用户输入，绝不能当 HTML 解析
  li.appendChild(el("span", "qtext", "〖" + q.text + "〗"));
  if (q.by) li.appendChild(el("span", "qby", "—— " + q.by));
  li.appendChild(statusTag("info", q.catLabel || q.cat || "自定义"));
  li.appendChild(statusTag("skipped", q.source === "custom" ? "自定义" : "内置"));

  const toggle = el("button", "mini", q.blocked ? "恢复" : "屏蔽");
  toggle.type = "button";
  toggle.addEventListener("click", () =>
    doQuote(q.blocked ? "unblock" : "block", { id: q.id }, toggle, q.blocked ? "恢复中…" : "屏蔽中…")
  );
  li.appendChild(toggle);

  if (q.source === "custom") {
    const del = el("button", "mini", "删除");
    del.type = "button";
    del.addEventListener("click", () => {
      if (window.confirm("删除这条自定义名言？\n\n〖" + q.text + "〗")) {
        doQuote("del", { id: q.id }, del, "删除中…");
      }
    });
    li.appendChild(del);
  }
  return li;
}

function renderQuotes() {
  const st = state.quotes.stats || {};
  const bits = [];
  if (st.total !== undefined) bits.push(`共 ${st.total} 条`);
  if (st.eligible !== undefined) bits.push(`可抽取 ${st.eligible}`);
  if (st.blocked) bits.push(`已屏蔽 ${st.blocked}`);
  if (st.custom) bits.push(`自定义 ${st.custom}`);
  els.quotesHint.textContent = bits.join(" · ");
  els.btnOnlyBlocked.textContent = state.qFilter.onlyBlocked ? "显示全部" : "只看已屏蔽";

  const rows = filteredQuotes();
  const total = (state.quotes.items || []).length;
  els.quotes.innerHTML = "";
  if (!total) {
    els.quotes.className = "empty";
    els.quotes.textContent = "名言库是空的（配置里把分类全关了？）。";
    return;
  }
  if (!rows.length) {
    els.quotes.className = "empty";
    els.quotes.textContent = `没有符合当前筛选的名言（全库共 ${total} 条）—— 点「重置筛选」看全部。`;
    return;
  }
  els.quotes.className = "";
  const ul = el("ul", "quotes");
  rows.forEach((q) => ul.appendChild(quoteRow(q)));
  els.quotes.appendChild(ul);
  if (state.quotes.truncated) {
    els.quotes.appendChild(el("div", "label", "（名言太多，这里只列了一部分）"));
  }
  if (rows.length !== total) {
    els.quotes.appendChild(el("div", "label", `（筛选出 ${rows.length} / ${total} 条）`));
  }
}

async function doQuote(action, body, btn, busyText) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyText;
  clearBannerIn(els.quoteBanner);
  try {
    const data = unwrap(await bridge.apiPost("quote", Object.assign({ action }, body || {})));
    if (data && data.quotes) applyQuotes(data.quotes);
    if (!data || data.ok === false) {
      bannerIn(els.quoteBanner, "err", (data && data.message) || "操作没有完成");
    } else {
      bannerIn(els.quoteBanner, "ok", data.message || "已保存");
    }
  } catch (e) {
    bannerIn(els.quoteBanner, "err", "名言库操作失败：" + errText(e));
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function doQuoteAdd() {
  const text = els.quoteNewText.value.trim();
  const by = els.quoteNewBy.value.trim();
  if (!text) {
    bannerIn(els.quoteBanner, "warn", "先填一句名言正文再点「添加」。");
    els.quoteNewText.focus();
    return;
  }
  await doQuote("add", { text, by }, els.btnQuoteAdd, "保存中…");
  // 只有真的写进去了才清输入框：失败了用户还能改一改重试
  const st = state.quotes.items || [];
  if (st.some((q) => q.text === text && q.source === "custom")) {
    els.quoteNewText.value = "";
    els.quoteNewBy.value = "";
  }
}

// ---------------------------------------------------------------- 缓存维护（1.6.0）

function cacheRow(kind, name, detail, count) {
  const li = el("li");
  li.appendChild(el("span", "cname", name));
  li.appendChild(el("span", "cnum", detail));
  const btn = el("button", "mini", count ? "清除" : "已空");
  btn.type = "button";
  btn.disabled = !count;
  btn.addEventListener("click", () => doClearCache(kind, btn));
  li.appendChild(btn);
  return li;
}

function renderCache() {
  const c = state.cacheStats || {};
  const profile = c.profile || 0;
  const contest = c.contest || 0;
  const nk = c.nk || 0;
  const prob = c.problemset || 0;
  const total = c.total || 0;
  els.cacheHint.textContent = total
    ? `共 ${total} 条${c.expired ? `（其中已过期 ${c.expired} 条）` : ""}`
    : "目前是空的";

  els.cacheBox.innerHTML = "";
  els.cacheBox.className = "";
  const ul = el("ul", "cache");
  ul.appendChild(
    cacheRow(
      "cf",
      "CF 查询缓存",
      `用户档案 ${profile} 条 · 单场成绩 ${contest} 条`,
      profile + contest
    )
  );
  ul.appendChild(cacheRow("nk", "牛客查询缓存", `用户档案 ${nk} 条`, nk));
  ul.appendChild(
    cacheRow("problemset", "CF 题库映射", `${prob} 条（全局共享，24 小时；清掉要重下题库）`, prob)
  );
  els.cacheBox.appendChild(ul);
  els.cacheBox.appendChild(
    el("div", "label", "赛程缓存（比赛数据）不在这里，清缓存不会动它。")
  );
}

async function doClearCache(kind, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "清理中…";
  clearBannerIn(els.cacheBanner);
  try {
    const data = unwrap(await bridge.apiPost("clear-cache", { kind }));
    if (data && data.cache) {
      state.cacheStats = data.cache;
      renderCache();
    }
    if (!data || data.ok === false) {
      bannerIn(els.cacheBanner, "err", (data && data.message) || "清理没有完成");
    } else {
      bannerIn(els.cacheBanner, "ok", data.message || "已清理");
    }
  } catch (e) {
    bannerIn(els.cacheBanner, "err", "清理失败：" + errText(e));
  } finally {
    if (btn.isConnected) {
      btn.disabled = false;
      btn.textContent = old;
    }
  }
}

// ---------------------------------------------------------------- 明细 / 结果

function renderDetail(title, rows) {
  els.sessionsTitle.textContent = title;
  els.sessions.innerHTML = "";
  if (!rows || !rows.length) {
    els.sessions.className = "empty";
    els.sessions.textContent = "（没有明细）";
    return;
  }
  els.sessions.className = "";
  const ul = el("ul", "sessions");
  rows.forEach((r) => {
    const li = el("li");
    li.appendChild(statusTag(r.status, r.label));
    li.appendChild(el("span", "name", nameOf(r.umo)));
    const code = el("span", "umo", r.umo);
    li.appendChild(code);
    ul.appendChild(li);
  });
  els.sessions.appendChild(ul);
}

// ---------------------------------------------------------------- 状态

function renderStatus(data) {
  els.time.textContent = data.dailyTime || "—";
  // 1.7.3：后端已把「已收到」收敛成「当前订阅表中已收到的」；这里再钳一道 0，
  // 就算账本出怪账（比如手工改过 sent_log.json）也不会显示「-1 个待发」。
  const sentN = Math.max(0, Math.min(data.sentToday || 0, data.subs || 0));
  els.subs.textContent = `${sentN} / ${data.subs || 0}`;
  els.sent.textContent = data.subs ? `${data.subs - sentN} 个待发` : "—";
  els.cacheCard.textContent = data.contestCount
    ? `${data.contestCount} 场 · ${fmtTime(data.generatedAt)}`
    : "空（等待抓取）";

  const bits = [];
  if (data.appliedTime && data.appliedTime !== data.dailyTime) {
    bits.push(`已生效时间 ${data.appliedTime}，和当前配置 ${data.dailyTime} 不一致 —— 下一轮会按新时间跑，并把当天记录清零。`);
  }
  if (data.resetOnTimeChange === false) {
    bits.push("「改时间就重置当天记录」已关闭，改了时间不会重复推给已经收到的群。");
  }
  bits.push(
    data.remindMinutes > 0
      ? `赛前提醒：开赛前 ${data.remindMinutes} 分钟`
      : "赛前提醒已关闭"
  );
  els.hint.textContent = bits.join("　·　");
}

async function loadStatus(opts) {
  const o = opts || {};
  if (!o.keepBanner) clearBanner();
  if (!o.keepTargets) {
    els.targets.className = "empty";
    els.targets.textContent = "正在读取…";
  }
  if (!o.keepDetail) {
    els.sessions.className = "empty";
    els.sessions.textContent = "正在读取…";
  }
  try {
    const data = unwrap(await bridge.apiGet("status"));
    if (!data || data.ok === false) {
      banner("err", (data && data.message) || "读取状态失败");
      return null;
    }
    renderStatus(data);

    state.targets = data.targets || [];
    state.reminders = data.reminders || [];
    state.plan = data.plan || {};
    // 会话被退订后，勾选集合里要同步剔掉，免得按钮数着「勾了 2 个」却发不出去
    const alive = new Set(state.targets.map((t) => t.umo));
    Array.from(state.selected).forEach((u) => {
      if (!alive.has(u)) state.selected.delete(u);
    });
    // 选中的比赛如果已经不在计划里（比完/被刷新），重置回「用最上面那场」
    if (state.picked && !state.reminders.some((r) => r.id === state.picked)) {
      state.picked = "";
    }

    renderTargets();
    renderPlan();
    // 1.6.0：缓存维护与名言库都挂在 status 上，一次请求全拿到
    state.cacheStats = data.cache || {};
    renderCache();
    if (data.quotes) applyQuotes(data.quotes);
    if (!o.keepDetail) {
      renderDetail(
        "订阅会话明细",
        (data.sessions || []).map((s) => ({
          umo: s.umo,
          status: s.sentToday ? "sent" : "skipped",
          label: s.sentToday ? "今天已收到日报" : "今天还没发",
        }))
      );
      if (data.sessionsTruncated) {
        els.sessions.appendChild(el("div", "label", "（订阅会话太多，这里只列了一部分）"));
      }
    }
    return data;
  } catch (e) {
    if (!o.keepTargets) els.targets.textContent = "读取失败：" + errText(e);
    if (!o.keepDetail) els.sessions.textContent = "读取失败：" + errText(e);
    return null;
  }
}

// ---------------------------------------------------------------- 动作

async function withBusy(btn, busyText, fn) {
  const old = btn.textContent;
  setBusy(true);
  btn.textContent = busyText;
  try {
    await fn();
  } finally {
    btn.textContent = old;
    setBusy(false);
  }
}

function selectedParams() {
  return { scope: "selected", targets: Array.from(state.selected) };
}

function noSelectionBanner() {
  banner("warn", "还没有勾选任何会话。先在「① 目标会话」里勾上要接收测试消息的群再点 —— 这样才不会打扰其它群。");
}

async function doPush() {
  if (!state.selected.size) {
    noSelectionBanner();
    return;
  }
  await withBusy(els.btnPush, "推送中…", async () => {
    clearBanner();
    try {
      const data = unwrap(await bridge.apiPost("push-test", selectedParams()));
      if (!data || data.ok === false) {
        banner(data && data.needSelection ? "warn" : "err", (data && data.message) || "推送没有完成");
      } else {
        const bad = (data.denied || 0) + (data.failed || 0);
        banner(
          bad ? "warn" : "ok",
          `日报测试完成：${data.message}\n` +
            `本轮只发给你勾选的会话，其它群没有被打扰。` +
            (bad ? "\n未送出的会话见下方明细。" : "") +
            (data.sent ? "\n已送达的会话记为「今天已发」，今天到点后不再重复推送。" : "")
        );
      }
      renderDetail("本次推送明细", (data && data.sessions) || []);
      await loadStatus({ keepBanner: true, keepDetail: true });
    } catch (e) {
      banner("err", "推送失败：" + errText(e));
    }
  });
}

async function doRemind() {
  if (!state.selected.size) {
    noSelectionBanner();
    return;
  }
  await withBusy(els.btnRemind, "推送中…", async () => {
    clearBanner();
    try {
      const params = selectedParams();
      if (state.picked) params.contestId = state.picked;
      const data = unwrap(await bridge.apiPost("remind-test", params));
      if (!data || data.ok === false) {
        banner(data && data.needSelection ? "warn" : "err", (data && data.message) || "提醒测试没有完成");
      } else {
        const bad = (data.denied || 0) + (data.failed || 0);
        const lines = [
          `提醒测试完成：${data.message}`,
          `用的比赛：${data.contestTitle}（${data.contestStart} 开赛，提前 ${data.lead} 分钟提醒）`,
        ];
        if (data.staleContest) {
          lines.push("⚠️ 缓存里没有即将开始的比赛，这条是用最近一场（已开赛）的文案渲染的。");
        }
        lines.push("测试提醒不会记入「已提醒」，不影响当天真正的赛前提醒。");
        if (bad) lines.push("未送出的会话见下方明细。");
        banner(bad ? "warn" : "ok", lines.join("\n"));
      }
      renderDetail("本次提醒测试明细", (data && data.sessions) || []);
      await loadStatus({ keepBanner: true, keepDetail: true });
    } catch (e) {
      banner("err", "提醒测试失败：" + errText(e));
    }
  });
}

async function doReset() {
  await withBusy(els.btnReset, "重置中…", async () => {
    clearBanner();
    try {
      const data = unwrap(await bridge.apiPost("reset-sent", {}));
      if (!data || data.ok === false) {
        banner("err", (data && data.message) || "重置失败");
        return;
      }
      banner(
        "ok",
        data.message + "\n没到推送时间就不会自动推；已经过了时间的话，30 秒内会自动补跑一轮，" +
          "也可以直接点「立即推送日报测试」。"
      );
      await loadStatus({ keepBanner: true });
    } catch (e) {
      banner("err", "重置失败：" + errText(e));
    }
  });
}

function selectAll(pred) {
  state.selected = new Set(
    state.targets.filter((t) => (pred ? pred(t) : true)).map((t) => t.umo)
  );
  renderTargets();
}

(async function init() {
  if (!bridge || typeof bridge.apiGet !== "function") {
    banner(
      "err",
      "没有检测到 Dashboard 的插件视图环境，面板无法调用后端接口。\n" +
        "请从 AstrBot WebUI 的「插件」→「竞赛日报」→「推送测试」打开本页面；" +
        "或者直接在群里 @机器人 发 /推送测试、/提醒测试、/清缓存 cf。"
    );
    els.sessions.textContent = "（不可用）";
    els.targets.textContent = "（不可用）";
    els.plan.textContent = "（不可用）";
    els.quotes.textContent = "（不可用）";
    els.cacheBox.textContent = "（不可用）";
    [els.btnPush, els.btnRemind, els.btnReset, els.btnRefresh,
     els.btnAll, els.btnNone, els.btnUnread, els.btnQuoteAdd,
     els.btnQuoteReset, els.btnOnlyBlocked].forEach((b) => {
      if (b) b.disabled = true;
    });
    return;
  }
  try {
    await bridge.ready(); // 拿初始上下文（插件名 / 主题 / 语言）；失败也继续，后面调用会给出真实错误
  } catch (e) {
    /* 忽略：AstrBot 会把主题写进 <html data-theme>，不依赖这里 */
  }
  els.btnPush.addEventListener("click", doPush);
  els.btnRemind.addEventListener("click", doRemind);
  els.btnReset.addEventListener("click", doReset);
  els.btnRefresh.addEventListener("click", () => loadStatus());
  els.btnAll.addEventListener("click", () => selectAll());
  els.btnNone.addEventListener("click", () => selectAll(() => false));
  els.btnUnread.addEventListener("click", () => selectAll((t) => !t.sentToday));
  // 名言库：筛选（纯前端过滤，不再打服务端）与新增
  els.quoteCat.addEventListener("change", () => {
    state.qFilter.cat = els.quoteCat.value || "";
    renderQuotes();
  });
  els.quoteSearch.addEventListener("input", () => {
    state.qFilter.kw = els.quoteSearch.value || "";
    renderQuotes();
  });
  els.btnOnlyBlocked.addEventListener("click", () => {
    state.qFilter.onlyBlocked = !state.qFilter.onlyBlocked;
    renderQuotes();
  });
  els.btnQuoteReset.addEventListener("click", () => {
    state.qFilter = { cat: "", kw: "", onlyBlocked: false };
    els.quoteSearch.value = "";
    els.quoteCat.value = "";
    renderQuotes();
  });
  els.btnQuoteAdd.addEventListener("click", doQuoteAdd);
  [els.quoteNewText, els.quoteNewBy].forEach((box) => {
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        doQuoteAdd();
      }
    });
  });
  syncButtons();
  await loadStatus();
})();
