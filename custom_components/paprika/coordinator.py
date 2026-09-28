import asyncio
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    PHOTO_DIR,
    PHOTO_URL_BASE,
    RECIPE_CACHE_VERSION,
    RECIPE_FETCH_BATCH,
    RECIPE_FETCH_DELAY,
)

if TYPE_CHECKING:
    from .api import (
        Category,
        GroceryListItem,
        MealType,
        PlannedMeal,
        Recipe,
        RecipeID,
        RecipeIndexEntry,
        SyncStatus,
    )
    from .data import PaprikaConfigEntry


@dataclass
class PaprikaData:
    status: "SyncStatus"
    meals: list["PlannedMeal"]
    groceries: list["GroceryListItem"]
    meal_types: list["MealType"]
    recipe_index: list["RecipeIndexEntry"] = field(default_factory=list)
    recipes: dict["RecipeID", "Recipe"] = field(default_factory=dict)
    recipes_pending: int = 0
    categories: dict[str, str] = field(default_factory=dict)


class PaprikaCoordinator(DataUpdateCoordinator[PaprikaData]):
    """Class to manage fetching data from the API."""

    config_entry: "PaprikaConfigEntry"
    last_status: "SyncStatus | None" = None

    # Recipe bodies are cached across updates and keyed by the index hash.
    # There is no bulk endpoint, so a full library is one request per recipe;
    # Paprika answers heavy traffic with network-level IP blocks rather than
    # 429s, which no retry logic can recover from. So bodies are backfilled a
    # few per cycle, and a large library simply warms up over a few hours.
    _recipes: dict["RecipeID", "Recipe"]
    _hashes: dict["RecipeID", str]
    _store: "Store | None" = None
    _photo_failed: set
    _photo_root: "Path | None" = None

    def photo_ref(self, uid: str) -> str:
        """Local path for a downloaded photo, or "" if there is not one.

        Never returns Paprika's own photo_url: it is a signed URL that expires
        in hours, so anything holding on to it ends up rendering broken
        images.
        """
        if self._photo_root and (self._photo_root / f"{uid}.jpg").exists():
            return f"{PHOTO_URL_BASE}/{uid}.jpg"
        return ""

    def _needs_photo(self, uid: str) -> bool:
        recipe = self._recipes.get(uid)
        if recipe is None or uid in self._photo_failed:
            return False
        if not recipe.get("photo_url"):
            return False          # genuinely has no picture; do not keep asking
        return not (self._photo_root / f"{uid}.jpg").exists()

    async def _fetch_photo(self, uid: str, recipe: dict) -> None:
        url = recipe.get("photo_url")
        if not url or self._photo_root is None:
            return
        target = self._photo_root / f"{uid}.jpg"

        def _download() -> None:
            # Photo URLs are pre-signed, so this is a plain unauthenticated
            # GET -- and it has to happen promptly after fetching the recipe,
            # while the signature is still valid.
            with urllib.request.urlopen(url, timeout=30) as resp:
                tmp = target.with_suffix(".part")
                tmp.write_bytes(resp.read())
                tmp.replace(target)

        try:
            await asyncio.to_thread(_download)
        except Exception as exc:  # noqa: BLE001
            self._photo_failed.add(uid)
            self.logger.debug("could not download photo for %s: %s", uid, exc)

    async def async_load_cache(self) -> None:
        """Restore cached recipe bodies from disk.

        Without this the cache is memory-only, so every Home Assistant restart
        discards the whole library and refetches it one request at a time --
        which, at the pace the throttling demands, a library of any size never
        finishes if restarts are at all frequent.
        """
        self._photo_failed = set()
        self._photo_root = Path(self.hass.config.path(PHOTO_DIR))
        await asyncio.to_thread(self._photo_root.mkdir, 0o755, True, True)
        self._store = Store(
            self.hass, RECIPE_CACHE_VERSION, f"{DOMAIN}.recipes.{self.config_entry.entry_id}"
        )
        cached = await self._store.async_load() or {}
        self._recipes = cached.get("recipes", {})
        self._hashes = cached.get("hashes", {})
        if self._recipes:
            self.logger.debug("Restored %s cached recipes", len(self._recipes))

    def _save_cache(self) -> None:
        if self._store is None:
            return
        # Delayed save: a batch writes many recipes in quick succession and
        # there is no need to hit the disk for each one.
        self._store.async_delay_save(
            lambda: {"recipes": self._recipes, "hashes": self._hashes}, 30
        )

    async def _async_update_data(self) -> Any:
        """Update data via library."""
        if not hasattr(self, "_recipes"):
            # async_load_cache should have run at setup; be safe if it did not.
            self._recipes = {}
            self._hashes = {}
        if not hasattr(self, "_photo_failed"):
            self._photo_failed = set()

        client = self.config_entry.runtime_data.client
        current_status = await client.get_status()
        status_changed = self.last_status != current_status

        # Even when nothing changed upstream there may still be bodies left to
        # backfill from a previous cycle, so an unchanged status alone is not
        # enough to skip the work.
        if not status_changed and self.data is not None and not self.data.recipes_pending:
            return self.data

        if status_changed:
            self.last_status = current_status
            meal_types = await client.get_meal_types()
            meals = await client.get_meals(meal_types)
            groceries = await client.get_groceries()
            recipe_index = await client.get_recipe_index()
            categories = {c["uid"]: c["name"] for c in await client.get_categories()}
        else:
            meal_types = self.data.meal_types
            meals = self.data.meals
            groceries = self.data.groceries
            recipe_index = self.data.recipe_index
            categories = self.data.categories

        # A recipe is worth fetching if its body changed, or if we have the
        # body but never managed to save its picture -- the second case needs
        # a fresh fetch purely to mint a photo_url that has not expired.
        stale = [
            entry
            for entry in recipe_index
            if self._hashes.get(entry["uid"]) != entry["hash"]
            or self._needs_photo(entry["uid"])
        ]
        fetched = 0
        for entry in stale[:RECIPE_FETCH_BATCH]:
            try:
                recipe = await client.get_recipe(entry["uid"])
            except Exception:  # noqa: BLE001 - one bad recipe must not stall the rest
                self.logger.warning("Could not fetch recipe %s", entry["uid"])
                continue
            self._recipes[entry["uid"]] = recipe
            self._hashes[entry["uid"]] = entry["hash"]
            await self._fetch_photo(entry["uid"], recipe)
            fetched += 1
            await asyncio.sleep(RECIPE_FETCH_DELAY)

        if fetched:
            self._save_cache()

        pending = max(len(stale) - RECIPE_FETCH_BATCH, 0)
        if pending:
            self.logger.debug("%s recipe bodies still to fetch", pending)

        # Drop bodies for recipes that have gone from the index.
        live = {entry["uid"] for entry in recipe_index}
        removed = [u for u in self._recipes if u not in live]
        for uid in removed:
            del self._recipes[uid]
            self._hashes.pop(uid, None)
        if removed:
            self._save_cache()

        return PaprikaData(
            status=current_status,
            meal_types=meal_types,
            meals=meals,
            groceries=groceries,
            recipe_index=recipe_index,
            recipes=dict(self._recipes),
            categories=categories,
            recipes_pending=pending,
        )
