"""
Recipe composition from `ingredient_master.jsonl`.

2,900 foods, each carrying USDA per-100g nutrition plus graded biomarker
effects, diet tags, allergens and safety findings. That is enough to build a
recipe whose title AND macros are both real: foods are chosen for the patient's
targeted biomarkers, given gram amounts, and the recipe's macros are computed
from those amounts rather than assumed.

Selection order:
    tier == "specific"        (drop supplements and category rows)
    diet + allergen filter
    drug-interaction filter   (against the patient's medication list)
    score by graded biomarker effect in the doctor's direction
"""
from __future__ import annotations

import functools
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Beside the app when deployed; falls back to the parent for local dev.
_HERE = Path(__file__).resolve().parent
DATASET = _HERE / "ingredient_master.jsonl"
if not DATASET.exists():
    DATASET = _HERE.parent / "ingredient_master.jsonl"

GRADE_WEIGHT = {"A": 1.00, "B": 0.80, "C": 0.55, "D": 0.30, "E": 0.15}

# Role -> acceptable food groups.
ROLES: Dict[str, Tuple[str, ...]] = {
    "protein": ("seafood", "legume", "egg", "dairy", "poultry", "meat"),
    "base":    ("grain",),
    "veg":     ("vegetable",),
    "fat":     ("fat_oil", "nut_seed"),
    "accent":  ("herb_spice", "fruit", "fermented"),
}

# Realistic portion range per food group: (start, min, max) in grams. A plate is
# scaled to hit its energy target, but only the staple roles scale — otherwise a
# uniform multiplier produces things like 33 g of black pepper.
GROUP_GRAMS: Dict[str, Tuple[float, float, float]] = {
    "seafood":    (130, 80, 260),
    "meat":       (130, 80, 250),
    "poultry":    (130, 80, 250),
    "egg":        (100, 50, 200),
    "legume":     (130, 70, 280),
    "dairy":      (150, 80, 350),
    "grain":      (75, 40, 180),
    "vegetable":  (140, 80, 280),
    "fat_oil":    (12, 5, 25),
    "nut_seed":   (20, 10, 40),
    "fruit":      (90, 50, 160),
    "fermented":  (60, 30, 120),
    "herb_spice": (4, 2, 8),
    "beverage":   (200, 100, 350),
    "sweetener":  (8, 3, 15),
    "other":      (60, 30, 120),
}
DEFAULT_GRAMS = (80.0, 40.0, 160.0)

# Aromatics the dataset files under `vegetable`, but nobody eats 170 g of garlic.
# Portioned as seasonings regardless of their group tag.
AROMATIC_PORTIONS = (6.0, 3.0, 15.0)
AROMATICS = {
    "garlic", "ginger", "onion", "onions", "shallot", "chilli", "chili",
    "chilli pepper", "horseradish", "scallion", "spring onion", "leek",
    "wasabi", "raw garlic", "garlic powder", "fresh ginger",
}

# Roles whose portions absorb the energy target. Fat and accent stay near their
# natural serving size.
SCALABLE_ROLES = {"protein", "base", "veg"}

# Macro distribution across the day. Matches the deployed daily-recommendation
# service — see its /health response, "meal_macro_distribution": 20/40/40.
MEAL_RATIO = {"breakfast": 1, "lunch": 2, "dinner": 2}
_ratio_total = sum(MEAL_RATIO.values())
MEAL_SPLIT = {meal: n / _ratio_total for meal, n in MEAL_RATIO.items()}
# Offsets keep lunch and dinner off the same slice of the ranked pool, so the
# two 40% meals don't land on identical foods.
MEAL_ROTATION_OFFSET = {"breakfast": 0, "lunch": 5, "dinner": 11}
DEFAULT_COUNTS = {"breakfast": 3, "lunch": 3, "dinner": 3}

FORMS = {
    "breakfast": ["Bowl", "Porridge", "Scramble", "Hash", "Toast Plate"],
    "lunch": ["Salad", "Grain Bowl", "Soup", "Traybake", "Pilaf"],
    "dinner": ["Stew", "Traybake", "Curry", "Skillet", "Roast Plate"],
}

