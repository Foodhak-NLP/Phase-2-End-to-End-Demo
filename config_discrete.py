"""
Configuration for Doctor-Guided Discrete CQL for Phase-2 Post-Op Metabolic Recovery.

Design locked in this version:
- Phase 1: first 6 weeks after surgery = wound/tissue recovery, not optimized here.
- Phase 2: next 90 days = metabolic recovery.
- CQL runs at Phase-2 Day 1, Day 30, Day 60, Day 90.
- Weekly state updates use adherence/wearable logs, but CQL is not rerun weekly
  unless there is an event-triggered clinical concern.
- At the initial Phase-2 Day-1 decision, monthly CQL does not use weekly
  adherence because no 7-day Phase-2 adherence history exists yet. It uses
  the Phase-0/Phase-1 patient snapshot: labs, vitals/wearables, nutrition
  snapshot, comorbidities, phase-1 macros, demographics, and safety flags.
- CQL action space is discrete and has 15 deterministic doctor-target
  cluster strategies plus one monthly recovery gate.
- Actions 0..14 are target-pursuit actions determined by doctor biomarker clusters.
- Action 15 is not a biomarker target. It means "recovery_stabilization":
  pause target pursuit for the current 30-day block and use gentle stabilization.
- Organ function and other validated biomarkers are handled by a safety layer,
  not as target-cluster actions.
"""
from __future__ import annotations

# VENDORED from Postop-CQL-Service/Staging/config_discrete.py so the demo
# deploys standalone. Pure constants, no dependencies. Re-copy if the source
# changes — this must not drift from the platform.
from dataclasses import dataclass
from typing import Dict, List, Tuple

CORE_20_BIOMARKERS: List[str] = [
    "HbA1c", "insulin", "C-peptide",
    "LDL cholesterol", "triglycerides", "total cholesterol", "non-HDL cholesterol",
    "hs-CRP", "homocysteine",
    "folate", "vitamin B12", "magnesium", "transferrin saturation", "hemoglobin", "sodium",
    "eGFR", "microalbumin", "free T3",
    "INR", "urine pH",
]

MAIN_CQL_CLUSTERS: Dict[str, List[str]] = {
    "metabolic": ["HbA1c", "insulin", "C-peptide"],
    "lipids": ["LDL cholesterol", "triglycerides", "total cholesterol", "non-HDL cholesterol"],
    "inflammation": ["hs-CRP", "homocysteine"],
    "nutrition_vitamins_minerals": [
        "folate", "vitamin B12", "magnesium", "transferrin saturation", "hemoglobin", "sodium"
    ],
}

SAFETY_LAYER_CLUSTERS: Dict[str, List[str]] = {
    "organ_function": ["eGFR", "microalbumin", "free T3"],
    "other_validated": ["INR", "urine pH"],
}

BIOMARKER_TO_CLUSTER: Dict[str, str] = {}
for cluster, biomarkers in {**MAIN_CQL_CLUSTERS, **SAFETY_LAYER_CLUSTERS}.items():
    for biomarker in biomarkers:
        BIOMARKER_TO_CLUSTER[biomarker] = cluster

TARGET_DIRECTIONS: Dict[str, int] = {
    "HbA1c": -1, "insulin": -1, "C-peptide": -1,
    "LDL cholesterol": -1, "triglycerides": -1, "total cholesterol": -1, "non-HDL cholesterol": -1,
    "hs-CRP": -1, "homocysteine": -1,
    "folate": +1, "vitamin B12": +1, "magnesium": +1, "transferrin saturation": +1, "hemoglobin": +1, "sodium": 0,
    "eGFR": +1, "microalbumin": -1, "free T3": 0,
    "INR": 0, "urine pH": 0,
}

BIOMARKER_PRIORS: Dict[str, Tuple[float, float]] = {
    "HbA1c": (6.2, 1.0), "insulin": (15.0, 6.0), "C-peptide": (2.5, 0.8),
    "LDL cholesterol": (130.0, 30.0), "triglycerides": (170.0, 60.0),
    "total cholesterol": (200.0, 35.0), "non-HDL cholesterol": (160.0, 35.0),
    "hs-CRP": (3.5, 1.8), "homocysteine": (12.0, 3.5),
    "folate": (8.0, 3.0), "vitamin B12": (350.0, 150.0), "magnesium": (2.0, 0.25),
    "transferrin saturation": (28.0, 8.0), "hemoglobin": (12.5, 1.5), "sodium": (140.0, 2.5),
    "eGFR": (85.0, 15.0), "microalbumin": (15.0, 8.0), "free T3": (3.0, 0.5),
    "INR": (1.0, 0.15), "urine pH": (6.0, 0.6),
}

