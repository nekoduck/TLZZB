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

## 第三、四批之后的接口变动（wave 3 / wave 4）

权威仍是代码与各包的 `__init__.py`；这里只列与上面几节不一样、或新增的东西。出处：`docs/wip/kernel-fix.md`、`game.md`、`speech-diverge.md`、`content-tune.md`、`polish.md`。

### kernel

- **底线匹配** `kernel/bottom.py`：`block_matches(state, defs, owner, block, verb, params, …)`、`card_blocks(defs, c)`。`actions.blocked_by_bottom`、`speech.say_blocked`、`speech.request_blocked` 共用它。一条 block 写出来的键都对得上才算命中：`act` 对 `attack` 看 `params.intent`（缺省 `injure`），对 `say` 看言语行为；`art`、`item`、`exit` 等与同名参数比（K1、K2）。
- **同门** `mood.sect_roles(defs, state)`、`mood.same_sect(defs, a, b, state)`：只认 `world.yaml` 的 `request.sect_roles`；没写时退回「占着地盘的身份」（K3）。
- **记账用的效果**（K4、K18、K20，`effects.EFFECT_SHAPES` 里有形状）：`{request: {frm?, to, kind?, action|about, action_class?, status?, condition?, id?, …}}`、`{superior: [c, boss|null]}`、`{dequeue: c}`、`{turn: n}`、`{offer: [...]}`、`{fact: {id, text, tags?, gossip?}}`、`{mark_char: [c, 字段, 值?]}`（只许 `reflect_mark`、`last_replan` 这类记账字段）。
- **timeflow** `run_due_scheduled(state, defs)` 公开（K5）；world 不再借用 `_run_scheduled`。
- **resolve** `apply(state, defs, action, speech=None, *, source, offscreen=False)`；`mark_offscreen(state, first_id)`（K6）。world/offscreen 全部走 `offscreen=True`。
- **perception** `ViewEvent` 新增 `actor_id`、`target_ids`、`loc`（K7）；`event_targets(event)`；`item_visible(...)`（K9：别人身上看不见的东西不进 `steal` 的候选）。
- **事件动词** 跟着别人换了地方的事件动词是 `follow_move`；`follow` 只表示「开始跟着某人」，不换地方（K8）。
- **actions** `edge_text(state, defs, exit)`（走出地图的出口写成「离开这一带」，K10）；`build(state, defs, actor, 'say', …)` 与 `build_speech_wait(state, defs, actor)`：只说话不动 = 1 刻的 `wait` 加一句 `Speech`（K12）。
- **checks** `roll_against(state, kind, chance, *, adverse=False)`：`adverse` 的判定在走运旗标下取「不成」（K11）。
- **speech** K14–K19：不从自己的话里学专名；说得出细节算弱凭据；每种言语行为都判断言；替别人求情；`gist_for` 不漏英文动词。新增 `asked_text(action, speaker=None)`（所求之事的说法：带了 `desc` 用它，否则 `ask_phrase`）。`accept()`：所求是已经散了的情境里的一招时，请求直接 `expired`，不插步、不记许诺；许诺的 `text` 是答应了的那件事。玩家自己打招呼的缺省要旨是「报了自己的姓名」。
- **effects**（P2）`setpiece_gone(state, action)`、`void_setpiece_requests(state)`：`close_situation` 时调用——求人在那场情境里出的一招再也做不成，没答复的请求过期，答应了没做的 `req_<id>` 步作废、请求过期、连带的许诺静默了结（不扣信任）。
- **danger**（T3）`lethal_deadline(state, c)`、`deadline_danger(state, c)`；`danger()` 以它为下限：身上到期致死的状态（`poisoned*`）不足一日 → 至少 1，只剩两个时辰 → 至少 2。
- **K13** 玩家的致命动作第一次只出警示，这一回合要说的话也不结算。
- **已知缺口** `invariants.check` 的 `pending_before` 不含「本次结算里才插进来的步」：一句答应加答应的那件事放在同一次 `resolve.apply` 里会整体回滚（「event … comes from finished or void step req_…」）。调用方的做法：先结算话（1 刻 `wait` 载着），再结算事（`minds.apply_decision`、`world.offscreen.reply` 都这样）。

### llm

- `await llm.jev.ask(kind, state, questions, *, hints=None, escalated=False, timeout_s=None)`：限时交给 `timeout_s`，到点抛 `JevTimeout`（既是 `JevError` 也是内置 `TimeoutError`），录制成一条错误行，重放不错位（L1）。`except TimeoutError` 要写在 `except LLMError` 之前；不要在外面套 `asyncio.wait_for`。
- 路由类别新增 `narrate`（C3）、`phrase`（C4）、`pipeline`；`route.uses_model(settings, category)`（L2）。
- `Replayer` 在同一种 `kind` 里按请求指纹对号，不看 `asyncio.gather` 的完成先后（L3）。
- `schemas.CardChoice`：`situation` / `did` / `para`（L4）。

