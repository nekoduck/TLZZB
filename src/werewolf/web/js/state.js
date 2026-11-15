// 全局状态：只有消息处理器写，渲染器读。重连时 resetGame() 丢弃对局状态，偏好保留。
import { store } from './dom.js';

export const S = {
  // 偏好（本地）
  marks: store('ww.marks') || {},
  logOpen: false,
  playerName: store('ww.name') || '',
  // 连接
  mode: 'live', demo: null, conn: 'idle', hello: null, speed: 'normal', cost: null, toastq: [],
};

export function resetGame() {
  Object.assign(S, {
    game: null, players: {}, you: 0, role: null, rules: null, replaying: false,
    day: 0, phase: 'setup', time: 'night', lastSeq: 0,
    alive: {}, deadInfo: {}, roles: {}, wolves: [], seerRes: {}, claims: {}, checkOn: {}, proposals: {}, spoken: [],
    order: [], candidates: [], events: [], parsedBy: {}, narration: null, secret: null, speaker: null,
    thinking: {}, reactions: {}, votes: {}, voteRound: 'main', reveal: null, dawn: null, shot: null, seerShow: null,
    witch: null, request: null, sent: false, pick: null, preview: null, previewing: false, draft: '',
    canGod: false, god: false, godRoles: null, godEvents: [], over: false, result: null, review: null, reviewOpen: false,
    banner: null, errorNote: null, myDead: false,
  });
}
resetGame();

export const nameOf = (seat) => {
  const p = S.players[seat];
  return p ? `${seat}号 ${p.name}` : `${seat}号`;
};
export const shortName = (seat) => (S.players[seat] ? S.players[seat].name : `${seat}号`);

let pending = false;
const subs = new Set();
export const onRefresh = (fn) => subs.add(fn);
export function refresh() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; subs.forEach((f) => f()); });
}
export function refreshNow() { pending = false; subs.forEach((f) => f()); }

export function saveMarks() { store('ww.marks', S.marks); }

export function toast(text, kind = '') {
  S.toastq.push({ text, kind, id: Math.random() });
  subs.forEach((f) => f('toast'));
}
