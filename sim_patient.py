"""
Patient model: healthy baseline -> surgery -> Phase-2 entry.

The disruption profiles are calibrated so that severity = 1.0 reproduces the
Phase-2 entry labs used in the staging test payload for the cardiac case. That
keeps the simulated arc consistent with the real request we validated against.

Reference ranges are the ones the platform already encodes in
Postop-CQL-Service/Staging/safety_layer.py where they exist; the rest are
standard adult clinical ranges and are marked as such.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config_discrete import (  # noqa: E402
    ACTION_TO_MAIN_CLUSTERS,
    BIOMARKER_TO_CLUSTER,
    CORE_20_BIOMARKERS,
    DISCRETE_ACTION_SPACE,
    MAIN_CQL_CLUSTERS,
    RECOVERY_ACTION_ID,
    TARGET_DIRECTIONS,
)

# --------------------------------------------------------------------------
# Reference ranges
# --------------------------------------------------------------------------
# (low, high, unit). Values from safety_layer.py where the platform defines
# them; the remainder are standard adult ranges. `None` means unbounded.
REFERENCE_RANGES: Dict[str, Tuple[Any, Any, str]] = {
    "HbA1c": (None, 5.7, "%"),
    "insulin": (2.0, 15.0, "uIU/mL"),
    "C-peptide": (0.8, 3.9, "ng/mL"),
    "LDL cholesterol": (None, 100.0, "mg/dL"),
    "triglycerides": (None, 150.0, "mg/dL"),
    "total cholesterol": (None, 200.0, "mg/dL"),
    "non-HDL cholesterol": (None, 130.0, "mg/dL"),
    "hs-CRP": (None, 3.0, "mg/L"),
    "homocysteine": (None, 15.0, "umol/L"),
    "folate": (3.0, None, "ng/mL"),
    "vitamin B12": (300.0, None, "pg/mL"),
    "magnesium": (1.7, 2.2, "mg/dL"),
    "transferrin saturation": (20.0, 50.0, "%"),
    "hemoglobin": (13.5, 17.5, "g/dL"),          # male; female band applied below
    "sodium": (135.0, 145.0, "mmol/L"),          # safety_layer.py
    "eGFR": (60.0, None, "mL/min/1.73m2"),       # safety_layer.py
    "microalbumin": (None, 30.0, "mg/L"),        # safety_layer.py
    "free T3": (2.3, 4.2, "pg/mL"),              # safety_layer.py
    "INR": (0.8, 1.2, ""),
    "urine pH": (4.5, 8.0, ""),
}
FEMALE_HEMOGLOBIN = (12.0, 15.5)


def reference_range(biomarker: str, sex: str = "Male") -> Tuple[Any, Any, str]:
    lo, hi, unit = REFERENCE_RANGES[biomarker]
    if biomarker == "hemoglobin" and str(sex).upper().startswith("F"):
        lo, hi = FEMALE_HEMOGLOBIN
    return lo, hi, unit


def is_abnormal(biomarker: str, value: float, sex: str = "Male") -> bool:
    lo, hi, _ = reference_range(biomarker, sex)
    if lo is not None and value < lo:
        return True
    if hi is not None and value > hi:
        return True
    return False


# --------------------------------------------------------------------------
# Healthy baseline
# --------------------------------------------------------------------------
HEALTHY_BASELINE: Dict[str, float] = {
    "HbA1c": 5.1, "insulin": 7.0, "C-peptide": 1.6,
    "LDL cholesterol": 95.0, "triglycerides": 95.0,
    "total cholesterol": 170.0, "non-HDL cholesterol": 113.0,
    "hs-CRP": 0.8, "homocysteine": 8.5,
    "folate": 14.0, "vitamin B12": 480.0, "magnesium": 2.05,
    "transferrin saturation": 32.0, "hemoglobin": 15.2, "sodium": 140.0,
    "eGFR": 105.0, "microalbumin": 8.0, "free T3": 3.2,
    "INR": 1.0, "urine pH": 6.4,
}

# --------------------------------------------------------------------------
# Surgery disruption
# --------------------------------------------------------------------------
# Post-op values at severity 1.0. The cardiac profile reproduces the staging
# payload's phase2_entry_labs. Each entry carries the mechanism so the UI and
# the day-30 narrative can explain *why* a marker moved.
DISRUPTION: Dict[str, Dict[str, Tuple[float, str]]] = {
    "cardiac": {
        "triglycerides":  (243.281, "Surgical stress and insulin resistance raise VLDL output"),
        "hs-CRP":         (3.096,   "Systemic inflammatory response to cardiopulmonary bypass"),
        "hemoglobin":     (11.561,  "Operative blood loss and haemodilution"),
        "free T3":        (2.25,    "Euthyroid sick syndrome — T4-to-T3 conversion falls after major surgery"),
        "insulin":        (20.793,  "Stress hyperglycaemia and transient insulin resistance"),
        "HbA1c":          (5.816,   "Drifts up with several weeks of stress hyperglycaemia"),
        "LDL cholesterol":(105.797, "Lipid profile shifts during the acute phase response"),
        "microalbumin":   (22.5,    "Transient glomerular permeability change after bypass"),
        "vitamin B12":    (245.006, "Reduced intake and dilution during recovery"),
        "magnesium":      (1.86,    "Renal losses and diuretic use"),
        "eGFR":           (110.457, "Hyperfiltration during the recovery phase"),
        "total cholesterol": (180.257, "Acute phase response"),
        "non-HDL cholesterol": (125.302, "Tracks the LDL and triglyceride shift"),
        "homocysteine":   (10.803, "Folate turnover during tissue repair"),
        "C-peptide":      (1.716,  "Follows insulin secretion"),
        "folate":         (12.151, "Consumed during tissue repair"),
        "transferrin saturation": (37.734, "Iron redistribution in the acute phase"),
        "sodium":         (140.996, "Fluid shifts"),
        "INR":            (0.809,  "Anticoagulation and clotting factor turnover"),
        "urine pH":       (6.838,  "Metabolic shift during recovery"),
    },
    "abdominal": {
        "hs-CRP":     (4.6,   "Peritoneal inflammatory response"),
        "hemoglobin": (10.9,  "Operative blood loss"),
        "folate":     (2.4,   "Reduced absorption after bowel handling"),
        "vitamin B12":(210.0, "Reduced ileal absorption"),
        "triglycerides": (185.0, "Catabolic stress"),
        "microalbumin": (18.0, "Transient permeability change"),
        "free T3":    (2.1,   "Euthyroid sick syndrome"),
    },
    "orthopedic": {
        "hs-CRP":     (5.2,  "Large soft-tissue and bone inflammatory load"),
        "hemoglobin": (10.4, "Operative blood loss"),
        "transferrin saturation": (14.0, "Functional iron deficiency of inflammation"),
        "triglycerides": (168.0, "Immobility and catabolic stress"),
        "magnesium":  (1.68, "Renal losses"),
    },
    "bariatric": {
        "vitamin B12": (180.0, "Reduced intrinsic factor and ileal absorption"),
        "folate":      (2.5,   "Reduced intake and absorption"),
        "hemoglobin":  (11.0,  "Iron and B12 malabsorption"),
        "transferrin saturation": (13.0, "Iron malabsorption"),
        "HbA1c":       (5.9,   "Improving but still elevated"),
        "triglycerides": (172.0, "Improving from pre-operative baseline"),
        "magnesium":   (1.55,  "Reduced absorption"),
    },
}

SURGERY_TYPES = list(DISRUPTION.keys())

# Surgery is defined to disrupt a quarter of the Core-20 panel. These are the
# markers that go clinically out of range for each surgery type; every other
# marker in the profile still shifts, but is held inside its reference range so
# the panel reads post-operative without being abnormal.
PRIMARY_DISRUPTION = {
    "cardiac":    ["triglycerides", "hs-CRP", "hemoglobin", "free T3", "insulin"],
    "abdominal":  ["hs-CRP", "hemoglobin", "folate", "vitamin B12", "free T3"],
    "orthopedic": ["hs-CRP", "hemoglobin", "transferrin saturation", "triglycerides", "magnesium"],
    "bariatric":  ["vitamin B12", "folate", "hemoglobin", "transferrin saturation", "magnesium"],
}
DISRUPTED_FRACTION = 0.25  # 5 of the 20 Core-20 biomarkers


def _hold_in_range(biomarker: str, value: float, sex: str) -> float:
    """Nudge a value just inside its reference range."""
    lo, hi, _ = reference_range(biomarker, sex)
    if lo is not None and value < lo:
        span = abs(lo) * 0.03 or 0.05
        return round(lo + span, 3)
    if hi is not None and value > hi:
        span = abs(hi) * 0.03 or 0.05
        return round(hi - span, 3)
    return value


def phase2_entry_labs(surgery_type: str, severity: float = 1.0, sex: str = "Male") -> Dict[str, float]:
    """Phase-2 entry panel: exactly 25% of the Core-20 out of range.

    Primary markers take the full post-operative value. The rest shift too —
    surgery moves more than five numbers — but are held inside their reference
    range so the disrupted count stays at a quarter of the panel.
    """
    labs = dict(HEALTHY_BASELINE)
    profile = DISRUPTION.get(surgery_type, {})
    primary = set(PRIMARY_DISRUPTION.get(surgery_type, []))
    for biomarker, (postop_value, _mechanism) in profile.items():
        healthy = HEALTHY_BASELINE[biomarker]
        value = round(healthy + severity * (postop_value - healthy), 3)
        if biomarker not in primary:
            value = _hold_in_range(biomarker, value, sex)
        labs[biomarker] = value
    return labs


def disruption_report(surgery_type: str, labs: Dict[str, float], sex: str = "Male") -> List[Dict[str, Any]]:
    """Which markers actually left their reference range, and why."""
    profile = DISRUPTION.get(surgery_type, {})
    out: List[Dict[str, Any]] = []
    for biomarker in CORE_20_BIOMARKERS:
        value = labs.get(biomarker)
        if value is None:
            continue
        lo, hi, unit = reference_range(biomarker, sex)
        abnormal = is_abnormal(biomarker, value, sex)
        if not abnormal:
            continue
        out.append({
            "biomarker": biomarker,
            "value": value,
            "unit": unit,
            "healthy": HEALTHY_BASELINE[biomarker],
            "direction": "high" if (hi is not None and value > hi) else "low",
            "cluster": BIOMARKER_TO_CLUSTER.get(biomarker),
            "mechanism": profile.get(biomarker, (None, "—"))[1],
        })
    return out


def suggested_doctor_targets(report: List[Dict[str, Any]]) -> Dict[str, str]:
    """Direction-only targets for every disrupted marker the system can act on."""
    targets: Dict[str, str] = {}
    for row in report:
        direction = TARGET_DIRECTIONS.get(row["biomarker"], 0)
        if direction == -1:
            targets[row["biomarker"]] = "reduce"
        elif direction == 1:
            targets[row["biomarker"]] = "increase"
    return targets


# --------------------------------------------------------------------------
# Action-space resolution (offline mirror of the CQL cluster mapping)
# --------------------------------------------------------------------------

def clusters_for_targets(doctor_targets: Dict[str, str]) -> List[str]:
    active = []
    for biomarker in doctor_targets:
        cluster = BIOMARKER_TO_CLUSTER.get(biomarker)
        if cluster in MAIN_CQL_CLUSTERS and cluster not in active:
            active.append(cluster)
    return active


def action_for_clusters(clusters: List[str]) -> Tuple[int, str]:
    """Find the discrete action whose cluster set matches exactly.

    Mirrors exact_action_id_from_cluster_mask without needing numpy or the
    trained model. Used for offline mode only; live mode uses the real CQL.
    """
    wanted = set(clusters)
    if not wanted:
        return RECOVERY_ACTION_ID, DISCRETE_ACTION_SPACE[RECOVERY_ACTION_ID]
    for action_id, action_clusters in ACTION_TO_MAIN_CLUSTERS.items():
        if action_id == RECOVERY_ACTION_ID:
            continue
        if set(action_clusters) == wanted:
            return action_id, DISCRETE_ACTION_SPACE[action_id]
    # No exact match: fall back to the action covering the most wanted clusters.
    best_id, best_score = RECOVERY_ACTION_ID, -1
    for action_id, action_clusters in ACTION_TO_MAIN_CLUSTERS.items():
        if action_id == RECOVERY_ACTION_ID:
            continue
        score = len(wanted & set(action_clusters)) - len(set(action_clusters) - wanted)
        if score > best_score:
            best_id, best_score = action_id, score
    return best_id, DISCRETE_ACTION_SPACE[best_id]


def uncoverable_targets(doctor_targets: Dict[str, str]) -> List[str]:
    """Doctor targets that no action space can pursue (safety-layer clusters)."""
    return [
        b for b in doctor_targets
        if BIOMARKER_TO_CLUSTER.get(b) not in MAIN_CQL_CLUSTERS
    ]


def bmi(weight_kg: float, height_cm: float) -> float:
    h = float(height_cm) / 100.0
    return round(float(weight_kg) / (h * h), 1)


def estimate_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin-St Jeor — same formula as safety_layer.estimate_bmr."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if str(sex).upper().startswith("M") else -161)
