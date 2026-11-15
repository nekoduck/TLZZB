// 消息处理：直播与演示共用的唯一入口。传输层只负责把消息交到 handle()。
import { S, resetGame, refresh, toast, nameOf } from './state.js';
import { NIGHT_PHASES } from './labels.js';
import { net } from './net.js';

const ROLE_VALUES = ['werewolf', 'villager', 'seer', 'witch', 'hunter'];

export function handle(msg) {
  const rep = !!msg.replay || S.replaying;
  switch (msg.t) {
    case 'hello': S.hello = msg; S.speed = msg.speed || S.speed; break;
    case 'game': onGame(msg); break;
    case 'state': onState(msg); break;
    case 'event': onEvent(msg, rep); break;
    case 'narration': S.narration = { key: msg.key, text: msg.text }; break;
    case 'thinking': if (msg.on) S.thinking[msg.seat] = msg.what; else delete S.thinking[msg.seat]; break;
    case 'reaction': onReaction(msg); break;
    case 'request': openRequest(msg); break;
    case 'accepted': onAccepted(msg); break;
    case 'parsed': onParsed(msg); break;
    case 'private': S.secret = { key: msg.key, text: msg.text, seq: msg.seq }; break;
    case 'god': onGod(msg); break;
    case 'review': S.review = msg; break;
    case 'cost': S.cost = msg; break;
    case 'error': onError(msg); break;
    default: return; // 未知消息：忽略
  }
  refresh();
}

function onGame(msg) {
  resetGame();
  S.replaying = !!msg.replay;
  S.game = msg; S.you = msg.you; S.role = msg.your_role; S.rules = msg.rules;
  for (const p of msg.players || []) { S.players[p.seat] = p; S.alive[p.seat] = true; }
  S.roles[msg.you] = msg.your_role;
  S.phase = 'setup'; S.time = 'night';
}

function onState(msg) {
  S.speed = msg.speed; S.god = !!msg.god_view; S.canGod = !!msg.can_god_view;
  if (msg.over) S.over = true;
  S.replaying = false;
  if (msg.open_request) openRequest(msg.open_request); else if (S.request && !msg.open_request) closeRequest();
}

function openRequest(msg) {
  S.reqN = (S.reqN || 0) + 1;
  S.request = msg; S.sent = false; S.preview = null; S.previewing = false; S.pick = null; S.draft = '';
}
export function closeRequest() {
  S.request = null; S.sent = false; S.preview = null; S.previewing = false; S.pick = null;
}
function onAccepted(msg) {
  if (S.request && S.request.id === msg.request_id) closeRequest();
}
function onParsed(msg) {
  if (msg.preview) {
    if (S.request && (!msg.request_id || msg.request_id === S.request.id)) { S.preview = msg; S.previewing = false; }
  } else if (msg.seq != null) S.parsedBy[msg.seq] = msg;
}
function onError(msg) {
  if (msg.code === 'illegal_action') {
    if (msg.detail === 'unknown_request') { if (!msg.request_id || (S.request && S.request.id === msg.request_id)) closeRequest(); return; }
    S.sent = false; S.previewing = false; // not_an_option / bad_speech：请求仍有效，可重试
    toast(msg.message || '这一步不合法，请重试', 'warn');
  } else if (msg.code === 'no_game') {
    toast(msg.message || '没有进行中的对局'); if (net.onNoGame) net.onNoGame();
  } else toast(msg.message || '出错了', 'warn');
}
function onGod(msg) {
  S.godRoles = {};
  for (const r of msg.roles || []) { S.godRoles[r.seat] = r.role; S.roles[r.seat] = r.role; }
  S.godEvents = msg.events || [];
  const wolves = (msg.roles || []).filter((r) => r.role === 'werewolf').map((r) => r.seat);
  S.wolves = wolves;
}
const MAX_BUBBLES = 3;
function onReaction(msg) {
  if (S.replaying || msg.seq !== S.lastSeq) return;
  const r = { label: msg.label, quip: msg.quip, emote: msg.emote, k: Math.random() };
  delete S.reactions[msg.seat]; // 重新插入以刷新先后顺序
  S.reactions[msg.seat] = r;
  for (const old of Object.keys(S.reactions).slice(0, -MAX_BUBBLES)) delete S.reactions[old]; // 同屏最多 3 个气泡，挤掉最旧的
  setTimeout(() => { if (S.reactions[msg.seat] === r) { delete S.reactions[msg.seat]; refresh(); } }, 2600);
}

// ---------------------------------------------------------------------------

function timeOf(p) {
  if (p === 'setup' || NIGHT_PHASES.has(p)) return 'night';
  if (p === 'hunter' || p === 'game_over') return S.time;
  return 'day';
}

