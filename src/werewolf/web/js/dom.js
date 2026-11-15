// 极简 DOM 工具。所有来自服务端的文字一律走文本节点，不拼 HTML。
export function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
      else if (k === 'data') Object.assign(e.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2).toLowerCase(), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const c of kids.flat(2)) {
    if (c == null || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const clear = (e) => { e.replaceChildren(); return e; };
export const reduceMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
export function store(key, val) {
  try {
    if (val === undefined) return JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify(val));
  } catch { /* 隐私模式等：忽略 */ }
  return null;
}
// 清空后放入子节点；null/false 跳过，数组展开（replaceChildren 会把 null 变成文字「null」）
export function fill(el, ...kids) {
  el.replaceChildren();
  el.append(...kids.flat(2).filter((c) => c != null && c !== false).map((c) => (c.nodeType ? c : document.createTextNode(String(c)))));
  return el;
}
