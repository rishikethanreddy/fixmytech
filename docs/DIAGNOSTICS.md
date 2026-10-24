# Diagnostics

Pipeline (each stage is a separate module; evidence collection, inference and reporting never mix):

1. **Identify** the device and its capabilities (agent `/devices/{serial}/summary`, capability registry).
2. **Collect** read-only observations (15 default checks) and, with separate consent, a bug-report summary.
3. **Flag missing/unreliable measurements**: any non-`ok` check is unavailable evidence, never "healthy".
4. **Build candidates** by firing documented rules over facts with provenance.
5. **Select follow-up questions and hands-on tests** that best separate the remaining candidates.
6. **Update evidence** after every answer or test result (diagnosis is recomputed from all stored facts).
7. **Rank** faults with ranking scores and support levels.
8. **Explain** supporting, contradicting and missing evidence, plus the basis for each ranking.
9. **State remaining uncertainty** in words.
10. **Recommend one next step**: safety advice > a discriminating test > a question > self-help > quote > inspection.

## 1. Device checks (`core/capabilities.py`)

Every observation records `check_id`, `timestamp`, `status`, scalar `value`, `source` command, `evidence_kind`
(`measured` / `system_reported` / `inferred` / `unavailable`) and `limitations`. All commands are fixed, allowlisted
and read-only (`agent/adb.py`); see https://developer.android.com/tools/adb.

| Check | Source | Kind / quality | Key limitation |
|---|---|---|---|
| `device.identity` | `getprop` (allowlisted keys) | system / high | OEM model codes |
| `device.integrity` | `getprop ro.boot.verifiedbootstate`, `ro.boot.flash.locked`, `ro.build.tags`, `ro.debuggable` | system / medium | not exposed by all OEMs |
| `battery.status` | `dumpsys battery` | measured / high | "health=good" says nothing about wear |
| `battery.capacity` | sysfs `charge_full*`/`cycle_count`, fallback `dumpsys batterystats` capacity lines | system / medium | often blocked; fuel-gauge estimate |
| `charging.state` | `dumpsys battery` | inferred / medium | computer USB may not charge; charge limits |
| `storage.data` | `df -k /data` | measured / high | - |
| `storage.io` | `dumpsys diskstats` | measured / low | single short benchmark |
| `memory.ram` | `/proc/meminfo` | measured / low | RAM is full by design |
| `thermal.status` | `dumpsys thermalservice` (API 29+) | measured / medium | point in time |
| `stability.crashes` | `dumpsys dropbox` headers + `date` | system / medium | limited retention; counts only |
| `stability.boot` | `/proc/uptime`, boot-reason props | system / medium | vendor-specific |
| `camera.enumeration` | `dumpsys media.camera` | system / low | not image quality |
| `input.touchscreen` | `dumpsys input` | system / low | not touch accuracy |
| `sensors.inventory` | `dumpsys sensorservice` (types only) | system / medium | listed ≠ working |
| `connectivity.basic` | `settings get global …`, `getprop gsm.*` | system / low | no signal quality |
| `bugreport.summary` (opt-in) | `adb bugreport` → counted on the PC → deleted | system / medium | logs cover hours to days |

