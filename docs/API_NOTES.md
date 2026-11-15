# 已实现模块的接口备忘（第一批）

由第一批代理的交付说明整理。权威仍是代码本身；这里是索引。

## kernel 与 content 的公开接口

## 2. Public API other modules will call
- **rng**: `roll(state)->1..100`, `uniform(state)->[0,1)`, `pick_weighted(state, weights)`.
- **refs**: `render(text, state, defs, viewer=None)`, `display_name(...)`, `fill_roles(text, roles, self_id)`, `refs_in(text)`, `lookup(state, defs, id)`. Features are written `⟦loc#feature⟧`.
- **Ctx** (`state.Ctx`, a dataclass): `Ctx(self_id=None, roles={}, situation=None)`, with `.with_roles(**kw)`.
- **predicates**: `holds(state, defs, pred, ctx=None)`, `resolve_id`, `loc_of`, `flag_key`. Errors are `KernelError` and `PredicateError`. `PREDICATE_SHAPES` drives the validator.
- **effects**:
  - `apply_effects(state, defs, effects, ctx=None, *, source, step=None, offscreen=False) -> list[Event]`
  - `EffectError`, `EFFECT_SHAPES`
  - Situation helpers: `open_situation(run, kind, loc, roles)`, `close_situation(state, defs, sid)`, `bind_roles(...)`, `close_finished(state, defs)`, `tick_situations(state, defs)` (call once per turn: increments `turns`, closes on `max_turns`)
  - Agenda helpers: `insert_step(state, c, Step)`, `set_step_state(state, c, step_id, state)`
- **events**: `emit(state, defs, *, actor, verb, text, source, params=None, outcome='ok', loc=None, speech=None, step=None, offscreen=False, loud=False, hidden=False, kinds=None) -> Event`, `kind(k, who, by)`, `add_kinds(...)`, `queue_replan(state, cid)`.
- **actions**:
  - `ActionInst` exactly as ARCHITECTURE 2.2; `VERBS` adds `struggle`.
  - `legal_actions(state, defs, actor, *, for_model=False)`
  - `build(state, defs, actor, verb, params) -> ActionInst|None` — any duration up to 12 hours or 96 ticks.
  - `is_legal`, `matches_do(action, do, ctx, *, state=None, speech=None)`, `path(state, defs, actor, frm, to) -> list[Exit]`, `step_ready`, `signature`.
- **resolve**:
  - `apply(state, defs, action, speech=None, *, source) -> list[Event]` — speech first, then the action; full rollback on failure.
  - `run_effects(state, defs, effs, ctx=None, *, source, step=None, offscreen=False)` — the transactional wrapper for world and storyteller.
  - `settle_offscreen(state, defs, situation_id_or_kind, *, loc=None, roles=None)` — the situation's offscreen table, then close.
  - `Snapshot(state)` / `.restore(state)`.
- **speech**: `speech_options(state, defs, actor) -> SpeechOptions(acts, facts, requests: [RequestView], star)`, `available_acts`, `classify_request`, `score_request`, `VERDICT_TEXT`.
- **beliefs**: `hearsay`, `hear`, `claim_verdict(state, speaker, ref, *, self_claim=False)`, `hear_claim` (returns a `Judgement` whose `.text` is the "你的判断…" sentence), `judge_text`.
- **perception**: `witnesses(state, defs, loc, actor, *, loud, hidden)`, `player_view(state, defs, events) -> PlayerView(clock, location, location_desc, scene_mode, events, heard, present, items_here, names, body, danger, ended)`, `learn_terms_from_event`, `visible_people`, `conscious`.
- **memory**: `write`, `write_event`, `salience_for`, `table(defs)`.
- **mood**: `on_event`, `decay`, `adj_rel`, `adj_mood`, `band(defs, state, a, b)`, plus the band texts `trust_text`, `affinity_text`, `fear_text`, `regard_text`, `relative_regard_text`, `mood_text`, `rel_text`.
- **growth**: `power(state, defs, c)`, `neili_band`, `flee_bonus`, `has_evade`, `absorb_amount`, `study_amount`, `practice_amount`, `apply_progress`, `can_teach`, `blocked_reason`.
- **checks**: `flee_chance`, `hide_chance`, `steal_chance`, `climb_chance`, `fall_survive_chance`, `death_save_chance`, `clamp`, `roll_against`.
- **conflict**: `fight`, `caught`, `outcome_effects`.
- **timeflow**: `advance_time(state, defs, ticks) -> list[Event]`, `clock_text(tick)` (e.g. 第一日 辰时), `time_of_day`, `cn_num`, `wake`.
- **danger**: `danger(state, defs)`, `is_hostile`, `hostiles_toward`, `current_step`, `intends_kill`, `attack_lethal`, `item_lethal`, `needs_warning(state, signature, lethal)`, `mark_warned`, `buffered`.
- **invariants**: `check(state, defs, *, new_events, tick_before, pending_before, able_before) -> list[str]`, `liveness(state, defs) -> list[CharId]`.
- **content**: `load(content_dir, *, player_name=None, seed=0) -> (Defs, WorldState)`, `content_hash(root)`, `validate(defs, state) -> list[str]`.
- **Contract with world**: `resolve.apply` itself advances the current agenda step of an on-scene actor whose action matches the step's `do`. World should only advance off-scene actors' steps.

