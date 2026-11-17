// 顶栏：第几天、昼夜、环节、速度、规则、发言记录、上帝视角、设置。画面由 phase 事件驱动。
import { h, fill } from './dom.js';
import { S, refresh } from './state.js';
import { net } from './net.js';
import { PHASE_ZH, SPEED_ZH, SPEEDS } from './labels.js';
import { ICON, LOGO } from './art.js';
import { openRules, openSettings } from './dialogs.js';
import { toggleLog } from './log.js';

let nodes = null;

export function buildTopbar() {
  const bar = document.getElementById('topbar');
  const day = h('span', { class: 'chip-day' });
  const time = h('span', { class: 'time-ico' });
  const timeTxt = h('span', null);
  const phase = h('span', { class: 'phase-label' });
  const speed = h('button', { type: 'button', class: 'tb-btn', title: '切换速度', onClick: () => cycleSpeed() });
  const god = h('button', { type: 'button', class: 'tb-btn god', hidden: true, onClick: () => net.send({ t: 'set', god_view: !S.god }) });
  const conn = h('span', { class: 'conn', title: '连接状态' });
  const logBtn = h('button', { type: 'button', class: 'tb-btn', 'aria-label': '发言记录', 'aria-expanded': 'false', onClick: () => toggleLog() }, h('span', { class: 'ico', html: ICON.log }), h('span', { class: 'tb-t' }, '记录'));
  fill(bar, 
    h('div', { class: 'brand' }, h('span', { class: 'logo', html: LOGO }), h('b', null, '狼人杀')),
    h('div', { class: 'status' }, day, h('span', { class: 'sep' }), time, timeTxt, h('span', { class: 'sep' }), phase),
    h('div', { class: 'tools' }, conn, god, speed,
      h('button', { type: 'button', class: 'tb-btn', 'aria-label': '规则', onClick: () => openRules() }, h('span', { class: 'ico', html: ICON.book }), h('span', { class: 'tb-t' }, '规则')),
      logBtn,
      h('button', { type: 'button', class: 'tb-btn', 'aria-label': '设置', onClick: () => openSettings() }, h('span', { class: 'ico', html: ICON.gear }), h('span', { class: 'tb-t' }, '设置'))));
  nodes = { day, time, timeTxt, phase, speed, god, conn, logBtn };
}

export function cycleSpeed() { setSpeed(SPEEDS[(SPEEDS.indexOf(S.speed) + 1) % SPEEDS.length]); }
export function setSpeed(v) {
  net.send({ t: 'set', speed: v });
  if (net.mode === 'demo') { S.speed = v; refresh(); }
}

export function updateTopbar() {
  if (!nodes) return;
  const n = nodes;
  const night = S.time === 'night';
  n.day.textContent = S.day ? `第 ${S.day} ${night ? '夜' : '天'}` : '开局';
  n.time.innerHTML = night ? ICON.moon : ICON.sun;
  n.timeTxt.textContent = night ? '黑夜' : '白天';
  n.phase.textContent = PHASE_ZH[S.phase] || '';
  n.speed.textContent = `速度 ${SPEED_ZH[S.speed] || ''}`;
  n.god.hidden = !S.canGod && !S.god;
  n.god.textContent = S.god ? '退出上帝视角' : '上帝视角';
  n.conn.className = `conn ${S.conn}`;
  n.conn.title = { open: '已连接', lost: '连接中断，正在重连…', connecting: '连接中…', demo: '演示模式', idle: '' }[S.conn] || '';
  n.logBtn.setAttribute('aria-expanded', String(S.logOpen));
  document.body.dataset.conn = S.conn;
  document.body.dataset.time = S.time;
  document.body.dataset.phase = S.phase;
  document.body.dataset.req = S.request ? S.request.kind : '';
}
