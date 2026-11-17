// 中央：法官旁白 + 随环节切换的面板。面板按「场景签名」重建，选择状态变化只调 update()。
import { h, reduceMotion, fill } from './dom.js';
import { S, refresh } from './state.js';
import { net } from './net.js';
import { speechDone } from './handlers.js';
import { roleCard, ICON, potion } from './art.js';
import { avatarEl, seatTag, btn } from './widgets.js';
import { ROLE_ZH, ROLE_DESC, ROLE_TEAM, PHASE_ZH, NIGHT_PHASES, STANCE_ZH, STANCE_HINT, SPEECH_KIND_ZH, WIN_REASON_ZH } from './labels.js';
import { openNewGame, openReview } from './dialogs.js';

let cur = { sig: '', update: null, cleanup: null };
let dealFlipped = false;
const el = (id) => document.getElementById(id);

export function updateCenter() {
  updateJudge();
  const sc = computeScene();
  const panel = el('panel');
  if (sc.sig !== cur.sig) {
    if (cur.cleanup) cur.cleanup();
    const built = BUILD[sc.key](sc) || {};
    cur = { sig: sc.sig, update: built.update || null, cleanup: built.cleanup || null };
    panel.replaceChildren(built.node);
    document.body.dataset.scene = sc.key;
    panel.classList.remove('enter'); void panel.offsetWidth; panel.classList.add('enter');
  }
  if (cur.update) cur.update();
}

function updateJudge() {
  const j = el('judge');
  const text = (S.narration && S.narration.text) || '';
  const sec = S.secret && S.secret.text;
  const sig = text + '|' + sec;
  if (j.dataset.sig === sig) return;
  j.dataset.sig = sig;
  fill(j, h('div', { class: 'judge-line' }, h('span', { class: 'judge-ico' }, '⚖'), h('span', null, text || '……')),
    sec ? h('div', { class: 'secret-line' }, h('span', null, '密'), sec) : null);
  j.classList.toggle('empty', !text && !sec);
}

function computeScene() {
  if (S.over && S.result) return { key: 'over', sig: 'over:' + (S.review ? 'r' : '') };
  const r = S.request;
  if (r) {
    const map = { wolf_kill: 'wolf', seer_check: 'seer', witch_act: 'witch', hunter_shoot: 'hunter', vote: 'vote', speak: 'speakme' };
    return { key: map[r.kind] || 'calm', sig: `${map[r.kind]}:${r.id}:${S.reqN}` };
  }
  if (!S.game) return { key: 'calm', sig: 'wait:' + S.conn };
  if (S.phase === 'setup') return { key: 'deal', sig: 'deal:' + S.role + S.wolves.join() };
  if (S.reveal) return { key: 'reveal', sig: 'reveal:' + S.reveal.seq };
  const th = Object.keys(S.thinking)[0];
  if (th && S.thinking[th] === 'speak') return { key: 'thinking', sig: 'think:' + th + ':' + S.lastSeq, seat: Number(th) };
  if (S.speaker) return { key: 'speech', sig: 'speech:' + S.speaker.seq };
  if (S.seerShow) return { key: 'seerres', sig: 'seerres:' + S.seerShow.k };
  if (S.shot) return { key: 'shot', sig: 'shot:' + S.shot.k };
  if (S.dawn) return { key: 'dawn', sig: 'dawn:' + S.dawn.k };
  if (NIGHT_PHASES.has(S.phase)) return { key: 'night', sig: 'night:' + S.phase };
  if (S.phase === 'day_vote' || S.phase === 'pk_vote') return { key: 'votewait', sig: 'votewait:' + S.phase };
  return { key: 'calm', sig: 'calm:' + S.phase + ':' + S.day };
}

// ---------------------------------------------------------------- 通用小件
const panelHead = (icon, title, sub) => h('div', { class: 'ph' }, h('div', { class: 'ph-ico', html: icon || '' }), h('h2', null, title), sub ? h('p', null, sub) : null);

function send(msg) { net.send(msg); S.sent = true; }