## kernel 的已知缺口（第一批交付时）

## 5. Known gaps / TODOs
- The 6.12 "someone literate reads the manual to you" path is not implemented.
- "Discovering a lie" (`deceived`) has no trigger other than a `false_self` claim being caught on the spot.
- Situation turn counting depends on the caller invoking `effects.tick_situations` once per turn (world or game).
- A successful steal is seen only by the thief.
- `legal_actions` lists stealing or examining items other people carry even if the player can't see them. Storyteller should filter these out of the options.
- `listen` reports only whether neighbouring places have voices, plus whisper fragments nearby.
- validate does not check `goals.yaml` (format still undecided). For routines and opportunities it only checks the `when`/`pre` predicates and effects it recognises.

## llm 与 config 的公开接口

### (2) Public API other modules will call
- **Config:** `load_settings(path='config.toml', *, env_file=None) -> Settings`
  - `Settings.jev_api_key()`, `.jev_key_source()`, `.claude_api_key()`, `.path(name)`. Key values are read from the environment at call time and never stored in `Settings`.
  - Sections: `jev.thresholds`, `route`, `models[kind]`, `limits`, `cache`, `prices`.
- **Facade:** `make_llm(settings, *, mock: bool, run_dir: Path | None, replay_dir: Path | None = None, on_budget_warn=None, strict_replay=True) -> LLM`
  - `LLM` has `.jev`, `.claude`, `.recorder`, `.ledger`, `.mode(category)`, `.jev_available`, `.claude_available`, `await .aclose()`.
- **Jev:** `await llm.jev.ask(kind, state, questions, *, hints=None, escalated=False) -> dict[str, JevAnswer]`
  - Answers are dict subclasses, so both raw JSON reads and attribute reads work.
  - `ChoiceAnswer`: `.choice`, `.top`, `.top_p`, `.margin`, `.confidence`, `.probabilities`, `.ranked()`, `.p(opt)`
  - `ScoreAnswer`: `.score`, `.level`, `.levels`, `.margin`, `.confidence`, `.normalized()`
  - `NoulAnswer`: `.noul`, `.yes(th)`, `.confidence` (= |2p−1|)
  - Kinds `J1`…`J6`; sub-kinds such as `J2a`/`J2b` are accepted.
- **Claude JSON:** `await llm.claude.call_json(kind, system_blocks, user_text, schema, *, mock_default=None, escalated=False, ttl=None) -> (obj, CallMeta)`
- **Claude streaming:** `llm.claude.stream_text(kind, system_blocks, user_text, *, mock_text=None, escalated=False, ttl=None, meta_out: list[CallMeta] | None = None)`, an async generator.
- **Batches:** `batch_submit(kind, list[BatchItem(custom_id, system_blocks, user_text, schema=None, mock_default=None)], *, ttl=None) -> str`, `batch_poll(id) -> BatchStatus(.ended)`, `batch_results(id, kind, *, schemas=None) -> dict[custom_id, BatchResult(ok, obj, text, error, usage, cost)]`
- **Errors** (`tianlong.llm`): `LLMError` ⊃ `LLMUnavailable`, `JevError`, `ClaudeError`, `RetryableLLMError`, `LLMRefusal(category)`, `BudgetExceeded`, `ReplayMismatch`. Callers can catch `LLMError` and apply the fallback table.
- **Route** (`llm/route.py`):
  - `mode(settings, category)`
  - `should_escalate(answer_or_group, thresholds, important=False, *, keys=None)`
  - `sample_choice(probs, roll_fn, floor) -> str`
