// 真浏览器整局驱动：像真人一样只用界面控件打完一局。
// 用法：node driver.mjs --url http://127.0.0.1:8765 --role seer --seed 11 [--speed fast] [--name 小明]
//        [--out DIR] [--shots deal,night,...] [--mobile] [--timeout 600] [--no-reload]
// 结果（JSON）写到 stdout 最后一行；DIR 下写 result.json 与截图。
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const argv = process.argv.slice(2);
const opt = (k, d) => { const i = argv.indexOf(`--${k}`); return i < 0 ? d : argv[i + 1]; };
const flag = (k) => argv.includes(`--${k}`);
const URL_ = opt('url', 'http://127.0.0.1:8765');
const ROLE = opt('role', 'random');
const SEED = opt('seed', '');
const SPEED = opt('speed', 'fast');
const NAME = opt('name', '小明');
const OUT = opt('out', '');
const SHOTS = new Set((opt('shots', '') || '').split(',').filter(Boolean));
const TIMEOUT = Number(opt('timeout', '900')) * 1000;
const DO_RELOAD = !flag('no-reload');
const MOBILE = flag('mobile');
const SEAT = opt('seat', '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 驱动自己的确定性随机数（不依赖服务端种子）
let rs = (Number(SEED) || 1) * 2654435761 % 2 ** 32;
const rnd = () => { rs = (rs * 1664525 + 1013904223) % 2 ** 32; return rs / 2 ** 32; };
const pick = (a) => a[Math.floor(rnd() * a.length)];

const res = {
  role_pref: ROLE, seed: SEED, console_errors: [], page_errors: [], problems: [], answered: [], speeches: [],
  scenes: [], layout: [], shots: [], reload: null, god: null, requests: 0,
};
const problem = (t) => { if (!res.problems.includes(t)) res.problems.push(t); console.error('PROBLEM', t); };

const SNAP = () => {
  const S = window.__ww.S;
  const q = S.request;
  return {
    game_id: S.game && S.game.game_id, you: S.you, role: S.role, day: S.day, phase: S.phase, over: S.over, hasResult: !!S.result,
    review: !!S.review, lastSeq: S.lastSeq, myDead: S.myDead, canGod: S.canGod, god: S.god, godRoles: S.godRoles ? Object.keys(S.godRoles).length : 0,
    alive: S.alive, replaying: S.replaying, conn: S.conn, scene: document.body.dataset.scene || '', sent: S.sent, previewing: S.previewing,
    hasPreview: !!S.preview, reqN: S.reqN || 0, speaker: S.speaker ? { seq: S.speaker.seq, seat: S.speaker.seat, done: S.speaker.done, ack: S.speaker.ack } : null,
    thinking: Object.keys(S.thinking || {}), winner: S.result && S.result.winner,
    req: q ? { id: q.id, kind: q.kind, speech_kind: q.speech_kind, context: q.context, options: q.options } : null,
    modal: !!document.querySelector('#modal-root .modal'), newgame: !!document.querySelector('#modal-root .newgame'),
    godBtn: (() => { const b = document.querySelector('#topbar .tb-btn.god'); return b ? { hidden: b.hidden, text: b.textContent } : null; })(),
    name: S.players && S.players[S.you] ? S.players[S.you].name : null,
  };
};

// 在页面里跑的布局体检
const LAYOUT = () => {
  const issues = [];
  const vw = innerWidth, vh = innerHeight;
  const root = document.documentElement;
  if (root.scrollWidth > vw + 1) issues.push(`横向溢出 scrollWidth=${root.scrollWidth} > ${vw}`);
  if (document.body.scrollWidth > vw + 1) issues.push(`body 横向溢出 ${document.body.scrollWidth} > ${vw}`);
  const rect = (el) => el.getBoundingClientRect();
  const sp = document.querySelectorAll('.seat.speaking').length;
  if (document.body.dataset.scene === 'speakme' && sp !== 1) issues.push(`轮到我发言时有 ${sp} 个座位亮着发言圈`);
  if (sp > 1) issues.push(`同时有 ${sp} 个座位亮着发言圈`);
  const inter = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const center = document.getElementById('center');
  const panel = document.getElementById('panel');
  const judge = document.getElementById('judge');
  const seats = [...document.querySelectorAll('.seat')];
  const wide = vw >= 900;
  if (center && wide) {
    const cr = rect(center);
    for (const s of seats) {
      const sr = rect(s);
      if (!sr.width) continue;
      if (inter(sr, cr) > 40) issues.push(`座位 ${s.dataset.seat} 压到中央舞台`);
    }
  }
  if (wide) {
    const rs = seats.map((s) => [s, rect(s)]);
    for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
      if (inter(rs[i][1], rs[j][1]) > 30) issues.push(`座位 ${rs[i][0].dataset.seat} 与 ${rs[j][0].dataset.seat} 重叠`);
    }
  }
  for (const [id, el] of [['panel', panel], ['judge', judge], ['bottom', document.getElementById('bottom')], ['topbar', document.getElementById('topbar')]]) {
    if (!el) continue;
    const r = rect(el);
    if (!r.width) continue;
    if (r.left < -1 || r.right > vw + 1) issues.push(`${id} 超出视口左右 [${Math.round(r.left)}, ${Math.round(r.right)}]`);
    if (id !== 'panel' && (r.top < -1 || r.bottom > vh + 1)) issues.push(`${id} 超出视口上下 [${Math.round(r.top)}, ${Math.round(r.bottom)}] vh=${vh}`);
  }
  // 内容被裁
  const clip = document.querySelectorAll('.nm, .chip, .btn, .pill, .bubble-big, .judge-line, .phrases button, .seat-tag, h1, h2');
  for (const el of clip) {
    const r = rect(el);
    if (!r.width || !r.height) continue;
    if (getComputedStyle(el).visibility === 'hidden') continue;
    if (el.scrollWidth > el.clientWidth + 2 && ['hidden', 'clip'].includes(getComputedStyle(el).overflowX)) issues.push(`文字被裁 ${el.className || el.tagName}: ${(el.textContent || '').slice(0, 20)}`);
  }
  // 面板内容超出面板（面板自己可以滚动，但中央面板里的关键按钮必须在视口内）
  for (const b of document.querySelectorAll('#panel .btn.primary, #bottom .btn.primary, #bottom textarea')) {
    const r = rect(b);
    if (!r.width) continue;
    if (r.bottom > vh + 1 || r.top < -1) issues.push(`关键控件在视口外 ${b.className || b.tagName} [${Math.round(r.top)}, ${Math.round(r.bottom)}]`);
  }
  return issues;
};

