// 底栏：轮到真人发言时是输入框 + 快捷短语 + 预览确认；其余时候是身份牌。
import { h, fill } from './dom.js';
import { S, refresh } from './state.js';
import { net } from './net.js';
import { actLabel, ROLE_ZH, SPEECH_KIND_ZH } from './labels.js';
import { roleCard } from './art.js';
import { btn, modal } from './widgets.js';

let cur = { sig: '', update: null };

export function updateBottom() {
  const box = document.getElementById('bottom');
  const r = S.request && S.request.kind === 'speak' ? S.request : null;
  const sig = r ? `speak:${r.id}:${S.reqN}` : `role:${S.role}:${S.myDead}:${S.over}`;
  if (sig !== cur.sig) {
    const built = r ? buildSpeak(r) : buildRole();
    cur = { sig, update: built.update || null };
    fill(box, built.node);
    box.classList.toggle('speaking', !!r);
  }
  if (cur.update) cur.update();
}

function buildRole() {
  const role = S.role;
  const status = S.over ? '游戏已结束' : S.myDead ? '你已出局，正在观战' : '';
  const peek = h('button', { type: 'button', class: 'role-peek', 'aria-label': `我的身份：${ROLE_ZH[role] || ''}，点击查看`, onClick: () => {
    const card = roleCard(role, { flip: false, size: 'big' });
    modal({ title: '我的身份牌', body: h('div', { class: 'peek-body' }, card) });
  } }, role ? h('span', { class: `mini-card role-${role}` }, ROLE_ZH[role]) : null, h('span', { class: 'peek-t' }, role ? `我是${ROLE_ZH[role]}` : '等待发牌', h('small', null, '点击查看身份牌')));
  return { node: h('div', { class: 'bottom-in idle' }, peek, status ? h('span', { class: 'status-note' }, status) : null) };
}

const PHRASES = [
  { label: '我是预言家', text: '我是预言家。' },
  { label: '查杀 N 号', pick: (n) => `昨晚验了${n}号，是狼人。` },
  { label: '金水 N 号', pick: (n) => `昨晚验了${n}号，是好人。` },
  { label: '我是女巫，救了 N 号', pick: (n) => `我是女巫，昨晚救了${n}号。`, self: true },
  { label: '我站 N 号', pick: (n) => `我站${n}号。` },
  { label: 'N 号你解释一下', pick: (n) => `${n}号你解释一下。` },
  { label: '今天出 N 号', pick: (n) => `今天出${n}号。` },
];

let ta = null;
export function submitDraft() {
  const r = S.request;
  if (!r || r.kind !== 'speak' || S.sent || S.previewing) return;
  const text = (ta ? ta.value : S.draft).trim();
  if (!text) return;
  if (r.context && r.context.confirm) {
    S.previewing = true; S.preview = null; S.keep = null;
    net.send({ t: 'preview', request_id: r.id, text });
    S.previewText = text;
  } else {
    S.sent = true; net.send({ t: 'action', request_id: r.id, text });
  }
  refresh();
}
export function confirmPreview() {
  const r = S.request, pv = S.preview;
  if (!r || !pv || S.sent) return;
  const n = pv.acts.length;
  const keep = S.keep ? [...S.keep].sort((a, b) => a - b) : null;
  S.sent = true;
  net.send({ t: 'action', request_id: r.id, text: S.previewText, ...(keep && keep.length !== n ? { keep } : {}) });
  refresh();
}

