# Wave 3 issue list (from wave-2 reports)

## Kernel (owner: kernel-fix agent)
K1 actions.blocked_by_bottom: BlockDef.act is a declared field, never compared with params.intent → any attack block removes ALL attacks (duan_yu, ma_wude have no attack at all; nanhai_eshen can't injure the weak). Compare act→intent (attack), art (study/teach), and all declared/extra keys.
K2 speech.request_blocked: ignores art / act(intent) keys → over-blocks (teach yijing by zuo_zimu = never; attack-injure request to duan_yu = never). Same matching as K1.
K3 mood.close_roles/band: "same_sect = any shared role" → jianhu_guest makes player/duan_yu/ma_wude "same sect". Only sect/house roles should count — make it data: world.yaml request.sect_roles (list) or role prefixes.
K4 setup/bookkeeping effects missing: create a Request ({request: {...}}), set superior ({superior: [c, boss|null]}), set Character.last_replan, remove from replan_queue ({dequeue: c}), set state.turn / pending_options (game bookkeeping, e.g. {turn: n}, {offer: [...]}), Character.reflect_mark.
K5 timeflow: public run_due_scheduled(state, defs) (world calls private _run_scheduled).
K6 resolve.apply: offscreen=True parameter (world patches Event.offscreen afterwards).
K7 perception.ViewEvent: add actor_id (and target ids) — narrator looks up events_tail.
K8 'follow' verb used both for "you start following X" (no move) and "X follows Y to Z" (move) — split the follow-move event verb (e.g. 'follow_move') so views can tell a location change.
K9 legal_actions lists steal of items the actor cannot see; filter in kernel (visible = on the ground here, mount/props.visible, or actor examined the holder / saw the item / believes a fact mentioning holder+item).
K10 actions._v go via edge exit: desc shows raw id ('往out_east去') → readable text (离开这一带).
K11 lucky flag (D-079) makes adverse checks succeed (custody 5% escape). Add check option adverse: true → lucky makes it fail. (content then marks such checks)
K12 actions.build(…, 'say', …) returns None; offer a build path for pure speech (wait 1 + speech) or document.

## LLM (owner: kernel-fix agent)
L1 jev.ask(..., timeout_s=None): per-call timeout recorded as an error line so replay stays aligned (narrator J5 uses 0.4 s wait_for → cancelled calls unrecorded → replay shift).
L2 route categories: add narrate (C3), phrase (C4), pipeline keys to config/route CATEGORIES.
L3 Replayer: match by request fingerprint within a kind (asyncio.gather completion order differs from call order) — strict but order-insensitive among identical-kind concurrent calls.
L4 llm/schemas CardChoice: fields situation/did/para (pipeline subclassed).

## Content (owner: content agent) — target: M5 gate "--lucky baseline all marks on time"
C1 situations/siege.yaml: news via schedule delay 1 misses people leaving in the same hour → delay 0.
C2 custody offscreen 5% escape → mark adverse (K11).
C3 poison_errand doesn't protect the messenger; camp_capture recaptures him → camp_pass status from poison_errand/let_go/dy_free_zhong, checked by camp_capture.
C4 challenge's vetted (96 ticks) exempts zhong_ling from camp_capture → diao_bite slips; separate cooldown statuses.
C5 ambush_mu: rui_popo rp_lay_wait vs duan_yu crossing on highroad → timing.
C6 ev_cow_wuliang orders zuo_zimu who doesn't obey the envoy (order only for subordinates) → use threaten/tell; xs/zz_receive_envoy go to jianhu_hall while envoy is at camp.
C7 gg_save_face requires guest_laughed, but bout_laugh offscreen never sets it.
C8 baseline m_zhong_freed / m_duan_cured hold at the opening → make predicates require the event happened (flags).
C9 world.yaml labels for roles (role:<id> → Chinese) — /状态 shows no identity.
C10 lexicon banned_game: 回合 and 经验 are ordinary words (keep 经验值); '大理' also hits 大理石 (ok, minor).
C11 facts F_duanchang_antidote_sikong text refers to duanchang_san → ⟦duanchang_antidote⟧.
C12 situations: add title / chronicle_open / chronicle_close for threads & chronicle.
C13 valley gate knocking has no mechanic (gate_hammer in a feature; knock isn't a verb) → model as use gate_hammer on the gate feature / situation; tinder & waterskin use semantics.
C14 m_lingbo (duan_yu learns lingbo) and m_ambushed missed in lucky baseline.

## Integration (owner: game agent)
I1 storyteller options are body actions only — add speech options (Option.speech): 人物 (greet/ask/request/tell/comfort/plead to present soul/named people), 介入 (劝/示警 to people in a running situation, e.g. 劝他赔个不是 — PLAN 2.1 example), C4 wording must handle speech options.
I2 divergence detection implemented twice: world.baseline.check (flag __baseline__) and storyteller.chronicle.check_divergences (diverge effect). Keep ONE detector (world, per 8.1 step 5) writing via the diverge effect; storyteller only displays + chronicle entries.
I3 game.py per PLAN 4.2 + ARCHITECTURE 8: turn loop, pass_time hour-by-hour with world.tick + interrupts, micro rounds, end_of_turn, threads/chronicle/pacing order, options + C3/C4 in parallel, autosave, checkpoints (day start, before danger 3), logs/transcript, commands dispatch, /心愿, endings + epilogue, --debug cheat commands (for 11.4 '要人身死' and '玩家变强'), budget pause/ask, replay.
I4 __main__.py CLI: play / pipeline / baseline / eval.
I5 nightly reflection trigger (player rests >= 16 ticks or day changes).

## Added from parser/minds reports
K13 resolve._apply: on a lethal-warning turn the utterance is dropped too. Keep (scene unchanged per 2.4) but document; narrator gets the warning only.
K14 perception.learn_terms_from_event learns names from the PLAYER's own gist ⟦id⟧ → never learn terms from one's own speech.
K15 speech.gather_evidence: weak evidence 「说得出当事人才知道的细节」 missing — a 'true' claim relevant to the request (in the speaker's own facts) should count as weak evidence (PLAN 4.5 example: 我熟山路 → +10).
K16 speech.process judges claims only for tell/warn → judge claims for every act (request/plead/… too; false_self trust -15 applies).
K17 speech.available_acts: plead only when someone is hostile to the speaker → also allowed when the addressee is hostile to someone present / holds a captive (求情 for others).
K18 effect to add a fact: {fact: {id, text, tags?, gossip?}} (setup/tests).
K19 speech.gist_for: '想让对方{verb}' leaks raw English verb → use the verb's Chinese label.
K20 effect for Character.reflect_mark (and last_replan) — e.g. {mark_char: [c, field, value]} restricted to bookkeeping fields.
C15 lexicon banned_modern: add more modern words (可乐, 汽水, 手表, 子弹, 飞机, 火车, 汽车, 电视, 网络, 微信, …).
C16 lean setpieces stay ★ after their beat (challenge 'question' has no pre; bout_laugh feint_win after bout_over) → add pre guards.
I6 on-scene agenda wait steps lag (only progress when picked as reactor and waiting 1 tick while player spent 8) → after pass_time(n), add n ticks of progress for on-scene chars whose current step is a wait step (progress effect) — helper in world (e.g. world.onscene_elapsed) called by game.