DISCRETE_ACTION_SPACE: Dict[int, str] = {
    0: "metabolic_focus",
    1: "lipid_focus",
    2: "inflammation_focus",
    3: "nutrition_vitamins_minerals_focus",
    4: "metabolic_lipid_recovery",
    5: "metabolic_inflammation_recovery",
    6: "metabolic_nutrition_recovery",
    7: "lipid_inflammation_recovery",
    8: "lipid_nutrition_recovery",
    9: "inflammation_nutrition_recovery",
    10: "metabolic_lipid_nutrition_recovery",
    11: "metabolic_lipid_inflammation_recovery",
    12: "lipid_inflammation_nutrition_recovery",
    13: "metabolic_lipid_inflammation_nutrition_recovery",
    14: "metabolic_inflammation_nutrition_recovery",
    15: "recovery_stabilization",
}
N_ACTIONS = len(DISCRETE_ACTION_SPACE)
RECOVERY_ACTION_ID = 15
TARGET_PURSUIT_ACTION_IDS = [aid for aid in DISCRETE_ACTION_SPACE if aid != RECOVERY_ACTION_ID]

ACTION_TO_MAIN_CLUSTERS: Dict[int, List[str]] = {
    0: ["metabolic"],
    1: ["lipids"],
    2: ["inflammation"],
    3: ["nutrition_vitamins_minerals"],
    4: ["metabolic", "lipids"],
    5: ["metabolic", "inflammation"],
    6: ["metabolic", "nutrition_vitamins_minerals"],
    7: ["lipids", "inflammation"],
    8: ["lipids", "nutrition_vitamins_minerals"],
    9: ["inflammation", "nutrition_vitamins_minerals"],
    10: ["metabolic", "lipids", "nutrition_vitamins_minerals"],
    11: ["metabolic", "lipids", "inflammation"],
    12: ["lipids", "inflammation", "nutrition_vitamins_minerals"],
    13: ["metabolic", "lipids", "inflammation", "nutrition_vitamins_minerals"],
    14: ["metabolic", "inflammation", "nutrition_vitamins_minerals"],
    15: [],
}

# State vector layout:
# 0..19   : normalized Core 20 biomarkers
# 20..23  : doctor target mask for main CQL clusters
# 24..28  : phase0/latest nutrition snapshot features, not weekly adherence
#           calories, protein_g_per_kg, fiber_g, carbs_pct, saturated_fat_pct
# 29..33  : phase1/current macro baseline normalized
# 34..38  : vitals/wearables: BMI, resting_hr, HRV, systolic_bp, sleep_hours
# 39..42  : demographics/comorbidities: age, sex_F, is_vegetarian, type_2_diabetes
# 43..44  : safety flags: organ_function_caution, other_validated_caution
STATE_DIM = 45
ADHERENCE_FEATURES = ["calorie", "protein", "fiber", "carbs", "fat"]  # weekly inner-rule only
CQL_PHASE0_NUTRITION_FEATURES = ["calories_kcal", "protein_g_per_kg", "fiber_g", "carbs_pct", "saturated_fat_pct"]
MACRO_NAMES = ["protein_g_per_kg", "carbs_pct", "fat_pct", "fiber_g", "saturated_fat_pct"]
MACRO_BOUNDS: Dict[str, Tuple[float, float]] = {
    "protein_g_per_kg": (0.8, 1.8),
    "carbs_pct": (25.0, 60.0),
    "fat_pct": (20.0, 45.0),
    "fiber_g": (12.0, 45.0),
    "saturated_fat_pct": (4.0, 13.0),
}
SURGERY_PROTEIN_CAP: Dict[str, float] = {"bariatric": 1.5, "cardiac": 1.6, "orthopedic": 1.8, "abdominal": 1.8}
RATE_OF_CHANGE_LIMIT = 0.20
CALORIC_FLOOR_BMR_MULTIPLIER = 0.85
DEFAULT_SEED = 42
CYCLE_DAYS = 30
PHASE2_DECISION_DAYS = [1, 30, 60, 90]
HIDDEN_RESPONDER_TYPES = ["strong", "average", "weak", "non_responder"]
RESPONDER_MULTIPLIER = {"strong": 1.35, "average": 1.0, "weak": 0.55, "non_responder": 0.15}

CQL_GAMMA = 0.95
CQL_ALPHA = 1.0
CQL_LR = 1e-4
CQL_BATCH_SIZE = 128
CQL_TARGET_TAU = 0.005
CQL_GRAD_CLIP = 1.0
CQL_LOG_EVERY = 500
CQL_HIDDEN_DIM = 256

@dataclass
class ActionTemplate:
    action_id: int
    name: str
    active_clusters: List[str]
    description: str

def get_action_template(action_id: int) -> ActionTemplate:
    name = DISCRETE_ACTION_SPACE[action_id]
    clusters = ACTION_TO_MAIN_CLUSTERS[action_id]
    if action_id == RECOVERY_ACTION_ID:
        desc = "Recovery stabilization month: pause target pursuit and use gentle adherence/safety-guided macros."
    else:
        desc = f"Targets main nutrition-intervention clusters: {', '.join(clusters)}"
    return ActionTemplate(action_id, name, clusters, desc)
