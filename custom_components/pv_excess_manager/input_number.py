"""Input number entities for PV Excess Manager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.number import RestoreNumber
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from . import const
from .coordinator import PVExcessManagerCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .managed_device import ManagedDevice

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up input_number entities for PV Excess Manager."""
    device_type = entry.data.get(const.CONF_DEVICE_TYPE, None)
    if device_type is None or device_type == const.CONF_DEVICE_MAIN:
        return

    coordinator = PVExcessManagerCoordinator.get_coordinator()
    unique_id = entry.data.get(const.CONF_UNIQUE_ID)
    device = coordinator.get_device_by_unique_id(unique_id)
    if device is None:
        logger.debug(
            "Device entry '%s' not found in coordinator - skipping input_number setup.",
            entry.data.get(const.CONF_NAME),
        )
        return

    async_add_entities([DevicePriorityNumber(hass, device)])


class DevicePriorityNumber(RestoreNumber):
    """Number entity for setting the priority of a managed device in the cascade algorithm."""

    _attr_name = "Priority"
    _attr_has_entity_name = True
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_icon = "mdi:order-numeric-ascending"

    def __init__(self, hass: HomeAssistant, device: ManagedDevice) -> None:
        """Initialize the priority number entity."""
        self.hass = hass
        self._device = device
        self._attr_unique_id = f"pv_excess_manager_{device.slug}_priority"
        self.entity_id = f"{INPUT_NUMBER_DOMAIN}.{self._attr_unique_id}"
        self._attr_native_value = float(const.DEFAULT_PRIORITY)

    async def async_added_to_hass(self) -> None:
        """Restore the last known priority value."""
        await super().async_added_to_hass()

        last_data = await self.async_get_last_number_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value

        self._device.set_priority(int(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        """Update the priority value and propagate it to the managed device."""
        self._attr_native_value = value
        self._device.set_priority(int(value))
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Get device info."""
        return DeviceInfo(
            entry_type=DeviceEntryType.DEVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )
