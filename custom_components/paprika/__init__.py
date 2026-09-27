"""The Paprika integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from .api import PaprikaApi
from .const import (
    CARDS_URL,
    CARDS_VERSION,
    DOMAIN,
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


def _summarise(uid, recipe, categories: dict[str, str]) -> dict:
    """The fields a browser needs, without the body."""
    return {
        "uid": uid,
        "name": recipe.get("name") or "",
        # Stored as uids upstream, so map them or every card shows GUIDs.
        "categories": [categories.get(c, c) for c in (recipe.get("categories") or [])],
        "rating": recipe.get("rating") or 0,
        "photo_url": recipe.get("photo_url") or "",
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
            results.append(_summarise(uid, recipe, coordinator.data.categories))

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
            **_summarise(uid, recipe, coordinator.data.categories),
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

    await hass.http.async_register_static_paths(
        [
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
