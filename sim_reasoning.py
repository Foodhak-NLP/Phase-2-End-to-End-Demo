"""
Day-30 narrative.

Turns the run into a clinician-facing explanation: what the block delivered, why
those foods were chosen — grounded in the ingredient-biomarker evidence — and what
the day-30 draw should be read against.

Uses Claude when credentials are available, and a deterministic writer otherwise so
the panel is never empty. Both paths are held to the same rule: no biomarker value
is predicted. Only the phase-2 entry labs exist; day-30 comes from a blood draw.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

MODEL = "claude-sonnet-5"

SYSTEM = """You are a clinical nutrition informatics analyst writing the end-of-block \
summary for a post-operative Phase-2 nutrition platform. Your reader is the \
supervising dietitian.

Ground rules you must not break:
- You are given the phase-2 ENTRY labs only. There is no projection and no day-30 \
value. Never invent, estimate or imply one — not as a number, a percentage, or a \
direction of travel with a magnitude. What you can say is what the plan delivered and \
what the day-30 draw should be checked against.
- Explain mechanism using the specific ingredients in the plan and the biomarker \
they act on. Do not assert effect sizes at all — the data does not support them.
- If a doctor target was not covered by the selected action space, say so directly \
and explain why it did not move.
- If adherence was low, say the strategy is untested rather than ineffective — those \
require opposite responses.
- Each ingredient carries `evidence`: "direct" means the food itself was studied for that \
biomarker; "via X" means the link runs through a subject the food contains, so the trials \
are of X, not of the food. Say which when it matters — an indirect link is materially \
weaker, and weaker still when `contains_source` is "factsheet" rather than "paper". \
`subject_trials` links the trials, `contains_subject` the containment evidence. Never \
invent a PMID.
- The block length is given in `block.length_days`. Never infer it from the review \
days — reviews stop at day 21 but the block continues past it.
- No hedging filler, no apologies. Write plainly for a busy clinician.

Structure your answer with these markdown headings, in this order:
## What the block delivered
## Why — the dietary mechanism
## What adherence tells us
## Recommendation for the next block
Keep it under 600 words."""


def build_payload(sim: Dict[str, Any], rationale: List[Dict[str, str]]) -> Dict[str, Any]:
    """Compact the run into the facts the narrative needs."""
    meta = sim["meta"]
    biomarkers = []
    for name, base in (sim.get("baseline_labs") or {}).items():
        if base in (None, 0):
            continue
        biomarkers.append({
            "biomarker": name,
            "phase2_entry": round(float(base), 2),
            "reference_range": (sim.get("reference_ranges") or {}).get(name),
            "covered_by_action": name in (meta.get("covered_biomarkers") or []),
        })
    weeks = [
        {
            "review_day": r["review_on_day"],
            "mean_adherence": r["mean_adherence_vs_plan"],
            "per_macro": r["adherence_vs_plan"],
            "decision": r["weekly_decision"],
            "intensity": r["new_intensity"],
            "days_counted": r.get("days_counted"),
        }
        for r in sim.get("weekly_reviews", [])
    ]
    # Days at each intensity, so the block length and the shape of the run are
    # stated rather than inferred from the review days.
    days_detail = sim.get("days_detail") or []
    intensity_days: Dict[str, int] = {}
    for d in days_detail:
        intensity_days[d["intensity"]] = intensity_days.get(d["intensity"], 0) + 1
    skipped = sum(
        1 for d in days_detail
        for log in (d.get("meal_log") or {}).values()
        if log.get("eaten") is False
    )

    return {
        "block": {
            "length_days": meta.get("days") or len(days_detail),
            "review_days": list(meta.get("thresholds", {}).get("review_days", []) or [7, 14, 21]),
            "note": (
                f"The block is {meta.get('days') or len(days_detail)} days. Weekly reviews fall "
                "on days 7, 14 and 21 only — the day-21 decision carries the remaining days to "
                "the end. Do not infer the block length from the last review."
            ),
            "days_at_each_intensity": intensity_days,
            "meals_skipped": skipped,
            "meals_offered": len(days_detail) * 3,
        },
        "surgery_type": meta.get("surgery_type"),
        "days_since_surgery": meta.get("days_since_surgery"),
        "demographics": meta.get("demographics"),
        "locked_action": {
            "id": meta.get("action_id"),
            "name": meta.get("action_name"),
            "clusters": meta.get("action_clusters"),
        },
        "doctor_targets": meta.get("doctor_targets"),
        "uncoverable_targets": meta.get("uncoverable_targets"),
        "target_adherence": meta.get("target_adherence"),
        "daily_macro_targets": sim.get("targets", {}).get("plan_shown_to_patient"),
        "weekly_reviews": weeks,
        "biomarker_baseline": biomarkers,
        "ingredients_used": rationale[:18],
        "model_caveats": {
            "no_projection": "This simulation does not predict biomarker values. Only the "
                             "phase-2 entry labs are given. Any day-30 figure must come from "
                             "an actual blood draw.",
            "citations": "Only 3% of the dataset's food-biomarker effects are direct; the "
                         "rest are indirect, established through a subject the food contains. "
                         "Indirect edges are down-weighted 0.55x when foods are scored.",
        },
    }


def _client(api_key: Optional[str] = None) -> Optional[Any]:
    """Build a client from an explicitly supplied key, or the environment.

    The key is passed in rather than read from a global: on a hosted Streamlit
    app a single process serves every visitor, so a key placed in os.environ
    would be visible to all of them.
    """
    try:
        import anthropic
    except ImportError:
        return None
    if api_key:
        try:
            return anthropic.Anthropic(api_key=api_key)
        except Exception:
            return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        return anthropic.Anthropic()
    except Exception:
        return None


def generate(
    sim: Dict[str, Any],
    rationale: List[Dict[str, str]],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Return {'text': str, 'source': 'claude'|'deterministic', 'note': str}."""
    payload = build_payload(sim, rationale)
    client = _client(api_key)
    if client is None:
        return {
            "text": deterministic(payload),
            "source": "deterministic",
            "note": "No ANTHROPIC_API_KEY found — showing the rule-based summary. "
                    "Set the key to get the Claude narrative.",
        }

    prompt = (
        "Write the end-of-block summary for this Phase-2 run.\n\n"
        + json.dumps(payload, indent=2)
    )

    # Preferred path: beta endpoint with server-side refusal fallback.
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
        return _read(message, payload)
    except Exception as beta_error:
        pass

    # Fallback: plain streaming call.
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
        return _read(message, payload)
    except Exception as exc:
        return {
            "text": deterministic(payload),
            "source": "deterministic",
            "note": f"Claude call failed ({type(exc).__name__}: {exc}). Showing the rule-based summary.",
        }


