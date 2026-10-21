# crisp-recipes

Every week, [crisp.nl](https://crisp.nl/weekbox) publishes the three recipes in your
meal box — in Dutch, for 2 portions, with no nutrition information.

This job runs once a week on GitHub Actions and emails you those recipes:

- **translated** to English: steps by Gemini with checks that reject any step whose numbers or length don't match the Dutch (those fall back to DeepL); title and ingredient names by DeepL,
- with the **ingredient list scaled** to the portion count you actually cook (default
  4); the steps keep crisp.nl's own amounts and say which portion count they're for,
- with a **nutrition table** per serving (macros, 10 minerals, 11 vitamins) from
  **USDA FoodData Central**, including the share of a 2000 kcal daily requirement,
- a **realistic cooking time** that accounts for prep, not just the site's active time,
- and **chef's notes** on which of your cookware each step needs and what can run in
  parallel on the spare equipment, cross-referenced to the step numbers.

You get one HTML email with a PDF per recipe attached, and every week is added to a
**recipe website** where you and others can rate each dish (how much you liked it, how
easy it was), mark favourites and leave notes — no account needed.

## How it works

```
scrape → scale amounts → translate → nutrition → times → chef's notes → render → email
```

| Module | Responsibility |
|---|---|
| `scraper.py` | Playwright; finds the box's 3 recipes, extracts title, portions, ingredients (with amounts) and steps |
| `models.py` | Pure dataclasses + amount arithmetic (unicode fractions, scaling) |
| `translator.py` | DeepL for title and ingredient names; Gemini for steps, with guardrails and per-step DeepL fallback |
| `nutrition.py` | Gemini estimates grams → USDA lookup with candidate scoring → aggregation |
| `nutrients.py` | The 28 tracked nutrients: labels, units, USDA ids, daily values |
| `cooking_time.py` | Official time from the site + prep-aware realistic estimate |
| `chef_notes.py` | Senior-chef tips: cookware per step and what can run in parallel, with step references |
| `render.py` | HTML email body and per-recipe PDFs |
| `site.py` | Recipe website: per-week recipe pages + PDFs, rebuilt index |
| `site_assets/` | The website's ratings app (plain HTML/CSS/JS) |
| `apps_script/Code.gs` | Ratings API: a Google Apps Script web app over a Google Sheet |
| `pipeline.py` | Orchestration |

A few design notes worth knowing:

- **Only the ingredient list is scaled.** Rewriting numbers inside step text was
  error-prone (`1el bij 2p` became `2el bij 2p`), so steps keep the site's wording and
  their heading names the portion count the amounts are for.
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

## Recipe website

The weekly job publishes to the `gh-pages` branch, which GitHub Pages serves at
`https://<user>.github.io/<repo>/`. Past weeks stay; a re-run replaces that week.
Ratings are stored in a Google Sheet you own, through a small Apps Script web app, so
people rate with just a name — no Google or GitHub account needed.

One-time setup:

1. **Ratings sheet.** Create a Google Sheet, open **Extensions → Apps Script**, replace
   the code with [`apps_script/Code.gs`](apps_script/Code.gs) and save.
2. **Deploy it.** **Deploy → New deployment → Web app**, *Execute as:* Me, *Who has
   access:* Anyone. Authorize, then copy the web-app URL (ends in `/exec`).
3. **Tell the site.** In the repo, **Settings → Secrets and variables → Actions →
   Variables**, add `RATINGS_API_URL` with that URL.
4. **Run the workflow once** (Actions → Weekly recipes → Run workflow). It creates the
   `gh-pages` branch.
5. **Turn on Pages.** **Settings → Pages → Build and deployment**: *Deploy from a
   branch*, branch `gh-pages`, folder `/ (root)`.

Ratings appear as rows in the sheet's `ratings` tab (one per person per recipe), so you
can fix or delete one by hand. If you edit `Code.gs` later, use **Deploy → Manage
deployments → Edit → New version** to keep the same URL.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CRISP_BOX` | `vegan` | which weekbox to scrape |
| `TARGET_PORTIONS` | `4` | portions the amounts are written for; set `2` to keep the site's |
| `TARGET_LANG` | `EN-GB` | target language |
| `STEP_TRANSLATOR` | `gemini` | `gemini` (checked, DeepL fallback) or `deepl` for the steps |
| `SITE_DIR` | — | publish the week to this folder (CI sets `site`) |
| `RATINGS_API_URL` | — | Apps Script web-app URL the website saves ratings to |

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
- The recipe website is public to anyone with its URL (it's marked `noindex`, so search
  engines skip it), and so is the ratings API. Fine for friends and family; don't post
  the link publicly.
