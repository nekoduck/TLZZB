// 原创矢量美术：五张身份牌、舞台（夜与昼）、徽记。全部内联 SVG，不引用任何外部资源。
import { h } from './dom.js';
import { ROLE_ZH, ROLE_DESC, ROLE_TEAM } from './labels.js';

const GOLD = '#f2c14e';

// ---- 身份牌插画（viewBox 0 0 200 200）
const ART = {
  werewolf: `<defs><radialGradient id="wg" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="#7a1d2c"/><stop offset="1" stop-color="#25101f"/></radialGradient></defs>
    <rect width="200" height="200" fill="url(#wg)"/><circle cx="100" cy="78" r="58" fill="#f6d27a" opacity=".92"/><circle cx="116" cy="70" r="52" fill="#4a1426"/>
    <path d="M44 150 L52 70 L78 98 L100 84 L122 98 L148 70 L156 150 C146 176 124 186 100 186 C76 186 54 176 44 150 Z" fill="#14091a" stroke="${GOLD}" stroke-width="2"/>
    <path d="M70 118 L88 126 L84 134 Z M130 118 L112 126 L116 134 Z" fill="${GOLD}"/><path d="M100 128 L92 148 L100 156 L108 148 Z" fill="#2a1030"/>
    <path d="M82 158 L88 170 L94 158 M106 158 L112 170 L118 158" fill="#f3e9d2"/><path d="M60 80 L74 100 M140 80 L126 100" stroke="#3a1a40" stroke-width="3"/>`,
  villager: `<defs><linearGradient id="vg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f3b06a"/><stop offset="1" stop-color="#4a3f6b"/></linearGradient></defs>
    <rect width="200" height="200" fill="url(#vg)"/><circle cx="150" cy="52" r="20" fill="#ffe7a8"/><path d="M0 150 C50 130 150 130 200 150 V200 H0 Z" fill="#2c3a2e"/>
    <path d="M52 138 V96 L100 62 L148 96 V138 Z" fill="#6b4a32" stroke="#2a1b14" stroke-width="2"/><path d="M42 98 L100 54 L158 98 L148 100 L100 66 L52 100 Z" fill="#a8412f" stroke="#2a1b14" stroke-width="2"/>
    <rect x="90" y="108" width="20" height="30" fill="#2a1b14"/><rect x="64" y="106" width="16" height="14" fill="${GOLD}"/><rect x="120" y="106" width="16" height="14" fill="${GOLD}"/>
    <rect x="124" y="54" width="10" height="22" fill="#4a3025"/>
    <g stroke="#e8c15a" stroke-width="3" fill="none" stroke-linecap="round"><path d="M22 186 C24 160 26 150 28 134"/><path d="M34 186 C34 166 34 154 36 138"/><path d="M174 186 C172 160 170 150 168 134"/><path d="M164 186 C164 166 166 154 164 138"/></g>
    <g fill="#e8c15a"><ellipse cx="28" cy="132" rx="3" ry="8"/><ellipse cx="36" cy="136" rx="3" ry="8"/><ellipse cx="168" cy="132" rx="3" ry="8"/><ellipse cx="164" cy="136" rx="3" ry="8"/></g>`,
  seer: `<defs><radialGradient id="sg" cx="50%" cy="45%" r="65%"><stop offset="0" stop-color="#2c4a8a"/><stop offset="1" stop-color="#0f1236"/></radialGradient><radialGradient id="sb" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#d9fff9"/><stop offset=".5" stop-color="#4fd1c5"/><stop offset="1" stop-color="#1d4a78"/></radialGradient></defs>
    <rect width="200" height="200" fill="url(#sg)"/>
    <g fill="#fff" opacity=".85"><circle cx="30" cy="30" r="1.6"/><circle cx="170" cy="24" r="2"/><circle cx="150" cy="60" r="1.4"/><circle cx="44" cy="70" r="1.4"/><circle cx="100" cy="18" r="1.8"/><circle cx="178" cy="100" r="1.4"/><circle cx="20" cy="110" r="1.6"/></g>
    <path d="M60 186 L72 156 H128 L140 186 Z" fill="#3a2a5a" stroke="${GOLD}" stroke-width="2"/><rect x="66" y="150" width="68" height="10" rx="3" fill="#4a3a72" stroke="${GOLD}" stroke-width="1.6"/>
    <circle cx="100" cy="98" r="52" fill="url(#sb)" stroke="${GOLD}" stroke-width="2.4"/><circle cx="82" cy="76" r="10" fill="#fff" opacity=".4"/>
    <path d="M62 100 C78 78 122 78 138 100 C122 122 78 122 62 100 Z" fill="#0f1236" stroke="${GOLD}" stroke-width="2"/><circle cx="100" cy="100" r="13" fill="${GOLD}"/><circle cx="100" cy="100" r="6" fill="#0f1236"/><circle cx="103" cy="97" r="2" fill="#fff"/>`,
  witch: `<defs><linearGradient id="wt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b1a5e"/><stop offset="1" stop-color="#101a2e"/></linearGradient></defs>
    <rect width="200" height="200" fill="url(#wt)"/><circle cx="46" cy="44" r="22" fill="#e9e1ff" opacity=".85"/><circle cx="56" cy="40" r="19" fill="#3b1a5e"/>
    <path d="M52 102 L98 18 L146 102 Z" fill="#241038" stroke="${GOLD}" stroke-width="2"/><path d="M98 18 C112 8 126 16 124 28" fill="none" stroke="#241038" stroke-width="7" stroke-linecap="round"/>
    <path d="M60 88 H138 L142 100 H56 Z" fill="#6a2a8a"/><rect x="92" y="87" width="14" height="14" fill="${GOLD}"/><rect x="96" y="91" width="6" height="6" fill="#241038"/>
    <ellipse cx="100" cy="106" rx="62" ry="12" fill="#241038" stroke="${GOLD}" stroke-width="2"/>
    <path d="M42 150 h22 l-4 -18 h-14 z M52 120 h2 v12 h-2 z" fill="#5be58a" stroke="#d8ffe6" stroke-width="1.4"/><path d="M122 154 C122 138 134 134 140 124 H152 C158 134 170 138 170 154 C170 172 122 172 122 154 Z" fill="#b254e8" stroke="#f1dcff" stroke-width="1.4"/><rect x="138" y="116" width="16" height="10" fill="#f3e9d2"/>
    <circle cx="146" cy="150" r="9" fill="#fff" opacity=".22"/><circle cx="52" cy="142" r="6" fill="#fff" opacity=".25"/>`,
  hunter: `<defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#27564a"/><stop offset="1" stop-color="#0d1f1c"/></linearGradient></defs>
    <rect width="200" height="200" fill="url(#hg)"/><circle cx="150" cy="44" r="18" fill="#f1ebc9" opacity=".9"/>
    <g fill="#0d2a24"><path d="M10 200 L34 120 L58 200 Z"/><path d="M30 200 L58 100 L86 200 Z"/><path d="M150 200 L172 110 L196 200 Z"/><path d="M120 200 L146 128 L172 200 Z"/></g>
    <path d="M62 36 C128 62 128 146 62 172" fill="none" stroke="#8a5a2e" stroke-width="7" stroke-linecap="round"/><path d="M62 36 C128 62 128 146 62 172" fill="none" stroke="${GOLD}" stroke-width="1.6" stroke-dasharray="4 8"/>
    <path d="M62 36 L62 172" stroke="#f3e9d2" stroke-width="1.8"/>
    <path d="M34 104 H150" stroke="#d8c7a0" stroke-width="4" stroke-linecap="round"/><path d="M150 104 L134 94 V114 Z" fill="${GOLD}"/><path d="M34 104 L24 96 M34 104 L24 112 M42 104 L32 96 M42 104 L32 112" stroke="#c8323c" stroke-width="3" stroke-linecap="round"/>`,
};