function onPhase(d) {
  const p = d.phase;
  const prev = S.phase;
  S.phase = p;
  if (d.day != null) S.day = d.day;
  const t = timeOf(p);
  if (t !== S.time) {
    S.time = t;
    if (!S.replaying) S.banner = { text: t === 'night' ? `第 ${S.day} 夜` : `第 ${S.day} 天`, sub: t === 'night' ? '天黑请闭眼' : '天亮了', k: Math.random() };
  }
  S.secret = null;
  if (p !== 'night_seer') S.seerShow = null;
  if (['day_speech', 'night_wolf', 'last_words', 'day_vote', 'pk_vote', 'pk_speech'].includes(p)) S.dawn = null;
  if (['day_speech', 'night_wolf', 'day_vote', 'pk_vote'].includes(p)) S.shot = null;
  if (['pk_speech', 'last_words', 'night_wolf', 'day_speech', 'dawn'].includes(p)) { S.reveal = null; }
  if (p === 'day_vote' || p === 'pk_vote') { S.votes = {}; S.reveal = null; S.voteRound = p === 'pk_vote' ? 'pk' : 'main'; }
  if (['pk_speech', 'last_words', 'night_wolf', 'day_speech'].includes(p)) S.votes = {};
  if (p !== prev) { if (S.speaker && S.speaker.ack) { net.send({ t: 'ack', seq: S.speaker.seq }); } S.speaker = null; }
  if (p === 'day_speech' || p === 'pk_speech' || p === 'last_words') { S.spoken = []; S.order = d.order || []; }
  S.candidates = d.candidates || [];
  S.phaseData = d;
}

function onEvent(msg, rep) {
  const ev = msg.ev;
  const e = { ...ev, seq: msg.seq };
  S.events.push(e);
  S.lastSeq = msg.seq;
  const d = ev.data || {};
  const mine = ev.actor === S.you;
  switch (ev.type) {
    case 'role_dealt': if (mine || S.god) S.roles[ev.actor] = d.role; if (mine) S.role = d.role; break;
    case 'wolf_team': S.wolves = d.wolves || []; for (const w of S.wolves) if (S.role === 'werewolf') S.roles[w] = 'werewolf'; break;
    case 'wolf_propose': S.proposals[ev.actor] = { target: ev.target, note: d.note, stance: d.stance }; break;
    case 'phase': onPhase(d); break;
    case 'seer_result':
      if (mine) { S.seerRes[ev.target] = { result: d.result, night: d.night }; S.seerShow = { target: ev.target, result: d.result, night: d.night, k: msg.seq }; }
      break;
    case 'witch_notice': S.witch = { ...(S.witch || {}), ...d, victim: ev.target }; break;
    case 'witch_save': S.witch = { ...(S.witch || {}), antidote: false, savedTarget: ev.target }; break;
    case 'witch_poison': S.witch = { ...(S.witch || {}), poison: false, poisonTarget: ev.target }; break;
    case 'dawn': S.dawn = { deaths: d.deaths || [], peaceful: !!d.peaceful, k: msg.seq }; break;
    case 'death':
      S.alive[ev.target] = false; S.deadInfo[ev.target] = { day: d.day, when: d.when };
      if (ev.target === S.you) S.myDead = true;
      break;
    case 'hunter_shot': if (ev.target) S.shot = { hunter: ev.actor, target: ev.target, k: msg.seq }; break;
    case 'role_revealed': if (ROLE_VALUES.includes(d.role)) S.roles[ev.target] = d.role; break;
    case 'speech': onSpeech(e, msg, rep); break;
    case 'vote': S.votes[ev.actor] = ev.target; break;
    case 'vote_result': S.reveal = { ...d, seq: msg.seq }; break;
    case 'game_over':
      S.over = true; S.result = d; S.phase = 'game_over'; S.shot = null; S.dawn = null; S.speaker = null;
      for (const r of d.roles || []) S.roles[r.seat] = r.role;
      break;
    default: break;
  }
}

function onSpeech(e, msg, rep) {
  const sp = e.data.speech || {};
  const prev = S.speaker;
  if (prev && !prev.done && prev.ack) { net.send({ t: 'ack', seq: prev.seq }); }
  S.spoken.push(e.actor);
  S.speaker = {
    seq: e.seq, seat: e.actor, kind: e.data.kind, text: sp.text || '', emote: sp.emote || null, tone: sp.tone, own: e.actor === S.you,
    ack: !!msg.wait_ack && !rep, instant: rep || S.speed === 'instant', done: false,
  };
  delete S.thinking[e.actor];
  // 公开声明：只读 acts
  const claim = S.claims[e.actor] || (S.claims[e.actor] = {});
  for (const a of sp.acts || []) {
    if (a.kind === 'claim_role' && a.role) claim.role = a.role;
    if (a.kind === 'claim_check' && a.target != null && a.result) S.checkOn[a.target] = { by: e.actor, result: a.result };
  }
}

// 打字完毕（或被点击跳过）：由中央面板调用
export function speechDone(seq) {
  const sp = S.speaker;
  if (!sp || sp.seq !== seq || sp.done) return;
  sp.done = true;
  if (sp.ack) { sp.ack = false; net.send({ t: 'ack', seq }); }
  refresh();
}

export { nameOf };