async function shotOf(page, tag) {
  if (!OUT || !SHOTS.has(tag) || res.shots.includes(tag)) return;
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${tag}.png`) });
  res.shots.push(tag);
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: MOBILE ? { width: 390, height: 844 } : { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') res.console_errors.push(m.text()); });
  page.on('pageerror', (e) => res.page_errors.push(String(e)));
  const t0 = Date.now();
  const snap = () => page.evaluate(SNAP);
  const shot = (tag) => shotOf(page, tag);

  await page.goto(`${URL_}/?dev=1`);
  await page.waitForFunction(() => window.__ww && (document.querySelector('#modal-root .newgame') || window.__ww.S.game), null, { timeout: 15000 });
  // 新局对话框：名字 / 身份 / 座位 / 速度 / 种子
  let s = await snap();
  if (!s.newgame) {
    await page.click('#topbar [aria-label="设置"]');
    await page.click('.settings .btn.primary');
    await page.waitForSelector('#modal-root .newgame');
  }
  await page.fill('.newgame input[aria-label="你的名字"]', NAME);
  await page.selectOption('.newgame select[aria-label="身份偏好"]', ROLE);
  if (SEAT) await page.selectOption('.newgame select[aria-label="座位"]', SEAT);
  await page.selectOption('.newgame select[aria-label="速度"]', SPEED);
  if (SEED) await page.fill('.newgame input[aria-label="种子"]', SEED);
  await shot('newgame');
  await page.click('.modal-actions .btn.primary');
  await page.waitForFunction(() => window.__ww.S.game && window.__ww.S.game.game_id, null, { timeout: 15000 });
  s = await snap();
  res.game_id = s.game_id; res.you = s.you; res.role = s.role; res.name = s.name;
  if (s.name !== NAME) problem(`名字没带上：${s.name}`);
  if (ROLE !== 'random' && s.role !== ROLE) problem(`身份偏好 ${ROLE} 没生效：${s.role}`);
  if (s.godBtn && !s.godBtn.hidden) problem('开局就显示上帝视角按钮');

  const attempts = {};
  let reloaded = false, lastScene = '', sceneSeen = new Set(), speakN = 0, lastProgress = Date.now(), lastKey = '';
  const layoutAt = async (key) => {
    if (sceneSeen.has(key)) return;
    sceneSeen.add(key);
    const iss = await page.evaluate(LAYOUT);
    for (const i of iss) res.layout.push({ at: key, issue: i });
  };

  for (;;) {
    if (Date.now() - t0 > TIMEOUT) { problem('整局超时'); break; }
    s = await snap();
    const key = `${s.phase}|${s.scene}|${s.lastSeq}|${s.req && s.req.id}|${s.reqN}|${s.sent}|${s.hasPreview}`;
    if (key !== lastKey) { lastKey = key; lastProgress = Date.now(); }
    if (Date.now() - lastProgress > 90000) { problem(`卡住超过 90 秒：${key}`); await shot('stuck'); break; }
    if (s.scene && s.scene !== lastScene) {
      lastScene = s.scene;
      res.scenes.push(s.scene);
      await sleep(350);
      await layoutAt(`${s.scene}`);
      if (s.scene === 'deal') await shot('deal');
      if (s.scene === 'dawn') await shot('dawn');
      if (s.scene === 'reveal') { await sleep(500); await shot('reveal'); }
      if (s.scene === 'night' || ['wolf', 'seer', 'witch'].includes(s.scene)) await shot('night');
      if (s.scene === 'speech') {
        await sleep(1200);
        await shot('speech');
      }
    }
    if (s.over && s.hasResult && s.review) break;

    // 上帝视角
    if (s.myDead && !res.god_done) {
      await handleGod(page, s, res);
    }

    // 中途刷新一次
    if (DO_RELOAD && !reloaded && s.req && (s.req.kind === 'vote' || s.req.kind === 'speak') && s.day >= 1) {
      reloaded = true;
      await sleep(400);
      const before = await snap();
      await page.reload();
      await page.waitForFunction(() => window.__ww && window.__ww.S.game && !window.__ww.S.replaying && window.__ww.S.conn === 'open', null, { timeout: 20000 }).catch(() => problem('刷新后没有恢复对局'));
      await sleep(900);
      const after = await snap();
      const same = ['phase', 'day', 'lastSeq', 'scene', 'you', 'role'].filter((k) => before[k] !== after[k]);
      const reqSame = (before.req && before.req.id) === (after.req && after.req.id);
      const aliveSame = JSON.stringify(before.alive) === JSON.stringify(after.alive);
      res.reload = { at: `${before.phase}/day${before.day}/${before.req && before.req.kind}`, before: { phase: before.phase, scene: before.scene, req: before.req && before.req.id, seq: before.lastSeq },
        after: { phase: after.phase, scene: after.scene, req: after.req && after.req.id, seq: after.lastSeq }, diff: same, reqSame, aliveSame };
      if (same.length || !reqSame || !aliveSame) problem(`刷新后不一致：${JSON.stringify(res.reload)}`);
      continue;
    }

    if (s.req && !s.sent) {
      const id = s.req.id;
      attempts[id] = (attempts[id] || 0) + 1;
      if (attempts[id] > 6) { problem(`请求 ${id}（${s.req.kind}）答不了`); await shot('stuck'); break; }
      try {
        await answer(page, s, { speakN: () => speakN++ });
      } catch (e) {
        problem(`作答 ${s.req.kind} 失败：${String(e).split('\n')[0]}`);
        await sleep(500);
      }
      continue;
    }
    // 打字演出：偶尔点击跳过，其余等它自己演完
    if (s.scene === 'speech' && s.speaker && !s.speaker.done && rnd() < 0.5) {
      await page.click('.bubble-big', { timeout: 1000 }).catch(() => {});
    }
    await sleep(120);
  }

  // 终局
  s = await snap();
  res.final = s;
  if (s.over && s.review) {
    await sleep(400);
    await shot('over');
    await layoutAt('over-final');
    await page.click('.over-actions .btn.primary').catch((e) => problem(`打不开复盘：${String(e).split('\n')[0]}`));
    await page.waitForSelector('#modal-root .modal', { timeout: 3000 }).catch(() => problem('复盘对话框没出现'));
    await sleep(400);
    await shot('review');
    const iss = await page.evaluate(() => {
      const out = [];
      const m = document.querySelector('#modal-root .modal');
      if (!m) return ['无对话框'];
      const r = m.getBoundingClientRect();
      if (r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || r.left < -1 || r.top < -1) out.push(`复盘对话框超出视口 ${JSON.stringify([r.left, r.top, r.right, r.bottom])}`);
      const t = m.textContent || '';
      for (const need of ['狼人', '号']) if (!t.includes(need)) out.push(`复盘里没有「${need}」`);
      return out;
    });
    for (const i of iss) res.layout.push({ at: 'review', issue: i });
    res.review_text_len = await page.evaluate(() => (document.querySelector('#modal-root .modal') || { textContent: '' }).textContent.length);
  } else problem(`没有打到终局与复盘 over=${s.over} review=${s.review}`);
  res.elapsed_s = Math.round((Date.now() - t0) / 1000);
  await sleep(300);
  await browser.close();
  if (OUT) { fs.mkdirSync(OUT, { recursive: true }); fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(res, null, 1)); }
  console.log(JSON.stringify(res));
}

// ---------------------------------------------------------------- 上帝视角
async function handleGod(page, s0, res) {
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))); // 等渲染赶上状态
  const s = await snap2(page);
  const g = res.god || (res.god = { events: [] });
  const b = s.godBtn;
  if (!b) return;
  const visible = !b.hidden;
  if (visible !== (s.canGod || s.god)) { g.events.push('按钮显隐与 canGod 不一致'); problem('上帝视角按钮显隐与 canGod 不一致'); }
  if (!visible || s.god) { return; }
  // 允许了：点开，检查，再关
  res.god_done = true;
  g.appeared_day = s.day; g.appeared_phase = s.phase;
  await page.click('#topbar .tb-btn.god');
  await page.waitForFunction(() => window.__ww.S.god === true, null, { timeout: 5000 }).catch(() => problem('点了上帝视角但没进入'));
  await sleep(500);
  const after = await snap2(page);
  g.roles_known = after.godRoles; g.btn = after.godBtn;
  if (after.godRoles < 9) problem(`上帝视角只给了 ${after.godRoles} 个身份`);
  if (!after.godBtn || !after.godBtn.text.includes('退出')) problem('进入上帝视角后按钮没变成退出');
  const seatBadges = await page.evaluate(() => document.querySelectorAll('.seat .chip.role').length);
  g.role_badges = seatBadges;
  if (seatBadges < 9) problem(`上帝视角下座位身份角标 ${seatBadges}/9`);
  const iss = await page.evaluate(LAYOUT);
  for (const i of iss) res.layout.push({ at: 'god', issue: i });
  await page.click('#topbar .tb-btn.god');
  await page.waitForFunction(() => window.__ww.S.god === false, null, { timeout: 5000 }).catch(() => problem('退出上帝视角失败'));
  g.toggled_back = true;
}
const snap2 = (page) => page.evaluate(SNAP);

// ---------------------------------------------------------------- 作答
async function clickSeat(page, n) {
  await page.click(`.seat[data-seat="${n}"].selectable`, { timeout: 3000 });
}
async function pickSeatAndConfirm(page, targets, label) {
  const t = pick(targets);
  await clickSeat(page, t);
  await sleep(250);
  await shotOf(page, 'pick');
  const btn = page.locator('#panel .pick-actions .btn.primary, #panel .poison-pick .btn.primary').first();
  await btn.waitFor({ state: 'visible', timeout: 3000 });
  const txt = await btn.textContent();
  if (!txt.includes(String(t))) problem(`${label} 确认键文案没带座位号 ${t}：${txt}`);
  await page.waitForFunction(() => { const b = document.querySelector('#panel .pick-actions .btn.primary'); return b && !b.disabled; }, null, { timeout: 2000 });
  await btn.click();
  return t;
}

async function answer(page, s, ctr) {
  const r = s.req;
  res.requests++;
  await page.waitForTimeout(200);
  const rec = { kind: r.kind, day: s.day, phase: s.phase };
  if (r.kind === 'wolf_kill') {
    const targets = r.options.filter((o) => o.action === 'kill' && o.target != null).map((o) => o.target);
    if (r.context.first_night) {
      const k = pick(['jump_seer', 'charge', 'deep', '']);
      rec.stance = k;
      if (k) await page.click(`#panel .pill[data-k="${k}"]`);
    }
    if (r.context.proposals && r.context.proposals.length) rec.proposals = r.context.proposals.length;
    await layoutAt2(page, 'wolf-panel');
    if (rnd() < 0.15 && r.options.find((o) => o.id === 'kill:none')) {
      await page.click('#panel .pick-actions .btn.ghost');
      rec.target = null;
      await page.click('#panel .pick-actions .btn.primary');
    } else rec.target = await pickSeatAndConfirm(page, targets, '刀人');
  } else if (r.kind === 'seer_check') {
    rec.target = await pickSeatAndConfirm(page, r.options.filter((o) => o.target != null).map((o) => o.target), '查验');
  } else if (r.kind === 'vote') {
    const targets = r.options.filter((o) => o.action === 'vote' && o.target != null).map((o) => o.target);
    if (rnd() < 0.1 && r.options.find((o) => o.id === 'vote:abstain')) {
      await page.click('#panel .pick-actions .btn.ghost');
      await page.click('#panel .pick-actions .btn.primary');
      rec.target = null;
    } else rec.target = await pickSeatAndConfirm(page, targets, '投票');
  } else if (r.kind === 'hunter_shoot') {
    const targets = r.options.filter((o) => o.action === 'shoot' && o.target != null).map((o) => o.target);
    if (rnd() < 0.25 && r.options.find((o) => o.id === 'shoot:none')) {
      await page.click('#panel .pick-actions .btn.ghost');
      await page.click('#panel .pick-actions .btn.primary');
      rec.target = null;
    } else rec.target = await pickSeatAndConfirm(page, targets, '开枪');
  } else if (r.kind === 'witch_act') {
    const save = r.options.find((o) => o.action === 'save');
    const poisons = r.options.filter((o) => o.action === 'poison');
    await layoutAt2(page, 'witch-panel');
    const roll = rnd();
    if (save && roll < 0.5) {
      await page.click('.potion-card:not(.off) .btn.good');
      rec.act = 'save';
    } else if (poisons.length && roll < 0.8) {
      await page.click('.potion-card:not(.off) .btn.bad');
      await sleep(150);
      const t = pick(poisons.map((p) => p.target));
      await clickSeat(page, t);
      await sleep(150);
      await page.waitForFunction(() => { const b = document.querySelector('#panel .poison-pick .btn.primary'); return b && !b.disabled; }, null, { timeout: 2000 });
      await page.click('#panel .poison-pick .btn.primary');
      rec.act = 'poison'; rec.target = t;
    } else {
      await page.click('#panel .pick-actions .btn.ghost');
      rec.act = 'pass';
    }
  } else if (r.kind === 'speak') {
    await answerSpeak(page, s, ctr, rec);
  } else throw new Error(`未知请求 ${r.kind}`);
  res.answered.push(rec);
  // 受理：请求应在几秒内消失
  await page.waitForFunction((id) => { const q = window.__ww.S.request; return !q || q.id !== id; }, r.id, { timeout: 8000 }).catch(() => { throw new Error(`${r.kind} 提交后请求没有关闭`); });
}
async function layoutAt2(page, key) {
  await sleep(250);
  const iss = await page.evaluate(LAYOUT);
  if (!res._seenL) res._seenL = new Set();
  if (res._seenL.has(key)) return;
  res._seenL.add(key);
  for (const i of iss) res.layout.push({ at: key, issue: i });
}

