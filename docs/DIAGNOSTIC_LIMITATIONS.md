# Diagnostic limitations

FixMyTech is decision support. The UI and reports state these limits too.

## Cannot be confirmed remotely

| Fault | What FixMyTech can do | What still needs a technician |
|---|---|---|
| Cracked/damaged display, dead pixels | colour test (customer judgement), touch grid (measured coverage), OS touch registration | panel vs glass damage, connector faults |
| Touch dead zones / ghost touches | touch-grid coverage at test time | intermittent or pressure-dependent faults |
| Speaker / earpiece / microphone | tones (customer hears), mic level (measured), call test | which component, blocked grille vs failed part |
| Camera | stream starts (measured), image clarity (customer judgement) | which lens/module, focus/stabilisation hardware |
| Charging port | connector stability while moving the plug (measured), charging state | wear vs debris vs board fault; fast charging |
| Battery health | OS health flag, capacity estimate where exposed, symptoms | load test; capacity on phones that hide it |
| Liquid damage / corrosion | questions only | internal inspection (stops questioning and recommends it) |
| Water-resistance seals, antennas, signal quality, mainboard faults | none | bench diagnosis |
| Vibration motor, buttons, sensors | browser/manual tests produce notes | repair diagnosis (no fault category in the taxonomy) |

## Limits of what is read

* Battery "health" reports gross faults only; capacity comes from the fuel gauge and is often unavailable.
* Charging state is a moment; computer USB ports may be too weak; charge limits pause charging.
* Temperatures are point readings while connected and usually idle.
* Crash history and bug reports cover only what the phone's logs still hold; counts show instability, not cause.
* Browser tests depend on browser support and permissions; camera, microphone and motion need a secure context
  (USB `localhost` forwarding or https). Unsupported tests are recorded as not run.
* **No physical phone has been tested yet.** Parsers were written against documented formats and recorded samples;
  unrecognised formats produce `error` observations, never guessed values.

## Limits of the reasoning

* Rule weights and thresholds are expert-set, not learned or calibrated; scores rank and are not probabilities.
* Customer observations can be mistaken; conflicts are shown, not hidden.
* Nine fault categories; anything else surfaces as `insufficient_evidence` with a bench-diagnosis recommendation.
* The adaptive questionnaire was compared with a fixed baseline only in simulation on developer-written personas.
* No ML model influences rankings (no real labelled data).

## Limits of prices and money

* Real prices exist only for Samsung phones in India (official parts + labour, before GST); everything else shows
  `insufficient_pricing_data`. Samsung data needs legal approval before production display.
* Official prices may differ from independent shops and from the final service-centre quote.
* No currency conversion; depreciation, fees and horizon are visible, editable assumptions.

## Compatibility and safety

* Android only, via USB debugging authorized by the customer. No root, unlock, settings changes or app installs.
  The only state-changing actions (port forward, opening the test page, writing a bug report on the PC) need an
  explicit request and are reversible.
* Thermal check needs Android 10+; other checks degrade per device.
* iOS: manual questions and the phone-browser tests (where Safari allows them) only.
* FixMyTech never asks customers to open devices, puncture or stress batteries, or expose phones to liquid.
