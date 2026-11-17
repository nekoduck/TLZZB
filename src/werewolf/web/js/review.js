// 终局复盘：时间线（含夜里的事件，AI 的想法挂在它引出的事件下面）、各席位、心情与怀疑。
import { h } from './dom.js';
import { avatarEl } from './widgets.js';
import { describeEvent, ROLE_ZH, MOOD_ZH, SOURCE_ZH, PHASE_ZH, NIGHT_PHASES } from './labels.js';

const roleChip = (role) => h('em', { class: `chip role ${role === 'werewolf' ? 'bad' : 'good'}` }, ROLE_ZH[role]);

export function renderReview(rv) {
  const seatInfo = Object.fromEntries(rv.seats.map((s) => [s.seat, s]));
  const nameOf = (seat) => (seatInfo[seat] ? `${seat}号 ${seatInfo[seat].name}` : `${seat}号`);
  const tabs = [['timeline', '时间线'], ['seats', '各席位'], ['mood', '心情与怀疑']];
  const pane = h('div', { class: 'rv-pane' });
  const bar = h('div', { class: 'tabs', role: 'tablist' });
  const show = (k) => {
    bar.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.k === k)));
    pane.replaceChildren(k === 'timeline' ? timeline(rv, seatInfo, nameOf) : k === 'seats' ? seatsTab(rv, seatInfo) : moodTab(rv));
  };
  for (const [k, t] of tabs) bar.append(h('button', { type: 'button', role: 'tab', class: 'tab', data: { k }, onClick: () => show(k) }, t));
  const st = rv.stats || {};
  const win = rv.winner === 'wolf' ? '狼人阵营获胜' : rv.winner === 'good' ? '好人阵营获胜' : '平局';
  const src = st.speech_sources && Object.keys(st.speech_sources).length
    ? '台词来源：' + Object.entries(st.speech_sources).map(([k, v]) => `${SOURCE_ZH[k] || k} ${v}`).join(' / ') : null;
  const head = h('div', { class: 'rv-head' }, h('b', null, win), h('span', null, `共 ${st.days || 0} 天`),
    st.seed != null ? h('span', null, `种子 ${st.seed}`) : null, st.spent_usd ? h('span', null, `花费 $${st.spent_usd.toFixed(2)}`) : null,
    src ? h('span', null, src) : null);
  show('timeline');
  return h('div', { class: 'review' }, head, bar, pane);
}

function thoughtEl(seatInfo, seat, t) {
  const si = seatInfo[seat];
  const differs = t.best && t.choice && t.best !== t.choice;
  const lies = t.lies || [];
  return h('div', { class: `thought k-${t.kind}` },
    h('div', { class: 'th-who' }, avatarEl(seat, 'xs'), h('b', null, `${seat}号 ${si ? si.name : ''}`), si ? roleChip(si.role) : null,
      h('span', { class: 'kind' }, { decision: '决定', speech: '发言', monologue: '独白', reflection: '反思' }[t.kind] || t.kind)),
    h('div', { class: 'th-text' }, t.text),
    h('div', { class: 'th-meta' }, t.choice ? h('span', null, `选了：${t.choice}`) : null, differs ? h('span', { class: 'warn' }, `规则头名：${t.best}`) : null,
      t.state ? h('span', null, `心境：${MOOD_ZH[t.state] || t.state}`) : null,
      lies.length ? h('span', { class: 'warn' }, `不是真心话：第 ${lies.map((i) => i + 1).join('、')} 条`) : null,
      t.source ? h('span', { class: 'src' }, SOURCE_ZH[t.source] || t.source) : null));
}

