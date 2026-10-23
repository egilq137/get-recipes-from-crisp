/**
 * Ratings API for the recipe site, backed by the Google Sheet this script is bound to.
 *
 * GET  -> {ok: true, ratings: [...]}            every rating
 * POST -> body {recipe_id, title, name, liked, ease, favourite, comment}
 *         upserts that person's rating for that recipe, returns the same as GET.
 *
 * One row per (recipe_id, name), matched case-insensitively, so a person re-rating a
 * recipe overwrites their earlier row. The sheet is the source of truth: you can edit
 * or delete rows by hand. Setup steps are in the README ("Recipe website").
 */
const SHEET_NAME = "ratings";
const HEADERS = ["recipe_id", "title", "name", "liked", "ease", "favourite", "comment", "updated"];
const RECIPE_ID_RE = /^\d{4}_Week_\d+\/[a-z0-9-]{1,80}$/;

function doGet() {
  return json_({ ok: true, ratings: readAll_() });
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: "invalid JSON" });
  }

  const row = clean_(body);
  if (row.error) return json_({ ok: false, error: row.error });

  // Serialize writers so two people saving at once can't both append a row.
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = sheet_();
    const values = sheet.getDataRange().getValues();
    const name = row.name.toLowerCase();
    let target = -1;
    for (let i = 1; i < values.length; i++) {
      if (values[i][0] === row.recipe_id && String(values[i][2]).toLowerCase() === name) {
        target = i + 1;  // 1-based sheet row
        break;
      }
    }
    const out = HEADERS.map((h) => row[h]);
    if (target === -1) sheet.appendRow(out);
    else sheet.getRange(target, 1, 1, HEADERS.length).setValues([out]);
  } finally {
    lock.releaseLock();
  }
  return json_({ ok: true, ratings: readAll_() });
}

function clean_(b) {
  const recipeId = String(b.recipe_id || "");
  if (!RECIPE_ID_RE.test(recipeId)) return { error: "bad recipe_id" };
  const name = String(b.name || "").trim().slice(0, 40);
  if (!name) return { error: "name required" };
  const stars = (v) => {
    const n = Number(v);
    return Number.isInteger(n) && n >= 1 && n <= 5 ? n : "";
  };
  return {
    recipe_id: recipeId,
    title: String(b.title || "").slice(0, 200),
    name: name,
    liked: stars(b.liked),
    ease: stars(b.ease),
    favourite: b.favourite === true,
    comment: String(b.comment || "").trim().slice(0, 500),
    updated: new Date(),
  };
}

function readAll_() {
  const values = sheet_().getDataRange().getValues();
  const header = values[0];
  return values.slice(1)
    .filter((r) => r[0])
    .map((r) => {
      const o = {};
      header.forEach((h, i) => { o[h] = r[i] instanceof Date ? r[i].toISOString() : r[i]; });
      return o;
    });
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
