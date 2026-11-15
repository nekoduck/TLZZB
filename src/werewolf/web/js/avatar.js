// 头像：按 AvatarSpec 画 SVG（10 种发型 x 10 种配饰）。颜色先过白名单，其余全是常量。
const HEX = /^#[0-9a-fA-F]{6}$/;
const col = (c, d) => (typeof c === 'string' && HEX.test(c) ? c : d);

export function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  const f = amt < 0 ? (v) => Math.round(v * (1 + amt)) : (v) => Math.round(v + (255 - v) * amt);
  return '#' + [n >> 16, (n >> 8) & 255, n & 255].map((v) => f(v).toString(16).padStart(2, '0')).join('');
}

export const DEFAULT_SPEC = { bg: '#3a4a7a', skin: '#f0c8a0', hair: '#2b2230', hair_style: 'short', accessory: 'none', outfit: '#4a5a8a' };
export const HAIR_STYLES = ['short', 'long', 'bun', 'cap', 'hood', 'bald', 'curly', 'ponytail', 'bob', 'spiky'];
export const ACCESSORIES = ['none', 'glasses', 'scarf', 'earring', 'hat', 'headphones', 'beard', 'pipe', 'flower', 'mask'];

const p = (d, fill, extra = '') => `<path d="${d}" fill="${fill}" ${extra}/>`;