Bug reports (https://developer.android.com/studio/debug/bug-report) can contain personal data. FixMyTech asks for
separate consent, writes the report to a private temp folder on the customer's computer, counts a fixed list of
markers (`FATAL EXCEPTION`, `ANR in`, tombstone headers, low-memory kills, watchdog, thermal shutdown, kernel panic)
and deletes the file. Only the counts are sent.

## 2. Guided hands-on tests (`data/guided_tests.json`, 12 tests)

USB cannot inspect most hardware, so FixMyTech guides safe tests. Results carry provenance:
`outcome` is the customer's judgement (`user_observed`) unless measured by the agent; numeric `measures` are
`phone_measured` (browser on the phone) or `agent_measured`. `not_run` (unsupported browser, permission denied,
skipped, no hardware, inconclusive) is recorded but never becomes pass/fail evidence.

| Test | Method | Measured | Targets |
|---|---|---|---|
| Screen colours / dead pixels | phone browser (full-screen colours) | - | display |
| Touchscreen coverage | phone browser (5×8 grid, touch events) | cells touched, coverage %, max simultaneous touches | display |
| Loudspeaker | phone browser (Web Audio tones) | - | audio |
| Microphone | phone browser (`getUserMedia`, level only, nothing recorded) | peak dBFS | audio |
| Rear / front camera | phone browser (`getUserMedia` preview, nothing captured) | stream started, resolution | camera |
| Vibration | phone browser (`navigator.vibrate`) | - | (note only) |
| Motion sensors | phone browser (`devicemotion`) | events, gravity m/s² | (note only) |
| Charging connector | agent: `dumpsys battery` every 1 s for 45 s while the plug is moved | plug events, ADB dropouts, charging seen, level delta, max temp | charging, battery |
| Earpiece call, buttons, mobile data | manual instructions | - | audio / (note) / connectivity |

The phone opens the test page either (a) via the agent: `adb reverse tcp:PORT tcp:PORT` plus opening
`http://localhost:PORT/p/<token>` in the phone's browser. `localhost` is a secure context, which browsers require for
camera, microphone and motion access. Or (b) by typing a link (camera/mic then need the site on https). The link token
is scoped to listing and submitting that session's phone tests and expires after 30 minutes. A battery at or above
80% with charging paused makes the connector test `inconclusive`, not a pass. No test asks the customer to open the
device, stress the battery or expose it to liquid.

## 3. Facts, rules and fusion

Fact namespaces: `obs.*`, `obs_status.*`, `ans.*`, `device.*`, `derived.*`, `test.<id>.<outcome|measure>`,
`test_status.*` (`domain/facts.py`). **113 rules** (`data/rules.json`) map facts to weighted effects on **9 faults**,
with rationale, limitations, confirmation flags and optional info/safety notes (note-only rules cover findings
without a fault category, such as a dead vibration motor).

Ranking: rules in the same `group` read the same evidence, so only the strongest supporting and strongest
contradicting item per (fault, group) counts. Score = sum of counted weights: a **ranking score, not a probability**.
Support: strong ≥ 5 from ≥ 2 groups; moderate ≥ 3; weak ≥ 1. Each candidate lists supporting, contradicting and
missing evidence (checks, questions, tests), suggested confirmation tests, a plain-language ranking basis, and whether
it rests on device readings, hands-on tests or answers only. Outcomes include the explicit `insufficient_evidence`
and `no_fault_indicated` (never stated as proof of health). Weights are expert-set and not calibrated.

## 4. Adaptive questions (`domain/questionnaire.py`, 25 questions)

Default strategy `rule_utility`: `priority/100 + Σ activity(target)` (top-3 in-play faults 1.0, other in-play 0.5,
others 0.15). Alternative `expected_entropy`: picks the question with the largest expected drop in the entropy of
softmax(score/2) over faults + "unknown", weighting each answer option equally (an explicit assumption).
Baseline `fixed_order`: every visible question in definition order.

Stopping: budget (12); safety concern (any safety note, e.g. swollen battery or recent liquid); professional
inspection (liquid damage moderately supported); leader strong and ≥ 3 points clear after ≥ 3 answers; no visible
questions; nothing informative left.

**Evaluation** (`python -m fixmytech.domain.question_eval`, 11 developer-written personas, simulation only):

| Strategy | Intended fault ranked top (moderate+) | Mean questions | Max |
|---|---|---|---|
| fixed_order | 11/11 | 6.00 | 8 |
| rule_utility (default) | 11/11 | 4.91 | 8 |
| expected_entropy | 10/11 | 4.36 | 7 |

This measures questioning efficiency on the rule base, **not real diagnostic accuracy**. Running it exposed and fixed
an under-weighted storage rule (an OS "storage full" warning reported by the customer now counts as strong evidence).
Entropy selection is kept as an option; it is not the default because it missed one persona.

## 5. Repair pricing and decisions

See [PRICING_SOURCES.md](PRICING_SOURCES.md) for prices. The decision engine (`domain/decision.py`) compares
repair-and-keep vs sell-and-replace over a horizon, and sell-as-is vs repair-then-sell for cash now (time bases never
mixed). It applies selling fees once per sale, propagates ranges with interval arithmetic, reports
`clear` / `uncertain` / `too_close` / `incomplete`, lists sensitivity drivers, explains each scenario in plain words,
warns on implausible inputs, and states that repairing to resell is not a guaranteed gain.
