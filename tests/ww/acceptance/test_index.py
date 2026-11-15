"""第 10 节验收表 → 测试的索引。下面每一行都由 `test_index_entries_exist` 核对：文件在、测试函数在。

性质（第 10 节主表）
- 人格一致：mind/test_policy_persona.py::test_same_situation_twenty_forks_is_consistent（众数 ≥ 60%、≥ 90% 落在前两名、
  人格差 ≥ 0.5 时总变差 ≥ 0.25）；test_boldness_changes_seer_reveal、test_talkative_changes_passing 等。
- 记忆与遗忘：mind/test_policy_memory.py::test_vengeful_brings_up_the_vote_with_evidence、
  test_unrelated_old_grudges_do_not_appear、test_heavy_accusations_and_vouches_have_evidence、
  test_forgotten_events_cannot_be_cited。
- 动机与主动性：sim/test_arena.py::test_balance_and_distribution_300_games（规则循环）；
  sim/test_selfplay.py::test_section10_targets_300_plus_300 与 test_real_pipeline_distribution_loose（会话层 + NpcAgent）。
- 情绪：mind/test_policy_emotion.py::test_tilted_vengeful_votes_the_grudge_more、test_decision_thought_mentions_mood_when_tilted。
- 知识边界：心智不变量每一步都跑——sim/test_arena.py::test_every_game_terminates_without_violations、
  acceptance/test_full_games.py::test_autopilot_games_with_mind_checks；反应与身份无关——
  mind/test_policy_reactions.py::test_reactions_are_role_independent；简报逐字相同——
  voice/test_brief.py::test_role_independence_same_public_input 与（端到端）
  acceptance/test_role_independence.py::test_brief_and_prompt_identical_across_roles。
- 不完美：sim/test_arena.py::test_good_ais_mostly_keep_their_word；非最优选择率、tilted 高于 steady、
  兑现率见 sim/test_selfplay.py::test_section10_targets_300_plus_300（stats 的 non_optimal_tilted / _steady）。
- 社会性：mind/test_mood.py::test_trust_up_down_decay；mind/test_policy_persona.py::test_conformity_changes_votes；
  mind/test_policy_refusal.py::test_overall_follow_rate_with_real_personas、test_never_follows_a_call_on_own_gold、
  test_call_on_self_is_not_an_option；全体跟票率见 sim/test_selfplay.py（stats 的 follow）。

另有
- 内核不变量：kernel/test_properties.py::test_invariants_at_end_1000_random_games；wire_check 对脚本托管的真人座位——
  server/test_session_games.py::test_many_scripted_games、acceptance/test_full_games.py::test_human_role_full_game。
- 口径一致：sim/test_arena.py::test_claims_are_consistent；会话层的同一指标（contradictions、two_wolf_seer）见
  sim/test_selfplay.py。
- 抗骗用例：acceptance/test_anticheat.py::test_anticheat（数据在 data/anticheat.yaml）。
- 防漏：金丝雀与狼人台词提示——acceptance/test_leaks.py::test_canaries_and_wolf_prompts；露底或计划外的投票被拦下换模板——
  acceptance/test_leaks.py::test_leaking_or_off_plan_claude_is_replaced_by_template（单元版：
  llm/test_speak.py::test_leaking_claude_falls_through_to_jev_select、test_extra_vote_not_in_plan_is_blocked）；
  真人猎人开枪前要上帝视角被拒——acceptance/test_leaks.py::test_human_hunter_god_view_refused_before_shooting、
  server/test_session_wire.py::test_god_view_rejected_until_settled；AST——llm/test_structure.py::test_no_private_types、
  voice/test_brief.py::test_ast_no_private_types。
- 平衡与分布：sim/test_selfplay.py::test_section10_targets_300_plus_300（含托管真人座位的被刀率、被放逐率之比与首日方案种类）。
- 基线对照：`python -m werewolf sim --compare`；sim/test_selfplay.py::test_model_modes_with_fake_models、
  test_compare_without_keys_skips_the_model_mode。
- 真模型冒烟：llm/test_smoke.py（替身版常驻；真模型用 `python -m werewolf smoke` 与 `sim --mode full`）。
- 浏览器整局：web/test_demos.py（演示文件逐条校验）；同一条线上流的体检见 acceptance/test_full_games.py。
  Playwright 实机不在这套测试里。
- NpcAgent 本身：sim/test_npc.py。集成暴露的问题的回归：sim/test_regressions.py。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_index_entries_exist() -> None:
    doc = __doc__ or ''
    seen = 0
    for line in doc.split('\n- '):
        current: Path | None = None
        for m in re.finditer(r'[a-z_]+/test_[a-z_0-9]+\.py(?:::[a-z_0-9]+)?|(?<![/:\w])test_[a-z_0-9]+(?!\.py)', line):
            tok = m.group(0)
            if '/' in tok:
                rel, _, fn = tok.partition('::')
                current = ROOT / rel
                assert current.is_file(), rel
                if fn:
                    assert fn in _functions(current), tok
                    seen += 1
            elif current is not None:
                assert tok in _functions(current), (current.name, tok)
                seen += 1
    assert seen >= 30
