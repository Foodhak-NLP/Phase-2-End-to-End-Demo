"""
Post-op Phase-2 30-day simulator.

Healthy patient -> surgery -> Phase-2 entry -> CQL action -> 30 days of
recommendations with weekly adherence control -> day-30 narrative.

Run:  streamlit run app.py
"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import streamlit as st

import sim_engine
import sim_foods
import sim_macros
import sim_patient as P
import sim_reasoning

st.set_page_config(page_title="Post-op Phase-2 Simulator", page_icon="🩺", layout="wide")


def _anthropic_key() -> str:
    """The key for THIS session only.

    Checks the session first, then Streamlit secrets, then the environment. A
    pasted key is held in session_state and never written to os.environ: one
    process serves every visitor of a hosted app, so a key in the environment
    would leak across sessions.
    """
    if st.session_state.get("anthropic_key"):
        return str(st.session_state["anthropic_key"])
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        key = None
    return str(key or os.environ.get("ANTHROPIC_API_KEY") or "")

# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
st.sidebar.title("Simulation setup")

st.sidebar.subheader("1 · Patient")
c1, c2 = st.sidebar.columns(2)
age = c1.number_input("Age", 18, 90, 32)
sex = c2.selectbox("Sex", ["Male", "Female"])
height_cm = c1.number_input("Height (cm)", 140, 210, 174)
weight_kg = c2.number_input("Weight (kg)", 40, 160, 78)

st.sidebar.caption(
    f"BMI {P.bmi(weight_kg, height_cm)} · BMR {P.estimate_bmr(weight_kg, height_cm, age, sex):.0f} kcal"
)

st.sidebar.subheader("2 · Surgery")
surgery_type = st.sidebar.selectbox("Type", P.SURGERY_TYPES, index=P.SURGERY_TYPES.index("cardiac"))
days_since_surgery = st.sidebar.number_input("Days since surgery at Phase-2 entry", 28, 90, 43)

# Surgery disrupts a quarter of the Core-20 panel.
labs = P.phase2_entry_labs(surgery_type, 1.0, sex)
report = P.disruption_report(surgery_type, labs, sex)
suggested = P.suggested_doctor_targets(report)

st.sidebar.subheader("3 · Diet & safety")
diet = st.sidebar.selectbox(
    "Dietary pattern",
    ["none", "pescetarian", "vegetarian", "vegan", "gluten_free", "dairy_free",
     "halal", "kosher", "low_fodmap", "paleo", "ketogenic"],
    index=1,
)
allergens = st.sidebar.multiselect("Allergens", ["egg", "milk", "fish", "shellfish", "soy",
                                                 "wheat", "peanut", "tree_nut", "sesame"])
# Part of the scenario rather than a control; the drug-interaction filter still runs.
medications = ["metformin"]

st.sidebar.subheader("4 · Doctor targets")
target_options = [r["biomarker"] for r in report] or list(P.HEALTHY_BASELINE)
# Keyed on the surgery: without this the widget carries selections across a
# surgery change, because the two panels share markers (hs-CRP and haemoglobin
# are disrupted by all four).
chosen = st.sidebar.multiselect(
    "Biomarkers to pursue", target_options,
    default=[b for b in suggested if b in target_options],
    key=f"targets_{surgery_type}",
)
doctor_targets = {b: suggested.get(b, "reduce") for b in chosen}

# Some disrupted markers cannot be pursued: TARGET_DIRECTIONS gives them no
# direction, so there is nothing for an action space to push toward.
_no_direction = [r["biomarker"] for r in report if r["biomarker"] not in suggested]
if _no_direction:
    st.sidebar.caption(
        f"{len(report)} markers disrupted, {len(suggested)} pursuable — "
        f"{', '.join(_no_direction)} has no target direction in the platform, "
        f"so no action space can pursue it."
    )

st.sidebar.subheader("5 · Run")
st.sidebar.caption("Adherence per review window")
w1 = st.sidebar.slider("Week 1 · days 1–6", 0.30, 1.00, 0.90, 0.01)
w2 = st.sidebar.slider("Week 2 · days 7–13", 0.30, 1.00, 0.70, 0.01)
w3 = st.sidebar.slider("Week 3 · days 14–20", 0.30, 1.00, 0.85, 0.01,
                       help="Also carries days 21–30 — the last review is on day 21")
weekly_adherence = [w1, w2, w3]
adherence = sum(weekly_adherence) / 3
log_rate = 1.0
rc1, rc2, rc3 = st.sidebar.columns(3)
n_break = rc1.number_input("B", 1, 6, 3, help="Breakfast options per day")
n_lunch = rc2.number_input("L", 1, 6, 3, help="Lunch options per day")
n_dinner = rc3.number_input("D", 1, 6, 3, help="Dinner options per day")
st.sidebar.caption(
    "Macros split "
    + " : ".join(str(v) for v in sim_foods.MEAL_RATIO.values())
    + " across B/L/D — "
    + ", ".join(f"{m[:1].upper()} {p:.0%}" for m, p in sim_foods.MEAL_SPLIT.items())
)
with st.sidebar.expander("Thresholds"):
    low_t = st.slider("Step down below", 0.30, 0.80, 0.55, 0.01)
    high_t = st.slider("Step up at or above", 0.60, 0.95, 0.80, 0.01)
seed = 42

run_clicked = st.sidebar.button("Run 30 days", type="primary", width="stretch")

# --------------------------------------------------------------------------
# Resolve action + macros
# --------------------------------------------------------------------------
clusters = P.clusters_for_targets(doctor_targets)
action_id, action_name = P.action_for_clusters(clusters)
action_clusters = P.ACTION_TO_MAIN_CLUSTERS.get(action_id, [])

@st.cache_data(show_spinner="Selecting foods from the ingredient master…")
def _food_selection(targets: dict, diet: str, allergens: tuple, medications: tuple):
    sel = sim_foods.select(
        targets,
        diet=None if diet == "none" else diet,
        allergens=list(allergens),
        medications=list(medications),
    )
    return sel, sim_foods.as_rationale(sel), sim_foods.evidence_table(sel)


wb = sim_macros.load()
row = sim_macros.resolve(wb, action_id=action_id, intensity="maintain",
                         sex=sex, age=age, weight_kg=weight_kg, height_cm=height_cm)

st.title("Post-op Phase-2 Simulator")
st.caption(
    "Healthy patient → surgery → Phase-2 entry → CQL locks one action for 30 days → "
    "daily recipes, weekly adherence control → day-30 review."
)

if not doctor_targets:
    st.warning("Pick at least one doctor target in the sidebar.")
    st.stop()
if row is None:
    st.error(
        f"No macro row in the workbook for {action_name} · {sex} · age {age} · "
        f"{weight_kg} kg · {height_cm} cm. Try a bucket the sheet covers."
    )
    st.stop()

daily_targets, _macro_notes = sim_macros.to_daily_targets(row)
food_sel, food_rationale, food_table = _food_selection(
    doctor_targets, diet, tuple(allergens), tuple(medications)
)

# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------
_scenario = (
    surgery_type, sex, age, height_cm, weight_kg, days_since_surgery,
    tuple(sorted(doctor_targets)), diet, tuple(sorted(allergens)),
    tuple(weekly_adherence), int(n_break), int(n_lunch), int(n_dinner),
    low_t, high_t,
)
if run_clicked or st.session_state.get("scenario") != _scenario:
    st.session_state.scenario = _scenario
    st.session_state.picked_day = 1
    st.session_state.sim = sim_engine.run(
        demographics={"age": age, "sex": sex, "height_cm": height_cm,
                      "weight_kg": weight_kg, "bmi": P.bmi(weight_kg, height_cm)},
        surgery_type=surgery_type,
        days_since_surgery=days_since_surgery,
        baseline_labs=labs,
        doctor_targets=doctor_targets,
        action_id=action_id,
        action_name=action_name,
        action_clusters=action_clusters,
        daily_targets=daily_targets,
        adherence=adherence,
        weekly_adherence=weekly_adherence,
        recipe_counts={"breakfast": int(n_break), "lunch": int(n_lunch), "dinner": int(n_dinner)},
        low_threshold=low_t,
        high_threshold=high_t,
        log_rate=log_rate,
        seed=int(seed),
        food_selection=food_sel,
    )
    st.session_state.pop("narrative", None)

sim = st.session_state.sim

# --------------------------------------------------------------------------
# Sidebar calendar — pick a day to inspect
# --------------------------------------------------------------------------
if "picked_day" not in st.session_state:
    st.session_state.picked_day = 1

_VIEWS = ["Patient & CQL", "Meals & adherence", "Day-30 review"]
if "view" not in st.session_state:
    st.session_state.view = _VIEWS[0]
st.segmented_control("View", _VIEWS, key="view", label_visibility="collapsed")
_view = st.session_state.view

# --------------------------------------------------------------------------
# Tab 1 — Patient & CQL
# --------------------------------------------------------------------------
if _view == "Patient & CQL":
    a, b, c, d = st.columns(4)
    a.metric("Markers disrupted", f"{len(report)} of 20",
             f"{len(report)/20:.0%} · {surgery_type}", delta_color="off")
    b.metric("Locked action", f"C{action_id:02d}")
    c.metric("Clusters covered", str(len(action_clusters)) if action_clusters else "none")
    d.metric("Daily energy", f"{daily_targets['calorie']:.0f} kcal")
    _short = {"nutrition_vitamins_minerals": "nutrition"}
    if action_clusters:
        st.caption(
            "Clusters: " + " · ".join(_short.get(x, x) for x in action_clusters)
        )

    st.subheader("What surgery did")
    st.caption("Healthy baseline against Phase-2 entry, and which CQL cluster each marker sits in.")
    st.dataframe(pd.DataFrame([{
        "Biomarker": r["biomarker"],
        "Healthy": r["healthy"],
        "Phase-2 entry": r["value"],
        "Unit": r["unit"],
        "Direction": r["direction"],
        "Cluster": r["cluster"],
    } for r in report]), hide_index=True, width="stretch")

    st.subheader(f"CQL selected C{action_id:02d} — {action_name}")

    st.subheader("Daily macro targets")
    st.caption(f"From `Macro-Details.xlsm` · {row['sheet']} · {sex} · the bucket containing "
               f"age {age}, {weight_kg} kg, {height_cm} cm.")
    st.dataframe(pd.DataFrame([{
        "Calories": row.get("calories_kcal"), "Protein g": row.get("protein_g"),
        "Carbs g": row.get("carbohydrates_g"), "Fat g": row.get("total_fat_g"),
        "Sat fat ≤ g": row.get("saturated_fat_g_max"), "Sodium ≤ mg": row.get("sodium_mg_max"),
        "Added sugar ≤ g": row.get("added_sugar_g_max"),
    }]), hide_index=True, width="stretch")

    st.subheader("Foods selected for these targets")
    st.caption(
        f"From `ingredient_master.jsonl` — {len(sim_foods.load()):,} foods with USDA per-100g "
        f"nutrition and graded biomarker effects. {len(food_sel['all']):,} passed the diet, "
        f"allergen and drug-interaction filters; {food_sel['excluded']['diet']:,} excluded by diet."
    )
    st.dataframe(
        pd.DataFrame(food_table), hide_index=True, width="stretch",
        column_config={
            "trials": st.column_config.LinkColumn(
                "Effect study", display_text="open",
                help="The study showing the effect on this biomarker. For an indirect link "
                     "it is a study of the subject, not of the food itself"),
            "contains": st.column_config.LinkColumn(
                "Why this food", display_text="open",
                help="Evidence that this food carries the subject the effect runs through. "
                     "Empty for direct links, where the food itself was studied"),
        },
    )
    if food_sel["interactions"]:
        st.warning("Excluded for drug interaction: " + "; ".join(food_sel["interactions"][:5]))



# --------------------------------------------------------------------------
# 30 days
# --------------------------------------------------------------------------
if _view == "Meals & adherence":
    st.subheader("Calendar")
    st.caption("Pick a day to see what was suggested and what was eaten. "
               "● all three meals eaten · ◐ two · ○ one or none")

    _marks = {}
    for _d in sim["days_detail"]:
        _n_meals = _d.get("meals_eaten")
        _marks[_d["day"]] = (
            "·" if _n_meals is None else "●" if _n_meals == 3 else "◐" if _n_meals == 2 else "○"
        )

    for _row_start in range(0, len(sim["days_detail"]), 10):
        _cols = st.columns(10)
        for _col, _d in zip(_cols, sim["days_detail"][_row_start:_row_start + 10]):
            _n = _d["day"]
            if _col.button(
                f"{_marks[_n]} {_n}",
                key=f"cal_{_n}",
                width="stretch",
                type="primary" if st.session_state.picked_day == _n else "secondary",
            ):
                st.session_state.picked_day = _n

    _labels = {"calorie": "kcal", "protein": "Protein g", "carbs": "Carbs g",
               "fat": "Fat g", "fiber": "Fibre g"}
    day = sim["days_detail"][st.session_state.picked_day - 1]
    st.subheader(f"Day {day['day']} · {day['date']}")
    _f = day.get("fraction")
    m1, m2, m3 = st.columns(3)
    m1.metric("Intensity", day["intensity"])
    m2.metric("Meals eaten", f"{day['meals_eaten']} of 3" if day["logged"] else "not logged")
    m3.metric("Of the plan eaten", f"{_f:.0%}" if _f is not None else "—")

    if day["consumed"]:
        st.caption(
            "Consumed vs target — "
            + " · ".join(
                f"{_labels[k]} {day['consumed'][k]:.0f}/{daily_targets[k]:.0f}" for k in _labels
            )
        )

    cols = st.columns(3)
    for col, meal in zip(cols, ("breakfast", "lunch", "dinner")):
        chosen = (day.get("chosen_by_meal") or {}).get(meal)
        log = (day.get("meal_log") or {}).get(meal) or {}
        with col:
            if log.get("eaten"):
                st.markdown(f"**{meal.title()}** — ✓ eaten")
            elif log.get("eaten") is False:
                st.markdown(f"**{meal.title()}** — :red[skipped]")
            else:
                st.markdown(f"**{meal.title()}** — not logged")

            for r in day["recipes"].get(meal, []):
                # `chosen_by_meal` records which option would be taken; it is set
                # for every meal. A skipped meal has no chosen recipe, so the tick
                # must depend on the meal actually being eaten.
                picked = (
                    bool(log.get("eaten"))
                    and chosen is not None
                    and r["title"] == chosen["title"]
                )
                with st.container(border=True):
                    st.markdown(("✓ **" + r["title"] + "**") if picked else r["title"])
                    st.caption(
                        f"{r['kcal']} kcal · P {r['protein_g']} g · C {r['carbs_g']} g · "
                        f"F {r['fat_g']} g"
                        + (f" · sat {r['sat_fat_g']} g" if "sat_fat_g" in r else "")
                    )

                    if r.get("items"):
                        st.caption(", ".join(f"{i['food']} {i['grams']} g" for i in r["items"]))
                    st.caption(f"_{r['why']}_")
    st.caption(
        "Three options are offered per meal. **✓ marks the one the patient chose and ate.** "
        "Meals are eaten in full or skipped — a skipped meal has no tick."
    )

    st.divider()
    st.subheader("Day by day")
    table = pd.DataFrame([{
        "Day": d["day"], "Date": d["date"], "Intensity": d["intensity"],
        "Meals eaten": d["meals_eaten"] if d["logged"] else None,
        **{label: (round(d["consumed"][key], 1) if d["consumed"] else None)
           for key, label in _labels.items()},
    } for d in sim["days_detail"]])
    st.caption(
        "Consumed against a daily target of "
        + " · ".join(f"{_labels[k]} {daily_targets[k]:.0f}" for k in _labels)
        + ". These five are what adherence is scored on."
    )
    st.dataframe(table, hide_index=True, width="stretch", height=380)


    st.divider()
    st.subheader("Weekly reviews")
    st.caption("The adherence gate runs on days 7, 14 and 21 and sets intensity for the "
               "week that follows.")
    if not sim["weekly_reviews"]:
        st.info("No weekly review fired.")
    for r in sim["weekly_reviews"]:
        with st.container(border=True):
            head, dec = st.columns([3, 1])
            missing = f", {r['days_missing']} unlogged and excluded" if r["days_missing"] else ""
            head.markdown(
                f"**Day {r['review_on_day']}** · {r['window']} · "
                f"{r['days_counted']} days counted{missing}"
            )
            dec.metric("Decision", r["weekly_decision"].replace("_intensity", ""))
            st.dataframe(pd.DataFrame([r["adherence_vs_plan"]]), hide_index=True, width="stretch")
            st.caption(
                f"Mean {r['mean_adherence_vs_plan']:.3f} · "
                f"target-specific gate {'passed' if r['target_specific_ok'] else 'not met'}"
            )

    st.subheader("Intensity across the 30 days")
    order = {"recovery": 0, "maintain": 1, "full": 2}
    idf = pd.DataFrame([{"Day": d["day"], "Intensity": d["intensity"],
                         "level": order[d["intensity"]]} for d in sim["days_detail"]])
    st.altair_chart(
        alt.Chart(idf).mark_line(interpolate="step-after", strokeWidth=3).encode(
            x=alt.X("Day:Q", title="Day"),
            y=alt.Y("level:Q", title="Intensity", axis=alt.Axis(
                values=[0, 1, 2], labelExpr="datum.value == 0 ? 'recovery' : datum.value == 1 ? 'maintain' : 'full'")),
            tooltip=["Day", "Intensity"],
        ).properties(height=200),
        use_container_width=True,
    )

# --------------------------------------------------------------------------
# Day-30 review
# --------------------------------------------------------------------------
if _view == "Day-30 review":
    st.subheader("Day-30 review")

    if not _anthropic_key():
        with st.expander("Connect Claude to write this review", expanded=True):
            st.caption("Paste your key below — this session only, never written to disk.")
            entered = st.text_input("Anthropic API key", type="password",
                                    placeholder="sk-ant-...")
            if entered:
                st.session_state["anthropic_key"] = entered.strip()
                st.success("Key set for this browser session only. Generate the review below.")
        st.caption("Without a key the review is written from the same data by a rule-based summariser.")

    if st.button("Generate review", type="primary"):
        with st.spinner("Analysing the 30 days…"):
            st.session_state.narrative = sim_reasoning.generate(
                sim, food_rationale, api_key=_anthropic_key() or None
            )
    n = st.session_state.get("narrative")
    if not n:
        st.info("Generate the day-30 review once the run looks right.")
    else:
        if n["note"]:
            st.warning(n["note"])
        st.caption(f"Source: {n['source']}")
        st.markdown(n["text"])

    with st.expander("Ingredient → biomarker evidence used"):
        st.caption(
            "**direct** — the food itself was studied for that biomarker; `subject_trials` "
            "are its trials. **via X** — two hops: `contains_subject` evidences that the food "
            "carries X, and `subject_trials` are the trials of X against the biomarker. Only "
            "3% of effects are direct, and indirect edges are down-weighted 0.55x when foods "
            "are scored. `contains_source` says whether hop 1 rests on a paper or a factsheet."
        )
        _ev = pd.DataFrame(food_rationale).drop(columns=["cluster"], errors="ignore")
        _order = [c for c in ["ingredient", "biomarker", "grade", "evidence", "contains_source",
                              "n_studies", "n_papers", "subject_trials", "contains_subject",
                              "mechanism"] if c in _ev.columns]
        st.dataframe(
            _ev[_order + [c for c in _ev.columns if c not in _order]],
            hide_index=True, width="stretch",
            column_config={
                         "subject_trials": st.column_config.LinkColumn(
                             "Effect study", display_text="open",
                             help="The study showing the effect on this biomarker"),
                         "contains_subject": st.column_config.LinkColumn(
                             "Why this food", display_text="open",
                             help="Evidence that this food carries the subject the effect "
                                  "runs through. Empty for direct links"),
                     },
        )
