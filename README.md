# crisp-recipes

Every week, [crisp.nl](https://crisp.nl/weekbox) publishes the three recipes in your
meal box — in Dutch, for 2 portions, with no nutrition information.

This job runs once a week on GitHub Actions and emails you those recipes:

- **translated** to English (DeepL),
- **scaled** to the portion count you actually cook (default 4) — ingredients *and*
  the amounts written inside the steps,
- with a **nutrition table** per serving (macros, 10 minerals, 11 vitamins) from
  **USDA FoodData Central**, including the share of a 2000 kcal daily requirement,
- a **realistic cooking time** that accounts for prep, not just the site's active time,
- and **chef's notes** on splitting the cooking between two people, cross-referenced to
  the step numbers.

You get one HTML email with a PDF per recipe attached.

## How it works

```
scrape → scale amounts → translate → nutrition → times → chef's notes → render → email
```

| Module | Responsibility |
|---|---|
| `scraper.py` | Playwright; finds the box's 3 recipes, extracts title, portions, ingredients (with amounts) and steps |
| `models.py` | Pure dataclasses + amount arithmetic (unicode fractions, scaling) |
| `translator.py` | DeepL, batched per recipe; ingredient names get a disambiguating suffix |
| `nutrition.py` | Gemini estimates grams → USDA lookup with candidate scoring → aggregation |
| `nutrients.py` | The 28 tracked nutrients: labels, units, USDA ids, daily values |
| `cooking_time.py` | Official time from the site + prep-aware realistic estimate |
| `chef_notes.py` | Senior-chef tips for two cooks, with step references |
| `render.py` | HTML email body and per-recipe PDFs |
| `pipeline.py` | Orchestration |

A few design notes worth knowing:

- **Scaling happens before translation.** The unit allowlist and "amount directly
  followed by a unit" word order only hold in the Dutch source text — DeepL rewrites
  `¾ zakje kaas` as `¾ of a bag of cheese`. Only food quantities are scaled; oven
  temperatures, times and thicknesses are left alone.
- **Nutrition is per serving.** Totals scale with the batch, per-serving figures don't.
- **A wrong USDA match is worse than none.** Candidates must contain the query's
  identity words, so `cumin, ground` can't match `Chicken, ground`. Unmatched
  ingredients are excluded and labelled as such rather than silently dropped.
- **Grams are estimates**; the nutrient values themselves are real USDA data. Each
  ingredient shows the USDA food it matched so you can sanity-check it.

## Setup

You need API keys for [DeepL](https://www.deepl.com/pro-api),
[Gemini](https://aistudio.google.com/apikey) and
[USDA FoodData Central](https://fdc.nal.usda.gov/api-key-signup.html) (all have free
tiers), plus a Gmail
[App Password](https://myaccount.google.com/apppasswords) — not your normal password.

### Local

```bash
cp .env.example .env    # then fill in the values
uv sync
uv run playwright install firefox
uv run python -m crisp_recipes
```

`.env` is git-ignored. Never commit it.

### Weekly on GitHub Actions

The workflow in `.github/workflows/weekly.yml` runs every Saturday at 07:00 UTC and can
also be triggered by hand from the **Actions** tab.

Add these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `DEEPL_API_KEY` | DeepL API key |
| `USDA_FDC_API_KEY` | FoodData Central key |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `SENDER_EMAIL` | the Gmail address sending the mail |
| `RECIPIENTS` | comma-separated list of recipients |

Optionally, under **Variables**: `CRISP_BOX` (`vegan`, `vega`, `weekbox`, `snelle`,
`snelle-vega`) and `TARGET_PORTIONS`.

Generated PDFs are also uploaded as run artifacts, so you can retrieve them even if the
email fails.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CRISP_BOX` | `vegan` | which weekbox to scrape |
| `TARGET_PORTIONS` | `4` | portions the amounts are written for; set `2` to keep the site's |
| `TARGET_LANG` | `EN-GB` | DeepL target language |

## Tests

```bash
uv run pytest
```

The suite runs fully offline against real page data captured in `tests/fixtures/`, so it
doesn't depend on crisp.nl being up. Two opt-in live checks exist for when you want to
verify the outside world still behaves:

```bash
CRISP_LIVE=1 uv run pytest tests/test_scraper_live.py   # crisp.nl structure unchanged
uv run pytest tests/test_nutrition_live.py              # USDA key + match quality
```

Run the scraper one after a crisp.nl redesign — it's the early warning that the
selectors drifted.

## Known limitations

- Ingredient weights are LLM estimates; unusual pantry items ("to taste") are guesses.
- USDA matching is good but not perfect — check the food shown next to each ingredient.
- Inline prose references such as `(1 tbsp per 2 people)` have their quantity scaled but
  not the "2 people" part.
