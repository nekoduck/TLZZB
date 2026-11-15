// 发言记录抽屉：按天分组，全部发言与公开事件；真人自己的发言下面带系统理解到的言语行为。
import { h, fill } from './dom.js';
import { S, nameOf } from './state.js';
import { avatarEl } from './widgets.js';
import { describeEvent, actLabel, SPEECH_KIND_ZH, NIGHT_PHASES } from './labels.js';

let lastSig = '';
const SHOW = new Set(['speech', 'dawn', 'vote_result', 'exile', 'hunter_shot', 'role_revealed', 'game_over', 'seer_result', 'wolf_kill', 'wolf_propose', 'witch_notice', 'witch_save', 'witch_poison', 'hunter_blocked']);

export function toggleLog(force) {
  S.logOpen = force != null ? force : !S.logOpen;
  lastSig = '';
  updateLog();
  document.getElementById('app').classList.toggle('log-open', S.logOpen);
}

const head = (ev) => (ev.day === 0 ? '开局' : `第 ${ev.day} ${NIGHT_PHASES.has(ev.phase) ? '夜' : '天'}`);

export function logEntries(events) {
  const out = [];
  let group = null, grp = null;
  for (const ev of events) {
    if (!SHOW.has(ev.type)) continue;
    const hd = head(ev);
    if (hd !== group) { group = hd; grp = { head: hd, items: [] }; out.push(grp); }
    grp.items.push(ev);
  }
  return out;
}

export function updateLog() {
  const box = document.getElementById('logdrawer');
  box.classList.toggle('open', S.logOpen);
  box.setAttribute('aria-hidden', String(!S.logOpen));
  if (!S.logOpen) return;
  const sig = `${S.events.length}:${Object.keys(S.parsedBy).length}:${S.godEvents.length}`;
  if (sig === lastSig) return;
  lastSig = sig;
  const old = box.querySelector('.log-body');
  const stick = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 60; // 只有本来就在底部才跟到最新，否则不打断翻看
  const keep = old ? old.scrollTop : 0;
  const body = h('div', { class: 'log-body' });
  for (const g of logEntries(S.events)) {
    body.append(h('h3', { class: 'log-day' }, g.head));
    for (const ev of g.items) body.append(entry(ev));
  }
  if (S.godEvents.length) {
    body.append(h('h3', { class: 'log-day god' }, '上帝视角补充'));
    for (const ev of S.godEvents) {
      const d = describeEvent(ev, nameOf);
      body.append(h('div', { class: `log-ev ${d.cls}` }, h('span', { class: 'lg-d' }, head(ev)), d.text));
    }
  }
  if (!body.childNodes.length) body.append(h('p', { class: 'dim pad' }, '还没有发言。'));
  fill(box, h('div', { class: 'log-head' }, h('h2', null, '发言记录'),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': '收起', onClick: () => toggleLog(false) }, '×')), body);
  body.scrollTop = stick ? body.scrollHeight : keep;
}

function entry(ev) {
  if (ev.type === 'speech') {
    const sp = ev.data.speech || {};
    const own = ev.actor === S.you;
    const pr = S.parsedBy[ev.seq];
    return h('div', { class: `log-sp ${own ? 'own' : ''}` }, avatarEl(ev.actor, 'xs'),
      h('div', { class: 'lg-main' },
        h('div', { class: 'lg-who' }, h('b', null, nameOf(ev.actor)), ev.data.kind !== 'day' ? h('em', null, SPEECH_KIND_ZH[ev.data.kind]) : null),
        h('div', { class: 'lg-text' }, sp.text),
        own && pr ? h('div', { class: 'lg-parsed' }, (pr.acts || []).map((a) => h('span', { class: 'chip act' }, actLabel(a))),
          (pr.ignored || []).map((t) => h('s', null, t)), pr.notice ? h('small', null, pr.notice) : null) : null));
  }
  const d = describeEvent(ev, nameOf);
  return h('div', { class: `log-ev ${d.cls}` }, d.cls === 'secret' ? h('span', { class: 'lg-d' }, '密') : null, d.text);
}