function buildSpeak(r) {
  const c = r.context || {};
  const max = c.max_chars || 200;
  ta = h('textarea', { rows: 3, maxlength: 600, placeholder: r.speech_kind === 'last_words' ? '留下遗言……' : '说点什么……（Enter 发送，Shift+Enter 换行）', 'aria-label': '发言内容' });
  const counter = h('span', { class: 'counter' });
  const sendBtn = btn('发送', () => submitDraft(), 'primary');
  const passBtn = btn('过', () => { if (!S.sent) { S.sent = true; net.send({ t: 'action', request_id: r.id, text: '过' }); refresh(); } }, 'ghost');
  const picker = h('div', { class: 'seat-picker', hidden: true });
  const previewBox = h('div', { class: 'preview', hidden: true });
  const chipRow = h('div', { class: 'phrases', role: 'group', 'aria-label': '快捷短语' });

  const insert = (txt) => {
    const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? s;
    ta.value = ta.value.slice(0, s) + txt + ta.value.slice(e);
    ta.focus(); ta.setSelectionRange(s + txt.length, s + txt.length);
    onInput();
  };
  const openPicker = (ph, btnEl) => {
    if (!picker.hidden && picker.dataset.for === ph.label) { picker.hidden = true; return; }
    picker.dataset.for = ph.label; picker.hidden = false;
    const seats = Object.keys(S.players).map(Number).filter((s) => S.alive[s] !== false && (ph.self || s !== S.you)).sort((a, b) => a - b);
    fill(picker, h('span', { class: 'pk-l' }, ph.label.replace('N', '？')), seats.map((s) => h('button', { type: 'button', class: 'pill', onClick: () => { insert(ph.pick(s)); picker.hidden = true; } }, `${s}号`)));
    void btnEl;
  };
  for (const ph of PHRASES) {
    const b = h('button', { type: 'button', class: 'pill phrase', onClick: () => (ph.pick ? openPicker(ph, b) : insert(ph.text)) }, ph.label);
    chipRow.append(b);
  }
  function onInput() {
    S.draft = ta.value;
    if (S.preview || S.previewing) { if (ta.value.trim() !== (S.previewText || '')) { S.preview = null; S.previewing = false; } }
    refresh();
  }
  ta.addEventListener('input', onInput);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitDraft(); } });

  const update = () => {
    if (document.activeElement !== ta && ta.value !== S.draft) ta.value = S.draft;
    const len = [...ta.value].length;
    counter.textContent = `${len}/${max}`;
    counter.classList.toggle('over', len > max);
    const pv = S.preview;
    const busy = S.sent || S.previewing;
    ta.disabled = S.sent;
    sendBtn.disabled = busy || !ta.value.trim() || len > max || !!pv;
    sendBtn.textContent = S.previewing ? '理解中…' : c.confirm ? '预览' : '发送';
    passBtn.disabled = S.sent;
    chipRow.querySelectorAll('button').forEach((b) => { b.disabled = S.sent; });
    previewBox.hidden = !pv && !S.previewing;
    previewBox.querySelectorAll('button').forEach((b) => { b.disabled = S.sent; });
    if (S.previewing && !pv) { fill(previewBox, h('span', { class: 'dim' }, '系统正在读你的发言……')); previewBox.dataset.k = ''; return; }
    if (pv && previewBox.dataset.k !== String(pv.acts.length) + S.previewText) {
      previewBox.dataset.k = String(pv.acts.length) + S.previewText;
      S.keep = new Set(pv.acts.map((_, i) => i));
      renderPreview();
    }
  };
  function renderPreview() {
    const pv = S.preview;
    const chips = pv.acts.length ? pv.acts.map((a, i) => h('span', { class: `chip act ${S.keep.has(i) ? '' : 'off'}` }, actLabel(a),
      h('button', { type: 'button', class: 'x', 'aria-label': S.keep.has(i) ? '去掉这一条' : '恢复这一条', onClick: () => { if (S.keep.has(i)) S.keep.delete(i); else S.keep.add(i); renderPreview(); } }, S.keep.has(i) ? '×' : '↺')))
      : [h('span', { class: 'dim' }, '没有读出需要记录的言语行为（只当普通闲聊）')];
    fill(previewBox, 
      h('div', { class: 'pv-t' }, '系统理解到：', h('small', null, '读错的条目点 × 去掉；只能删，不能加')),
      h('div', { class: 'pv-chips' }, chips),
      (pv.ignored || []).length ? h('div', { class: 'pv-ign' }, h('span', null, '已忽略：'), pv.ignored.map((t) => h('s', null, t))) : null,
      pv.notice ? h('div', { class: 'pv-note' }, pv.notice) : null,
      h('div', { class: 'pv-actions' }, btn('修改发言', () => { S.preview = null; S.previewing = false; refresh(); ta.focus(); }, 'ghost'), btn('确认发言', () => confirmPreview(), 'primary', { disabled: S.sent })));
  }
  const node = h('div', { class: 'bottom-in speak' },
    h('div', { class: 'speak-head' }, h('b', null, SPEECH_KIND_ZH[r.speech_kind || 'day']), h('span', { class: 'dim' }, c.confirm ? '发送前会先让系统读一遍，你确认后才发出' : '')),
    previewBox, picker, chipRow,
    h('div', { class: 'input-row' }, ta, h('div', { class: 'input-side' }, counter, sendBtn, passBtn)));
  return { node, update };
}