function timeline(rv, seatInfo, nameOf) {
  const byEvent = {}, loose = {}, lastOfGroup = {};
  for (const s of rv.seats) {
    for (const t of s.thoughts || []) {
      if (t.event_id != null) (byEvent[t.event_id] ||= []).push([s.seat, t]);
      else (loose[`${t.day}:${t.phase}`] ||= []).push([s.seat, t]);
    }
  }
  for (const ev of rv.events) lastOfGroup[`${ev.day}:${ev.phase}`] = ev.id;
  const root = h('div', { class: 'tl' });
  let head = '';
  const used = new Set();
  for (const ev of rv.events) {
    const hd = ev.day === 0 ? '开局' : `第 ${ev.day} ${NIGHT_PHASES.has(ev.phase) ? '夜' : '天'}`;
    if (hd !== head) { head = hd; root.append(h('h3', { class: 'tl-day' }, hd)); }
    const d = describeEvent(ev, nameOf);
    if (ev.type === 'phase') root.append(h('div', { class: 'tl-phase' }, PHASE_ZH[ev.data.phase] || ev.data.phase));
    else if (ev.type === 'speech') root.append(h('div', { class: 'tl-ev speech' }, h('b', null, nameOf(ev.actor)), h('span', { class: 'tl-sp' }, d.text)));
    else root.append(h('div', { class: `tl-ev ${d.cls}` }, d.text));
    for (const [seat, t] of byEvent[ev.id] || []) root.append(thoughtEl(seatInfo, seat, t));
    const key = `${ev.day}:${ev.phase}`;
    if (lastOfGroup[key] === ev.id && loose[key]) {
      used.add(key);
      for (const [seat, t] of loose[key]) root.append(thoughtEl(seatInfo, seat, t));
    }
  }
  for (const [key, list] of Object.entries(loose)) {
    if (!used.has(key)) for (const [seat, t] of list) root.append(thoughtEl(seatInfo, seat, t));
  }
  return root;
}

function seatsTab(rv, seatInfo) {
  return h('div', { class: 'rv-seats' }, rv.seats.map((s) => h('details', { class: 'rv-seat' },
    h('summary', null, avatarEl(s.seat, 'sm'), h('b', null, `${s.seat}号 ${s.name}`), roleChip(s.role),
      s.is_human ? h('em', { class: 'chip me-chip' }, '你') : null, h('span', { class: 'dim' }, s.alive ? '存活' : '出局')),
    s.strategy ? h('p', { class: 'strategy' }, s.strategy) : null,
    (s.thoughts || []).map((t) => thoughtEl(seatInfo, s.seat, t)))));
}

function spark(days) {
  const W = 220, H = 48;
  const pts = days.map((d, i) => [days.length === 1 ? W / 2 : 8 + (i * (W - 16)) / (days.length - 1), H - 6 - (d.mood / 100) * (H - 12)]);
  const svg = `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="心情曲线"><line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="currentColor" opacity=".2" stroke-dasharray="3 4"/>`
    + `<polyline points="${pts.map((p) => p.join(',')).join(' ')}" fill="none" stroke="#f2c14e" stroke-width="2"/>`
    + pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#f2c14e"/>`).join('') + '</svg>';
  return h('div', { html: svg });
}

function moodTab(rv) {
  const cards = rv.seats.filter((s) => (s.days || []).length).map((s) => {
    const last = s.days[s.days.length - 1];
    const top = (last.suspicion || []).slice().sort((a, b) => b.p - a.p).slice(0, 4);
    return h('div', { class: 'mood-card' },
      h('div', { class: 'mc-h' }, avatarEl(s.seat, 'xs'), h('b', null, `${s.seat}号 ${s.name}`), roleChip(s.role)),
      spark(s.days), h('div', { class: 'mc-states' }, s.days.map((d) => h('span', null, `第${d.day}天 ${MOOD_ZH[d.state] || d.state}`))),
      h('div', { class: 'susp' }, h('small', null, `第 ${last.day} 天结束时最怀疑`),
        top.map((t) => h('div', { class: 'susp-row' }, h('span', null, `${t.seat}号`), h('div', { class: 'bar' }, h('i', { style: { width: `${Math.round(t.p * 100)}%` } })), h('span', null, `${Math.round(t.p * 100)}%`)))));
  });
  return h('div', { class: 'rv-mood' }, cards.length ? cards : h('p', { class: 'dim pad' }, '没有心情记录。'));
}
