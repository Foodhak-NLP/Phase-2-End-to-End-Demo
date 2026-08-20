# Post-op Phase-2 30-day simulator

Simulates the full Phase-2 arc for one patient: healthy → surgery → Phase-2 entry →
CQL locks one action space for 30 days → daily recipes with weekly adherence control
→ day-30 review.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens on <http://localhost:8501>.

## What you can drive

| Control | Effect |
|---|---|
| Age / sex / height / weight | Selects the demographic bucket in `Macro-Details.xlsm`, which sets the daily macro targets |
| Surgery type + severity | Which biomarkers get disrupted and by how much. Severity 1.0 reproduces the staging test case |
| Doctor targets | Which biomarkers to pursue. Determines the cluster set, which determines the action space |
| Adherence | Chance the patient eats each prescribed meal |
| Days logged | Chance the day is recorded at all — unlogged days are *unknown*, not zero intake |
| Responder type | Hidden in reality; exposed here so you can see the spread it causes |
| B / L / D | Recipe options per meal (default 2 / 2 / 4) |
| Thresholds | Step-down / step-up gates. Workbook says 0.55 / 0.80; staging runs 0.65 / 0.85 |

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `sim_engine.py` | 30-day block: weekly reviews, intensity gate, biomarker steps |
| `sim_patient.py` | Healthy baseline, surgery disruption profiles, reference ranges, action resolution |
| `sim_macros.py` | Reads `Macro-Details.xlsm` into a macro-profile lookup (stdlib only, no openpyxl) |
| `sim_recipes.py` | Offline recipe composer with ingredient → biomarker mechanisms |
| `sim_reasoning.py` | Day-30 narrative via Claude, with a deterministic fallback |
| `postop_sim.py` | CLI harness that also drives the live staging services |

Constants are imported from `Postop-CQL-Service/Staging/config_discrete.py` rather than
copied, so the action space, cluster mapping and responder multipliers stay in sync.

## The day-30 narrative

Set `ANTHROPIC_API_KEY` to get the Claude write-up (Claude Opus 5, adaptive thinking,
server-side refusal fallback enabled). Without a key the panel falls back to a
rule-based summary built from the same data, so it is never empty.

## What is real and what is simulated

**Real** — the action-space selection rules, cluster coverage and attenuation, the
adherence formula (`clip(1 − min(|actual−target|/target, 1), 0, 1)`), the weekly
intensity gate, the macro targets (straight from the dietitian workbook), and the
platform's own `MACRO_BOUNDS` checks.

**Simulated** — the patient's eating behaviour only.

**No biomarker values are predicted.** The app shows the phase-2 entry labs and stops
there; day-30 values come from an actual blood draw. An earlier version projected them
from `conformal_v9.predict_next_labs_proxy` (4%/30d) scaled by intensity and adherence
multipliers written for this simulator. That was removed: the base constant is a
self-described *"conservative proxy, not a medical outcome model"*, the two multipliers
exist nowhere in the platform, and the result was indefensible if a clinician asked
where a number came from.

Retraining on real paired data (foods, adherence, day-0 and day-30 labs) is the route to
a defensible estimate — and even then the output is block-level with an interval, not a
daily curve, because that is the resolution the labels have.

## PubMed links

Citations come from `biomarker_effects[].pmids` in `ingredient_master.jsonl` — a real
paper list for each food-biomarker pair. Coverage is 13,007 of 13,007 effects, median
6 PMIDs each.

Where an effect lists several, the one shown is the least reused across other pairs
(`sim_foods._pmid_breadth()`), so a paper cited for one pair is preferred over one cited
against hundreds.

Do **not** reconstruct these by joining `cleaned_relationships.json` record-level
`source_pmids` onto the foods in its `ingredients` list. Those records are subject-level
— the subject is often a dietary pattern and `ingredients` lists the 15-30 foods it
contains — so that join attributes a paper to foods it never studied.

## Known data gaps

These are detected but no longer surfaced in the UI, to keep the demo clean. The
checks still run — see `sim_macros.bound_violations()` and `sim_macros.load()["warnings"]`
if you want them back.

- 15 of 16 workbook action sheets carry no fibre target, yet the Intensity Rules sheet
  requires `Fibre ≥ 0.60` for a lipid step-up. The app substitutes 25 g so the score
  stays computable; fibre adherence is not trustworthy until the sheet carries it.
- The C01 bucket's protein (0.72 g/kg) is below the platform's own `MACRO_BOUNDS`
  floor of 0.8, and its carbohydrate share (60.9%) exceeds the 60% ceiling.
- Biomarkers in safety-layer clusters (microalbumin, free T3) cannot be pursued by any
  action space; the app still flags this when one is chosen as a doctor target.

## Connecting Claude for the day-30 review

The app checks three places, in order:

1. **Environment variable** — `export ANTHROPIC_API_KEY=sk-ant-...` before `streamlit run app.py`
2. **Streamlit secrets** — create `postop-30day-simulation/.streamlit/secrets.toml`:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Add `.streamlit/secrets.toml` to `.gitignore`; never commit it.
3. **In-app** — the Day-30 review tab shows a password field when no key is found.
   Session-only, never written to disk.

Without a key the review still renders, written from the same data by the
rule-based summariser.
