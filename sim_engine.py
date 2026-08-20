"""
30-day block engine for the Streamlit app.

Reuses the primitives in sim_adherence.py (adherence formula, target-specific
gate) and adds per-day recipe assembly, whole-meal logging, and the day-by-day
record the UI renders.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from typing import Any, Dict, List, Optional

import sim_adherence as core
import sim_foods
import sim_patient as patient_model

ADHERENCE_FEATURES = core.ADHERENCE_FEATURES
WEEKLY_REVIEW_DAYS = core.WEEKLY_REVIEW_DAYS

def run(
    *,
    demographics: Dict[str, Any],
    surgery_type: str,
    days_since_surgery: int,
    baseline_labs: Dict[str, float],
    doctor_targets: Dict[str, str],
    action_id: int,
    action_name: str,
    action_clusters: List[str],
    daily_targets: Dict[str, float],
    adherence: float,
    weekly_adherence: Optional[List[float]] = None,
    days: int = 30,
    start_date: str = "2026-09-01",
    recipe_counts: Optional[Dict[str, int]] = None,
    low_threshold: float = 0.55,
    high_threshold: float = 0.80,
    log_rate: float = 0.95,
    portion_sigma: float = 0.05,
    seed: int = 42,
    live_recipes: Optional[Any] = None,
    food_selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Simulate one 30-day CQL block.

    `log_rate` is the chance the patient records the day at all — separate from
    adherence. A day that is not logged is unknown, and is excluded from the
    weekly mean rather than counted as zero intake.
    """
    rng = random.Random(seed)
    counts = recipe_counts or dict(sim_foods.DEFAULT_COUNTS)

    # One adherence level per review window, so a run is reproducible and each
    # weekly decision can be driven directly. Days 21-30 continue at week 3's
    # level, since the last review is on day 21.
    weeks = list(weekly_adherence or [adherence, adherence, adherence])
    while len(weeks) < 3:
        weeks.append(weeks[-1])

    def rate_for(day_index: int) -> float:
        if day_index <= 6:
            return weeks[0]
        if day_index <= 13:
            return weeks[1]
        return weeks[2]

    intensity, weekly_decision = "maintain", "maintain_intensity"

    is_recovery = action_id == core.RECOVERY_ACTION_ID
    covered = [
        b for b in doctor_targets
        if patient_model.BIOMARKER_TO_CLUSTER.get(b) in action_clusters
    ]

    days_detail: List[Dict[str, Any]] = []
    weekly_reviews: List[Dict[str, Any]] = []
    week_buffer: List[Optional[Dict[str, float]]] = []
    week_start_day = 1

    for day_index in range(1, days + 1):
        date = (dt.date.fromisoformat(start_date) + dt.timedelta(days=day_index - 1)).isoformat()

        # ---- weekly review -------------------------------------------------
        review = None
        if day_index in WEEKLY_REVIEW_DAYS and week_buffer:
            known = [d for d in week_buffer if d is not None]
            if known:
                mean_intake = {
                    f: sum(d[f] for d in known) / len(known) for f in ADHERENCE_FEATURES
                }
                scores = core.adherence_scores(mean_intake, daily_targets)
                mean_adherence = sum(scores.values()) / len(scores)
                ts_ok, ts_failures = core.target_specific_ok(action_name, scores)
                intensity, weekly_decision = _gate(
                    mean_adherence, ts_ok, low_threshold, high_threshold, is_recovery
                )
                review = {
                    "review_on_day": day_index,
                    "window": f"days {week_start_day}–{day_index - 1}",
                    "days_counted": len(known),
                    "days_missing": len(week_buffer) - len(known),
                    "mean_intake": {k: round(v, 1) for k, v in mean_intake.items()},
                    "adherence_vs_plan": {k: round(v, 3) for k, v in scores.items()},
                    "mean_adherence_vs_plan": round(mean_adherence, 3),
                    "target_specific_ok": ts_ok,
                    "target_specific_failures": ts_failures,
                    "new_intensity": intensity,
                    "weekly_decision": weekly_decision,
                }
                weekly_reviews.append(review)
            week_buffer = []
            week_start_day = day_index

        # ---- recipes -------------------------------------------------------
        if live_recipes is not None:
            day_recipes = live_recipes(day_index, date, intensity)
        else:
            day_recipes = sim_foods.compose_day(
                day=day_index, date=date, daily_targets=daily_targets,
                selection=food_selection, counts=counts, seed=seed,
            )

        # ---- patient eats and (maybe) logs ---------------------------------
        chosen_by_meal = {m: (opts[0] if opts else None) for m, opts in day_recipes.items()}

        logged = rng.random() < log_rate
        if logged:
            # Real non-adherence is whole meals missed, not eating 83% of every
            # plate. Meals are eaten or skipped; the day's share of the plan is
            # therefore quantised by the meal split (B 20 / L 40 / D 40), giving
            # 0, 0.2, 0.4, 0.6, 0.8 or 1.0.
            eaten_meals = _pick_meals(rate_for(day_index), rng)
            fraction = sum(sim_foods.MEAL_SPLIT[m] for m in eaten_meals)
            consumed = {f: daily_targets[f] * fraction for f in ADHERENCE_FEATURES}
            meals_eaten = len(eaten_meals)
            week_buffer.append(consumed)
            meal_log = {
                m: {"eaten": m in eaten_meals}
                for m in ("breakfast", "lunch", "dinner")
            }
        else:
            consumed, fraction, meals_eaten = None, None, None
            meal_log = {m: {"eaten": None} for m in ("breakfast", "lunch", "dinner")}
            week_buffer.append(None)

        # ---- biomarkers ----------------------------------------------------

        days_detail.append({
            "day": day_index,
            "date": date,
            "intensity": intensity,
            "weekly_decision": weekly_decision,
            "weekly_review": review,
            "logged": logged,
            "meals_eaten": meals_eaten,
            "consumed": {k: round(v, 1) for k, v in consumed.items()} if consumed else None,
            "recipes": day_recipes,
            "chosen_by_meal": chosen_by_meal,
            "meal_log": meal_log,
            "fraction": round(fraction, 3) if fraction is not None else None,
            "exposure": _day_exposure(chosen_by_meal),
        })

    sex = demographics.get("sex", "Male")
    return {
        "meta": {
            "surgery_type": surgery_type,
            "days_since_surgery": days_since_surgery,
            "demographics": demographics,
            "action_id": action_id,
            "action_name": action_name,
            "action_clusters": action_clusters,
            "covered_biomarkers": covered,
            "uncoverable_targets": patient_model.uncoverable_targets(doctor_targets),
            "doctor_targets": doctor_targets,
            "target_adherence": adherence,
            "weekly_adherence_set": weeks,
            "log_rate": log_rate,
            "thresholds": {"step_down_below": low_threshold, "step_up_at": high_threshold},
            "days": days,
            "start_date": start_date,
        },
        "targets": {"plan_shown_to_patient": {k: round(v, 1) for k, v in daily_targets.items()}},
        "baseline_labs": {b: baseline_labs[b] for b in doctor_targets if b in baseline_labs},
        "reference_ranges": {
            b: _range_text(b, sex) for b in doctor_targets if b in patient_model.REFERENCE_RANGES
        },
        "weekly_reviews": weekly_reviews,
        "days_detail": days_detail,
    }


