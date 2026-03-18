"""Switch entities for PV Excess Manager devices."""

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
            ManagedDeviceManagedSwitch(hass, device),
        ]
    )


class ManagedDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity reflecting the active/inactive state of a managed device."""

    coordinator: PVExcessManagerCoordinator

    _attr_icon = "mdi:toggle-switch"
    _attr_name = "Active"

    _entity_component_unrecorded_attributes = SwitchEntity._entity_component_unrecorded_attributes.union(
        frozenset(
            {
                "is_managed",
                "is_active",
                "is_waiting",
                "is_usable",
                # "can_change_power",
                # "duration_ontime_sec",
                # "duration_power_sec",
                "power_nominal",
                "power_max",
                "locked_until",
                "power_locked_until",
                "battery_min_soc",
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

        super().__init__(coordinator, context=device.unique_id)
        self._hass = hass
        self._device = device

        self._attr_name = "Active"
        self._attr_has_entity_name = True
        self._attr_unique_id = f"pv_excess_manager_{device.slug}_active"
        self.entity_id = f"{SWITCH_DOMAIN}.pv_excess_manager_{self._attr_unique_id}"

        self._attr_is_on = device.is_active

    async def async_added_to_hass(self) -> None:
        """Register state-change and managed-state-change listeners."""
        await super().async_added_to_hass()

        # Track the underlying device entity if configured, to react immediately to ON/OFF state changes
        if self._device.entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._device.entity_id],
                    self._on_state_change,
                )
            )

        # React when the Managed switch toggles this device on or off
        self.async_on_remove(
            self.hass.bus.async_listen(
                event_type=const.EVENT_PV_EXCESS_MANAGER_MANAGED_STATE_CHANGE,
                listener=self._on_managed_state_change,
            )
        )

        self._update_custom_attributes(self._device)

    @callback
    def _on_managed_state_change(self, event: Event) -> None:
        """Update state when the device's managed flag changes."""
        if not event.data or event.data.get("device_unique_id") != self._device.unique_id:
            return

        device = self.coordinator.get_device_by_unique_id(self._device.unique_id) if self.coordinator else None
        if device is None:
            return

        logger.info("Managed state changed for %s → %s", self._device.name, device.is_managed)
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

        device = self.coordinator.get_device_by_unique_id(self._device.unique_id) if self.coordinator else None
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
        extra_attrs = {
            "is_managed": device.is_managed,
            "is_active": device.is_active,
            "is_locked": device.is_locked,
            "is_usable": device.is_usable,
            "current_power": device.current_power,
            "requested_power": device.requested_power,
            "duration_ontime_sec": device.duration_ontime.total_seconds(),
            "duration_offtime_sec": device.duration_offtime.total_seconds(),
            "power_nominal": device.power_nominal,
            "locked_until": device.locked_until.isoformat(),
            "battery_min_soc": device.battery_min_soc,
            "battery_soc": device.battery_soc,
            "device_name": device.name,
        }
        if device.can_change_power:
            extra_attrs.update(
                {
                    "can_change_power": device.can_change_power,
                    "duration_power_sec": device.duration_power.total_seconds(),
                    "power_max": device.power_max,
                    "power_locked_until": device.power_locked_until.isoformat(),
                }
            )

        self._attr_extra_state_attributes = extra_attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator or not self.coordinator.data:
            logger.warning("Coordinator not set!")
            return

        device: ManagedDevice | None = self.coordinator.data.get(self._device.unique_id)
        if device is None:
            logger.debug(
                "Device %s (%s) not present in coordinator update",
                self._device.name,
                self._device.unique_id,
            )
            return

        self._attr_is_on = device.is_active
        self._update_custom_attributes(device)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Schedule async turn-on."""
        self.hass.async_create_task(self.async_turn_on(**kwargs))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Activate the managed device."""
        logger.info("Manually turning on %s", self._device.name)
        device = self.coordinator.get_device_by_unique_id(self._device.unique_id) if self.coordinator else None
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
        logger.info("Manually turning off %s", self._device.name)
        device = self.coordinator.get_device_by_unique_id(self._device.unique_id) if self.coordinator else None
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
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )


class ManagedDeviceManagedSwitch(SwitchEntity, RestoreEntity):
    """Enable a device for PV Excess Manager optimisation."""

    _attr_icon = "mdi:check"

    def __init__(self, hass: HomeAssistant, device: ManagedDevice) -> None:
        """Initialize the managed switch."""
        self._hass = hass
        self._device = device

        self._attr_name = "Managed"
        self._attr_has_entity_name = True
        self._attr_unique_id = f"pv_excess_manager_{device.slug}_managed"
        self.entity_id = f"{SWITCH_DOMAIN}.{self._attr_unique_id}"

        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        """Restore the last known managed state."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        # Default to True (managed) on first setup so devices participate in optimisation immediately
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
        """Propagate the managed state to the device and notify listeners."""
        if not self._device:
            return

        self._device.is_managed = self._attr_is_on
        self._hass.bus.async_fire(
            event_type=const.EVENT_PV_EXCESS_MANAGER_MANAGED_STATE_CHANGE,
            event_data={"device_unique_id": self._device.unique_id},
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the managed device."""
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )
