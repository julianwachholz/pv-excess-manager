"""Sensors that hold the algorithm results."""

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfPower, UnitOfTime
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.restore_state import async_get as restore_async_get
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import now
from homeassistant.util.unit_conversion import DurationConverter

from . import const
from .coordinator import PVExcessManagerCoordinator
from .managed_device import ManagedDevice

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configure our sensors."""
    # check that there is some data to configure
    device_type = entry.data.get(const.CONF_DEVICE_TYPE, None)
    if device_type is None:
        return

    # Sets the config entries values to PVExcessManager coordinator
    coordinator = PVExcessManagerCoordinator.get_coordinator()

    if device_type == const.CONF_DEVICE_MAIN:
        async_add_entities(
            [
                ManagedPowerSensorEntity(coordinator),
                VirtualExcessSensorEntity(coordinator),
            ],
        )
        await coordinator.configure(entry)
        return

    device = coordinator.get_device_by_unique_id(entry.data.get(const.CONF_UNIQUE_ID))
    if device is None and entry.data.get(const.CONF_NOMINAL_POWER):
        device = ManagedDevice(hass, entry.data, coordinator)
        coordinator.add_device(device)
        async_add_entities(
            [
                DailyRuntimeSensor(
                    hass,
                    coordinator,
                    device,
                )
            ]
        )

    elif device is None:
        logger.debug(
            "Device entry '%s' has no nominal_power yet - configure it via the options flow.",
            entry.data.get(const.CONF_NAME),
        )

    # Add services
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        const.SERVICE_RESET_RUNTIME,
        {},
        "service_reset_device_runtime",
    )


class PVExcessManagerSensor(CoordinatorEntity, SensorEntity):
    """Base sensor class for PV Excess Manager."""

    key: str
    icon: str | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator or not self.coordinator.data:
            logger.warning("Coordinator not set!")
            return

        value = self.coordinator.data.get(self.key)
        if value is None:
            logger.debug("No value for %s", self.key)
            return

        self._attr_native_value = value
        self.async_write_ha_state()

    @property
    def device_info(self):
        """Get device info."""
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, const.CONF_DEVICE_MAIN)},
            name=const.NAME,
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )


class ManagedPowerSensorEntity(PVExcessManagerSensor):
    """
    Sensor for the total amount of currently managed power usage.

    It is the sum of the actual power of all devices that are currently managed by the PV Excess Manager.

    """

    key = "managed_power"
    _attr_icon = "mdi:flash"
    _attr_name = "Managed Power"

    _attr_unique_id = "pv_excess_manager_" + key
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT


class VirtualExcessSensorEntity(PVExcessManagerSensor):
    """
    Sensor for the calculated virtual excess power.

    Actual power usage of all managed devices plus remaining real excess power.

    """

    key = "virtual_excess_power"
    _attr_icon = "mdi:flash"
    _attr_name = "Virtual Excess Power"

    _attr_unique_id = "pv_excess_manager_" + key
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT


LAST_DATETIME_ON = "last_datetime_on"
SHOULD_BE_FORCED_OFFPEAK = "should_be_forced_offpeak"


class DailyRuntimeSensor(SensorEntity, RestoreEntity):
    """Track daily runtime of a device."""

    _attr_icon = "mdi:timer-play"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0

    _entity_component_unrecorded_attributes = SensorEntity._entity_component_unrecorded_attributes.union(
        frozenset(
            {
                const.CONF_MAX_DAILY_RUNTIME,
                const.CONF_RESET_TIME,
                const.CONF_OFFPEAK_TIME,
                LAST_DATETIME_ON,
                SHOULD_BE_FORCED_OFFPEAK,
            }
        )
    )

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PVExcessManagerCoordinator,
        device: ManagedDevice,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._device = device
        self._coordinator = coordinator

        self._attr_name = "Daily runtime"
        self._attr_has_entity_name = True
        self._attr_unique_id = f"pv_excess_manager_{device.slug}_daily_runtime"
        self.entity_id = f"{SENSOR_DOMAIN}.{self._attr_unique_id}"

        self.last_datetime_on = None
        self._attr_native_value = 0
        self._old_state = None

    async def async_added_to_hass(self) -> None:
        """Start listening to state changes of the underlying entity."""
        await super().async_added_to_hass()

        listener_cancel = async_track_state_change_event(
            self.hass,
            [self._device.entity_id],
            self.on_state_change,
        )
        self.async_on_remove(listener_cancel)

        # Add listener to end of day cycle
        reset_time: time = self._coordinator.reset_time
        self.async_on_remove(
            async_track_time_change(
                hass=self.hass,
                action=self.on_midnight,
                hour=reset_time.hour,
                minute=reset_time.minute,
                second=0,
            )
        )

        # Add a listener to calculate runtime at each minute
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self.on_update_on_time,
                interval=timedelta(minutes=1),
            )
        )

        # restore the last value or set to 0
        old_state = await self.async_get_last_state()
        if old_state is not None:
            if old_state.state is not None and old_state.state != "unknown":
                # Maybe convert if unit was changed
                value = DurationConverter.convert(
                    float(old_state.state),
                    old_state.attributes.get("unit_of_measurement"),
                    UnitOfTime.SECONDS,
                )
                self._attr_native_value = value
                self._device.daily_runtime = self._attr_native_value
                logger.info(
                    "%s - loaded runtime from storage is %s",
                    self._device.name,
                    self._attr_native_value,
                )

            old_value = old_state.attributes.get(LAST_DATETIME_ON)
            if old_value is not None:
                self.last_datetime_on = datetime.fromisoformat(old_value)

        self.update_custom_attributes()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Try to force backup of entity."""
        logger.info(
            "%s - force write before remove. runtime is %s",
            self,
            self._attr_native_value,
        )
        await restore_async_get(self.hass).async_dump_states()

    @callback
    async def on_state_change(self, event: Event) -> None:
        """Listen for entity state changes."""
        if not event.data:
            return

        new_state: State = event.data.get("new_state")

        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            logger.debug("No available state. Event is ignored")
            return

        need_save = False
        # We search for the date of the event
        new_state = self._device.is_active  # new_state.state == STATE_ON
        if new_state and not self._old_state:
            logger.debug("The managed device becomes on - store the last_datetime_on")
            self.last_datetime_on = now()
            need_save = True

        if not new_state:
            if self._old_state and self.last_datetime_on is not None:
                logger.debug("The managed device becomes off - increment the delta time")
                self._attr_native_value += round((now() - self.last_datetime_on).total_seconds())
            self.last_datetime_on = None
            need_save = True

        # Save the new state
        if need_save:
            self._old_state = new_state
            self.update_custom_attributes()
            self.async_write_ha_state()
            self._device.daily_runtime = self._attr_native_value

    @callback
    async def on_midnight(self, _=None) -> None:
        """Reset the counter at end of the day cycle."""
        self._attr_native_value = 0

        logger.info("Call of on_midnight to reset runtime")

        # reset _last_datetime_on to now if it was active
        if self.last_datetime_on is not None:
            self.last_datetime_on = now()

        self.update_custom_attributes()
        self.async_write_ha_state()
        self._device.daily_runtime = self._attr_native_value

    @callback
    async def on_update_on_time(self, _=None) -> None:
        """Update the runtime sensor every minute."""
        if self.last_datetime_on is not None and self._device.is_active:
            self._attr_native_value += round((now() - self.last_datetime_on).total_seconds())
            self.last_datetime_on = now()
            self.update_custom_attributes()
            self.async_write_ha_state()

            self._device.daily_runtime = self._attr_native_value

    def update_custom_attributes(self):
        """Add custom attributes to the entity."""
        self._attr_extra_state_attributes: dict = {
            const.CONF_MAX_DAILY_RUNTIME: self._device._max_daily_runtime,
            const.CONF_RESET_TIME: self._coordinator.reset_time,
            const.CONF_OFFPEAK_TIME: self._device.offpeak_time,
            "last_datetime_on": self.last_datetime_on,
            "should_be_forced_offpeak": self._device.should_be_forced_offpeak(),
        }

    @property
    def device_info(self):
        """Get device info."""
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name=f"{const.NAME}: {self._device.name}",
            manufacturer=const.AUTHOR,
            model=const.NAME,
        )

    @property
    def get_attr_extra_state_attributes(self):
        """Get the extra state attributes for the entity."""
        return self._attr_extra_state_attributes

    async def service_reset_device_runtime(self):
        """Listen for service calls to reset the runtime counter."""
        logger.info("%s - Calling service_reset_device_runtime", self)
        await self.on_midnight()
        await self.on_midnight()
