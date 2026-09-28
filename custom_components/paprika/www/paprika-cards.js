/*
 * Lovelace cards for the Paprika integration.
 *
 * Recipe data is pulled through the integration's response-only actions
 * (paprika.get_recipes / paprika.get_recipe) rather than entity attributes,
 * so a large library costs nothing in the state machine and bodies are only
 * fetched for the recipe actually being viewed.
 *
 * Plain custom elements, no build step and no bundled framework: the file is
 * served straight from the integration.
 */

const CARD_CSS = `
  :host { display: block; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
  .bar input, .bar select {
    background: var(--card-background-color, #1c1e22);
    color: var(--primary-text-color, #fff);
    border: 1px solid var(--divider-color, rgba(255,255,255,.12));
    border-radius: 10px; padding: 8px 10px; font: inherit; min-width: 0;
  }
  .bar input { flex: 1 1 140px; }
  .muted { color: var(--secondary-text-color, #9aa0a6); font-size: .85em; }
  .grid { display: grid; gap: 12px; grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr)); }
  .tile {
    cursor: pointer; border-radius: 14px; overflow: hidden;
    background: var(--ha-card-background, var(--card-background-color, #1c1e22));
    border: 1px solid var(--divider-color, rgba(255,255,255,.08));
    display: flex; flex-direction: column; transition: transform .12s ease;
  }
  .tile:hover, .tile:focus-visible { transform: translateY(-2px); outline: none;
    border-color: var(--primary-color, #03a9f4); }
  .tile img { width: 100%; aspect-ratio: 4/3; object-fit: cover; background: rgba(255,255,255,.04); display: block; }
  .tile .noimg { width: 100%; aspect-ratio: 4/3; display: grid; place-items: center;
    background: rgba(255,255,255,.04); font-size: 2em; }
  .tile .body { padding: 8px 10px; }
  .tile .name { font-weight: 600; line-height: 1.25; }
  .tile .meta { margin-top: 3px; font-size: .8em; color: var(--secondary-text-color, #9aa0a6); }
  .detail { position: fixed; inset: 0; z-index: 9; background: rgba(0,0,0,.6);
    display: grid; place-items: center; padding: 16px; }
  .sheet {
    max-width: 780px; width: 100%; max-height: 86vh; overflow: auto; border-radius: 18px;
    padding: 18px 20px; background: var(--ha-card-background, var(--card-background-color, #1c1e22));
    border: 1px solid var(--divider-color, rgba(255,255,255,.12));
  }
  .sheet h2 { margin: 0 0 4px; }
  .sheet h3 { margin: 16px 0 6px; }
  .sheet img { max-width: 100%; border-radius: 12px; margin-top: 10px; }
  .sheet ul, .sheet ol { padding-left: 20px; margin: 0; }
  .sheet li { margin: 3px 0; }
  .close { float: right; cursor: pointer; border: none; background: transparent;
    color: var(--primary-text-color, #fff); font-size: 1.4em; line-height: 1; }
  table.week { width: 100%; border-collapse: collapse; }
  table.week { table-layout: fixed; }
  table.week th, table.week td {
    border: 1px solid var(--divider-color, rgba(255,255,255,.1));
    padding: 6px 8px; vertical-align: top; font-size: .9em;
  }
  table.week th:first-child { width: 92px; }
  table.week th { text-align: left; font-weight: 600; white-space: nowrap; }
  table.week td.today { background: rgba(3,169,244,.1); }
  .slot { color: var(--secondary-text-color, #9aa0a6); white-space: nowrap; }
  .meal { cursor: pointer; display: flex; gap: 8px; align-items: center; padding: 3px 0; }
  .meal:hover .mtitle, .meal:focus-visible .mtitle { color: var(--primary-color, #03a9f4); }
  .meal:focus-visible { outline: none; }
  .meal img, .meal .thumb {
    width: 40px; height: 40px; flex: 0 0 40px; border-radius: 8px; object-fit: cover;
    background: rgba(255,255,255,.06);
  }
  .meal .thumb { display: grid; place-items: center; font-size: 1.1em; }
  .mtitle { min-width: 0; overflow-wrap: anywhere; }
  .plain { display: flex; gap: 8px; align-items: center; padding: 3px 0;
           color: var(--secondary-text-color, #9aa0a6); }
`;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const stars = (n) => "★".repeat(Math.max(0, Math.min(5, n | 0)));

async function fetchRecipe(hass, uid) {
  try {
    const res = await hass.callService(
      "paprika", "get_recipe", { uid }, undefined, false, true);
    return (res && res.response) || null;
  } catch (err) {
    return { name: "Could not load recipe", directions: [String(err)] };
  }
}

/* One detail sheet shared by both cards: opening a recipe from the week grid
   should look identical to opening it from the browser. */
