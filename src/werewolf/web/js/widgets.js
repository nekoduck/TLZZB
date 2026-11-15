import { h } from './dom.js';
import { avatarSVG } from './avatar.js';
import { S } from './state.js';

// 圆形头像；seat 不在名单里时画默认头像
export function avatarEl(seat, cls = '') {
  const p = S.players[seat];
  return h('div', { class: `avatar ${cls}`, html: avatarSVG(p && p.avatar) });
}
export function seatTag(seat, { name = true } = {}) {
  const p = S.players[seat];
  return h('span', { class: 'seat-tag' }, avatarEl(seat, 'xs'), h('b', null, `${seat}号`), name && p ? h('span', null, p.name) : null);
}
export function btn(label, onClick, cls = '', props = {}) {
  return h('button', { type: 'button', class: `btn ${cls}`, onClick, ...props }, label);
}
export function modal({ title, body, wide = false, onClose, actions }) {
  const root = document.getElementById('modal-root');
  const close = () => { back.remove(); document.removeEventListener('keydown', esc); if (onClose) onClose(); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  const x = h('button', { type: 'button', class: 'icon-btn modal-x', 'aria-label': '关闭', onClick: close }, '×');
  const box = h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'modal-head' }, h('h2', null, title), x), h('div', { class: 'modal-body' }, body), actions ? h('div', { class: 'modal-actions' }, actions) : null);
  const back = h('div', { class: 'modal-back', onClick: (e) => { if (e.target === back) close(); } }, box);
  root.append(back);
  document.addEventListener('keydown', esc);
  const first = box.querySelector('input,select,textarea,button:not(.modal-x)');
  (first || x).focus();
  return { close, box };
}
