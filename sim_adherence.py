"""
Adherence scoring and the weekly intensity gate.

Extracted from the CLI harness so the deployed demo carries no client for the
staging services. The formulas mirror the platform:

  score  = clip(1 - min(|actual - target| / target, 1), 0, 1)
           — Postop-CQL-Service synthetic_data.compute_derived_adherence_7d

  gate   = target-specific cluster thresholds from the dietitian workbook's
           Intensity Rules sheet
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from config_discrete import ACTION_TO_MAIN_CLUSTERS, RECOVERY_ACTION_ID  # noqa: F401

ADHERENCE_FEATURES = ["calorie", "protein", "fiber", "carbs", "fat"]
WEEKLY_REVIEW_DAYS = (7, 14, 21)


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def adherence_scores(actual: Dict[str, float], target: Dict[str, float]) -> Dict[str, float]:
    """Exact platform formula: 1 - min(relative error, 1), clipped to [0, 1]."""
    out: Dict[str, float] = {}
    for feature in ADHERENCE_FEATURES:
        tgt = max(float(target.get(feature, 0.0)), 1e-6)
        act = float(actual.get(feature, tgt))
        out[feature] = clip(1.0 - min(abs(act - tgt) / tgt, 1.0), 0.0, 1.0)
    return out


def target_specific_ok(action_name: str, scores: Dict[str, float]) -> Tuple[bool, List[str]]:
    """Mirrors target_specific_adherence_check for the locked action's clusters."""
    name = (action_name or "").lower()
    failures: List[str] = []

    def require(feature: str, threshold: float, reason: str) -> None:
        val = scores.get(feature)
        if val is not None and val < threshold:
            failures.append(f"{reason}: {feature} {val:.2f} < {threshold:.2f}")

    if "lipid" in name:
        require("fiber", 0.60, "lipid target needs fiber adherence")
        require("fat", 0.65, "lipid target needs fat-quality adherence")
    if "metabolic" in name:
        require("carbs", 0.65, "metabolic target needs carbohydrate adherence")
        require("calorie", 0.65, "metabolic target needs calorie consistency")
    if "nutrition" in name:
        require("protein", 0.65, "nutrition target needs protein adherence")
        require("calorie", 0.65, "nutrition target needs calorie adequacy")
    if "inflammation" in name:
        require("fiber", 0.60, "inflammation target needs fiber adherence")
        require("fat", 0.65, "inflammation target needs fat-quality adherence")
    return len(failures) == 0, failures