NUTRIENT_KEYS = {
    "calorie": "energy_kcal_per_100g",
    "protein": "protein_g_per_100g",
    "carbs": "carbs_g_per_100g",
    "fat": "fat_g_per_100g",
    "sat_fat": "sat_fat_g_per_100g",
    "sodium": "sodium_mg_per_100g",
    "sugar": "sugar_g_per_100g",
    "iron": "iron_mg_per_100g",
    "calcium": "calcium_mg_per_100g",
}


def _resolve_dataset() -> Optional[Path]:
    """The dataset, tolerating numbered copies like `ingredient_master.jsonl 4`.

    Re-downloading the file often lands a numbered duplicate and removes the
    plain name, which silently emptied the food pool. Take the newest match
    rather than failing closed.
    """
    if DATASET.exists():
        return DATASET
    candidates = sorted(
        DATASET.parent.glob("ingredient_master*.jsonl*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


@functools.lru_cache(maxsize=1)
def load(path: Optional[str] = None) -> List[Dict[str, Any]]:
    resolved = Path(path) if path else _resolve_dataset()
    if resolved is None or not resolved.exists():
        return []
    path = str(resolved)
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _canon(name: str) -> str:
    return str(name or "").strip().lower()


def _norm_biomarker(name: str) -> str:
    return _canon(name).replace("-", "").replace(" ", "")


def _interacts(food: Dict[str, Any], medications: List[str]) -> Optional[str]:
    meds = [_canon(m) for m in medications if m]
    if not meds:
        return None
    for finding in food.get("safety") or []:
        if finding.get("kind") not in {"drug_interactions", "contraindications"}:
            continue
        blob = _canon(f"{finding.get('target','')} {finding.get('span','')}")
        for med in meds:
            if med and med in blob:
                return f"{finding.get('kind')}: {finding.get('target') or med}"
    return None


def select(
    doctor_targets: Dict[str, str],
    *,
    diet: Optional[str] = None,
    allergens: Optional[List[str]] = None,
    medications: Optional[List[str]] = None,
    per_role: int = 26,
) -> Dict[str, Any]:
    """Score and bucket foods by the role they can play in a meal."""
    from config_discrete import TARGET_DIRECTIONS

    wanted = {}
    for biomarker, instruction in doctor_targets.items():
        d = -1 if str(instruction).lower().startswith(("reduce", "lower", "decrease")) else 1
        if str(instruction).lower().startswith(("increase", "raise")):
            d = 1
        wanted[_norm_biomarker(biomarker)] = (biomarker, d or TARGET_DIRECTIONS.get(biomarker, 0))

    allergens = [_canon(a) for a in (allergens or [])]
    medications = medications or []
    excluded = {"diet": 0, "allergen": 0, "interaction": 0, "no_nutrition": 0}
    interactions: List[str] = []

    scored: List[Dict[str, Any]] = []
    for food in load():
        if food.get("tier") != "specific":
            continue
        nutrition = food.get("nutrition") or {}
        if nutrition.get("energy_kcal_per_100g") is None:
            excluded["no_nutrition"] += 1
            continue
        if diet and diet not in (food.get("diets") or []):
            excluded["diet"] += 1
            continue
        if any(a in [_canon(x) for x in (food.get("allergens") or [])] for a in allergens):
            excluded["allergen"] += 1
            continue
        interaction = _interacts(food, medications)
        if interaction:
            excluded["interaction"] += 1
            interactions.append(f"{food['food']} — {interaction}")
            continue

        score = 0.0
        evidence: List[Dict[str, Any]] = []
        for effect in food.get("biomarker_effects") or []:
            key = _norm_biomarker(effect.get("biomarker", ""))
            if key not in wanted:
                continue
            display, direction = wanted[key]
            if int(effect.get("direction", 0)) != direction:
                continue
            # `best_grade` is the best across both paths. When the claim is direct
            # it must show the direct grade — strawberry -> hs-CRP is best_grade A
            # but direct_grade D, and presenting the A overstates it.
            attribution = str(effect.get("attribution") or "indirect").lower()
            shown_grade = (
                effect.get("direct_grade") if attribution == "direct" else effect.get("indirect_grade")
            ) or effect.get("best_grade") or "C"
            grade = GRADE_WEIGHT.get(str(shown_grade).upper(), 0.4)
            n_studies = int(effect.get("n_studies") or 0)
            n_records = int(effect.get("n_records") or 1)
            contra = int(effect.get("n_contradicting") or 0)
            depth = min(math.log10(1 + n_studies) / 3.0, 1.0)
            penalty = 1.0 - min(contra / max(n_records, 1), 0.5)
            # Direct evidence — the food itself was studied for this biomarker —
            # is far stronger than an indirect link through a subject the food
            # merely contains (e.g. egg -> hs-CRP via "zinc supplementation").
            contribution = grade * depth * penalty * (1.0 if attribution == "direct" else 0.55)
            score += contribution
            evidence.append({
                "biomarker": display,
                "direction": "lower" if direction == -1 else "raise",
                "grade": shown_grade,
                "best_grade": effect.get("best_grade"),
                "n_studies": n_studies,
                "max_sample_size": effect.get("max_sample_size"),
                "n_contradicting": contra,
                "n_papers": effect.get("n_papers"),
                "pmids": tuple(effect.get("pmids") or ()),
                "attribution": attribution,
                "direct_pmids": tuple(effect.get("direct_pmids") or ()),
                "direct_papers": effect.get("direct_papers"),
                "via": _top_via(effect),
                "weight": round(contribution, 3),
            })
        if score <= 0:
            continue

        groups = [g for g in (food.get("food_groups") or [])]
        scored.append({
            "food": food["food"],
            "groups": groups,
            "score": round(score, 3),
            "nutrition": nutrition,
            "evidence": sorted(evidence, key=lambda e: -e["weight"]),
            "diets": food.get("diets") or [],
            "n_biomarkers": len(evidence),
        })

    scored.sort(key=lambda f: -f["score"])
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for role, groups in ROLES.items():
        pool = [f for f in scored if any(g in groups for g in f["groups"])]
        if role == "protein":
            # `dairy` covers both yoghurt and butter. Require protein to carry a
            # real share of the food's energy, or butter ends up as the protein.
            pool = [f for f in pool if _protein_energy_share(f) >= 0.15]
        by_role[role] = pool[:per_role]

    return {
        "by_role": by_role,
        "all": scored,
        "excluded": excluded,
        "interactions": sorted(set(interactions))[:10],
    }


def _portion(food: Dict[str, Any]) -> Tuple[float, float, float]:
    if _canon(food.get("food")) in AROMATICS:
        return AROMATIC_PORTIONS
    for group in food.get("groups") or []:
        if group in GROUP_GRAMS:
            return GROUP_GRAMS[group]
    return DEFAULT_GRAMS


def _protein_energy_share(food: Dict[str, Any]) -> float:
    """Fraction of a food's energy that comes from protein."""
    n = food.get("nutrition") or {}
    kcal = float(n.get("energy_kcal_per_100g") or 0.0)
    protein = float(n.get("protein_g_per_100g") or 0.0)
    return (protein * 4.0) / kcal if kcal > 0 else 0.0


def _macros_for(food: Dict[str, Any], grams: float) -> Dict[str, float]:
    n = food["nutrition"]
    f = grams / 100.0
    return {k: float(n.get(src) or 0.0) * f for k, src in NUTRIENT_KEYS.items()}


# Items that can be dropped when a plate cannot get small enough, least
# essential first.
DROP_ORDER = ("accent", "fat", "veg")


def _plate_kcal(grams: Dict[str, float], chosen: List[Tuple[Dict[str, Any], str]]) -> float:
    return sum(_macros_for(f, grams[f["food"]])["calorie"] for f, _ in chosen)


def _fit(
    chosen: List[Tuple[Dict[str, Any], str]], target_kcal: float
) -> Tuple[List[Tuple[Dict[str, Any], str]], Dict[str, float]]:
    """Portion a plate so its energy lands near the target.

    Staples absorb the budget first. If the plate is still too large — energy-dense
    picks can exceed a small breakfast target even at minimum portions — shrink
    everything within its floor, and drop the least essential item as a last resort.
    """
    work = list(chosen)
    grams: Dict[str, float] = {}
    for _attempt in range(4):
        grams = {f["food"]: _portion(f)[0] for f, _ in work}

        fixed = sum(
            _macros_for(f, grams[f["food"]])["calorie"]
            for f, role in work if role not in SCALABLE_ROLES
        )
        scalable = [(f, r) for f, r in work if r in SCALABLE_ROLES]
        raw = sum(_macros_for(f, _portion(f)[0])["calorie"] for f, _ in scalable)
        remaining = target_kcal - fixed
        if raw > 0 and remaining > 0:
            scale = min(remaining / raw, 3.0)
            for food, _role in scalable:
                start, lo, hi = _portion(food)
                grams[food["food"]] = max(lo * 0.6, min(start * scale, hi))

        total = _plate_kcal(grams, work)
        if total <= target_kcal * 1.08:
            return work, grams

        # Shrink the whole plate, floors included, before dropping anything.
        factor = target_kcal / total
        for food, _role in work:
            _start, lo, _hi = _portion(food)
            grams[food["food"]] = max(lo * 0.5, grams[food["food"]] * factor)
        if _plate_kcal(grams, work) <= target_kcal * 1.08 or len(work) <= 2:
            return work, grams

        droppable = [(i, f) for i, (f, role) in enumerate(work) if role in DROP_ORDER]
        if not droppable:
            return work, grams
        idx = max(droppable, key=lambda c: _macros_for(c[1], grams[c[1]["food"]])["calorie"])[0]
        work.pop(idx)
    return work, grams


def compose_meal(
    meal: str,
    target_kcal: float,
    selection: Dict[str, Any],
    rng: random.Random,
    *,
    rotation: int = 0,
) -> Optional[Dict[str, Any]]:
    """Pick foods by role, then scale portions so the plate hits its energy target."""
    by_role = selection["by_role"]
    plan = [("protein", 1), ("base", 1), ("veg", 2), ("fat", 1), ("accent", 1)]
    if meal == "breakfast":
        plan = [("protein", 1), ("base", 1), ("accent", 1), ("fat", 1)]

    chosen: List[Tuple[Dict[str, Any], str]] = []
    used = set()
    for role, count in plan:
        pool = by_role.get(role) or []
        if not pool:
            continue
        offset = rotation % len(pool)
        rotated = pool[offset:] + pool[:offset]
        picked = 0
        for food in rotated:
            if food["food"] in used:
                continue
            chosen.append((food, role))
            used.add(food["food"])
            picked += 1
            if picked >= count:
                break
    if not chosen:
        return None

    chosen, grams_by_food = _fit(chosen, target_kcal)

    totals = {k: 0.0 for k in NUTRIENT_KEYS}
    items = []
    for food, _role in chosen:
        g = round(grams_by_food[food["food"]])
        m = _macros_for(food, g)
        for k in totals:
            totals[k] += m[k]
        items.append({"food": food["food"], "grams": g, "kcal": round(m["calorie"])})

    evidence = []
    # Dose-weighted evidence per biomarker: how much this plate actually acts on
    # each target, given the grams served and the strength of each food's edge.
    exposure: Dict[str, float] = {}
    for food, _role in chosen:
        grams = grams_by_food[food["food"]]
        for e in food["evidence"]:
            exposure[e["biomarker"]] = exposure.get(e["biomarker"], 0.0) + e["weight"] * grams / 100.0
        for e in food["evidence"][:2]:
            evidence.append({"food": food["food"], **e})

    return {
        "exposure": {k: round(v, 4) for k, v in exposure.items()},
        "title": _title([f for f, _ in chosen], meal, rng),
        "kcal": round(totals["calorie"]),
        "protein_g": round(totals["protein"], 1),
        "carbs_g": round(totals["carbs"], 1),
        "fat_g": round(totals["fat"], 1),
        "sat_fat_g": round(totals["sat_fat"], 1),
        "sodium_mg": round(totals["sodium"]),
        "iron_mg": round(totals["iron"], 1),
        "items": items,
        "ingredients": [f["food"] for f, _ in chosen],
        "evidence": sorted(evidence, key=lambda e: -e["weight"])[:4],
        "why": _why(evidence),
    }


def _title(foods: List[Dict[str, Any]], meal: str, rng: random.Random) -> str:
    names = [f["food"].strip().title() for f in foods[:2]]
    return f"{' & '.join(names)} {rng.choice(FORMS[meal])}"


def _why(evidence: List[Dict[str, Any]]) -> str:
    best: Dict[str, Dict[str, Any]] = {}
    for e in sorted(evidence, key=lambda x: -x["weight"]):
        if e["biomarker"] not in best:
            best[e["biomarker"]] = e
    bits = [
        f"{e['food']} → {e['direction']} {e['biomarker']} "
        f"(grade {e['grade']}, {e['n_studies']} studies)"
        for e in list(best.values())[:3]
    ]
    return "; ".join(bits)


def compose_day(
    *,
    day: int,
    date: str,
    daily_targets: Dict[str, float],
    selection: Dict[str, Any],
    counts: Optional[Dict[str, int]] = None,
    seed: int = 42,
    **_ignored: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    counts = counts or DEFAULT_COUNTS
    rng = random.Random(seed * 1000 + day)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for meal, share in MEAL_SPLIT.items():
        target_kcal = float(daily_targets.get("calorie", 2000.0)) * share
        options = []
        for i in range(int(counts.get(meal, 2))):
            rotation = (day - 1) * 3 + i + MEAL_ROTATION_OFFSET.get(meal, 0)
            recipe = compose_meal(meal, target_kcal, selection, rng, rotation=rotation)
            if recipe:
                options.append(recipe)
        out[meal] = options
    return out


def evidence_table(selection: Dict[str, Any], limit: int = 30) -> List[Dict[str, Any]]:
    rows = []
    for food in selection["all"][:limit]:
        top = food["evidence"][0] if food["evidence"] else {}
        rows.append({
            "food": food["food"],
            "direction": top.get("direction"),
            "biomarker": top.get("biomarker"),
            "grade": top.get("grade"),
            "n_studies": top.get("n_studies"),
            "kcal_100g": food["nutrition"].get("energy_kcal_per_100g"),
            "evidence": citation_for(top)[1] if top else None,
            "trials": citation_for(top)[0] if top else None,
            "contains": containment_for(top) if top else None,
        })
    return rows


# Better query terms than the raw column name.
SEARCH_TERMS = {
    "hs-CRP": "C-reactive protein",
    "HbA1c": "glycated hemoglobin OR HbA1c",
    "LDL cholesterol": "LDL cholesterol",
    "non-HDL cholesterol": "non-HDL cholesterol",
    "transferrin saturation": "transferrin saturation OR iron status",
    "free T3": "triiodothyronine",
    "microalbumin": "albuminuria",
    "eGFR": "glomerular filtration rate",
}

_CONDITION_KEYWORDS = {
    "triglycerides": ("triglycer", "dyslipidem", "lipid profile", "blood lipids", "hyperlipid"),
    "LDL cholesterol": ("ldl", "hypercholesterol", "cholesterol", "dyslipidem"),
    "total cholesterol": ("cholesterol", "dyslipidem", "lipid profile"),
    "non-HDL cholesterol": ("cholesterol", "dyslipidem"),
    "HbA1c": ("hba1c", "glycated", "glycaemic control", "glycemic control"),
    "insulin": ("insulin", "homa-ir"),
    "C-peptide": ("c-peptide",),
    "hs-CRP": ("crp", "c-reactive", "inflammat"),
    "homocysteine": ("homocystein",),
    "hemoglobin": ("anemia", "anaemia", "hemoglobin", "haemoglobin"),
    "vitamin B12": ("b12", "cobalamin"),
    "folate": ("folate", "folic"),
    "magnesium": ("magnesium",),
    "transferrin saturation": ("iron", "ferritin", "transferrin"),
    "eGFR": ("renal", "kidney", "gfr"),
    "microalbumin": ("albuminur", "microalbumin", "proteinur"),
    "free T3": ("thyroid",),
}


@functools.lru_cache(maxsize=1)
def _condition_citations() -> Dict[tuple, tuple]:
    """(food, biomarker) -> (pmid, condition text). Food-level and real.

    `biomarker_effects` in ingredient_master.jsonl are aggregates and carry no
    identifiers, so the only food-level PMIDs are on `conditions` and `safety`.
    Where a condition names the biomarker ("elevated triglycerides"), that PMID
    is a genuine citation for this food.
    """
    out: Dict[tuple, tuple] = {}
    for food in load():
        name = _canon(food.get("food"))
        for cond in food.get("conditions") or []:
            pmid = str(cond.get("pmid") or "").strip()
            if not pmid.isdigit():
                continue
            text = _canon(cond.get("condition"))
            for biomarker, keywords in _CONDITION_KEYWORDS.items():
                if any(k in text for k in keywords):
                    out.setdefault((name, biomarker), (pmid, cond.get("condition")))
    return out


# A food can contain a nutrient or a dietary component. It cannot contain a
# supplement product or a dietary pattern — yet the graph links broccoli to
# "ginger supplementation" and milk to 177 distinct supplement subjects. Those
# containment edges are spurious, so only these types are accepted.
CONTAINABLE_SUBJECT_TYPES = {"nutrient", "dietary_component", "food_item"}


def _top_via(effect: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The strongest subject an indirect link runs through, with its trials."""
    subjects = [
        v for v in (effect.get("via_subjects") or [])
        if v.get("subject_type") in CONTAINABLE_SUBJECT_TYPES
    ]
    if not subjects:
        return None
    best = max(subjects, key=lambda v: (v.get("n_studies") or 0))
    # An indirect link is two hops, each with its own evidence:
    #   hop 1  ingredient contains the subject
    #   hop 2  the subject moves the biomarker
    # Prefer PMIDs for hop 1 when the dataset carries them; fall back to the
    # containment source URLs it currently ships.
    containment_pmids = tuple(
        best.get("containment_pmids")
        or best.get("subject_ingredient_pmids")
        or best.get("ingredient_pmids")
        or ()
    )[:20]
    # A quarter of containment sources are PMC/PubMed papers; the rest are
    # consumer factsheets. Prefer the literature when both are present.
    _urls = list(best.get("containment_source_urls") or ())
    _lit = [u for u in _urls if "pubmed.ncbi" in u or "pmc.ncbi" in u]
    _urls = _lit + [u for u in _urls if u not in _lit]
    return {
        "subject": best.get("subject"),
        "subject_type": best.get("subject_type"),
        "grade": best.get("grade"),
        "n_studies": best.get("n_studies"),
        "trial_pmids": tuple(best.get("trial_pmids") or ())[:20],
        "containment_pmids": containment_pmids,
        "containment_urls": tuple(_urls)[:2],
        "containment_is_literature": bool(_lit),
    }


@functools.lru_cache(maxsize=1)
def _direct_pmid_breadth() -> Dict[str, int]:
    """How many distinct foods each direct PMID is cited for."""
    import collections

    counter: collections.Counter = collections.Counter()
    for food in load():
        seen = set()
        for effect in food.get("biomarker_effects") or []:
            seen.update(effect.get("direct_pmids") or ())
        counter.update(seen)
    return dict(counter)


def _paper_url(pmids, prefer_specific: bool = False) -> Optional[str]:
    """A single paper, not an OR-query.

    `direct_pmids` are specific to this food and biomarker, so one of them is a
    real citation. The multi-PMID query was only needed back when the dataset
    exposed a shared pool with no per-pair attribution.
    """
    ids = [str(p).strip() for p in (pmids or ()) if str(p).strip().isdigit()]
    if not ids:
        return None
    # Ranking by rarity was tried and made attributions worse — the frequently
    # cited PMIDs are usually the systematic reviews, the rare ones the noise.
    return f"https://pubmed.ncbi.nlm.nih.gov/{ids[0]}/"


def citation_for(row: Dict[str, Any]) -> tuple:
    """(url, label) — the trial establishing the food-biomarker link."""
    if row.get("attribution") == "direct" and row.get("direct_pmids"):
        return _paper_url(row["direct_pmids"]), "direct"
    via = row.get("via") or {}
    if via.get("trial_pmids"):
        return _paper_url(via["trial_pmids"]), f"via {via.get('subject')}"
    return _paper_url(row.get("pmids")), "pooled"


def containment_for(row: Dict[str, Any]) -> Optional[str]:
    """Hop 1: evidence that the ingredient carries the subject at all.

    Only meaningful for indirect links. Returns a PubMed query when the dataset
    supplies containment PMIDs, otherwise the source URL it ships today.
    """
    if row.get("attribution") == "direct":
        return None
    via = row.get("via") or {}
    if via.get("containment_pmids"):
        return _paper_url(via["containment_pmids"])
    urls = via.get("containment_urls") or ()
    return urls[0] if urls else None


def pubmed_pool_url(pmids, limit: int = 20) -> Optional[str]:
    """A PubMed query over the papers behind this food-biomarker edge.

    The dataset's `pmids` are the paper pool for an aggregated edge, not a
    per-ingredient attribution: a single PMID is shared with a median of 27 other
    food-biomarker effects (max 585). So a study of, say, zinc supplementation that
    happened to include milk lands in milk's pool. Presenting any one of them as
    "the citation for milk -> triglycerides" is unreliable, and that is what the
    zinc paper was.

    Linking to the set instead makes no claim about a specific paper, and lets the
    reader scan the actual evidence base. Closing this properly needs per-ingredient
    paper attribution in the dataset, not a change here.
    """
    ids = list(pmids or ())[:limit]
    if not ids:
        return None
    return "https://pubmed.ncbi.nlm.nih.gov/?term=" + "+OR+".join(ids)


def as_rationale(selection: Dict[str, Any], per_target: int = 5) -> List[Dict[str, Any]]:
    """Evidence rows for the day-30 narrative, balanced across doctor targets.

    Taking the globally top-scoring foods and their strongest edges silently drops
    whole targets: haemoglobin edges are all indirect and carry fewer studies than
    lipid ones, so a haemoglobin target ended up with no food behind it at all even
    though 153 eligible foods have one. Allocate a slot per target instead, so every
    biomarker the doctor asked for is represented by real foods and citations.
    """
    by_target: Dict[str, List[tuple]] = {}
    for food in selection["all"]:
        for e in food["evidence"]:
            by_target.setdefault(e["biomarker"], []).append((food, e))

    rows: List[Dict[str, Any]] = []
    seen = set()
    for biomarker, entries in by_target.items():
        entries.sort(key=lambda fe: -fe[1]["weight"])
        for food, e in entries[:per_target]:
            key = (food["food"], biomarker)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "ingredient": food["food"],
                "cluster": ", ".join(food["groups"]),
                "biomarker": biomarker,
                "intervention": (e.get("via") or {}).get("subject") or food["food"],
                "mechanism": (
                    f"{food['food']} — evidence to {e['direction']} {e['biomarker']} "
                    f"(grade {e['grade']}, {e['n_studies']} studies"
                    + (f", max n={e['max_sample_size']}" if e.get("max_sample_size") else "")
                    + (f", {e['n_contradicting']} contradicting" if e.get("n_contradicting") else "")
                    + ")"
                ),
                "grade": e["grade"],
                "n_studies": e["n_studies"],
                "n_papers": e.get("n_papers"),
                "evidence": citation_for(e)[1],
                "n_cited": len(e.get("direct_pmids") or ()) or len(((e.get("via") or {}).get("trial_pmids")) or ()),
                "subject_trials": citation_for(e)[0],
                "contains_subject": containment_for(e),
            })
    rows.sort(key=lambda r: (r["biomarker"], -(r["n_studies"] or 0)))
    return rows
