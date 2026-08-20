"""
Reads Macro-Details.xlsm into a macro-profile lookup.

Stdlib only — an .xlsm is a zip of XML, so no openpyxl dependency. This is the
local stand-in for the `macro_profile` table that change 1 of the spec puts in
Postgres next to `recipe_pool`.

Normalisation done here (the sheet's formats do not match what the pool's band
SQL parses):
    en-dash   "60–80"     -> (60, 80)
    open low  "<19"       -> (0, 18)
    open high "71+"       -> (71, 120)
    intensity "step_up"   -> "stepup"   (macro_profile_version suffix)
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_HERE = Path(__file__).resolve().parent
WORKBOOK = _HERE / "Macro-Details.xlsm"
if not WORKBOOK.exists():
    WORKBOOK = _HERE.parent / "Macro-Details.xlsm"

INTENSITY_SUFFIX = {"step_down": "stepdown", "maintain": "maintain", "step_up": "stepup"}
ENGINE_TO_SHEET = {"recovery": "step_down", "maintain": "maintain", "full": "step_up"}

# Sheet header -> our field name. Anything not listed is carried as an extra.
FIELD_MAP = {
    "calories (kcal)": "calories_kcal",
    "protein (g)": "protein_g",
    "carbohydrates (g)": "carbohydrates_g",
    "total fat (g)": "total_fat_g",
    "sodium (mg) ≤": "sodium_mg_max",
    "saturated fat (g) ≤": "saturated_fat_g_max",
    "cholesterol (mg) ≤": "cholesterol_mg_max",
    "sugar-added (g) ≤": "added_sugar_g_max",
    "fibre (g)": "fiber_g",
    "fiber (g)": "fiber_g",
    "calcium (mg)": "calcium_mg",
    "iron (mg)": "iron_mg",
    "potassium (mg)": "potassium_mg",
    "vitamin c (mg)": "vitamin_c_mg",
    "vitamin e (mg)": "vitamin_e_mg",
    "vitamin d (iu)": "vitamin_d_iu",
}


def _col(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_band(text: Any) -> Optional[Tuple[float, float]]:
    s = str(text or "").strip().replace("–", "-").replace("—", "-").replace(" ", "")
    if not s or s.lower() in {"any", "all"}:
        return (float("-inf"), float("inf"))
    if s.startswith("<"):
        return (0.0, float(s[1:]) - 1.0)
    if s.endswith("+"):
        return (float(s[:-1]), float("inf"))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return None


def _rows(z: zipfile.ZipFile, target: str, shared: List[str]):
    sheet = ET.fromstring(z.read(target))
    for row in sheet.iter(NS + "row"):
        cells: Dict[int, str] = {}
        for c in row.iter(NS + "c"):
            v = c.find(NS + "v")
            if v is None or v.text is None:
                continue
            cells[_col(c.get("r"))] = shared[int(v.text)] if c.get("t") == "s" else v.text
        if cells:
            yield [cells.get(i, "") for i in range(max(cells) + 1)]


def load(path: Path = WORKBOOK) -> Dict[str, Any]:
    """Return {'rows': [...], 'sheets': {action_id: sheet_name}, 'warnings': [...]}."""
    if not Path(path).exists():
        return {"rows": [], "sheets": {}, "warnings": [f"Workbook not found at {path}"]}

    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    sheet_targets = {
        s.get("name"): (rmap[s.get(rid)] if rmap[s.get(rid)].startswith("xl/") else "xl/" + rmap[s.get(rid)])
        for s in wb.iter(NS + "sheet")
    }
    try:
        shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    except KeyError:
        shared = []

    rows: List[Dict[str, Any]] = []
    sheets: Dict[int, str] = {}
    warnings: List[str] = []

    for name, target in sheet_targets.items():
        m = re.match(r"C(\d\d)\b", name.strip())
        if not m:
            continue
        action_id = int(m.group(1))
        sheets[action_id] = name

        header: Optional[List[str]] = None
        idx: Dict[str, int] = {}
        for raw in _rows(z, target, shared):
            lowered = [str(c).strip().lower() for c in raw]
            if header is None:
                if "sex" in lowered and "intensity" in lowered:
                    header = lowered
                    for i, h in enumerate(header):
                        if h in FIELD_MAP:
                            idx[FIELD_MAP[h]] = i
                    idx["sex"] = header.index("sex")
                    idx["intensity"] = header.index("intensity")
                    for key, label in (("age", "age band"), ("weight", "weight band (kg)"), ("height", "height band (cm)")):
                        for i, h in enumerate(header):
                            if h.startswith(label[:10]):
                                idx[key] = i
                                break
                continue
            if len(raw) <= idx.get("intensity", 0) or str(raw[idx["sex"]]).strip() not in {"Male", "Female"}:
                continue
            entry: Dict[str, Any] = {
                "action_id": action_id,
                "sheet": name,
                "sex": str(raw[idx["sex"]]).strip(),
                "intensity": str(raw[idx["intensity"]]).strip(),
                "age_band": parse_band(raw[idx["age"]]),
                "weight_band": parse_band(raw[idx["weight"]]),
                "height_band": parse_band(raw[idx["height"]]),
            }
            for field, i in idx.items():
                if field in {"sex", "intensity", "age", "weight", "height"}:
                    continue
                try:
                    entry[field] = float(raw[i])
                except (ValueError, IndexError, TypeError):
                    entry[field] = None
            rows.append(entry)

        if header and not any(h in ("fibre (g)", "fiber (g)") for h in header):
            warnings.append(f"{name}: no fibre column")

    if warnings:
        warnings = [
            f"{len(warnings)} of {len(sheets)} action sheets have no fibre target — "
            "fibre adherence cannot be scored, and the Intensity Rules sheet requires "
            "Fibre ≥ 0.60 for a lipid step-up."
        ]
    return {"rows": rows, "sheets": sheets, "warnings": warnings}


def resolve(
    data: Dict[str, Any], *, action_id: int, intensity: str,
    sex: str, age: float, weight_kg: float, height_cm: float,
) -> Optional[Dict[str, Any]]:
    """Find the single row matching this patient's bucket."""
    sheet_intensity = ENGINE_TO_SHEET.get(intensity, intensity)
    for r in data.get("rows", []):
        if r["action_id"] != action_id or r["intensity"] != sheet_intensity:
            continue
        if r["sex"].lower() != str(sex).lower():
            continue
        for value, band in ((age, r["age_band"]), (weight_kg, r["weight_band"]), (height_cm, r["height_band"])):
            if band is None or not (band[0] <= value <= band[1]):
                break
        else:
            return r
    return None