// 选目标的通用面板。cfg: {icon,title,sub,confirm(selSeat)->label, optionFor(sel)->option, extra:Node, none:{label,option}}
function pickPanel(cfg) {
  const r = S.request;
  const targets = new Set(r.options.filter((o) => o.target != null && cfg.accept(o)).map((o) => o.target));
  S.pick = { seats: targets, sel: null, noneSel: false, stance: null };
  const readout = h('div', { class: 'pick-read' });
  const go = btn('', () => {
    const o = S.pick.noneSel ? cfg.none.option : cfg.optionFor(S.pick.sel);
    if (!o || S.sent) return;
    send({ t: 'action', request_id: r.id, option_id: o.id, ...(S.pick.stance ? { stance: S.pick.stance } : {}) });
  }, 'primary big');
  const noneBtn = cfg.none ? btn(cfg.none.label, () => { S.pick.noneSel = !S.pick.noneSel; S.pick.sel = null; cfg.refreshAll(); }, 'ghost') : null;
  const node = h('div', { class: 'scene pick' }, panelHead(cfg.icon, cfg.title, cfg.sub), cfg.extra || null, readout,
    h('div', { class: 'pick-actions' }, noneBtn, go), cfg.footer || null);
  const update = () => {
    const pk = S.pick;
    if (!pk) return;
    if (pk.sel != null) pk.noneSel = false;
    const sel = pk.sel;
    const label = pk.noneSel ? cfg.none.confirm : sel != null ? cfg.confirm(sel) : cfg.placeholder || '请在左右两侧点选一名玩家';
    fill(readout, sel != null ? seatTag(sel) : null, h('span', null, label));
    readout.classList.toggle('ready', sel != null || pk.noneSel);
    go.textContent = sel != null || pk.noneSel ? (pk.noneSel ? cfg.none.confirm : cfg.confirm(sel)) : '确认';
    go.disabled = S.sent || !(sel != null || pk.noneSel);
    if (noneBtn) { noneBtn.classList.toggle('on', pk.noneSel); noneBtn.disabled = S.sent; }
  };
  cfg.refreshAll = () => { update(); refresh(); };
  return { node, update, cleanup: () => { S.pick = null; } };
}
const optBy = (pre) => (sel) => S.request.options.find((o) => o.id === `${pre}:${sel}`);

