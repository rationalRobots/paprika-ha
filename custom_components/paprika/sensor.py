"""One lean sensor per Paprika recipe, plus a library summary.

Deliberately lean: recipe bodies (ingredients, directions) are NOT exposed as
attributes. Home Assistant's recorder silently discards state attributes over
MAX_STATE_ATTRS_BYTES (16 KB) -- it logs a warning and stores the row without
them -- and attributes are re-serialised and pushed to every connected client
on each state write. A few hundred recipes' worth of method text would make a
wall tablet crawl for no benefit. Bodies are served instead by the
paprika.get_recipe action, which returns response data on demand.
"""

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .api import Recipe, RecipeID
    from .coordinator import PaprikaCoordinator
    from .data import PaprikaConfigEntry

LOGGER = logging.getLogger(__name__)

# States are capped at 255 characters, so the state is the category (short and
# useful for filtering) and the recipe name is the entity name.
MAX_STATE = 255


def _device(entry: "PaprikaConfigEntry") -> DeviceInfo:
    """Group everything under one Paprika device.

    Also the reason entity ids come out as sensor.paprika_*: with
    has_entity_name the device name prefixes them. Without it a recipe lands
    on sensor.lamb_biryani, which squats on the global namespace, can collide
    with unrelated sensors, and -- because the ids share no prefix -- cannot
    be excluded from the recorder by glob.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Paprika",
        manufacturer="Hindsight Labs",
        entry_type=None,
    )


class PaprikaRecipeSensor(SensorEntity, CoordinatorEntity["PaprikaCoordinator"]):
    _attr_icon = "mdi:chef-hat"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: "PaprikaCoordinator",
        entry: "PaprikaConfigEntry",
        uid: "RecipeID",
    ):
        super().__init__(coordinator)
        self._uid = uid
        self._attr_unique_id = f"{entry.entry_id}_recipe_{uid}"
        self._attr_device_info = _device(entry)

    @property
    def _recipe(self) -> "Recipe | None":
        return self.coordinator.data.recipes.get(self._uid)

    @property
    def _category_names(self) -> list[str]:
        """Recipe categories are stored as uids upstream; resolve to names."""
        recipe = self._recipe
        if not recipe:
            return []
        lookup = self.coordinator.data.categories
        return [lookup.get(c, c) for c in (recipe.get("categories") or [])]

    @property
    def available(self) -> bool:
        return self._recipe is not None

    @property
    def name(self) -> str:
        recipe = self._recipe
        # Entities are only created once the body exists, so the fallback is
        # for a recipe deleted upstream while its entity still lingers.
        return recipe["name"] if recipe else "Unknown recipe"

    @property
    def native_value(self) -> str | None:
        recipe = self._recipe
        if not recipe:
            return None
        names = sorted(self._category_names)
        return (names[0] if names else "Uncategorised")[:MAX_STATE]

    @property
    def entity_picture(self) -> str | None:
        # Paprika's photo_url is a signed URL that expires after a few hours,
        # so it is read live from the cached body rather than stored anywhere.
        recipe = self._recipe
        return recipe.get("photo_url") if recipe else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        recipe = self._recipe
        if not recipe:
            return {}
        return {
            "uid": self._uid,
            "categories": self._category_names,
            "rating": recipe.get("rating") or 0,
            "servings": recipe.get("servings") or "",
            "prep_time": recipe.get("prep_time") or "",
            "cook_time": recipe.get("cook_time") or "",
            "total_time": recipe.get("total_time") or "",
            "source_url": recipe.get("source_url") or "",
            "on_favorites": bool(recipe.get("on_favorites")),
        }


class PaprikaLibrarySensor(SensorEntity, CoordinatorEntity["PaprikaCoordinator"]):
    """How many recipes exist, and how many bodies are still being fetched."""

    _attr_icon = "mdi:book-open-variant"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "recipes"

    def __init__(self, coordinator: "PaprikaCoordinator", entry: "PaprikaConfigEntry"):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_recipe_library"
        self._attr_device_info = _device(entry)

    @property
    def name(self) -> str:
        return "Recipes"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.recipe_index)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "loaded": len(data.recipes),
            "pending": data.recipes_pending,
            "categories": sorted(set(data.categories.values())),
        }


async def async_setup_entry(
    hass: "HomeAssistant",  # noqa: ARG001
    entry: "PaprikaConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """Set up recipe sensors, adding more as the library backfills."""
    coordinator = entry.runtime_data.coordinator
    known: "set[RecipeID]" = set()

    def _sync_entities() -> None:
        # Only register a recipe once its body has arrived. Entity ids are
        # generated from the name at first registration and never revised, so
        # registering straight off the index -- where only the uid is known --
        # permanently brands every recipe sensor.<guid>.
        new = [uid for uid in coordinator.data.recipes if uid not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(
            PaprikaRecipeSensor(coordinator=coordinator, entry=entry, uid=uid)
            for uid in new
        )

    async_add_entities([PaprikaLibrarySensor(coordinator=coordinator, entry=entry)])
    _sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))