export function roleArt(role) {
  return `<svg viewBox="0 0 200 200" aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid slice">${ART[role] || ART.villager}</svg>`;
}

// 翻牌：正面是身份，背面是月亮纹样
export function roleCard(role, { flip = true, size = '' } = {}) {
  const front = h('div', { class: `rc-face rc-front role-${role}` },
    h('div', { class: 'rc-art', html: roleArt(role) }),
    h('div', { class: 'rc-name' }, ROLE_ZH[role] || '?'),
    h('div', { class: 'rc-team' }, ROLE_TEAM[role] || ''),
    h('div', { class: 'rc-desc' }, ROLE_DESC[role] || ''));
  const back = h('div', { class: 'rc-face rc-back', html: backSVG() });
  return h('div', { class: `rolecard ${size} ${flip ? 'is-back' : ''}`, data: { role } }, h('div', { class: 'rc-inner' }, front, back));
}
function backSVG() {
  return `<svg viewBox="0 0 120 170" aria-hidden="true"><rect x="6" y="6" width="108" height="158" rx="10" fill="#1a1440" stroke="${GOLD}" stroke-width="2"/>
    <rect x="14" y="14" width="92" height="142" rx="6" fill="none" stroke="${GOLD}" stroke-width=".8" opacity=".6"/>
    <circle cx="60" cy="85" r="30" fill="${GOLD}" opacity=".92"/><circle cx="72" cy="79" r="27" fill="#1a1440"/>
    <g fill="${GOLD}"><circle cx="30" cy="34" r="2"/><circle cx="92" cy="30" r="1.6"/><circle cx="26" cy="140" r="1.6"/><circle cx="94" cy="138" r="2"/></g>
    <path d="M60 24 L63 32 L60 40 L57 32 Z M60 130 L63 138 L60 146 L57 138 Z" fill="${GOLD}" opacity=".8"/></svg>`;
}

// ---- 舞台：夜/昼两层，月亮与太阳，星点，云，村庄与树林剪影
function rng(seed) { let s = seed; return () => { s = (s * 16807) % 2147483647; return s / 2147483647; }; }