async function answerSpeak(page, s, ctr, rec) {
  const r = s.req, c = r.context || {};
  const n = ctr.speakN();
  const alive = Object.entries(s.alive).filter(([, v]) => v).map(([k]) => Number(k)).filter((k) => k !== s.you);
  const t = pick(alive.length ? alive : [1]);
  await layoutAt2(page, `speakme-${r.speech_kind}`);
  const mode = ['typed', 'phrase', 'role', 'pass', 'typed', 'phrase'][n % 6];
  rec.mode = mode; rec.confirm = !!c.confirm;
  const ta = page.locator('#bottom textarea');
  await ta.waitFor({ state: 'visible', timeout: 3000 });
  if (n === 0) { await shotComposer(page, s); }
  if (mode === 'pass') {
    await page.click('#bottom .input-side .btn.ghost');
    return;
  }
  if (mode === 'typed') {
    await ta.fill(`我觉得${t}号有点可疑，今天先听听大家怎么说。`);
  } else if (mode === 'phrase') {
    await page.click(`.phrases button:has-text("今天出 N 号")`);
    await page.click(`.seat-picker .pill:has-text("${t}号")`);
    await page.click(`.phrases button:has-text("我站 N 号")`);
    await page.click(`.seat-picker .pill:has-text("${t}号")`);
  } else {
    if (s.role === 'seer') {
      await page.click('.phrases button:has-text("我是预言家")');
      await page.click('.phrases button:has-text("金水 N 号")');
      await page.click(`.seat-picker .pill:has-text("${t}号")`);
    } else if (s.role === 'witch') {
      await page.click('.phrases button:has-text("我是女巫，救了 N 号")');
      await page.click(`.seat-picker .pill:has-text("${t}号")`);
    } else {
      await page.click(`.phrases button:has-text("N 号你解释一下")`);
      await page.click(`.seat-picker .pill:has-text("${t}号")`);
    }
  }
  const val = await ta.inputValue();
  if (!val.trim()) throw new Error('输入框是空的');
  if (c.confirm) {
    await page.click('#bottom .input-side .btn.primary'); // 预览
    await page.waitForSelector('#bottom .preview:not([hidden]) .pv-chips', { timeout: 8000 });
    await sleep(200);
    rec.preview_acts = await page.locator('#bottom .preview .chip.act').count();
    if (rec.preview_acts > 1 && rnd() < 0.5) { await page.click('#bottom .preview .chip.act .x'); rec.removed_one = true; }
    await shotPreview(page);
    await page.click('#bottom .preview .btn.primary');
  } else {
    await page.click('#bottom .input-side .btn.primary');
  }
}
let composerShot = false;
async function shotComposer(page, s) {
  void page; void s;
}
async function shotPreview(page) {
  if (composerShot) return;
  composerShot = true;
  if (OUT && SHOTS.has('composer')) { fs.mkdirSync(OUT, { recursive: true }); await page.screenshot({ path: path.join(OUT, 'composer.png') }); res.shots.push('composer'); }
  const iss = await page.evaluate(LAYOUT);
  for (const i of iss) res.layout.push({ at: 'composer-preview', issue: i });
}

main().catch((e) => { console.error('DRIVER FATAL', e); console.log(JSON.stringify({ fatal: String(e), ...res })); process.exit(1); });