def _read(message: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    if getattr(message, "stop_reason", None) == "refusal":
        return {
            "text": deterministic(payload),
            "source": "deterministic",
            "note": "The model declined this request; showing the rule-based summary.",
        }
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    return {"text": text.strip(), "source": "claude", "note": ""}


# --------------------------------------------------------------------------
# Deterministic writer
# --------------------------------------------------------------------------

def deterministic(p: Dict[str, Any]) -> str:
    action = p["locked_action"]
    lines: List[str] = []
    lines.append("## What the block delivered\n")
    lines.append(
        f"The patient was held on **{action['name']}** (action {action['id']}, covering "
        f"{', '.join(action['clusters']) or 'no main cluster'}) for the full "
        f"{p['block']['length_days']} days. Reviews fell on days "
        f"{', '.join(str(d) for d in p['block']['review_days'])}; the last decision carried "
        f"the remaining days.\n"
    )
    lines.append("No biomarker values are predicted here. These are the phase-2 entry labs "
                 "the block started from — the day-30 draw is the only source for where they "
                 "have moved.\n")
    for row in p["biomarker_baseline"]:
        mark = "targeted by the action" if row["covered_by_action"] else "**not covered by this action**"
        lines.append(
            f"- **{row['biomarker']}** entry {row['phase2_entry']} "
            f"(reference {row['reference_range']}) — {mark}"
        )
    if p.get("uncoverable_targets"):
        lines.append(
            f"\n{', '.join(p['uncoverable_targets'])} sits in a safety-layer cluster that no "
            f"action space pursues, so it was never a lever this block."
        )

    lines.append("\n## Why — the dietary mechanism\n")
    seen = set()
    for row in p["ingredients_used"][:6]:
        if row["ingredient"] in seen:
            continue
        seen.add(row["ingredient"])
        mech = row["mechanism"]
        # Mechanism strings already lead with the food name; don't repeat it.
        tail = mech.split("—", 1)[1].strip() if "—" in mech else mech
        lines.append(f"- **{row['ingredient']}** — {tail}")
    lines.append(
        "\nEach recipe served this block was built from that set, which is the basis for "
        "expecting movement in the targeted direction."
    )

    lines.append("\n## What adherence tells us\n")
    weeks = p.get("weekly_reviews") or []
    if not weeks:
        lines.append("No weekly review fired, so intensity stayed at its starting value.")
    for w in weeks:
        counted = f", {w['days_counted']} days counted" if w.get("days_counted") else ""
        lines.append(
            f"- Day {w['review_day']}: mean {w['mean_adherence']:.2f}{counted} → "
            f"**{w['decision']}** (intensity now {w['intensity']})"
        )
    means = [w["mean_adherence"] for w in weeks]
    if means and sum(means) / len(means) < 0.65:
        lines.append(
            "\nAdherence was low enough that this block does not test the strategy. "
            "Treat a disappointing day-30 result as untested rather than ineffective."
        )

    lines.append("\n## Recommendation for the next block\n")
    lines.append(
        "- Draw the day-30 panel. Nothing here substitutes for it.\n"
        "- Read the result against the adherence above: a disappointing lab with high adherence "
        "means the strategy is wrong for this patient; with low adherence it means the strategy "
        "was never tested.\n"
        "- Check whether the targets the action could not cover need a different action space "
        "next block, or a route other than diet."
    )
    lines.append(f"\n_{p['model_caveats']['no_projection']}_")
    return "\n".join(lines)