- **Recorder and ledger:** `llm.recorder.annotate(seq, escalated=..., violations=[...])`; `llm.ledger.check()`, `.extend(usd)`, `.summary()`, `.spent`.
- **Schemas** (`llm/schemas.py`):
  - Runtime calls: `Intent` (C1), `Decision`/`SpeechOut` (C2), `OptionsOut` (C4), `ReplanOut` (C5), `ReflectOut` (C6).
  - Pipeline: `EntitiesOut` (P1), `TimelineOut` (P2), `OpeningDraft` (P3), `CardsOut` (P4), `WorldFilesDraft` (P5), `StyleDraft` (P6).
  - `DEFAULT_SCHEMAS[kind]` maps each call kind to its default schema.
- **Mock marker:** `STAR_PREFIX = '（你本来要做的）'`.

## llm 的已知缺口

### (5) Known gaps / TODOs
- The real Claude smoke still needs a machine with a key: `.venv/bin/python scripts/api_smoke.py --skip-eval`.
- `stream_text` does not raise on max_tokens; it only records it in `meta.stop_reason`. The narrator should check it, and should `aclose()` (or use `contextlib.aclosing`) when it stops early, so the call is billed and recorded right away rather than at garbage collection.
- The 30-example set is close to saturated (J2 is 100% everywhere), so the language decision should be revisited on the M3 sets (300 classification examples, the decision set).
- Minimum cacheable prefix lengths are not verified; the cache check uses a system prompt of roughly 5k tokens.
- Batch replay is not implemented.
- config.toml is untouched. Recommended values: `jev.model='jev-1.14.0'`, `jev.lang='en'`, and optionally `[claude] server_fallback = true`.

## 内容里用到的约定（种子内容交付时）

**(2) What other modules rely on** (data contract)
- **Roles in use:**
  - Wuliang sect: `wuliang_disciple`, `wuliang_east`, `wuliang_west`, `wuliang_head`, `lingjiu_vassal`.
  - Shennong gang: `shennong_member`, `shennong_head`.
  - Wanjie valley: `wanjie_family`, `wanjie_master`, `wanjie_servant`.
  - Visitors and outsiders: `jianhu_guest`, `lingjiu_envoy`, `four_evils`, `dali_guard`, `wang_household`, `mu_quarry`.
  - Player and caravan: `ma_caravan_hand`, `ma_caravan_master`.
  - Extras: `herb_gatherer`, `merchant`.
  - Dynamic: `'surname:段'`, `'disciple_of:nanhai_eshen'`, `errand_volunteer`.
- **Extra statuses:** `hidden` (in the opening), and short-lived statuses `vetted`, `ambushed`, `pursued`, `tended`, `chatted`, `lesson_recent`, `custody_recent`, `standoff_recent`.
- **Global flags:** `bout_running`, `bout_over`, `guest_laughed`, `slapped`, `apologized`, `beam_girl_done`, `siege_on`, `siege_began`, `diao_bite_done`, `errand_sent`, `lovers_witnessed`, `zhong_news_delivered`, `envoy_received`, `zhuha_seen_today`.
- **Routine names** the routines file must define: `shennong_guard`, `wuliang_disciple`, `wanjie_servant`, `herb_gatherer`, `merchant`.
- **Opportunity goods:** `chopper`, `extra_rations`, `primer`, all `in_world: false`.
- **Item prop keys used:** `power_mod`, `flee_mod`, `opens`, `literacy_aid`, `important`.

**(5) Known gaps / TODOs**
- **Injury stacking:** the stall described at the top.
- **Replan timing:** the kernel queues a replan on capture or poisoning. If `world/replan` replaces a canon agenda whose current step is still valid, the canon chain breaks; replan should keep an agenda whose current step still holds.
- **Canon `when` that holds at the opening:** `cave_find`, `zhuha` and the generic situations depend on picks to fail binding, so the world module must treat a failed binding as silent.
- **Timing guesses:** the timeline, the travel costs and every baseline `by` are guesses. The canon chain lines up only roughly (for example Duan Yu's arrival at the back trail against the lovers' 32-tick window there).
- **Everything marked 待原文核对** in `docs/content_notes.md` still needs checking against the novel, especially the zhuha episode, Sikong Xuan's fate, the envoy, and who comes from Dali.
- **Empty text fields:** `samples` and `paragraphs` are empty because no corpus is available.
- **Missing extras:** Rui Popo's followers have no entries of their own.
- **Not in my paths:** goals.yaml, routines.yaml and opportunities.yaml belong to other modules.