def to_daily_targets(row: Dict[str, Any], fallback_fiber_g: float = 25.0) -> Tuple[Dict[str, float], List[str]]:
    """Map a workbook row onto the five adherence features."""
    notes: List[str] = []
    kcal = float(row.get("calories_kcal") or 0.0)
    fiber = row.get("fiber_g")
    if fiber in (None, 0):
        fiber = fallback_fiber_g
        notes.append(
            f"Workbook has no fibre target for this bucket — using {fallback_fiber_g:g} g so the "
            "score is computable. Fibre adherence is not trustworthy until the sheet carries it."
        )
    targets = {
        "calorie": kcal,
        "protein": float(row.get("protein_g") or 0.0),
        "carbs": float(row.get("carbohydrates_g") or 0.0),
        "fat": float(row.get("total_fat_g") or 0.0),
        "fiber": float(fiber),
    }
    return targets, notes


def bound_violations(row: Dict[str, Any], weight_kg: float) -> List[str]:
    """Check the workbook row against the platform's own MACRO_BOUNDS."""
    out: List[str] = []
    kcal = float(row.get("calories_kcal") or 0.0)
    protein = float(row.get("protein_g") or 0.0)
    carbs = float(row.get("carbohydrates_g") or 0.0)
    if kcal <= 0:
        return out
    g_per_kg = protein / max(weight_kg, 1e-6)
    if g_per_kg < 0.8:
        out.append(f"protein {protein:g} g = {g_per_kg:.2f} g/kg, below MACRO_BOUNDS floor of 0.8")
    carbs_pct = carbs * 4.0 / kcal * 100.0
    if carbs_pct > 60.0:
        out.append(f"carbohydrate {carbs_pct:.1f}% of energy, above MACRO_BOUNDS ceiling of 60%")
    return out
