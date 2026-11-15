// 对话框：规则、开新局、设置、复盘。
import { h, store } from './dom.js';
import { S, toast, refresh } from './state.js';
import { net } from './net.js';
import { modal, btn } from './widgets.js';
import { ROLE_ZH, SELF_SAVE_ZH, SPEED_ZH, SPEEDS } from './labels.js';
import { renderReview } from './review.js';

export const NAME_RE = /^[一-鿿A-Za-z]{1,8}$/;

export function openRules() {
  const r = S.rules;
  const rows = r ? [
    ['板子', r.board],
    ['身份', Object.entries(r.role_counts).map(([k, v]) => `${ROLE_ZH[k] || k} ${v}`).join('、')],
    ['女巫自救', SELF_SAVE_ZH[r.witch_self_save] || r.witch_self_save],
    ['遗言', r.last_words_nights > 0 ? `前 ${r.last_words_nights} 夜的夜间死者有遗言；被放逐的人总有遗言` : '被放逐的人有遗言'],
    ['弃票', r.allow_abstain ? '允许' : '不允许'],
    ['空刀', r.allow_empty_kill ? '狼人可以空刀' : '狼人必须刀人'],
    ['天数上限', `第 ${r.max_days} 天白天结束仍未分胜负则平局`],
  ] : [];
  const body = h('div', { class: 'rules' },
    r ? h('dl', null, rows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])) : h('p', { class: 'dim' }, '开局后显示本局规则。'),
    h('h3', null, '玩法要点'),
    h('ul', null,
      h('li', null, '天黑：狼人刀人，预言家验人，女巫用药。天亮公布死者，不说死因。'),
      h('li', null, '白天：从死者的下一位起轮流发言，然后全体同时投票，最高票出局；平票的人上台 PK 后再投一次。'),
      h('li', null, '胜负（屠边）：狼人全部出局则好人胜；平民或神职全部出局则狼人胜。'),
      h('li', null, '猎人被刀或被放逐可以开枪，被毒不能。'),
      h('li', null, '座位上的「狼 / 好 / 神 / ?」是你自己的记号，只存在你的浏览器里。')));
  modal({ title: '规则说明', body });
}

export function openSettings() {
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': '速度' }, SPEEDS.map((s) => h('button', { type: 'button', class: 'pill', 'aria-pressed': String(S.speed === s), onClick: (e) => {
    net.send({ t: 'set', speed: s });
    if (net.mode === 'demo') { S.speed = s; refresh(); }
    seg.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === e.currentTarget)));
  } }, SPEED_ZH[s])));
  const llm = S.hello && S.hello.llm;
  const body = h('div', { class: 'settings' },
    h('label', null, '演出速度'), seg, h('p', { class: 'dim' }, '快：停顿减半；瞬：不等动画，也不打字。'),
    llm ? h('p', { class: 'dim' }, `台词来源：${{ claude: 'Claude', jev_select: 'Jev 选句', template: '模板' }[llm.speech] || llm.speech}${llm.offline ? '（离线模式）' : ''}`) : null,
    S.cost ? h('p', { class: 'dim' }, `本局花费约 $${S.cost.spent_usd.toFixed(2)}`) : null,
    h('div', { class: 'row' }, btn('开新局', () => { m.close(); openNewGame(); }, 'primary'),
      S.mode === 'demo' ? null : btn('重新连接并续上', () => { m.close(); net.send({ t: 'resume' }); }, 'ghost')));
  const m = modal({ title: '设置', body });
}

export function openNewGame({ first = false } = {}) {
  if (S.mode === 'demo') { toast('演示模式下不能开新局'); return null; }
  const name = h('input', { type: 'text', maxlength: 8, value: S.playerName || '你', 'aria-label': '你的名字', autocomplete: 'off' });
  const err = h('div', { class: 'field-err', role: 'alert' });
  const role = h('select', { 'aria-label': '身份偏好' }, h('option', { value: 'random' }, '随机'), ['werewolf', 'villager', 'seer', 'witch', 'hunter'].map((k) => h('option', { value: k }, ROLE_ZH[k])));
  const seat = h('select', { 'aria-label': '座位' }, h('option', { value: '' }, '随机'), Array.from({ length: 9 }, (_, i) => h('option', { value: i + 1 }, `${i + 1} 号`)));
  const dev = new URLSearchParams(location.search).has('dev'); // /?dev=1：开发模式下可填种子
  const seed = dev ? h('input', { type: 'text', inputmode: 'numeric', placeholder: '随机', 'aria-label': '种子', autocomplete: 'off' }) : null;
  const speed = h('select', { 'aria-label': '速度' }, SPEEDS.map((s) => h('option', { value: s, selected: s === S.speed }, SPEED_ZH[s])));
  const start = () => {
    const v = name.value.trim();
    if (!NAME_RE.test(v)) { err.textContent = '名字只能是 1 到 8 个汉字或字母'; name.focus(); return; }
    const sd = seed && /^\d{1,15}$/.test(seed.value.trim()) ? Number(seed.value.trim()) : null;
    S.playerName = v; store('ww.name', v);
    net.send({ t: 'new_game', name: v, role: role.value, seat: seat.value ? Number(seat.value) : null, speed: speed.value, ...(sd != null ? { seed: sd } : {}) });
    m.close();
  };
  name.addEventListener('keydown', (e) => { if (e.key === 'Enter') start(); });
  const body = h('div', { class: 'newgame' },
    h('p', { class: 'dim' }, '9 人局：你和 8 位 AI 玩家。'),
    h('label', null, '你的名字', name), err,
    h('div', { class: 'grid2' }, h('label', null, '身份偏好', role), h('label', null, '座位', seat)),
    h('label', null, '演出速度', speed), seed ? h('label', null, '种子（开发）', seed) : null);
  const m = modal({ title: first ? '欢迎入座' : '开新局', body, actions: [btn('开始游戏', start, 'primary big')] });
  return m;
}

export function openReview() {
  if (!S.review) return;
  modal({ title: '终局复盘', wide: true, body: renderReview(S.review) });
}
