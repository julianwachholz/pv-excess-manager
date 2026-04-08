import logging
from typing import TYPE_CHECKING

from homeassistant.const import SERVICE_RELOAD
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.service import async_register_admin_service

from .const import CONF_DEVICE_MAIN, CONF_DEVICE_TYPE, CONF_UNIQUE_ID, DOMAIN, PLATFORMS
from .coordinator import PVExcessManagerCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

logger = logging.getLogger(__name__)


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Initialize the custom integration."""
    logger.info(
        "Initializing %s integration with platforms: %s with config: %s",
        DOMAIN,
        PLATFORMS,
        config.get(DOMAIN),
    )

    hass.data.setdefault(DOMAIN, {})

    # The config argument contains your configuration.yaml
    pv_excess_manager_config = config.get(DOMAIN)

    hass.data[DOMAIN]["coordinator"] = coordinator = PVExcessManagerCoordinator(hass, pv_excess_manager_config)

    async def _handle_reload(*args, **kwargs):
        await reload_config(hass)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
    )

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    hass.bus.async_listen_once("homeassistant_started", coordinator.on_ha_started)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle setup of an entry from config flow."""
    logger.debug(
        "Calling async_setup_entry entry: entry_id='%s', data='%s'",
        entry.entry_id,
        entry.data,
    )

    hass.data.setdefault(DOMAIN, {})

    # Recreate coordinator if it was reset (e.g. main config was deleted and re-added)
    if hass.data[DOMAIN].get("coordinator") is None:
        hass.data[DOMAIN]["coordinator"] = PVExcessManagerCoordinator(hass, None)

    # Register the update listener for this config entry
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Force reload of entities associated with a configEntry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if entry.data.get(CONF_DEVICE_TYPE) == CONF_DEVICE_MAIN:
            PVExcessManagerCoordinator.reset()
        else:
            coordinator = PVExcessManagerCoordinator.get_coordinator()
            if coordinator is not None:
                unique_id = entry.data.get(CONF_UNIQUE_ID)
                if unique_id:
                    coordinator.remove_device(unique_id)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def reload_config(hass: HomeAssistant):
    """Handle reload service call."""
    logger.info("Service %s.reload called: reloading integration", DOMAIN)
    entries = hass.config_entries.async_entries(DOMAIN)
    for entry in entries:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except ConfigEntryError, ConfigEntryNotReady, HomeAssistantError:
            logger.exception("Could not reload config entry %s", entry.entry_id)
