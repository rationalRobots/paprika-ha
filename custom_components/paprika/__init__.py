"""The Paprika integration."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import PaprikaApi
from .const import (
    CARDS_URL,
    CARDS_VERSION,
    DOMAIN,
    PHOTO_DIR,
    PHOTO_URL_BASE,
    SERVICE_GET_GROCERIES,
    SERVICE_GET_MEALS,
    SERVICE_GET_RECIPE,
    SERVICE_GET_RECIPES,
)
from .coordinator import PaprikaCoordinator
from .data import PaprikaConfigEntry, PaprikaRuntimeData

_PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]


LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: PaprikaConfigEntry) -> bool:
    """Set up Paprika from a config entry."""

    coordinator = PaprikaCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        update_interval=timedelta(minutes=15),
    )

    token = entry.data["token"]
    client = PaprikaApi(token)
    entry.runtime_data = PaprikaRuntimeData(client=client, coordinator=coordinator)

    # Must happen before the first refresh, or that refresh starts from an
    # empty cache and refetches everything.
    await coordinator.async_load_cache()

    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    await coordinator.async_config_entry_first_refresh()

    _async_register_services(hass)
    await _async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    # entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: PaprikaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


def _coordinator_for(hass: HomeAssistant, entry_id: str | None):
    """Resolve which config entry to answer from.

    Nearly everyone has a single Paprika account, so config_entry_id is
    optional and only has to be given when there is more than one.
    """
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if getattr(e, "runtime_data", None)
    ]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(f"No loaded Paprika entry {entry_id}")
    if not entries:
        raise ServiceValidationError("Paprika is not loaded")
    if len(entries) > 1:
        raise ServiceValidationError(
            "Several Paprika accounts are configured; pass config_entry_id"
        )
    return entries[0].runtime_data.coordinator


def _summarise(uid, recipe, categories: dict[str, str], photo: str = "") -> dict:
    """The fields a browser needs, without the body."""
    return {
        "uid": uid,
        "name": recipe.get("name") or "",
        # Stored as uids upstream, so map them or every card shows GUIDs.
        "categories": [categories.get(c, c) for c in (recipe.get("categories") or [])],
        "rating": recipe.get("rating") or 0,
        # The local copy: Paprika's own photo_url expires within hours.
        "photo_url": photo,
        "total_time": recipe.get("total_time") or "",
        "servings": recipe.get("servings") or "",
        "source_url": recipe.get("source_url") or "",
    }


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_GET_RECIPES):
        return

    async def _get_recipes(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        search = (call.data.get("search") or "").strip().lower()
        category = (call.data.get("category") or "").strip().lower()
        min_rating = int(call.data.get("min_rating") or 0)
        limit = int(call.data.get("limit") or 50)

        results = []
        for uid, recipe in coordinator.data.recipes.items():
            if min_rating and (recipe.get("rating") or 0) < min_rating:
                continue
            names = [
                coordinator.data.categories.get(c, c).lower()
                for c in (recipe.get("categories") or [])
            ]
            if category and category not in names:
                continue
            if search and search not in (recipe.get("name") or "").lower():
                continue
            results.append(
                _summarise(uid, recipe, coordinator.data.categories,
                           coordinator.photo_ref(uid))
            )

        results.sort(key=lambda r: r["name"].lower())
        return {
            "count": len(results),
            "pending": coordinator.data.recipes_pending,
            "recipes": results[:limit],
        }

    async def _get_recipe(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        uid = call.data["uid"]
        recipe = coordinator.data.recipes.get(uid)
        if recipe is None:
            raise ServiceValidationError(f"Recipe {uid} is not loaded yet")
        # Ingredients and directions are newline-delimited text upstream;
        # split them so cards do not each have to reimplement that.
        return {
            **_summarise(uid, recipe, coordinator.data.categories,
                         coordinator.photo_ref(uid)),
            "description": recipe.get("description") or "",
            "notes": recipe.get("notes") or "",
            "nutritional_info": recipe.get("nutritional_info") or "",
            "prep_time": recipe.get("prep_time") or "",
            "cook_time": recipe.get("cook_time") or "",
            "difficulty": recipe.get("difficulty") or "",
            "source": recipe.get("source") or "",
            "ingredients": [
                ln.strip()
                for ln in (recipe.get("ingredients") or "").splitlines()
                if ln.strip()
            ],
            "directions": [
                ln.strip()
                for ln in (recipe.get("directions") or "").splitlines()
                if ln.strip()
            ],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPES,
        _get_recipes,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): str,
                vol.Optional("search"): str,
                vol.Optional("category"): str,
                vol.Optional("min_rating"): vol.All(vol.Coerce(int), vol.Range(0, 5)),
                vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(1, 500)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    async def _get_meals(call: ServiceCall) -> dict:
        """Planned meals in a date window, each carrying its recipe uid.

        The calendar entities cannot answer this: a CalendarEvent has nowhere
        to put the recipe it came from, so a dashboard built on them can show
        what is planned but cannot link through to the recipe.
        """
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        start = call.data.get("start_date") or dt_util.now().date()
        if isinstance(start, str):
            start = dt_util.parse_date(start) or dt_util.now().date()
        days = int(call.data.get("days") or 7)
        end = start + timedelta(days=days)

        recipes = coordinator.data.recipes
        out = []
        for meal in coordinator.data.meals:
            when = meal["date"]
            if not (start <= when < end):
                continue
            recipe_uid = meal.get("recipe_uid")
            # A meal can be free text with no recipe behind it; say so rather
            # than handing back a uid that resolves to nothing.
            loaded = recipe_uid in recipes if recipe_uid else False
            out.append(
                {
                    "date": when.isoformat(),
                    "slot": (meal.get("type") or {}).get("name") or "",
                    "slot_order": (meal.get("type") or {}).get("order_flag") or 0,
                    "name": meal.get("name") or "",
                    "recipe_uid": recipe_uid or "",
                    "recipe_loaded": loaded,
                    "photo_url": coordinator.photo_ref(recipe_uid) if loaded else "",
                    "order_flag": meal.get("order_flag") or 0,
                }
            )
        out.sort(key=lambda m: (m["date"], m["slot_order"], m["order_flag"]))
        return {"count": len(out), "start": start.isoformat(), "days": days, "meals": out}

    async def _get_groceries(call: ServiceCall) -> dict:
        """Grocery items with their parts kept separate.

        A todo entity can only carry one summary string, and for Paprika that
        is the raw recipe line -- "1/2 fennel bulb, trimmed and finely
        chopped, fronds reserved for garnish".

        Paprika does store a normalised "ingredient", but it cannot be
        trusted on its own: it truncates multi-word ingredients, giving
        "cherry" for cherry tomatoes and "fennel" for fennel bulb, which is
        worse than the raw line rather than better. So shopping_term is built
        by cleaning the name instead -- dropping the recipe's parenthetical
        notes and everything after the first comma, which is preparation
        rather than product -- and only falls back to the ingredient when
        there is no name at all.
        """
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        include_purchased = bool(call.data.get("include_purchased"))

        def clean(name: str) -> str:
            text = re.sub(r"\([^)]*\)", " ", name)
            text = text.split(",")[0]
            return re.sub(r"\s+", " ", text).strip(" -–—.")

        items = []
        for g in coordinator.data.groceries:
            if g.get("purchased") and not include_purchased:
                continue
            ingredient = (g.get("ingredient") or "").strip()
            quantity = (g.get("quantity") or "").strip()
            name = (g.get("name") or "").strip()
            term = clean(name) or f"{quantity} {ingredient}".strip() or name
            items.append(
                {
                    "uid": g.get("uid"),
                    "name": name,
                    "ingredient": ingredient,
                    "quantity": quantity,
                    "shopping_term": term,
                    "aisle": g.get("aisle") or "",
                    "recipe": g.get("recipe") or "",
                    "purchased": bool(g.get("purchased")),
                }
            )
        items.sort(key=lambda i: (i["aisle"].lower(), i["shopping_term"].lower()))
        return {"count": len(items), "items": items}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_GROCERIES,
        _get_groceries,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): str,
                vol.Optional("include_purchased"): bool,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MEALS,
        _get_meals,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): str,
                vol.Optional("start_date"): str,
                vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(1, 31)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPE,
        _get_recipe,
        schema=vol.Schema(
            {vol.Required("uid"): str, vol.Optional("config_entry_id"): str}
        ),
        supports_response=SupportsResponse.ONLY,
    )


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the Lovelace cards from the integration and register them.

    Shipping the cards here rather than asking for a separate HACS frontend
    repo keeps the two halves versioned together -- the cards call this
    integration's own actions, so a mismatch between them is the likeliest way
    for a dashboard to break.
    """
    if hass.data.get(f"{DOMAIN}_frontend_registered"):
        return
    hass.data[f"{DOMAIN}_frontend_registered"] = True

    photo_dir = Path(hass.config.path(PHOTO_DIR))
    await hass.async_add_executor_job(lambda: photo_dir.mkdir(parents=True, exist_ok=True))

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                # Downloaded recipe photos. Cached hard: the file for a given
                # uid only changes when the recipe's picture does, and the
                # card asks for it on every tile.
                PHOTO_URL_BASE,
                str(photo_dir),
                cache_headers=True,
            ),
            StaticPathConfig(
                CARDS_URL,
                str(Path(__file__).parent / "www" / "paprika-cards.js"),
                # The file changes with the integration, not per request; the
                # version query string below is what busts the cache.
                cache_headers=False,
            )
        ]
    )

    versioned = f"{CARDS_URL}?v={CARDS_VERSION}"
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        LOGGER.warning(
            "Could not register the Paprika cards automatically. Add %s as a "
            "Lovelace resource of type 'module' by hand.",
            versioned,
        )
        return

    try:
        if hasattr(resources, "async_get_info"):
            await resources.async_get_info()
        existing = [item["url"] for item in resources.async_items()]
        # Match on the path so a version bump replaces rather than duplicates.
        stale = [u for u in existing if u.split("?")[0] == CARDS_URL and u != versioned]
        if versioned in existing and not stale:
            return
        for item in list(resources.async_items()):
            if item["url"] in stale:
                await resources.async_delete_item(item["id"])
        if versioned not in existing:
            await resources.async_create_item({"res_type": "module", "url": versioned})
            LOGGER.info("Registered Paprika Lovelace cards at %s", versioned)
    except Exception:  # noqa: BLE001 - never block setup over a dashboard resource
        LOGGER.exception(
            "Failed to register the Paprika cards; add %s manually as a module resource",
            versioned,
        )
