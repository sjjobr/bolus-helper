#!/usr/bin/env python3
"""
usda_verify.py — confirm the NON-RESTAURANT carb estimates in Bolus Helper
against USDA FoodData Central, and emit VERIFIED entries for the ones that match.

The dose engine is never touched. This only reads the FOODS list and checks numbers.

Usage:
    export USDA_API_KEY=your_key_here      # never hard-code the key
    python3 usda_verify.py                 # prints a report + a VERIFIED snippet

What it does:
  - pulls the FOODS rows out of index.html
  - for the non-restaurant categories, searches USDA FDC
  - reads carbs (nutrient 205) for the best match, scaled to a sensible serving
  - compares to our estimate; within tolerance -> propose "USDA" verified
  - prints a report and a ready-to-paste VERIFIED map you can review before adding

Nothing is written back automatically. You review, then paste the good ones into
the VERIFIED map in index.html / bolus-helper.html.
"""

import os, re, sys, json, time, urllib.parse, urllib.request

API_KEY = os.environ.get("USDA_API_KEY")
if not API_KEY:
    sys.exit("Set USDA_API_KEY in your environment first:  export USDA_API_KEY=...")

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "index.html")

# Only verify generic / packaged foods here. Restaurant items should be confirmed
# against each chain's official nutrition data instead (see the spec).
NONRESTAURANT = {"Snacks", "Candy", "Fruit & Dairy", "Drinks", "School Lunch", "Treat a Low"}

TOLERANCE_G = 5      # grams
TOLERANCE_PCT = 0.10 # or 10%, whichever is larger

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"


def load_foods():
    html = open(HTML, encoding="utf-8").read()
    body = re.search(r"const FOODS = \[(.*?)\n\];", html, re.S).group(1)
    rows = re.findall(r'\["([^"]*)","([^"]*)",(\-?\d+)\]', body)
    return [(c, n, int(g)) for c, n, g in rows]


def clean_query(name):
    # drop parenthetical serving notes for the search text
    return re.sub(r"\([^)]*\)", "", name).replace("’", "'").strip()


def usda_search(query):
    params = urllib.parse.urlencode({
        "api_key": API_KEY,
        "query": query,
        "pageSize": 3,
        "dataType": "Branded,SR Legacy,Foundation",
    })
    req = urllib.request.Request(f"{SEARCH_URL}?{params}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def carbs_from_food(food):
    """Return (carbs_g, basis_str) best-effort for a single USDA food record."""
    # nutrient 205 = Carbohydrate, by difference (per 100g unless label serving given)
    per100 = None
    for nut in food.get("foodNutrients", []):
        if str(nut.get("nutrientNumber")) == "205":
            per100 = nut.get("value")
            break
    if per100 is None:
        return None, None
    # Branded items usually carry a label serving size in grams
    ss = food.get("servingSize")
    ssu = (food.get("servingSizeUnit") or "").lower()
    if ss and ssu in ("g", "gram", "grams"):
        return round(per100 * ss / 100.0), f"{ss} g serving"
    return round(per100), "per 100 g"


def within_tol(est, usda):
    return abs(est - usda) <= max(TOLERANCE_G, est * TOLERANCE_PCT)


def main():
    foods = load_foods()
    verified = {}
    print(f"Checking {sum(1 for c,_,_ in foods if c in NONRESTAURANT)} non-restaurant items "
          f"against USDA FoodData Central...\n")
    for cat, name, est in foods:
        if cat not in NONRESTAURANT:
            continue
        q = clean_query(name)
        try:
            data = usda_search(q)
        except Exception as e:
            print(f"  [error] {name}: {e}")
            continue
        hits = data.get("foods", [])
        if not hits:
            print(f"  [no match] {name} (est {est} g)")
            continue
        usda_g, basis = carbs_from_food(hits[0])
        desc = hits[0].get("description", "?")
        if usda_g is None:
            print(f"  [no carb data] {name} <- {desc}")
            continue
        ok = within_tol(est, usda_g)
        mark = "OK " if ok else "CHK"
        print(f"  [{mark}] {name:<34} est {est:>3} g | USDA {usda_g:>3} g ({basis}) <- {desc}")
        if ok:
            verified[f"{cat}|{name}"] = "USDA"
        time.sleep(0.15)  # be gentle on the rate limit

    print("\n--- VERIFIED snippet (review, then paste into the VERIFIED map) ---")
    for k, v in verified.items():
        print(f'  "{k}": "{v}",')
    print(f"\n{len(verified)} items matched within tolerance "
          f"(+/- {TOLERANCE_G} g or {int(TOLERANCE_PCT*100)}%). "
          "Items marked CHK need a manual look — usually a serving-size mismatch.")


if __name__ == "__main__":
    main()
