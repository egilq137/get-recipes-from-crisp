// Recipe ratings app. Recipes come from recipes.json (written by the weekly job);
// ratings live in a Google Sheet behind an Apps Script web app (window.RATINGS_API_URL).
// One rating per (recipe, person): saving again overwrites that person's row.
(() => {
  "use strict";

  const API = (window.RATINGS_API_URL || "").trim();
  const NAME_KEY = "crisp-ratings-name";

  const state = { weeks: [], ratings: [] };
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  const norm = (s) => (s || "").trim().toLowerCase();

  function myName() {
    return $("name").value.trim();
  }

  function storage(fn) {
    try { return fn(window.localStorage); } catch { return null; }
  }

  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
  }

  function notice(msg) {
    const n = $("notice");
    n.textContent = msg;
    n.hidden = !msg;
  }

  // ---------------------------------------------------------------- data
  async function loadRecipes() {
    const res = await fetch("recipes.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`recipes.json: ${res.status}`);
    state.weeks = (await res.json()).weeks || [];
  }

  async function loadRatings() {
    if (!API) return;
    const res = await fetch(API, { cache: "no-store" });
    if (!res.ok) throw new Error(`ratings: ${res.status}`);
    state.ratings = (await res.json()).ratings || [];
  }

  async function saveRating(rating) {
    // text/plain keeps this a "simple" request, so the browser skips the CORS
    // preflight that Apps Script can't answer.
    const res = await fetch(API, { method: "POST", body: JSON.stringify(rating) });
    const body = await res.json();
    if (!body.ok) throw new Error(body.error || "save failed");
    state.ratings = body.ratings || state.ratings;
  }

  function ratingsFor(id) {
    return state.ratings.filter((r) => r.recipe_id === id);
  }

  function mine(id) {
    const me = norm(myName());
    return me ? ratingsFor(id).find((r) => norm(r.name) === me) : undefined;
  }

  function average(rows, key) {
    const vals = rows.map((r) => Number(r[key])).filter((v) => v >= 1 && v <= 5);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  // ---------------------------------------------------------------- saving
  async function update(recipe, patch) {
    const name = myName();
    if (!name) {
      $("name").focus();
      toast("Enter your name first");
      return;
    }
    const current = mine(recipe.id) || {};
    const rating = {
      recipe_id: recipe.id,
      title: recipe.title,
      name,
      liked: current.liked || "",
      ease: current.ease || "",
      favourite: current.favourite === true || current.favourite === "TRUE",
      comment: current.comment || "",
      ...patch,
    };

    // Optimistic: show the change now, then reconcile with what the sheet returns.
    state.ratings = state.ratings.filter(
      (r) => !(r.recipe_id === recipe.id && norm(r.name) === norm(name))
    );
    state.ratings.push(rating);
    render();

    try {
      await saveRating(rating);
      toast("Saved");
    } catch (err) {
      console.error(err);
      toast("Couldn't save — try again");
      await loadRatings().catch(() => {});
    }
    render();
  }

  // ---------------------------------------------------------------- render
  function starInput(recipe, key, value, label) {
    const v = Number(value) || 0;
    return el("div", { class: "stars", role: "group", "aria-label": label },
      [1, 2, 3, 4, 5].map((n) =>
        el("button", {
          type: "button",
          class: n <= v ? "on" : "",
          "aria-label": `${label}: ${n} star${n > 1 ? "s" : ""}`,
          "aria-pressed": String(n === v),
          disabled: !API,
          // Clicking the current value again clears it.
          onclick: () => update(recipe, { [key]: n === v ? "" : n }),
        }, "★")
      )
    );
  }

  function summary(rows) {
    const liked = average(rows, "liked");
    const ease = average(rows, "ease");
    const n = (key) => rows.filter((r) => Number(r[key]) >= 1).length;
    const favs = rows.filter((r) => r.favourite === true || r.favourite === "TRUE");
    return el("div", { class: "summary" },
      liked === null
        ? el("span", { class: "none" }, "Not rated yet")
        : el("span", {}, "Liked ", el("span", { class: "avg" }, `★ ${liked.toFixed(1)}`), ` (${n("liked")})`),
      ease !== null &&
        el("span", {}, "Ease ", el("span", { class: "avg" }, `★ ${ease.toFixed(1)}`), ` (${n("ease")})`),
      favs.length > 0 &&
        el("span", { class: "favs" }, `♥ ${favs.map((r) => r.name).join(", ")}`)
    );
  }

  function card(recipe, week) {
    const rows = ratingsFor(recipe.id);
    const me = mine(recipe.id) || {};
    const fav = me.favourite === true || me.favourite === "TRUE";
    const comments = rows.filter((r) => (r.comment || "").trim());

    const metaBits = [week.label, recipe.time];
    if (recipe.kcal) metaBits.push(`${recipe.kcal} kcal/serving`);

    return el("article", { class: "card" },
      el("h3", {}, el("a", { href: encodeURI(recipe.page) }, recipe.title)),
      el("div", { class: "meta" },
        metaBits.join(" · "),
        recipe.pdf && [" · ", el("a", { href: encodeURI(recipe.pdf) }, "PDF")]
      ),
      summary(rows),
      el("div", { class: "mine" + (API ? "" : " disabled") },
        el("span", { class: "label" }, "I liked it"),
        starInput(recipe, "liked", me.liked, "How much I liked it"),
        el("span", { class: "label" }, "Easy to make"),
        starInput(recipe, "ease", me.ease, "How easy it was"),
        el("span", { class: "label" }, "Favourite"),
        el("div", {},
          el("button", {
            type: "button",
            class: "heart" + (fav ? " on" : ""),
            "aria-pressed": String(fav),
            "aria-label": "Favourite",
            disabled: !API,
            onclick: () => update(recipe, { favourite: !fav }),
          }, "♥")
        ),
        el("textarea", {
          rows: 1,
          maxlength: 500,
          placeholder: "Notes (e.g. add more chilli)",
          disabled: !API,
          onchange: (e) => update(recipe, { comment: e.target.value.trim() }),
        }, me.comment || "")
      ),
      comments.length > 0 &&
        el("ul", { class: "comments" },
          comments.map((r) => el("li", {}, el("b", {}, r.name), `: ${r.comment}`))
        )
    );
  }

  function render() {
    // Keep the textarea someone is typing in alive across re-renders.
    const active = document.activeElement;
    if (active && active.tagName === "TEXTAREA" && $("list").contains(active)) return;

    const query = norm($("search").value);
    const view = $("view").value;
    const sort = $("sort").value;

    let items = [];
    for (const week of state.weeks) {
      for (const recipe of week.recipes || []) items.push({ recipe, week });
    }
    if (query) items = items.filter(({ recipe }) => norm(recipe.title).includes(query));
    if (view === "favourites") {
      items = items.filter(({ recipe }) =>
        ratingsFor(recipe.id).some((r) => r.favourite === true || r.favourite === "TRUE"));
    } else if (view === "unrated") {
      items = items.filter(({ recipe }) => !mine(recipe.id)?.liked);
    }

    const score = ({ recipe }) => {
      const rows = ratingsFor(recipe.id);
      const liked = average(rows, "liked") ?? 0;
      const ease = average(rows, "ease") ?? 0;
      return sort === "liked" ? liked : sort === "easy" ? ease : liked + ease;
    };

    const list = $("list");
    list.replaceChildren();
    if (!items.length) {
      list.append(el("p", { class: "empty" }, "No recipes match."));
      return;
    }

    if (sort === "newest") {
      let current = null;
      for (const item of items) {
        if (item.week !== current) {
          current = item.week;
          list.append(el("h2", { class: "week" }, current.label));
        }
        list.append(card(item.recipe, item.week));
      }
    } else {
      items
        .map((item, i) => ({ item, i, s: score(item) }))
        .sort((a, b) => b.s - a.s || a.i - b.i)   // stable: newest first on ties
        .forEach(({ item }) => list.append(card(item.recipe, item.week)));
    }
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    $("name").value = storage((s) => s.getItem(NAME_KEY)) || "";
    $("name").addEventListener("change", () => {
      storage((s) => s.setItem(NAME_KEY, myName()));
      render();
    });
    for (const id of ["search", "view", "sort"]) $(id).addEventListener("input", render);

    if (!API) notice("Ratings aren't connected yet — recipes are read-only.");

    try {
      await loadRecipes();
    } catch (err) {
      console.error(err);
      $("list").replaceChildren(el("p", { class: "empty" }, "Couldn't load the recipes."));
      return;
    }
    render();

    try {
      await loadRatings();
    } catch (err) {
      console.error(err);
      notice("Couldn't load ratings right now — showing recipes only.");
    }
    render();
  }

  boot();
})();