# Every subset of the day's meals, with the share of the plan it represents.
def _meal_subsets() -> List[tuple]:
    import itertools

    meals = ("breakfast", "lunch", "dinner")
    out = []
    for size in range(len(meals) + 1):
        for combo in itertools.combinations(meals, size):
            out.append((sum(sim_foods.MEAL_SPLIT[m] for m in combo), combo))
    out.sort()
    return out


def _pick_meals(rate: float, rng: random.Random) -> tuple:
    """Choose which meals were eaten so the long-run share matches `rate`.

    Picks between the two achievable shares bracketing the target, weighted so
    the expectation lands on it — a 0.83 rate becomes a mix of full days and
    one-meal-missed days rather than 83% of every plate.
    """
    subsets = _meal_subsets()
    shares = sorted({sh for sh, _ in subsets})
    lo = max((sh for sh in shares if sh <= rate), default=shares[0])
    hi = min((sh for sh in shares if sh >= rate), default=shares[-1])
    if hi > lo:
        share = hi if rng.random() < (rate - lo) / (hi - lo) else lo
    else:
        share = lo
    options = [combo for sh, combo in subsets if abs(sh - share) < 1e-9]
    return rng.choice(options)


def _day_exposure(chosen_by_meal: Dict[str, Any]) -> Dict[str, float]:
    """Dose-weighted evidence the day's chosen plates carry, per biomarker."""
    out: Dict[str, float] = {}
    for recipe in chosen_by_meal.values():
        for biomarker, value in ((recipe or {}).get("exposure") or {}).items():
            out[biomarker] = out.get(biomarker, 0.0) + float(value)
    return {k: round(v, 3) for k, v in out.items()}


def weekly_exposure(sim: Dict[str, Any], first_day: int, last_day: int) -> Dict[str, float]:
    """Exposure actually consumed over a window — only meals that were eaten."""
    out: Dict[str, float] = {}
    for d in sim["days_detail"]:
        if not (first_day <= d["day"] <= last_day) or not d.get("logged"):
            continue
        for meal, recipe in (d.get("chosen_by_meal") or {}).items():
            if not ((d.get("meal_log") or {}).get(meal) or {}).get("eaten"):
                continue
            for biomarker, value in ((recipe or {}).get("exposure") or {}).items():
                out[biomarker] = out.get(biomarker, 0.0) + float(value)
    return {k: round(v, 2) for k, v in out.items()}


def _gate(mean_adherence, ts_ok, low, high, is_recovery):
    """Workbook policy: safety > low > high+target-specific > maintain."""
    if is_recovery:
        return ("recovery", "step_down_intensity") if mean_adherence < low else ("maintain", "maintain_intensity")
    if mean_adherence < low:
        return "recovery", "step_down_intensity"
    if mean_adherence >= high and ts_ok:
        return "full", "step_up_intensity"
    return "maintain", "maintain_intensity"


def _range_text(biomarker: str, sex: str) -> str:
    lo, hi, unit = patient_model.reference_range(biomarker, sex)
    if lo is not None and hi is not None:
        return f"{lo}–{hi} {unit}".strip()
    if hi is not None:
        return f"< {hi} {unit}".strip()
    if lo is not None:
        return f"> {lo} {unit}".strip()
    return unit