function detailHtml(d) {
  if (!d) return "";
  return `
    <div class="detail">
      <div class="sheet">
        <button class="close" title="Close">×</button>
        <h2>${esc(d.name)}</h2>
        <div class="muted">
          ${esc((d.categories || []).join(" · "))}
          ${d.rating ? " · " + stars(d.rating) : ""}
          ${d.total_time ? " · " + esc(d.total_time) : ""}
          ${d.servings ? " · " + esc(d.servings) : ""}
        </div>
        ${d.photo_url ? `<img src="${esc(d.photo_url)}" alt="">` : ""}
        ${d.description ? `<p>${esc(d.description)}</p>` : ""}
        ${(d.ingredients || []).length
          ? `<h3>Ingredients</h3><ul>${d.ingredients.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
        ${(d.directions || []).length
          ? `<h3>Method</h3><ol>${d.directions.map((i) => `<li>${esc(i)}</li>`).join("")}</ol>` : ""}
        ${d.notes ? `<h3>Notes</h3><p>${esc(d.notes)}</p>` : ""}
        ${d.source_url ? `<p><a href="${esc(d.source_url)}" target="_blank" rel="noopener">Source</a></p>` : ""}
      </div>
    </div>`;
}

function wireDetail(root, close) {
  const btn = root.querySelector(".close");
  if (!btn) return;
  btn.addEventListener("click", close);
  root.querySelector(".detail").addEventListener("click", (e) => {
    if (e.target.classList.contains("detail")) close();
  });
}

class PaprikaRecipeBrowser extends HTMLElement {
  static getStubConfig() {
    return { type: "custom:paprika-recipe-browser", columns: 3 };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._recipes = [];
    this._categories = [];
    this._search = "";
    this._category = "";
    this._minRating = 0;
    this._loaded = false;
    this._open = null;
  }

  setConfig(config) {
    this._config = { columns: 3, limit: 120, ...config };
    this._loaded = false;
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    this._hass = hass;
    // hass is reassigned on every state change in the house; fetching there
    // would hammer the action. Load once, then only on user input.
    if (!this._loaded) {
      this._loaded = true;
      this._fetch();
    }
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      const res = await this._hass.callService(
        "paprika", "get_recipes",
        {
          search: this._search || undefined,
          category: this._category || undefined,
          min_rating: this._minRating || undefined,
          limit: this._config.limit,
        },
        undefined, false, true,
      );
      const data = (res && res.response) || {};
      this._recipes = data.recipes || [];
      this._pending = data.pending || 0;
      this._total = data.count || 0;
      if (!this._categories.length) {
        const lib = this._hass.states["sensor.paprika_recipes"];
        this._categories = (lib && lib.attributes.categories) || [];
      }
      this._error = null;
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
    }
    this._render();
  }

  async _openRecipe(uid) {
    this._open = await fetchRecipe(this._hass, uid);
    this._render();
  }

  _render() {
    const r = this.shadowRoot;
    const cols = this._config.columns || 3;
    const tiles = this._recipes.map((x) => `
      <div class="tile" tabindex="0" data-uid="${esc(x.uid)}">
        ${x.photo_url
          ? `<img loading="lazy" src="${esc(x.photo_url)}" alt="">`
          : `<div class="noimg">🍽️</div>`}
        <div class="body">
          <div class="name">${esc(x.name)}</div>
          <div class="meta">
            ${esc((x.categories || []).slice(0, 2).join(" · "))}
            ${x.rating ? " · " + stars(x.rating) : ""}
            ${x.total_time ? " · " + esc(x.total_time) : ""}
          </div>
        </div>
      </div>`).join("");

    const detail = detailHtml(this._open);

    r.innerHTML = `
      <style>${CARD_CSS}</style>
      <ha-card header="${esc(this._config.title || "Recipes")}">
        <div style="padding: 0 16px 16px;">
          <div class="bar">
            <input type="search" placeholder="Search recipes" value="${esc(this._search)}">
            <select>
              <option value="">All categories</option>
              ${this._categories.map((c) =>
                `<option value="${esc(c)}"${c === this._category ? " selected" : ""}>${esc(c)}</option>`).join("")}
            </select>
            <select class="rating">
              ${[0, 3, 4, 5].map((n) =>
                `<option value="${n}"${n === this._minRating ? " selected" : ""}>${n ? stars(n) + "+" : "Any rating"}</option>`).join("")}
            </select>
          </div>
          ${this._error ? `<div class="muted">Could not load recipes: ${esc(this._error)}</div>` : ""}
          <div class="muted" style="margin-bottom:8px;">
            Showing ${this._recipes.length} of ${this._total || 0}
            ${this._pending ? ` · ${this._pending} still syncing from Paprika` : ""}
          </div>
          <div class="grid" style="--cols:${cols};">${tiles}</div>
        </div>
      </ha-card>
      ${detail}`;

    const input = r.querySelector('input[type="search"]');
    let timer;
    input.addEventListener("input", (e) => {
      this._search = e.target.value;
      clearTimeout(timer);
      // Debounced: every keystroke would otherwise be an action call.
      timer = setTimeout(() => this._fetch(), 300);
    });
    r.querySelectorAll("select")[0].addEventListener("change", (e) => {
      this._category = e.target.value; this._fetch();
    });
    r.querySelector("select.rating").addEventListener("change", (e) => {
      this._minRating = parseInt(e.target.value, 10) || 0; this._fetch();
    });
    r.querySelectorAll(".tile").forEach((el) => {
      const go = () => this._openRecipe(el.dataset.uid);
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
    wireDetail(r, () => { this._open = null; this._render(); });
  }
}

class PaprikaMealPlan extends HTMLElement {
  static getStubConfig() {
    return { type: "custom:paprika-meal-plan", days: 7 };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._meals = [];
    this._loaded = false;
    this._open = null;
  }

  setConfig(config) {
    this._config = { days: 7, title: "Meal plan", ...config };
    this._loaded = false;
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._fetch();
    }
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      // paprika.get_meals rather than calendar.get_events: a CalendarEvent
      // has nowhere to carry the recipe it came from, so the calendar route
      // can show what is planned but cannot link through to the recipe.
      const res = await this._hass.callService(
        "paprika", "get_meals", { days: this._config.days }, undefined, false, true);
      this._meals = ((res && res.response) || {}).meals || [];
      this._error = null;
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
    }
    this._render();
  }

  async _openRecipe(uid) {
    this._open = await fetchRecipe(this._hass, uid);
    this._render();
  }

  _render() {
    const base = new Date(); base.setHours(0, 0, 0, 0);
    const days = [];
    for (let i = 0; i < this._config.days; i++) {
      const d = new Date(base); d.setDate(base.getDate() + i);
      days.push(d);
    }
    const iso = (d) => {
      const p = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    };

    const slots = [...new Set(this._meals.map((m) => m.slot).filter(Boolean))];
    const order = {};
    for (const m of this._meals) order[m.slot] = m.slot_order;
    slots.sort((a, b) => (order[a] || 0) - (order[b] || 0));

    const cell = {};
    for (const m of this._meals) {
      const k = `${m.date}|${m.slot}`;
      (cell[k] = cell[k] || []).push(m);
    }

    const rows = days.map((d) => {
      const today = iso(d) === iso(base);
      const tds = slots.map((slot) => {
        const items = cell[`${iso(d)}|${slot}`] || [];
        const thumb = (m) =>
          m.photo_url
            ? `<img loading="lazy" src="${esc(m.photo_url)}" alt="">`
            : `<span class="thumb">🍽️</span>`;
        const inner = items.map((m) =>
          m.recipe_loaded
            ? `<div class="meal" tabindex="0" data-uid="${esc(m.recipe_uid)}">
                 ${thumb(m)}<span class="mtitle">${esc(m.name)}</span>
               </div>`
            // Free-text meals, and recipes whose body has not synced yet,
            // are shown but not offered as links that would do nothing.
            : `<div class="plain" title="${m.recipe_uid ? "Still syncing from Paprika" : "No recipe attached"}">
                 ${thumb(m)}<span class="mtitle">${esc(m.name)}</span>
               </div>`
        ).join("");
        return `<td class="${today ? "today" : ""}">${inner || '<span class="muted">—</span>'}</td>`;
      }).join("");
      return `<tr><th class="${today ? "today" : ""}">${
        d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
      }</th>${tds}</tr>`;
    }).join("");

    this.shadowRoot.innerHTML = `
      <style>${CARD_CSS}</style>
      <ha-card header="${esc(this._config.title)}">
        <div style="padding: 0 16px 16px;">
          ${this._error ? `<div class="muted">Could not load meals: ${esc(this._error)}</div>` : ""}
          ${slots.length ? `
          <table class="week">
            <tr><th></th>${slots.map((x) => `<th class="slot">${esc(x)}</th>`).join("")}</tr>
            ${rows}
          </table>
          <div class="muted" style="margin-top:8px;">Tap a meal to see the recipe.</div>`
          : `<div class="muted">No meals planned in the next ${this._config.days} days.</div>`}
        </div>
      </ha-card>
      ${detailHtml(this._open)}`;

    this.shadowRoot.querySelectorAll(".meal").forEach((el) => {
      const go = () => this._openRecipe(el.dataset.uid);
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
    wireDetail(this.shadowRoot, () => { this._open = null; this._render(); });
  }
}

customElements.define("paprika-recipe-browser", PaprikaRecipeBrowser);
customElements.define("paprika-meal-plan", PaprikaMealPlan);

window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "paprika-recipe-browser",
    name: "Paprika Recipe Browser",
    description: "Searchable grid of your Paprika recipes, with ingredients and method.",
  },
  {
    type: "paprika-meal-plan",
    name: "Paprika Meal Plan",
    description: "A week of planned meals as a day x slot grid.",
  },
);
