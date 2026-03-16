"""Switch entities for PV Excess Manager devices."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.components.switch.const import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import const
from .coordinator import PVExcessManagerCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .managed_device import ManagedDevice

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch entries for each ManagedDevice."""
    if entry.data.get(const.CONF_DEVICE_TYPE) == const.CONF_DEVICE_MAIN:
        return

    coordinator = PVExcessManagerCoordinator.get_coordinator()
    unique_id = entry.data.get(const.CONF_UNIQUE_ID)
    device = coordinator.get_device_by_unique_id(unique_id)
    if device is None:
        logger.error(
            "switch.async_setup_entry: device with unique_id '%s' not found in coordinator",
            unique_id,
        )
        return

    async_add_entities(
        [
            ManagedDeviceSwitch(coordinator, hass, device),
            ManagedDeviceEnable(hass, device),
        ]
    )


class ManagedDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity reflecting the active/inactive state of a managed device."""

    _entity_component_unrecorded_attributes = SwitchEntity._entity_component_unrecorded_attributes.union(  # noqa: SLF001
        frozenset(
            {
                "is_enabled",
                "is_active",
                "is_waiting",
                "is_usable",
                "can_change_power",
                "duration_ontime_sec",
                "duration_power_sec",
                "power_nominal",
                "power_max",
                "locked_until",
                "power_locked_until",
                "battery_min_soc",
                "battery_soc",
            }
        )
    )

    def __init__(
        self,
        coordinator: PVExcessManagerCoordinator,
        hass: HomeAssistant,
        device: ManagedDevice,
    ) -> None:
        """Initialize the managed device switch."""
        logger.debug("Adding ManagedDeviceSwitch for %s", device.name)
        idx = device.unique_id
        super().__init__(coordinator, context=idx)
        self._hass = hass
        self._device = device
        self.idx = idx
        self._attr_has_entity_name = True
        self.entity_id = f"{SWITCH_DOMAIN}.pv_excess_manager_{idx}"
        self._attr_name = "Active"
        self._attr_unique_id = f"pv_excess_manager_active_{idx}"
        self._device_entity_id = device.entity_id
        self._attr_is_on = device.is_active

    async def async_added_to_hass(self) -> None:
        """Register state-change and enable-state-change listeners."""
        await super().async_added_to_hass()

        # Track the underlying device entity if configured, to react immediately to ON/OFF state changes
        if self._device_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._device_entity_id],
                    self._on_state_change,
                )
            )

        # React when the Enable switch toggles this device on or off
        self.async_on_remove(
            self.hass.bus.async_listen(
                event_type=const.EVENT_PV_EXCESS_MANAGER_ENABLE_STATE_CHANGE,
                listener=self._on_enable_state_change,
            )
        )

        self._update_custom_attributes(self._device)

    @callback
    def _on_enable_state_change(self, event: Event) -> None:
        """Update state when the device's enabled flag changes."""
        if not event.data or event.data.get("device_unique_id") != self.idx:
            return

        device = self.coordinator.get_device_by_unique_id(self.idx) if self.coordinator else None
        if device is None:
            return

        logger.info("Enable state changed for %s → %s", self.idx, device.is_enabled)
        self._update_custom_attributes(device)
        self.async_write_ha_state()

    @callback
    def _on_state_change(self, event: Event) -> None:
        """Update switch state when the underlying power-sensor entity changes."""
        if not event.data:
            return

        new_state: State | None = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        device = self.coordinator.get_device_by_unique_id(self.idx) if self.coordinator else None
        if device is None:
            return

        new_is_on = device.is_active
        if new_is_on == self._attr_is_on:
            return

        self._attr_is_on = new_is_on
        self._update_custom_attributes(device)
        self.async_write_ha_state()

    def _update_custom_attributes(self, device: ManagedDevice) -> None:
        """Populate extra state attributes from the device."""
        self._attr_extra_state_attributes: dict[str, Any] = {
            "is_enabled": device.is_enabled,
            "is_active": device.is_active,
            "is_waiting": device.is_waiting,
            "is_usable": device.is_usable,
            "can_change_power": device.can_change_power,
            "current_power": device.current_power,
            "requested_power": device.requested_power,
            "duration_ontime_sec": device.duration_ontime.total_seconds(),
            "duration_power_sec": device.duration_power.total_seconds(),
            "power_nominal": device.power_nominal,
            "power_max": device.power_max,
            "locked_until": device.locked_until.isoformat(),
            "power_locked_until": device.power_locked_until.isoformat(),
            "battery_min_soc": device.battery_min_soc,
            "battery_soc": device.battery_soc,
            "device_name": device.name,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        logger.debug("_handle_coordinator_update for %s", self._attr_name)

        if not self.coordinator or not self.coordinator.data:
            logger.debug("No coordinator or data available")
            return

        device: ManagedDevice | None = self.coordinator.data.get(self.idx)
        if device is None:
            logger.debug("Device %s not present in coordinator update", self.idx)
            return

        self._attr_is_on = device.is_active
        self._update_custom_attributes(device)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Schedule async turn-on."""
        self.hass.async_create_task(self.async_turn_on(**kwargs))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Activate the managed device."""
        logger.info("Manually turning on %s", self._attr_name)
        device = self.coordinator.get_device_by_unique_id(self.idx) if self.coordinator else None
        if device is None:
            return

        if not self._attr_is_on:
            await device.activate(device.power_nominal)
            self._attr_is_on = True
            self._update_custom_attributes(device)
            self.async_write_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Schedule async turn-off."""
        self.hass.async_create_task(self.async_turn_off(**kwargs))

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Deactivate the managed device."""
        logger.info("Manually turning off %s", self._attr_name)
        device = self.coordinator.get_device_by_unique_id(self.idx) if self.coordinator else None
        if device is None:
            return

        if self._attr_is_on:
            await device.deactivate()
            self._attr_is_on = False
            self._update_custom_attributes(device)
            self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the managed device."""
        return DeviceInfo(
            entry_type=DeviceEntryType.DEVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )


class ManagedDeviceEnable(SwitchEntity, RestoreEntity):
    """Switch entity to enable or disable a device for PV Excess Manager optimisation."""

    _attr_icon = "mdi:check"

    def __init__(self, hass: HomeAssistant, device: ManagedDevice) -> None:
        """Initialize the enable switch."""
        self._hass = hass
        self._device = device
        self._attr_has_entity_name = True
        self.entity_id = f"{SWITCH_DOMAIN}.pv_excess_manager_{device.unique_id}_enable"
        self._attr_name = "Enable"
        self._attr_unique_id = f"pv_excess_manager_{device.unique_id}_enable"
        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        """Restore the last known enabled state."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        # Default to True (enabled) on first setup so devices participate in optimisation immediately
        self._attr_is_on = last_state.state == STATE_ON if last_state is not None else True

        self._apply_to_device()

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Enable the device for PV Excess Manager optimisation."""
        self._attr_is_on = True
        self.async_write_ha_state()
        self._apply_to_device()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Disable the device for PV Excess Manager optimisation."""
        self._attr_is_on = False
        self.async_write_ha_state()
        self._apply_to_device()

    def _apply_to_device(self) -> None:
        """Propagate the enabled state to the device and notify listeners."""
        if not self._device:
            return

        self._device.set_enable(self._attr_is_on)
        self._hass.bus.async_fire(
            event_type=const.EVENT_PV_EXCESS_MANAGER_ENABLE_STATE_CHANGE,
            event_data={"device_unique_id": self._device.unique_id},
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the managed device."""
        return DeviceInfo(
            entry_type=DeviceEntryType.DEVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )
