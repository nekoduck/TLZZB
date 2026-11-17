// 座位：左 1-5、右 6-9，九张卡。头像与名字只建一次，状态按签名增量更新。
import { h } from './dom.js';
import { avatarSVG } from './avatar.js';
import { S, refresh, saveMarks } from './state.js';
import { ROLE_ZH, MARKS, claimShort, resZh } from './labels.js';

const cards = {};

export function buildSeats() {
  const L = document.getElementById('seats-l'), R = document.getElementById('seats-r');
  L.replaceChildren(); R.replaceChildren();
  for (const k of Object.keys(cards)) delete cards[k];
  const seats = Object.keys(S.players).map(Number).sort((a, b) => a - b);
  for (const seat of seats) {
    const p = S.players[seat];
    const mark = h('button', { class: 'mark', type: 'button', 'aria-label': `给${seat}号打标记`, title: '点击切换我的标记（狼/好/神/?）',
      onClick: (e) => { e.stopPropagation(); S.marks[seat] = ((S.marks[seat] || 0) + 1) % MARKS.length; saveMarks(); refresh(); } });
    const dyn = h('div', { class: 'dyn' });
    const tip = h('div', { class: 'tip', role: 'tooltip' }, h('b', null, `${p.name}`), p.title ? h('span', null, p.title) : null,
      p.tags && p.tags.length ? h('span', { class: 'tags' }, p.tags.map((t) => h('em', null, t))) : null);
    const el = h('div', { class: 'seat', data: { seat } },
      h('div', { class: 'av', html: avatarSVG(p.avatar) }), h('span', { class: 'num' }, seat),
      h('div', { class: 'nm' }, p.name), mark, dyn, tip,
      h('div', { class: 'stamp' }, '出局'));
    el.addEventListener('click', () => onPick(seat));
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && el.classList.contains('selectable')) { e.preventDefault(); onPick(seat); } });
    cards[seat] = { el, dyn, mark, sig: '' };
    (seat <= 5 ? L : R).append(el);
  }
}

function onPick(seat) {
  const pk = S.pick;
  if (!pk || !pk.seats.has(seat)) return;
  pk.sel = pk.sel === seat ? null : seat;
  refresh();
}

function roleBadge(seat) {
  const r = S.roles[seat];
  if (!r) return null;
  const mine = seat === S.you;
  if (S.over || S.god || mine || r === 'hunter' || (S.role === 'werewolf' && r === 'werewolf')) return r;
  return null;
}

export function updateSeats() {
  const sp = S.speaker;
  const votes = S.votes || {};
  const counts = {};
  for (const t of Object.values(votes)) if (t) counts[t] = (counts[t] || 0) + 1;
  const hasVotes = Object.keys(votes).length > 0;
  for (const [k, c] of Object.entries(cards)) {
    const seat = Number(k);
    const alive = S.alive[seat] !== false;
    const myTurn = !!(S.request && S.request.kind === 'speak');
    const speaking = myTurn ? seat === S.you : !!(sp && sp.seat === seat && !sp.done);
    const pk = S.pick;
    const selectable = !!(pk && pk.seats.has(seat));
    const selected = selectable && pk.sel === seat;
    const rb = roleBadge(seat);
    const claim = S.claims[seat] && S.claims[seat].role;
    const sr = S.seerRes[seat], co = S.checkOn[seat];
    const re = S.reactions[seat];
    const mk = S.marks[seat] || 0;
    const th = S.thinking[seat];
    const sig = JSON.stringify([alive, speaking, selectable, selected, !!pk && !selectable, rb, claim, sr && sr.result, co && co.by + co.result, re && re.k, mk, th,
      votes[seat], counts[seat], hasVotes, seat === S.you, S.wolves.includes(seat) && S.role === 'werewolf', S.over, S.god]);
    if (sig === c.sig) continue;
    c.sig = sig;
    const el = c.el;
    el.classList.toggle('dead', !alive);
    el.classList.toggle('speaking', speaking);
    el.classList.toggle('thinking', !!th);
    el.classList.toggle('me', seat === S.you);
    el.classList.toggle('selectable', selectable);
    el.classList.toggle('selected', selected);
    el.classList.toggle('disabled', !!pk && !selectable);
    if (selectable) { el.setAttribute('role', 'button'); el.tabIndex = 0; el.setAttribute('aria-pressed', selected ? 'true' : 'false'); }
    else { el.removeAttribute('role'); el.removeAttribute('tabindex'); el.removeAttribute('aria-pressed'); }
    c.mark.textContent = MARKS[mk] || '＋';
    c.mark.className = 'mark' + (mk ? ` m${mk}` : '');
    const kids = [];
    if (seat === S.you) kids.push(h('span', { class: 'chip me-chip' }, '我'));
    if (rb) kids.push(h('span', { class: `chip role ${rb === 'werewolf' ? 'bad' : 'good'}` }, ROLE_ZH[rb]));
    if (sr) kids.push(h('span', { class: `chip ${sr.result === 'wolf' ? 'bad' : 'gold'}`, title: '我的查验' }, sr.result === 'wolf' ? '查杀' : '金水'));
    if (claim) kids.push(h('span', { class: 'chip claim' }, claimShort(claim)));
    if (co) kids.push(h('span', { class: `chip claim ${co.result === 'wolf' ? 'bad' : 'gold'}`, title: `${co.by}号报：${resZh(co.result)}` }, `${co.by}报${co.result === 'wolf' ? '杀' : '水'}`));
    const badges = h('div', { class: 'badges' }, kids);
    const parts = [badges];
    if (th) parts.push(h('div', { class: 'think-dots', 'aria-label': '思考中' }, h('i'), h('i'), h('i')));
    if (hasVotes && seat in votes) parts.push(h('div', { class: 'vote-arrow' }, votes[seat] ? `→${votes[seat]}` : '弃'));
    if (counts[seat]) parts.push(h('div', { class: 'vote-count', 'aria-label': `${counts[seat]}票` }, counts[seat]));
    if (re) parts.push(h('div', { class: 'bubble' }, h('b', null, re.label), re.quip ? h('span', null, re.quip) : null));
    c.dyn.replaceChildren(...parts);
  }
}
