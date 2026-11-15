// 传输层：直播（WebSocket /ws，断线重连并 resume）与演示（按 JSON 剧本回放）。
// 两者把消息交给同一个 handle()；演示播放器像服务端一样遵守 hold_ms 与 wait_ack。
import { sleep } from './dom.js';
import { S, resetGame, refresh, toast } from './state.js';
import { net } from './net.js';
import { handle } from './handlers.js';
import { openNewGame } from './dialogs.js';
import { submitDraft, confirmPreview } from './bottom.js';

// ---------------------------------------------------------------- 直播
let ws = null, retry = 0, hadGame = false, timer = 0;

export function startLive() {
  net.mode = 'live'; S.mode = 'live';
  net.send = (msg) => {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
    else toast('连接中断，稍后会自动重连', 'warn');
  };
  net.onNoGame = () => openNewGame({ first: true });
  connect();
}

function connect() {
  clearTimeout(timer);
  S.conn = 'connecting'; refresh();
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
  try { ws = new WebSocket(url); } catch { schedule(); return; }
  ws.onopen = () => { S.conn = 'open'; retry = 0; refresh(); };
  ws.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    if (!m || typeof m.t !== 'string') return;
    if (m.t === 'hello') onHello(m); else handle(m);
    if (m.t === 'game') hadGame = true;
  };
  ws.onclose = () => { S.conn = 'lost'; refresh(); schedule(); };
  ws.onerror = () => { /* onclose 会跟着来 */ };
}

function schedule() {
  retry = Math.min(retry + 1, 6);
  timer = setTimeout(connect, Math.min(8000, 700 * 2 ** (retry - 1)));
}

function onHello(m) {
  handle(m);
  if (hadGame || m.resumable) {
    resetGame(); S.replaying = true; // 丢弃本地对局状态，等服务端重放
    net.send({ t: 'resume' });
  } else openNewGame({ first: true });
}

// ---------------------------------------------------------------- 演示
const SPEED_SCALE = { slow: 1.5, normal: 1, fast: 0.5, instant: 0 };

export async function startDemo(name, { auto = false, t = null } = {}) {
  net.mode = 'demo'; S.mode = 'demo'; S.conn = 'demo'; S.demo = name;
  S.tscale = t != null ? t : auto ? 0.4 : 1;
  let ackRes = null, userRes = null;
  net.send = (msg) => {
    if (msg.t === 'ack') { if (ackRes) ackRes(); } else if (msg.t === 'preview' || msg.t === 'action') { if (userRes) userRes(msg); }
    S.lastSent = msg;
  };
  let data;
  try {
    const r = await fetch(`demo/${encodeURIComponent(name)}.json`);
    data = await r.json();
  } catch { toast(`找不到演示 ${name}`, 'warn'); return; }
  const waitUser = () => new Promise((res) => { userRes = (m) => { userRes = null; res(m); }; });
  for (const step of data.steps || []) {
    const msg = step.msg;
    const rep = !!msg.replay;
    const ackP = msg.wait_ack && !rep ? new Promise((res) => { ackRes = () => { ackRes = null; res(); }; }) : null;
    const userP = isOpen(msg) || (msg.t === 'parsed' && msg.preview) ? waitUser() : null;
    handle(msg);
    document.body.dataset.demoStep = String(S.events.length);
    const base = msg.t === 'thinking' && msg.on ? 1000 : (msg.hold_ms || 0);
    const hold = base * (SPEED_SCALE[S.speed] ?? 1) * (S.tscale || 1);
    if (!rep && hold) await sleep(hold);
    if (ackP && S.speed !== 'instant') await Promise.race([ackP, sleep(45000)]);
    ackRes = null;
    if (userP) {
      if (auto) autoAnswer(msg, step.answer || {});
      await userP;
    }
  }
  document.body.dataset.demoDone = '1';
}

const isOpen = (m) => m.t === 'request' || (m.t === 'state' && m.open_request);

async function autoAnswer(msg, ans) {
  await sleep(0);
  if (msg.t === 'parsed') { await sleep(1500); confirmPreview(); return; }
  const r = msg.t === 'state' ? msg.open_request : msg;
  if (r.kind === 'speak') {
    S.draft = ans.text || '过'; refresh();
    await sleep(1500);
    submitDraft();
    return;
  }
  const opt = r.options.find((o) => o.id === ans.option_id) || r.options[0];
  await sleep(800);
  if (opt && opt.target != null) {
    if (S.pick) S.pick.sel = opt.target;
    else if (opt.action === 'poison') S.pick = { seats: new Set(r.options.filter((o) => o.action === 'poison').map((o) => o.target)), sel: opt.target };
    refresh();
  } else if (S.pick && opt && opt.action === 'none') { S.pick.noneSel = true; refresh(); }
  await sleep(1400);
  net.send({ t: 'action', request_id: r.id, option_id: opt ? opt.id : null, ...(ans.stance ? { stance: ans.stance } : {}) });
  S.sent = true; refresh();
}
