// Cooking view: tick off ingredients and steps (remembered on this device per recipe)
// and keep the screen awake while cooking, where the browser supports it.
(() => {
  "use strict";

  const key = "crisp-ticks:" + (document.body.dataset.recipeId || location.pathname);
  const boxes = [...document.querySelectorAll(".ingredients input")];
  const steps = [...document.querySelectorAll(".step")];

  function load() {
    try { return JSON.parse(localStorage.getItem(key)) || {}; } catch { return {}; }
  }
  function save() {
    const state = {
      ingredients: boxes.map((b) => b.checked),
      steps: steps.map((s) => s.getAttribute("aria-pressed") === "true"),
    };
    try { localStorage.setItem(key, JSON.stringify(state)); } catch { /* private mode */ }
  }

  const saved = load();
  boxes.forEach((b, i) => {
    b.checked = Boolean(saved.ingredients && saved.ingredients[i]);
    b.addEventListener("change", save);
  });
  steps.forEach((s, i) => {
    s.setAttribute("aria-pressed", String(Boolean(saved.steps && saved.steps[i])));
    s.addEventListener("click", () => {
      s.setAttribute("aria-pressed", String(s.getAttribute("aria-pressed") !== "true"));
      save();
    });
  });

  document.getElementById("reset").addEventListener("click", () => {
    boxes.forEach((b) => { b.checked = false; });
    steps.forEach((s) => s.setAttribute("aria-pressed", "false"));
    save();
  });

  // Screen Wake Lock: the browser drops it when the tab is hidden, so re-acquire it
  // when the cook comes back to the page.
  const wake = document.getElementById("wake");
  if (!("wakeLock" in navigator)) return;
  let lock = null;
  let wanted = false;
  wake.hidden = false;
  wake.setAttribute("aria-pressed", "false");

  async function acquire() {
    try {
      lock = await navigator.wakeLock.request("screen");
      lock.addEventListener("release", () => { lock = null; render(); });
    } catch {
      wanted = false;
    }
    render();
  }
  function render() {
    const on = Boolean(lock);
    wake.setAttribute("aria-pressed", String(on));
    wake.textContent = on ? "Screen stays on" : "Keep screen on";
  }

  wake.addEventListener("click", async () => {
    wanted = !lock;
    if (lock) await lock.release();
    else await acquire();
  });
  document.addEventListener("visibilitychange", () => {
    if (wanted && !lock && document.visibilityState === "visible") acquire();
  });
})();
