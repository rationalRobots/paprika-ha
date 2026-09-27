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
  table.week th, table.week td {
    border: 1px solid var(--divider-color, rgba(255,255,255,.1));
    padding: 6px 8px; vertical-align: top; font-size: .9em;
  }
  table.week th { text-align: left; font-weight: 600; white-space: nowrap; }
  table.week td.today { background: rgba(3,169,244,.1); }
  .slot { color: var(--secondary-text-color, #9aa0a6); white-space: nowrap; }
`;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const stars = (n) => "★".repeat(Math.max(0, Math.min(5, n | 0)));

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
    try {
      const res = await this._hass.callService(
        "paprika", "get_recipe", { uid }, undefined, false, true);
      this._open = (res && res.response) || null;
    } catch (err) {
      this._open = { name: "Could not load recipe", directions: [String(err)] };
    }
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

    const d = this._open;
    const detail = d ? `
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
      </div>` : "";

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
    const close = r.querySelector(".close");
    if (close) {
      close.addEventListener("click", () => { this._open = null; this._render(); });
      r.querySelector(".detail").addEventListener("click", (e) => {
        if (e.target.classList.contains("detail")) { this._open = null; this._render(); }
      });
    }
  }
}

class PaprikaMealPlan extends HTMLElement {
  static getStubConfig() {
    return { type: "custom:paprika-meal-plan", days: 7 };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
    this._loaded = false;
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

  _calendars() {
    if (this._config.entities) return this._config.entities;
    // Every Paprika meal-type calendar except the roll-up, which would
    // duplicate every entry.
    return Object.keys(this._hass.states).filter(
      (e) => e.startsWith("calendar.paprika_") && e !== "calendar.paprika_all_meals");
  }

  async _fetch() {
    if (!this._hass) return;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    try {
      const res = await this._hass.callService(
        "calendar", "get_events",
        { start_date_time: this._local(start), duration: { days: this._config.days } },
        { entity_id: this._calendars() }, false, true);
      const resp = (res && res.response) || {};
      this._events = [];
      for (const [entity, payload] of Object.entries(resp)) {
        const slot = (this._hass.states[entity]?.attributes.friendly_name || entity)
          .replace(/^Paprika\s*/i, "");
        for (const ev of payload.events || []) this._events.push({ slot, ...ev });
      }
      this._error = null;
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
    }
    this._render();
  }

  _local(d) {
    // calendar.get_events wants a naive local datetime string; toISOString
    // would silently shift it to UTC and skew the first/last day.
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  _render() {
    const days = [];
    const base = new Date(); base.setHours(0, 0, 0, 0);
    for (let i = 0; i < this._config.days; i++) {
      const d = new Date(base); d.setDate(base.getDate() + i);
      days.push(d);
    }
    const slots = [...new Set(this._events.map((e) => e.slot))].sort();
    const key = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const byDaySlot = {};
    for (const ev of this._events) {
      const when = new Date((ev.start || "").length <= 10 ? `${ev.start}T00:00:00` : ev.start);
      const k = `${key(when)}|${ev.slot}`;
      (byDaySlot[k] = byDaySlot[k] || []).push(ev.summary);
    }

    const rows = days.map((d) => {
      const isToday = key(d) === key(base);
      const cells = slots.map((s) =>
        `<td class="${isToday ? "today" : ""}">${(byDaySlot[`${key(d)}|${s}`] || [])
          .map((x) => esc(x)).join("<br>") || "<span class=muted>—</span>"}</td>`).join("");
      return `<tr><th class="${isToday ? "today" : ""}">${
        d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
      }</th>${cells}</tr>`;
    }).join("");

    this.shadowRoot.innerHTML = `
      <style>${CARD_CSS}</style>
      <ha-card header="${esc(this._config.title)}">
        <div style="padding: 0 16px 16px;">
          ${this._error ? `<div class="muted">Could not load meals: ${esc(this._error)}</div>` : ""}
          ${slots.length ? `
          <table class="week">
            <tr><th></th>${slots.map((s) => `<th class="slot">${esc(s)}</th>`).join("")}</tr>
            ${rows}
          </table>` : `<div class="muted">No meals planned in the next ${this._config.days} days.</div>`}
        </div>
      </ha-card>`;
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
