// 入口：解析地址参数，搭好舞台与顶栏，订阅状态刷新，启动演示或直播。
import { h, $, fill } from './dom.js';
import { S, onRefresh, refreshNow, refresh } from './state.js';
import { buildStage } from './art.js';
import { buildTopbar, updateTopbar } from './topbar.js';
import { buildSeats, updateSeats } from './seats.js';
import { updateCenter } from './center.js';
import { updateBottom } from './bottom.js';
import { updateLog, toggleLog } from './log.js';
import { startLive, startDemo } from './transport.js';

let seatsFor = null, bannerK = null, bannerTimer = 0;

function render() {
  if (S.game !== seatsFor) { seatsFor = S.game; buildSeats(); }
  updateTopbar(); updateCenter(); updateSeats(); updateBottom(); updateLog(); // 座位在中央之后：选目标面板在 updateCenter 里才建好 S.pick
  const b = S.banner;
  if (b && b.k !== bannerK) {
    bannerK = b.k;
    const el = $('#banner');
    fill(el, h('div', { class: 'banner-in' }, h('b', null, b.text), h('span', null, b.sub)));
    el.classList.remove('show'); void el.offsetWidth; el.classList.add('show');
    clearTimeout(bannerTimer); bannerTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }
}

function renderToasts() {
  const root = $('#toasts');
  while (S.toastq.length) {
    const t = S.toastq.shift();
    const n = h('div', { class: `toast ${t.kind}`, role: 'status' }, t.text);
    root.append(n);
    setTimeout(() => n.remove(), 4200);
  }
}

function boot() {
  buildStage($('#stage'));
  buildTopbar();
  onRefresh((what) => (what === 'toast' ? renderToasts() : render()));
  const q = new URLSearchParams(location.search);
  const demo = q.get('demo');
  refreshNow();
  if (demo) startDemo(demo, { auto: q.get('auto') === '1', t: q.has('t') ? Number(q.get('t')) : null });
  else startLive();
  // Esc 收起发言记录抽屉（对话框打开时 Esc 归对话框）
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.logOpen && !document.querySelector('#modal-root .modal')) toggleLog(false); });
  window.__ww = { S, refresh };
}
boot();