function skyline() {
  const r = rng(77);
  const W = 1600, H = 420;
  let far = `M0 ${H} L0 250`;
  for (let x = 0; x <= W; x += 80) far += ` L${x} ${230 + Math.sin(x / 170) * 30 + r() * 24}`;
  far += ` L${W} ${H} Z`;
  let trees = '';
  for (let x = -20; x < W + 20; x += 28 + r() * 26) {
    const th = 70 + r() * 90, tw = 26 + r() * 20, base = H - 6 - r() * 26;
    trees += `M${x} ${base} L${x + tw / 2} ${base - th} L${x + tw} ${base} Z `;
  }
  let houses = '', wins = '';
  const xs = [150, 330, 520, 700, 900, 1080, 1260, 1450];
  xs.forEach((x, i) => {
    const w = 70 + r() * 40, hh = 46 + r() * 30, base = H - 14;
    houses += `M${x} ${base} V${base - hh} L${x + w / 2} ${base - hh - 34} L${x + w} ${base - hh} V${base} Z `;
    if (i % 3 === 0) houses += `M${x + w * 0.7} ${base - hh - 18} v-26 h10 v30 Z `;
    wins += `<rect x="${x + w * 0.2}" y="${base - hh * 0.7}" width="12" height="14" rx="1"/>`;
    if (i % 2 === 0) wins += `<rect x="${x + w * 0.6}" y="${base - hh * 0.7}" width="12" height="14" rx="1"/>`;
  });
  return `<svg class="skyline" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
    <path class="sil-far" d="${far}"/><path class="sil-trees" d="${trees}"/><path class="sil-houses" d="${houses}"/><g class="sil-wins">${wins}</g>
    <rect class="sil-ground" x="0" y="${H - 18}" width="${W}" height="18"/></svg>`;
}

export function buildStage(root) {
  const r = rng(2024);
  const stars = Array.from({ length: 80 }, () => {
    const s = 1 + r() * 2.2;
    return `<i style="left:${(r() * 100).toFixed(1)}%;top:${(r() * 62).toFixed(1)}%;width:${s.toFixed(1)}px;height:${s.toFixed(1)}px;animation-delay:${(r() * 5).toFixed(1)}s;animation-duration:${(2.5 + r() * 4).toFixed(1)}s"></i>`;
  }).join('');
  root.innerHTML = `<div class="sky sky-night"></div><div class="sky sky-day"></div>
    <div class="stars">${stars}</div>
    <div class="orb moon"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="40" fill="#f6ecc8"/><circle cx="36" cy="40" r="7" fill="#e3d6a6" opacity=".7"/><circle cx="60" cy="58" r="10" fill="#e3d6a6" opacity=".6"/><circle cx="50" cy="28" r="4" fill="#e3d6a6" opacity=".6"/><circle cx="64" cy="34" r="3" fill="#e3d6a6" opacity=".5"/></svg></div>
    <div class="orb sun"></div>
    <div class="clouds"><i></i><i></i><i></i></div>${skyline()}<div class="vignette"></div>`;
}

// 徽记：金色月牙（顶栏标志）
export const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M20 4 A12 12 0 1 0 28 22 A10 10 0 1 1 20 4 Z" fill="${GOLD}"/><circle cx="25" cy="8" r="1.4" fill="${GOLD}"/></svg>`;
export const ICON = {
  sun: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="5" fill="currentColor"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></g></svg>',
  moon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 3a9 9 0 1 0 6 15 7.5 7.5 0 0 1-6-15z" fill="currentColor"/></svg>',
  book: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5c3-1.5 6-1.5 8 0v14c-2-1.5-5-1.5-8 0zM20 5c-3-1.5-6-1.5-8 0v14c2-1.5 5-1.5 8 0z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  log: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>',
  gear: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  eye: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12c3-5 7-7 10-7s7 2 10 7c-3 5-7 7-10 7S5 17 2 12z" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>',
  skull: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3C7 3 4 6.5 4 11c0 3 1.5 4.5 3 5.5V20h10v-3.5c1.5-1 3-2.5 3-5.5 0-4.500-3-8-8-8z" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="9" cy="11" r="1.8" fill="currentColor"/><circle cx="15" cy="11" r="1.8" fill="currentColor"/></svg>',
};
// 药瓶（女巫面板）
export function potion(kind, used) {
  const c = kind === 'antidote' ? ['#5be58a', '#d8ffe6'] : ['#b254e8', '#f1dcff'];
  return `<svg viewBox="0 0 60 80" aria-hidden="true" class="potion ${used ? 'used' : ''}"><rect x="23" y="4" width="14" height="10" rx="2" fill="#f3e9d2"/><path d="M24 14h12v12C48 32 54 42 54 54c0 14-10 22-24 22S6 68 6 54c0-12 6-22 18-28z" fill="${c[0]}" stroke="${c[1]}" stroke-width="2"/><ellipse cx="22" cy="48" rx="5" ry="9" fill="#fff" opacity=".3"/><path d="M12 56c6 3 12-3 18 0s12 3 18 0v8c0 6-8 10-18 10s-18-4-18-10z" fill="#000" opacity=".15"/></svg>`;
}