function hairBack(style, H, Hd, outfit) {
  switch (style) {
    case 'long': return p('M27 44 C22 70 25 86 32 92 L68 92 C75 86 78 70 73 44 Z', H);
    case 'bob': return p('M27 44 C24 62 27 72 34 74 L66 74 C73 72 76 62 73 44 Z', H);
    case 'ponytail': return p('M64 28 C88 24 92 54 80 70 C80 58 76 48 68 42 Z', H) + p('M70 36 C82 38 84 52 80 62', 'none', `stroke="${Hd}" stroke-width="1.4"`);
    case 'hood': return p('M22 52 C16 18 36 6 50 6 C64 6 84 18 78 52 C78 66 70 74 62 76 L38 76 C30 74 22 66 22 52 Z', shade(outfit, -0.12));
    default: return '';
  }
}
function hairFront(style, H, Hd, outfit) {
  const dome = `M30 46 C25 22 41 13 50 13 C59 13 75 22 70 46 C68 38 64 32 58 30 C52 34 41 34 37 31 C33 35 31 40 30 46 Z`;
  switch (style) {
    case 'short': return p(dome, H) + p('M38 24 C44 19 54 19 60 23', 'none', `stroke="${Hd}" stroke-width="1.2" opacity=".6"`);
    case 'long': return p('M30 46 C25 22 41 13 50 13 C59 13 75 22 70 46 C67 36 60 27 50 26 C40 27 33 36 30 46 Z', H);
    case 'bun': return p(dome, H) + `<circle cx="50" cy="12" r="8.5" fill="${H}"/><circle cx="47" cy="10" r="2.6" fill="${shade(H, 0.25)}" opacity=".6"/>` + p('M42 17 Q50 21 58 17', 'none', `stroke="#e04a5a" stroke-width="2.2"`);
    case 'cap': return p('M31 40 C30 24 40 17 50 17 C60 17 70 24 69 40 C64 33 36 33 31 40 Z', H)
      + p('M30 34 C30 18 41 11 50 11 C59 11 70 18 70 34 Z', shade(outfit, -0.05)) + p('M52 32 C62 30 80 32 88 38 C82 42 64 40 52 38 Z', shade(outfit, -0.28)) + `<circle cx="50" cy="11.5" r="2" fill="${shade(outfit, -0.3)}"/>`;
    case 'hood': return p('M30 44 C28 28 38 20 50 20 C62 20 72 28 70 44 C66 34 62 31 56 31 C50 35 42 35 36 31 C33 34 31 38 30 44 Z', Hd)
      + `<ellipse cx="50" cy="46" rx="21" ry="24.5" fill="none" stroke="${shade(outfit, -0.32)}" stroke-width="3.4"/>`;
    case 'bald': return '<ellipse cx="43" cy="28" rx="6" ry="3.4" fill="#fff" opacity=".3" transform="rotate(-25 43 28)"/>';
    case 'curly': return [[33, 32], [40, 23], [50, 19], [60, 23], [67, 32], [31, 42], [69, 42], [45, 30], [56, 30]]
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="8" fill="${H}"/><circle cx="${x - 2}" cy="${y - 2}" r="3" fill="${shade(H, 0.18)}" opacity=".55"/>`).join('');
    case 'ponytail': return p(dome, H) + `<circle cx="67" cy="31" r="3.2" fill="#f2c14e"/>`;
    case 'bob': return p(dome, H) + p('M31 33 C40 28 60 28 69 33 L69 41 C60 36 40 36 31 41 Z', H) + p('M31 41 L30 62 C32 66 34 66 36 62 L36 44 Z', H) + p('M69 41 L70 62 C68 66 66 66 64 62 L64 44 Z', H);
    case 'spiky': return p('M30 44 L26 26 L37 31 L38 11 L47 25 L54 6 L60 25 L71 12 L68 32 L76 27 L70 46 C66 37 60 32 50 32 C40 32 34 37 30 44 Z', H);
    default: return '';
  }
}
function accessory(kind, hair) {
  switch (kind) {
    case 'glasses': return '<g fill="rgba(170,220,255,.22)" stroke="#2a2433" stroke-width="1.7"><circle cx="42" cy="47" r="6.8"/><circle cx="58" cy="47" r="6.8"/></g><path d="M48.8 46 Q50 44.5 51.2 46 M35.4 46 L31 45 M64.6 46 L69 45" stroke="#2a2433" stroke-width="1.5" fill="none"/>';
    case 'scarf': return p('M33 66 C40 75 60 75 67 66 L69 77 C60 84 40 84 31 77 Z', '#c8323c') + p('M56 77 L66 98 L57 99 L52 80 Z', '#a8222c')
      + '<path d="M38 72 L36 80 M46 74 L45 82 M54 74 L54 82 M62 72 L63 80" stroke="#f3e9d2" stroke-width="1.6" opacity=".8"/>';
    case 'earring': return '<g fill="none" stroke="#f2c14e" stroke-width="1.6"><circle cx="30.5" cy="56" r="2.8"/><circle cx="69.5" cy="56" r="2.8"/></g><circle cx="30.5" cy="60" r="1.6" fill="#f2c14e"/><circle cx="69.5" cy="60" r="1.6" fill="#f2c14e"/>';
    case 'hat': return '<ellipse cx="50" cy="29" rx="28" ry="5.5" fill="#2e2540"/>' + p('M36 29 L37 8 C43 5 57 5 63 8 L64 29 Z', '#4b3a63') + '<rect x="36.4" y="22" width="27.2" height="5.4" fill="#f2c14e"/><rect x="36.4" y="22" width="27.2" height="1.4" fill="#fff" opacity=".35"/>';
    case 'headphones': return '<path d="M27 46 C25 14 75 14 73 46" stroke="#2a2a33" stroke-width="4" fill="none"/><rect x="22" y="41" width="9" height="17" rx="4" fill="#d23c4a"/><rect x="69" y="41" width="9" height="17" rx="4" fill="#d23c4a"/><rect x="23.5" y="44" width="2.5" height="11" rx="1.2" fill="#fff" opacity=".35"/>';
    case 'beard': return p('M31 52 C31 72 40 79 50 79 C60 79 69 72 69 52 C65 63 58 66 50 66 C42 66 35 63 31 52 Z', shade(hair, -0.05)) + p('M41 58.5 Q45.5 54.5 50 58 Q54.5 54.5 59 58.5 Q54.5 62 50 59.5 Q45.5 62 41 58.5 Z', shade(hair, -0.05));
    case 'pipe': return '<path d="M52 59 L68 65" stroke="#6b4423" stroke-width="2.6" stroke-linecap="round"/>' + p('M66 58 L75 58 L74 68 C72 70 68 70 66 68 Z', '#6b4423') + '<circle cx="71" cy="52" r="2.4" fill="#fff" opacity=".45"/><circle cx="74" cy="46" r="3.2" fill="#fff" opacity=".3"/>';
    case 'flower': return '<g transform="translate(35 24)">' + [0, 72, 144, 216, 288].map((a) => `<circle cx="0" cy="-5" r="3.8" fill="#ff8fb0" transform="rotate(${a})"/>`).join('') + '<circle r="2.8" fill="#ffd45e"/></g>';
    case 'mask': return p('M31 51 C37 57 63 57 69 51 L68 63 C62 73 38 73 32 63 Z', '#e8ecf4', 'stroke="#9aa3b8" stroke-width=".9"') + '<path d="M36 58 H64 M36 63 H62 M38 68 H60" stroke="#b6bdd0" stroke-width=".9" fill="none"/><path d="M31 51 C28 49 28 47 29 45 M69 51 C72 49 72 47 71 45" stroke="#9aa3b8" stroke-width="1" fill="none"/>';
    default: return '';
  }
}

export function avatarSVG(spec) {
  const s = spec || DEFAULT_SPEC;
  const bg = col(s.bg, DEFAULT_SPEC.bg), skin = col(s.skin, DEFAULT_SPEC.skin), hair = col(s.hair, DEFAULT_SPEC.hair);
  const outfit = col(s.outfit, DEFAULT_SPEC.outfit);
  const style = HAIR_STYLES.includes(s.hair_style) ? s.hair_style : 'short';
  const acc = ACCESSORIES.includes(s.accessory) ? s.accessory : 'none';
  const Hd = shade(hair, -0.28), skinD = shade(skin, -0.14);
  const mouth = acc === 'mask' ? '' : '<path d="M45 57.5 Q50 61 55 57.5" fill="none" stroke="#8a4040" stroke-width="1.5" stroke-linecap="round"/>';
  return `<svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">`
    + `<rect width="100" height="100" fill="${bg}"/><ellipse cx="50" cy="40" rx="46" ry="40" fill="#fff" opacity=".13"/>`
    + hairBack(style, hair, Hd, outfit)
    + p('M12 100 C12 80 30 72 50 72 C70 72 88 80 88 100 Z', outfit) + p('M41 72 L50 85 L59 72 Z', skin)
    + p('M44 58 H56 V75 C54 79 46 79 44 75 Z', skinD)
    + `<circle cx="31.5" cy="49" r="4.2" fill="${skinD}"/><circle cx="68.5" cy="49" r="4.2" fill="${skinD}"/>`
    + `<ellipse cx="50" cy="46" rx="19.5" ry="22.5" fill="${skin}"/>`
    + `<ellipse cx="42" cy="47.5" rx="2.2" ry="2.9" fill="#1c1a24"/><ellipse cx="58" cy="47.5" rx="2.2" ry="2.9" fill="#1c1a24"/>`
    + `<circle cx="42.8" cy="46.4" r=".8" fill="#fff"/><circle cx="58.8" cy="46.4" r=".8" fill="#fff"/>`
    + `<path d="M38 41.5 Q42 39.5 46 41.5 M54 41.5 Q58 39.5 62 41.5" fill="none" stroke="${style === 'bald' ? skinD : Hd}" stroke-width="1.5" stroke-linecap="round"/>`
    + `<circle cx="37" cy="54" r="3.4" fill="#ff8a8a" opacity=".22"/><circle cx="63" cy="54" r="3.4" fill="#ff8a8a" opacity=".22"/>`
    + mouth
    + hairFront(style, hair, Hd, outfit) + accessory(acc, hair) + '</svg>';
}
