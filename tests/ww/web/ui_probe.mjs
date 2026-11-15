// 演示页上的界面体检（由 test_ui_behavior.py 调用）：气泡不压中央面板、Esc 只关最上层对话框并还原焦点、Esc 收起记录抽屉、重放的 god 不提示。
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [base, w, h] = [process.argv[2], Number(process.argv[3]), Number(process.argv[4])];
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: w, height: h } })).newPage();
await p.goto(`${base}/index.html?demo=villager&t=40`);
await p.waitForSelector('.seat');
await p.waitForTimeout(800);
const out = await p.evaluate(async () => {
  const { S, refresh } = window.__ww;
  for (const s of [1, 2, 3, 4, 6, 7, 8, 9]) S.reactions[s] = { label: '冷笑', quip: '这话你自己信吗', k: Math.random() };
  refresh();
  await new Promise((r) => setTimeout(r, 450));
  const c = document.getElementById('center').getBoundingClientRect();
  return { bubbleOverlap: [...document.querySelectorAll('.bubble')].map((e) => {
    const r = e.getBoundingClientRect();
    return Math.max(0, Math.min(r.right, c.right) - Math.max(r.left, c.left)) * Math.max(0, Math.min(r.bottom, c.bottom) - Math.max(r.top, c.top));
  }) };
});
const modals = () => p.locator('#modal-root .modal').count();
await p.click('#topbar [aria-label="规则"]');
await p.evaluate(() => document.querySelector('#topbar [aria-label="设置"]').click()); // 再叠一层（遮罩会挡住真点击）
out.stacked = await modals();
await p.keyboard.press('Escape');
out.afterOneEsc = await modals();
await p.keyboard.press('Escape');
out.afterTwoEsc = await modals();
out.focusBack = await p.evaluate(() => document.activeElement && document.activeElement.getAttribute('aria-label'));
await p.click('#topbar [aria-label="发言记录"]');
out.logOpen = await p.evaluate(() => document.getElementById('logdrawer').classList.contains('open'));
await p.keyboard.press('Escape');
out.logClosed = await p.evaluate(() => !document.getElementById('logdrawer').classList.contains('open'));
out.godToasts = await p.evaluate(() => { // 重放的 god 静默还原（不提示、不动画），现场的 god 提示一次
  const { S, handle } = window.__ww;
  const god = { t: 'god', roles: [{ seat: 3, role: 'werewolf' }], events: [] };
  const n = () => [...document.querySelectorAll('#toasts .toast')].filter((e) => e.textContent.includes('上帝视角')).length;
  handle({ ...god, replay: true });
  const afterReplay = n();
  const wolvesAfterReplay = S.wolves.join(',');
  handle(god);
  return [afterReplay, n(), wolvesAfterReplay];
});
await b.close();
console.log(JSON.stringify(out));