// ---------------------------------------------------------------- 场景
const BUILD = {
  deal() {
    const card = roleCard(S.role, { flip: !S.replaying && !dealFlipped });
    if (!dealFlipped && !S.replaying && !reduceMotion()) setTimeout(() => { card.classList.remove('is-back'); dealFlipped = true; }, 800);
    else { card.classList.remove('is-back'); dealFlipped = true; }
    const mates = S.role === 'werewolf' && S.wolves.length ? h('div', { class: 'deal-mates' }, h('span', null, '你的同伴'), S.wolves.filter((w) => w !== S.you).map((w) => seatTag(w))) : null;
    card.addEventListener('click', () => card.classList.toggle('is-back'));
    return { node: h('div', { class: 'scene deal' }, h('div', { class: 'deal-title' }, '你的身份'), card,
      h('div', { class: 'deal-sub' }, `${ROLE_ZH[S.role] || ''} · ${ROLE_TEAM[S.role] || ''}`), h('p', { class: 'deal-desc' }, ROLE_DESC[S.role] || ''), mates) };
  },

  night() {
    const who = { night_wolf: '狼人正在商量今夜的目标……', night_seer: '预言家正在验人……', night_witch: '女巫正在看她的药瓶……' }[S.phase];
    const mine = { werewolf: '你是狼人，请闭眼等候同伴', villager: '你是平民，安心闭眼，等天亮', seer: '你是预言家', witch: '你是女巫', hunter: '你是猎人，请闭眼等候' }[S.role];
    const dead = S.myDead ? '你已出局，正在观战' : null;
    const own = dead || (S.phase === 'night_seer' && S.role === 'seer') ? '今夜你无人可验' : (S.phase === 'night_witch' && S.role === 'witch') ? '你今夜没有需要决定的事' : null;
    return { node: h('div', { class: 'scene calm night-wait' }, h('div', { class: 'calm-ico big-moon', html: ICON.moon }), h('h2', null, `第 ${S.day} 夜`),
      h('p', { class: 'lead' }, own || who), h('p', { class: 'dim' }, dead ? who : mine), h('div', { class: 'zzz' }, h('i'), h('i'), h('i'))) };
  },

  calm() {
    if (!S.game) return { node: h('div', { class: 'scene calm' }, h('div', { class: 'calm-ico', html: ICON.moon }), h('h2', null, S.mode === 'demo' ? '载入演示……' : '等待开局……'), h('div', { class: 'zzz' }, h('i'), h('i'), h('i'))) };
    const txt = { hunter: '技能环节', day_speech: '发言即将开始', last_words: '遗言', dawn: '天亮了', day_vote: '投票中', pk_vote: 'PK 投票中', pk_speech: 'PK 发言即将开始' }[S.phase] || PHASE_ZH[S.phase] || '';
    return { node: h('div', { class: 'scene calm' }, h('div', { class: 'calm-ico', html: ICON[S.time === 'night' ? 'moon' : 'sun'] }), h('h2', null, txt), h('div', { class: 'zzz' }, h('i'), h('i'), h('i'))) };
  },
  votewait() {
    return { node: h('div', { class: 'scene calm' }, h('div', { class: 'calm-ico', html: ICON.eye }), h('h2', null, S.phase === 'pk_vote' ? 'PK 投票中' : '投票中'),
      h('p', { class: 'dim' }, '所有人同时投票，收齐后一起公开'), h('div', { class: 'zzz' }, h('i'), h('i'), h('i'))) };
  },

  thinking() {
    const seat = arguments[0].seat, p = S.players[seat] || {};
    return { node: h('div', { class: 'scene speech' }, orderStrip(seat), speakerHead(seat, p),
      h('div', { class: 'bubble-big thinking-b' }, h('span', { class: 'dots' }, h('i'), h('i'), h('i')), h('em', null, '正在组织语言'))) };
  },

  speech() {
    const sp = S.speaker, p = S.players[sp.seat] || {};
    const txt = h('span', { class: 'typed' });
    const cursor = h('span', { class: 'cursor' }, '▍');
    const tag = h('div', { class: 'emote-tag' });
    const hint = h('div', { class: 'skip-hint' }, '点击跳过');
    const bubble = h('div', { class: `bubble-big tone-${sp.tone || 'calm'}`, tabindex: 0, role: 'button', 'aria-label': '点击跳过打字' }, txt, cursor, tag, hint);
    const finish = (silent) => {
      clearInterval(timer);
      txt.textContent = sp.text; bubble.classList.add('done');
      if (sp.emote) tag.textContent = `（${emoteZh(sp.emote)}）`;
      if (!silent) speechDone(sp.seq);
    };
    let timer = 0;
    const full = [...sp.text];
    const instant = sp.done || sp.instant || sp.own || reduceMotion() || S.speed === 'instant';
    if (instant) { setTimeout(() => finish(sp.done), 0); } else {
      const cps = ({ slow: 16, normal: 30, fast: 60 }[S.speed] || 30) * (S.tscale ? 1 / S.tscale : 1);
      let i = 0, acc = 0;
      timer = setInterval(() => { acc += cps * 0.04; const step = Math.floor(acc); if (step) { acc -= step; i += step; txt.textContent = full.slice(0, i).join(''); if (i >= full.length) finish(false); } }, 40);
    }
    bubble.addEventListener('click', () => { if (!sp.done) finish(false); });
    bubble.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && !sp.done) { e.preventDefault(); finish(false); } });
    return { node: h('div', { class: 'scene speech' }, orderStrip(sp.seat), speakerHead(sp.seat, p, sp.kind), bubble), cleanup: () => clearInterval(timer) };
  },

  speakme() {
    const r = S.request, c = r.context || {};
    const kind = r.speech_kind || 'day';
    const recent = S.events.filter((e) => e.type === 'speech' && e.actor !== S.you).slice(-2);
    const rivals = (c.rivals || []).length ? h('div', { class: 'rivals' }, h('span', null, 'PK 对手'), c.rivals.map((s) => seatTag(s))) : null;
    return { node: h('div', { class: 'scene speech me-speak' }, orderStrip(S.you),
      h('div', { class: 'speaker-head' }, avatarEl(S.you, 'lg speaking'), h('div', { class: 'who' }, h('b', null, '轮到你了'), h('span', null, `${SPEECH_KIND_ZH[kind]}${c.index != null && c.total ? ` · 第 ${c.index + 1}/${c.total} 位` : ''}`))),
      h('p', { class: 'lead' }, kind === 'last_words' ? '留下你的遗言吧，这是最后的机会。' : kind === 'pk' ? '你在 PK 台上，为自己争取票数。' : '在下方输入你的发言，或点快捷短语。'),
      rivals,
      recent.length ? h('div', { class: 'recent' }, recent.map((e) => h('div', { class: 'recent-line' }, h('b', null, `${e.actor}号`), h('span', null, e.data.speech.text)))) : null) };
  },

  wolf() {
    const r = S.request, c = r.context || {};
    const props = h('div', { class: 'proposals' }, (c.proposals || []).map((p) => h('div', { class: 'proposal' }, avatarEl(p.seat, 'sm'),
      h('div', null, h('b', null, `${p.seat}号提议${p.target ? `刀 ${p.target} 号` : '空刀'}`), p.note ? h('q', null, p.note) : null),
      p.stance ? h('span', { class: 'chip claim' }, STANCE_ZH[p.stance] || p.stance) : null)));
    let stanceBox = null;
    const cfg = {
      icon: ICON.skull, title: '狼人之夜', sub: S.wolves.length ? `你的同伴：${S.wolves.filter((w) => w !== S.you).map((w) => `${w}号`).join('、') || '无'}` : '', accept: (o) => o.action === 'kill',
      confirm: (s) => `刀 ${s} 号`, optionFor: optBy('kill'), placeholder: '点选今夜的刀口',
      none: r.options.find((o) => o.id === 'kill:none') ? { label: '空刀', confirm: '空刀', option: r.options.find((o) => o.id === 'kill:none') } : null,
    };
    if (c.first_night) {
      stanceBox = h('div', { class: 'stance' }, h('div', { class: 'stance-t' }, '首夜打法（告诉队友你的意愿，可不选）'),
        h('div', { class: 'stance-row' }, [null, 'jump_seer', 'charge', 'deep'].map((k) => h('button', { type: 'button', class: 'pill', 'aria-pressed': 'false', title: k ? STANCE_HINT[k] : '', data: { k: k || '' },
          onClick: (e) => { S.pick.stance = k; stanceBox.querySelectorAll('.pill').forEach((b) => b.setAttribute('aria-pressed', String(b === e.currentTarget))); } }, k ? STANCE_ZH[k] : '不指定'))));
      stanceBox.querySelector('.pill').setAttribute('aria-pressed', 'true');
    }
    cfg.extra = h('div', null, props.childNodes.length ? props : h('p', { class: 'dim' }, '队友还没有提议，由你决定。'), stanceBox);
    return pickPanel(cfg);
  },

  seer() {
    return pickPanel({ icon: ICON.eye, title: '预言家 · 今夜验谁', sub: '选一名玩家，天亮前就能知道他是狼人还是好人。', accept: (o) => o.action === 'check', confirm: (s) => `查验 ${s} 号`, optionFor: optBy('check') });
  },

  vote() {
    const r = S.request, c = r.context || {};
    const abst = r.options.find((o) => o.id === 'vote:abstain');
    return pickPanel({ icon: ICON.skull, title: c.round === 'pk' ? 'PK 投票' : '投票放逐', sub: c.round === 'pk' ? '只能投 PK 台上的候选人。' : '点选你认为是狼人的玩家。', accept: (o) => o.action === 'vote', confirm: (s) => `投 ${s} 号`, optionFor: optBy('vote'),
      none: abst ? { label: '弃票', confirm: '弃票', option: abst } : null });
  },

  hunter() {
    const r = S.request;
    const none = r.options.find((o) => o.id === 'shoot:none');
    return pickPanel({ icon: ICON.skull, title: '猎人 · 最后一枪', sub: r.context && r.context.after === 'vote' ? '你被放逐了，可以带走一人。' : '你倒下了，可以带走一人。', accept: (o) => o.action === 'shoot', confirm: (s) => `开枪带走 ${s} 号`,
      optionFor: optBy('shoot'), none: none ? { label: '不开枪', confirm: '不开枪', option: none } : null });
  },

  witch() {
    const r = S.request, c = r.context || {};
    const save = r.options.find((o) => o.id === 'save'), pass = r.options.find((o) => o.id === 'pass');
    const poisons = r.options.filter((o) => o.action === 'poison');
    S.pick = null;
    const info = c.victim_known && c.victim ? `今夜 ${c.victim} 号倒在了狼爪下。` : c.antidote ? '今夜平安无事。' : '解药已用完，你不知道今夜谁被刀了。';
    const box = h('div', { class: 'witch-area' });
    const act = (o) => { if (o && !S.sent) send({ t: 'action', request_id: r.id, option_id: o.id }); };
    const readout = h('div', { class: 'pick-read' });
    const confirm = btn('', () => act(poisons.find((p) => p.target === S.pick.sel)), 'primary big');
    const cancel = btn('改主意了', () => { S.pick = null; refresh(); }, 'ghost');
    let built = '';
    const update = () => {
      const poisoning = !!S.pick;
      const key = `${poisoning}:${S.sent}`;
      if (built !== key) {
        built = key;
        fill(box, 
          h('div', { class: 'potions' },
            h('div', { class: `potion-card ${save ? '' : 'off'}` }, h('div', { html: potion('antidote', !c.antidote) }), h('b', null, '解药'),
              btn(save ? `救 ${save.target} 号` : c.antidote ? '不可使用' : '已用完', () => act(save), save ? 'good' : '', { disabled: !save || S.sent || poisoning })),
            h('div', { class: `potion-card ${poisons.length ? '' : 'off'}` }, h('div', { html: potion('poison', !c.poison) }), h('b', null, '毒药'),
              btn(poisons.length ? (poisoning ? '选择毒杀对象' : '使用毒药') : '已用完', () => { S.pick = { seats: new Set(poisons.map((p) => p.target)), sel: null }; refresh(); }, poisons.length ? 'bad' : '', { disabled: !poisons.length || S.sent || poisoning }))),
          poisoning ? h('div', { class: 'poison-pick' }, readout, h('div', { class: 'pick-actions' }, cancel, confirm))
            : h('div', { class: 'pick-actions' }, btn('不用药', () => act(pass), 'ghost big', { disabled: !pass || S.sent })));
      }
      if (S.pick) {
        const sel = S.pick.sel;
        fill(readout, sel != null ? seatTag(sel) : null, h('span', null, sel != null ? `毒 ${sel} 号` : '在两侧点选要毒杀的人'));
        readout.classList.toggle('ready', sel != null);
        confirm.textContent = sel != null ? `毒 ${sel} 号` : '确认'; confirm.disabled = sel == null || S.sent;
      }
    };
    return { node: h('div', { class: 'scene witch' }, panelHead(ICON.moon, '女巫 · 今夜用药', info), box), update, cleanup: () => { S.pick = null; } };
  },

  seerres() {
    const s = S.seerShow, bad = s.result === 'wolf';
    return { node: h('div', { class: 'scene seerres' }, panelHead(ICON.eye, '查验结果'),
      h('div', { class: `res-card ${bad ? 'bad' : 'good'}` }, avatarEl(s.target, 'lg'), h('div', { class: 'res-t' }, `${s.target} 号`), h('div', { class: 'res-v' }, bad ? '狼人' : '好人'), h('div', { class: 'res-s' }, bad ? '查杀' : '金水'))) };
  },

  dawn() {
    const d = S.dawn;
    const body = d.peaceful || !d.deaths.length ? [h('div', { class: 'calm-ico big-sun', html: ICON.sun }), h('h2', null, '平安夜'), h('p', { class: 'lead' }, '昨夜无人倒下。')]
      : [h('div', { class: 'ph-ico', html: ICON.skull }), h('h2', null, '昨夜倒下的是'), h('div', { class: 'dead-row' }, d.deaths.map((s) => h('div', { class: 'dead-one' }, avatarEl(s, 'lg grey'), h('b', null, `${s} 号`), h('span', null, (S.players[s] || {}).name || ''))))];
    return { node: h('div', { class: 'scene dawn' }, h('div', { class: 'dawn-t' }, '天亮了'), ...body) };
  },

  shot() {
    const s = S.shot;
    return { node: h('div', { class: 'scene shot' }, panelHead(ICON.skull, '猎人开枪'), h('div', { class: 'shot-row' }, h('div', { class: 'dead-one' }, avatarEl(s.hunter, 'lg'), h('b', null, `${s.hunter} 号`), h('span', { class: 'chip good' }, '猎人')),
      h('div', { class: 'shot-arrow' }, '➤'), h('div', { class: 'dead-one hit' }, avatarEl(s.target, 'lg grey'), h('b', null, `${s.target} 号`)))) };
  },

  reveal() {
    const d = S.reveal;
    const rows = (d.tally || []).map((t, i) => h('div', { class: 'rv-row', style: { '--i': i } }, h('div', { class: 'rv-target' }, avatarEl(t.target, 'sm'), h('b', null, `${t.target} 号`), h('span', { class: 'rv-n' }, `${t.voters.length} 票`)),
      h('div', { class: 'rv-voters' }, t.voters.map((v) => seatTag(v, { name: false })))));
    if ((d.abstain || []).length) rows.push(h('div', { class: 'rv-row abst', style: { '--i': rows.length } }, h('div', { class: 'rv-target' }, h('b', null, '弃票')), h('div', { class: 'rv-voters' }, d.abstain.map((v) => seatTag(v, { name: false })))));
    const out = d.outcome === 'exile' ? `${(d.tally[0] || {}).target} 号被放逐` : d.outcome === 'pk' ? `平票：${(d.tied || []).map((s) => `${s}号`).join('、')} 进入 PK` : '无人出局';
    return { node: h('div', { class: 'scene reveal' }, panelHead(ICON.eye, d.round === 'pk' ? 'PK 投票结果' : '投票结果'), h('div', { class: 'rv-list' }, rows),
      h('div', { class: `rv-out ${d.outcome}`, style: { '--i': rows.length } }, out)) };
  },

  over() {
    const r = S.result, w = r.winner;
    const title = w === 'wolf' ? '狼人阵营获胜' : w === 'good' ? '好人阵营获胜' : '平局';
    const mine = S.role === 'werewolf' ? 'wolf' : 'good';
    const grid = h('div', { class: 'over-grid' }, (r.roles || []).slice().sort((a, b) => a.seat - b.seat).map((x) => h('div', { class: `over-one ${S.alive[x.seat] === false ? 'dead' : ''}` },
      avatarEl(x.seat, 'sm'), h('b', null, `${x.seat}号`), h('span', null, (S.players[x.seat] || {}).name || ''), h('em', { class: `chip role ${x.role === 'werewolf' ? 'bad' : 'good'}` }, ROLE_ZH[x.role]))));
    const rev = btn(S.review ? '查看复盘' : '复盘生成中…', () => openReview(), 'primary big pulse', { disabled: !S.review });
    return { node: h('div', { class: `scene over win-${w}` }, h('div', { class: 'over-banner' }, h('div', { class: 'over-sub' }, w ? (w === mine ? '你赢了' : '你输了') : '势均力敌'), h('h1', null, title), h('p', null, WIN_REASON_ZH[r.reason] || r.reason || '')), grid,
      h('div', { class: 'over-actions' }, rev, btn('再来一局', () => openNewGame(), 'ghost big'))) };
  },
};

function emoteZh(k) { return ({ nod: '点头', frown: '皱眉', sneer: '冷笑', shock: '一愣', think: '沉吟', laugh: '笑了', angry: '拍桌', sweat: '冒汗', silent: '沉默', stare: '盯着看', sigh: '叹气', shrug: '摊手' })[k] || k; }

function speakerHead(seat, p, kind) {
  return h('div', { class: 'speaker-head' }, avatarEl(seat, 'lg speaking'), h('div', { class: 'who' }, h('b', null, `${seat}号 ${p.name || ''}`), h('span', null, [p.title, kind ? SPEECH_KIND_ZH[kind] : null].filter(Boolean).join(' · '))));
}
function orderStrip(cur) {
  if (!S.order.length || !['day_speech', 'pk_speech', 'last_words'].includes(S.phase)) return null;
  return h('div', { class: 'order', 'aria-label': '发言顺序' }, S.order.map((s) => h('span', { class: `o ${s === cur ? 'now' : S.spoken.includes(s) && s !== cur ? 'done' : ''}` }, s)));
}

