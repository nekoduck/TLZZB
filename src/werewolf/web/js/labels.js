// 中文标签与事件描述：发言记录、复盘、顶栏共用。
export const ROLE_ZH = { werewolf: '狼人', villager: '平民', seer: '预言家', witch: '女巫', hunter: '猎人' };
export const ROLE_TEAM = { werewolf: '狼人阵营', villager: '好人阵营', seer: '好人阵营', witch: '好人阵营', hunter: '好人阵营' };
export const ROLE_DESC = {
  werewolf: '每夜与同伴商量刀一名玩家。白天隐藏身份，把好人带进沟里。',
  villager: '没有技能，但有一双眼睛和一张嘴。听发言、看票型，找出狼人。',
  seer: '每夜验一名玩家的阵营。你的话最有分量，也最容易挨刀。',
  witch: '一瓶解药，一瓶毒药，各用一次。夜里你知道谁被刀了。',
  hunter: '被刀或被放逐时可以开枪带走一人。被毒则无法开枪。',
};
export const PHASE_ZH = {
  setup: '发牌', night_wolf: '狼人行动', night_seer: '预言家验人', night_witch: '女巫用药', dawn: '天亮了',
  hunter: '技能环节', last_words: '遗言', day_speech: '轮流发言', day_vote: '投票', pk_speech: 'PK 发言',
  pk_vote: 'PK 投票', game_over: '游戏结束',
};
export const NIGHT_PHASES = new Set(['night_wolf', 'night_seer', 'night_witch']);
export const STANCE_ZH = { jump_seer: '悍跳预言家', charge: '冲锋', deep: '深水' };
export const STANCE_HINT = { jump_seer: '跳出来冒充预言家', charge: '力挺队友、攻击真预言家', deep: '装平民、必要时倒钩' };
export const SPEECH_KIND_ZH = { day: '发言', pk: 'PK 发言', last_words: '遗言' };
export const WIN_REASON_ZH = {
  wolves_dead: '狼人全部出局', villagers_dead: '平民全部出局（屠边）', gods_dead: '神职全部出局（屠边）', max_days: '到达天数上限',
};
export const SELF_SAVE_ZH = { never: '不可自救', first_night: '仅首夜可自救', always: '可自救' };
export const MARKS = ['', '狼', '好', '神', '?'];
export const MOOD_ZH = { confident: '自信', tense: '紧张', tilted: '破防', steady: '平稳' };
export const SOURCE_ZH = { rules: '规则', jev: 'Jev', claude: 'Claude', jev_select: 'Jev 选句', template: '模板', fallback: '兜底' };
export const SPEED_ZH = { slow: '慢', normal: '中', fast: '快', instant: '瞬' };
export const SPEEDS = ['slow', 'normal', 'fast', 'instant'];

const RES = { wolf: '狼人', good: '好人' };
export const resZh = (r) => RES[r] || '?';

// 言语行为的中文说明
export function actLabel(a) {
  const t = a.target != null ? `${a.target} 号` : '';
  const night = a.night ? `（第${a.night}夜）` : '';
  switch (a.kind) {
    case 'claim_role': return `自称${ROLE_ZH[a.role] || '?'}`;
    case 'claim_check': return `报查验：${t}是${resZh(a.result)}${night}`;
    case 'claim_save': return `自称救了 ${t}${night}`;
    case 'claim_poison': return `自称毒了 ${t}${night}`;
    case 'accuse': return `指认 ${t}`;
    case 'vouch': return `保 ${t}`;
    case 'defend': return a.target != null ? `替 ${t}辩解` : '替自己辩解';
    case 'vote_intent': return `表态投 ${t}`;
    case 'back_claim': return `站边 ${t}`;
    case 'doubt_claim': return `质疑 ${t}的身份`;
    case 'question': return `点名 ${t}解释`;
    case 'pass': return '过';
    default: return a.kind;
  }
}
const CLAIM_SHORT = { seer: '跳预', witch: '跳女巫', hunter: '跳猎人', villager: '报平民', werewolf: '跳狼' };
export const claimShort = (role) => CLAIM_SHORT[role] || '';