### minds

- `order(state, decisions, events=())`：话冲着他说的人最先，其余当事人次之，旁观者最后；同级按 initiative。会让情境散场的一招（`MindDecision.closing`）整体排在不散场的招之后。`addressed(state, events, loc)`、`closes_situation(state, defs, action)`。
- **答应即照做** `agreed_action(state, defs, actor, speech)`、`keep_word(state, defs, decision)`：在场的抉择里话是「答应」、所求之事在他的合法清单里，动作就换成那件事（「住手」类的请求是等）。`decide_one(..., offscreen=True)` 不换（离场的答复由 world 一次结算）。
- `apply_decision(state, defs, d)`：结算前先核 `speech_stands(state, defs, d)`（指名的听话人还在、要答复的请求还开着），不成立的话不说、也不进 `violations`；「答应 + 那件事」分两次进内核。
- `talk.step_ask(state, c, act)`：★ 的那一步写明了所求之事（`do.ask` / `do.cls`）就照它求；`talk.describe_do(do, speaker=None)`；`talk.star_speech` 指名的人不在跟前时返回 None（不对满屋子说）。
- `minds.reflect.nightly(llm, state, defs)` 由 game 在玩家歇够两个时辰或跨日时调用（I5）。

### storyteller

- `Option(action, tag, score, rule, thread_ids, speech, id, signature, desc)`：`speech` 不空时这是一个开口的选项，`action` 是 1 刻的 `wait`；`id` 在这一组里唯一（J4、C4 对号），`signature` 用来去重与记「上回合选过」，`desc` 是一句中性的意图（实体写成 `⟦id⟧`）。结算：`resolve.apply(state, defs, option.action, option.speech, source='player')`。
- `storyteller.speaking`：开口的候选（招呼、打听、告知、示警、求情、相求、相劝、安慰、答复）；`speech_signature`、`speech_pair`、`ask_key`。相劝只在那一招此刻做得出、不伤人、不是冲着玩家自己来的时候才给。
- `note_choice(state, defs, option)`；`note_action(state, defs, action, speech=None)`（自由输入只说了一句话时把那句话一并传来）。
- `threads.recent_closed(state, defs, n, *, kinds=None)`：最近了结的牵挂，同一句只留一条。
- 分岔只由 `world.baseline.check` 认定（`diverge` 效果）；`storyteller.chronicle` 只读 `state.divergences`（I2）。情境的年谱句：`chronicle_open` / `chronicle_close` → 没写就借 `title` → 再没有才用通用句。

### narrator

- `phrase_options(llm, state, defs, options, *, avoid=(), report=None)`：开口的选项按言语行为取模板句（低声劝……、问一问……、求……），给 C4 的事实句由 `templates.speech_fact` 写。`option_template(state, defs, action, *, variant=0, avoid=(), speech=None, desc='', signature='')`。
- `templates.speech_sentence(actor, speech_view, *, by_player=False)`：把一句话写成通顺的一句叙述（要旨里的「对方」写成听话的人，语气写成状语）。模板叙述用它，不再照抄内核的记录体事实句；同一回合里同一句只写一次。
- J5 的限时走 `jev.ask(timeout_s=)`。`player_brief` 的身份标签按 `role:<id>` 取。

### world

- `world.tick(llm, state, defs, *, decide=None, rules_only=False)`；`world.onscene_elapsed(...)`（I6：在场者「等」的进度，game 在 `pass_time` 里调用）；`world.baseline.check(state, defs)`、`divergences_report(state, defs)`。
- `world.offscreen`：`say` 的一步指名的人不在跟前 → 这一步作废（不调内核）；`reply()` 遇到「话 + 非等的动作」分两次结算。
- `world.goals` 的 `safe_place` / `away_from`：避开对头正要去的地方；两个互相敌视、同在一处的人里 id 小的先挑，另一个避开他会挑的那处；同样近的几处各人取舍不同（确定性）。

### game 与命令行

- `tianlong.game.Game`：`await Game.new(settings, *, seed, mock, player_name=None, run_id=None, replay_dir=None, debug=False)`、`await Game.load(settings, save_name, *, mock)`、`game.start()`、`game.turn(raw)`（都是界面事件的异步迭代器）、`await game.aclose()`；`game.last_turn`（`TurnRecord`）、`game.last_view`、`game.latencies`、`game.run_dir`、`game.save_dir`。
- `python -m tianlong play | pipeline | baseline | eval`，见 README 与方案 10.4。
- 测试驱动：`tests/game/conftest.py` 的 `new_session(settings, seed=…)` → `Session.send(raw)`。
