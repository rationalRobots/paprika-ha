"""Constants for the Paprika integration."""

DOMAIN = "paprika"

# Recipe bodies have no bulk endpoint, and Paprika throttles by blocking the IP
# outright rather than returning 429, so they are fetched a few per update
# cycle with a pause between them rather than all at once.
RECIPE_FETCH_BATCH = 20
RECIPE_FETCH_DELAY = 2.0

SERVICE_GET_RECIPES = "get_recipes"
SERVICE_GET_RECIPE = "get_recipe"
