# Carb Data Pipeline — Integration Spec

**Purpose:** define how the fast-food / food carb dictionary in Bolus Helper gets built,
verified, and kept current from trusted sources — as a component that is **completely
separate from the insulin dose calculation.**

**Status:** draft / planning. Nothing here is wired up yet. The app currently ships a
broad set of *best-effort estimates* labeled `est.` in the UI.

---

## 1. Guiding principles

1. **Separation of concerns.** The food/carb data and the dose-calculation engine are
   independent components. The dose engine consumes a single number (grams of carb) and
   knows nothing about where it came from. The carb pipeline produces that number and
   knows nothing about insulin. They can be built, tested, and (eventually) regulated on
   separate tracks. This is deliberate: the dose engine is the higher-risk component and
   should not inherit risk or churn from the food database.

2. **Every value has a provenance.** No carb number is "just there." Each is tagged with
   the source it came from and when it was last confirmed. The UI already distinguishes
   an unverified estimate (`est.`) from a confirmed value (`✓ Source`).

3. **Trust ordering is explicit.** When two sources disagree, a documented precedence
   decides which wins (see §3).

4. **Estimates are allowed but always labeled.** Broad coverage with honest labeling beats
   narrow coverage. A visible `est.` badge is the safety mechanism — the user (a parent or
   the student) is told to confirm before relying on it.

---

## 2. Data sources

| Source | Best for | Access | Notes |
|---|---|---|---|
| **USDA FoodData Central** | Non-restaurant foods: snacks, candy, fruit, dairy, drinks, generic school-lunch items | Free public API, key required | Authoritative, government-maintained. `Branded` and `Foundation`/`SR Legacy` datasets. |
| **Restaurant official nutrition** | The gold standard for a specific chain's menu items | Per-chain — PDFs, allergen/nutrition pages, some JSON feeds | Highest trust for that chain. No single format; needs per-chain adapters. |
| **Nutritionix** | Broad restaurant coverage in one place, fast to integrate | Commercial API, app id + key, rate-limited | Great breadth; treat as a strong estimate to be *confirmed* by an official source where stakes are high. |

### Recommended backbone
- **Nutritionix** for broad initial restaurant coverage (fills the dictionary fast).
- **Official restaurant feeds** to verify and upgrade the high-frequency items.
- **USDA FoodData Central** for all non-restaurant foods.

---

## 3. Source precedence (conflict resolution)

When more than one source has a value for the same item, use the highest available:

```
1. Official restaurant nutrition   (label: "Official")
2. USDA FoodData Central           (label: "USDA")      — for non-restaurant foods
3. Nutritionix                     (label: "Nutritionix")
4. Best-effort estimate            (label: "est.")      — no external confirmation
```

Rules:
- A value only earns a green `✓` badge at levels 1–3. Level 4 keeps the yellow `est.` badge.
- If Official and Nutritionix disagree by more than a set tolerance (suggest **±10%** or
  **±5 g**, whichever is larger), keep Official, and log the discrepancy for review rather
  than silently overwriting.
- Record the *serving basis* with every value (e.g. "6-inch", "medium", "1 slice",
  "½ lb entrée"). Most carb errors are really serving-size mismatches, not bad numbers.

---

## 4. Data model

The app's current row format is intentionally minimal and forward-compatible:

```js
["Chain", "Item name", grams]
```

Verification lives in a separate override map so the 400+ rows don't have to change:

```js
const VERIFIED = {
  "McDonald’s|Big Mac": "Official",
  // key = "Chain|Item"  (exact match to FOODS)
  // value = "Official" | "USDA" | "Nutritionix"
};
```

Target schema once the pipeline is generating data (one record per item):

```json
{
  "chain": "McDonald’s",
  "item": "Big Mac",
  "serving": "1 sandwich",
  "carbs_g": 45,
  "source": "Official",
  "source_ref": "https://www.mcdonalds.com/.../nutrition",
  "confirmed_at": "2026-07-18",
  "nutritionix_id": "...",
  "usda_fdc_id": null,
  "tolerance_flag": false
}
```

The build step flattens this into the app's `FOODS` rows plus the `VERIFIED` map, so the
runtime data structure the app loads stays tiny and the rich metadata lives in the pipeline.

---

## 5. Nutritionix integration

- **Endpoints:**
  - `POST /v2/natural/nutrients` — natural-language lookup ("medium fries"), returns
    `nf_total_carbohydrate` and serving info. Good for matching how people actually talk.
  - `GET /v2/search/instant` — typeahead / branded-item search to resolve a chain + item
    to a stable Nutritionix item id.
  - `GET /v2/search/item?nix_item_id=...` — fetch a specific branded item by id.
- **Auth:** `x-app-id` + `x-app-key` headers. Store as secrets, never in the client HTML.
- **Rate limits:** commercial tier limited — batch nightly, cache aggressively, never call
  from the phone at dose time.
- **Matching strategy:** resolve each `FOODS` row to a `nix_item_id` once, store the id,
  then refresh values on a schedule rather than re-searching each time.
- **Field:** carbs come from `nf_total_carbohydrate` (grams). Capture `serving_qty` /
  `serving_unit` and reconcile against the serving basis in our row.

## 6. USDA FoodData Central integration

- **Endpoints:** `GET /v1/foods/search` to find an item, `GET /v1/food/{fdcId}` for detail.
- **Auth:** free `api_key` query param (data.gov key).
- **Carb field:** nutrient number **205** ("Carbohydrate, by difference"), grams per the
  food's serving/portion. Prefer `Branded` dataset for packaged snacks; `SR Legacy` /
  `Foundation` for generic foods (banana, apple, white rice).
- **Use:** run this for the non-restaurant categories — Snacks, Candy, Fruit & Dairy,
  Drinks, School Lunch, Treat a Low.

## 7. Official restaurant feeds

- No universal format. Build small per-chain adapters, prioritized by how often the family
  eats there.
- Where a chain publishes structured data (some expose JSON behind their nutrition
  calculator), parse it directly. Otherwise, a periodic manual confirmation against the
  published PDF/website, recorded with `confirmed_at`, is acceptable.
- Every official confirmation upgrades the item to `✓ Official` and takes precedence.

---

## 8. Build & update workflow

1. **Seed** — one pass through Nutritionix + USDA to populate values and ids for all rows.
2. **Verify** — run per-chain official adapters; upgrade matched items to `Official`.
3. **Diff & flag** — compare sources; flag any item outside tolerance for human review.
4. **Emit** — regenerate `FOODS` rows + `VERIFIED` map (a build artifact, not hand-edited).
5. **Schedule** — re-run monthly, or when a chain announces a menu/recipe change.
6. **Version** — stamp each data build with a date so a bad update can be rolled back.

Nothing in this workflow touches the dose engine. The only interface between the two is the
grams value the user adds to their plate.

---

## 9. Open risks / to decide

- **Serving-size drift** is the top source of carb error — enforce a serving basis on every
  row and surface it in the UI.
- **Combo / build-your-own items** (Chipotle components, Cane's boxes) vary by how they're
  assembled; keep these as components or clearly-labeled estimates.
- **Regional & LTO (limited-time) items** change constantly; decide whether to include them
  and how fast they expire from the dataset.
- **Client secrets** — Nutritionix/USDA keys must live server-side or in a build step, never
  in the shipped HTML.
- **Regulatory scope** — this pipeline is a food reference. The dose engine's clinical and
  regulatory pathway is tracked separately and is out of scope for this document.