// 事件的一句话描述（发言记录与复盘用）。nameOf(seat) → 'N号 名字'
export function describeEvent(ev, nameOf) {
  const d = ev.data || {};
  const n = (s) => (s == null ? '无人' : nameOf(s));
  switch (ev.type) {
    case 'game_start': return { text: '游戏开始', cls: 'sys' };
    case 'role_dealt': return { text: `${n(ev.actor)} 拿到了 ${ROLE_ZH[d.role]}`, cls: 'secret' };
    case 'wolf_team': return { text: `狼队互认：${(d.wolves || []).map((s) => `${s}号`).join('、')}`, cls: 'secret' };
    case 'phase': return { text: PHASE_ZH[d.phase] || d.phase, cls: 'phase' };
    case 'wolf_propose': return { text: `${n(ev.actor)} 提议刀 ${ev.target ? `${ev.target} 号` : '空刀'}${d.note ? `：${d.note}` : ''}`, cls: 'secret' };
    case 'wolf_kill': return { text: `狼人决定刀 ${ev.target ? `${ev.target} 号` : '空刀'}`, cls: 'secret' };
    case 'seer_result': return { text: `${n(ev.actor)} 验 ${n(ev.target)}：${resZh(d.result)}`, cls: 'secret' };
    case 'witch_notice': return { text: `女巫得知刀口：${ev.target ? `${ev.target} 号` : '未知'}`, cls: 'secret' };
    case 'witch_save': return { text: `女巫用解药救了 ${n(ev.target)}`, cls: 'secret' };
    case 'witch_poison': return { text: `女巫用毒药毒了 ${n(ev.target)}`, cls: 'secret' };
    case 'dawn': return { text: d.peaceful ? '天亮了，昨夜是平安夜' : `天亮了，昨夜 ${(d.deaths || []).map((s) => `${s}号`).join('、')} 倒下`, cls: 'pub' };
    case 'death': return { text: `${n(ev.target)} 出局`, cls: 'death' };
    case 'death_cause': return { text: `${n(ev.target)} 的死因：${{ wolf: '被刀', poison: '被毒', vote: '被放逐', shot: '被枪杀' }[d.cause] || d.cause}`, cls: 'secret' };
    case 'hunter_blocked': return { text: '猎人被毒，无法开枪', cls: 'secret' };
    case 'hunter_shot': return ev.target ? { text: `猎人 ${n(ev.actor)} 开枪带走了 ${n(ev.target)}`, cls: 'death' } : { text: `猎人 ${n(ev.actor)} 选择不开枪`, cls: 'secret' };
    case 'role_revealed': return { text: `${n(ev.target)} 亮出身份：${ROLE_ZH[d.role]}`, cls: 'pub' };
    case 'speech': return { text: (d.speech || {}).text || '', cls: 'speech', kind: d.kind };
    case 'vote': return { text: `${n(ev.actor)} 投 ${ev.target ? n(ev.target) : '弃票'}`, cls: 'vote' };
    case 'vote_result': return { text: voteSummary(d), cls: 'pub' };
    case 'exile': return { text: `${n(ev.target)} 被放逐`, cls: 'death' };
    case 'game_over': return { text: `游戏结束：${d.winner === 'wolf' ? '狼人阵营获胜' : d.winner === 'good' ? '好人阵营获胜' : '平局'}`, cls: 'pub' };
    default: return { text: ev.type, cls: 'sys' };
  }
}
export function voteSummary(d) {
  const rows = (d.tally || []).map((r) => `${r.voters.join('、')} 投 ${r.target} 号（${r.voters.length} 票）`);
  if ((d.abstain || []).length) rows.push(`${d.abstain.join('、')} 弃票`);
  const out = { exile: '放逐', pk: '平票，进入 PK', none: '无人出局' }[d.outcome];
  return `${d.round === 'pk' ? 'PK ' : ''}投票：${rows.join('；') || '无人投票'}。${out}`;
}
