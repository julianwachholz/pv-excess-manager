"""Sensors that hold the algorithm results."""

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import (
    RestoreEntity,
)
from homeassistant.helpers.restore_state import (
    async_get as restore_async_get,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import now

from . import const
from .coordinator import PVExcessManagerCoordinator
from .managed_device import ManagedDevice

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback,
    )


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
    elif device is None:
        logger.debug(
            "Device entry '%s' has no nominal_power yet - configure it via the options flow.",
            entry.data.get(const.CONF_NAME),
        )

    # entity1 = TodayOnTimeSensor(
    #     hass,
    #     coordinator,
    #     device,
    # )
    # async_add_entities([entity1])

    # Add services
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        const.SERVICE_RESET_ON_TIME,
        {},
        "service_reset_on_time",
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
    icon: str = "mdi:flash"

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
    icon: str = "mdi:flash"

    _attr_name = "Virtual Excess Power"
    _attr_unique_id = "pv_excess_manager_" + key
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT


# TODO
class TodayOnTimeSensor(SensorEntity, RestoreEntity):
    """Gives the duration in which the device was on for a day."""

    _entity_component_unrecorded_attributes = SensorEntity._entity_component_unrecorded_attributes.union(
        frozenset(
            {
                "max_on_time_per_day_sec",
                "max_on_time_per_day_min",
                "max_on_time_hms",
                "on_time_hms",
                "reset_time",
                "should_be_forced_offpeak",
            }
        )
    )

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PVExcessManagerCoordinator,
        device: ManagedDevice,
    ) -> None:
        """Initialize the sensor"""
        self.hass = hass
        idx = device.unique_id
        self._attr_name = "On time today"
        self._attr_has_entity_name = True
        self.entity_id = f"{SENSOR_DOMAIN}.on_time_today_pv_excess_manager_{idx}"
        self._attr_unique_id = "pv_excess_manager_on_time_today_" + idx
        self._attr_native_value = None
        self._entity_id = device.entity_id
        self._device = device
        self._coordinator = coordinator
        self._last_datetime_on = None
        self._old_state = None

    async def async_added_to_hass(self) -> None:
        """The entity have been added to hass, listen to state change of the underlying entity"""
        await super().async_added_to_hass()

        # Arme l'écoute de la première entité
        listener_cancel = async_track_state_change_event(
            self.hass,
            [self._entity_id],
            self._on_state_change,
        )
        # desarme le timer lors de la destruction de l'entité
        self.async_on_remove(listener_cancel)

        # Add listener to midnight to reset the counter
        reset_time: time = self._coordinator.reset_time
        self.async_on_remove(
            async_track_time_change(
                hass=self.hass,
                action=self._on_midnight,
                hour=reset_time.hour,
                minute=reset_time.minute,
                second=0,
            )
        )

        # Add a listener to calculate OnTime at each minute
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._on_update_on_time,
                interval=timedelta(minutes=1),
            )
        )

        # restore the last value or set to 0
        self._attr_native_value = 0
        old_state = await self.async_get_last_state()
        if old_state is not None:
            if old_state.state is not None and old_state.state != "unknown":
                self._attr_native_value = round(float(old_state.state))
                logger.info(
                    "%s - read on_time from storage is %s",
                    self,
                    self._attr_native_value,
                )

            old_value = old_state.attributes.get("last_datetime_on")
            if old_value is not None:
                self._last_datetime_on = datetime.fromisoformat(old_value)

        self.update_custom_attributes()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Try to force backup of entity"""
        logger.info(
            "%s - force write before remove. on_time is %s",
            self,
            self._attr_native_value,
        )
        # Force dump in background
        await restore_async_get(self.hass).async_dump_states()

    @callback
    async def _on_state_change(self, event: Event) -> None:
        """The entity have change its state"""
        logger.info("Call of on_state_change at %s with event %s", now(), event)

        if not event.data:
            return

        new_state: State = event.data.get("new_state")
        # old_state: State = event.data.get("old_state")

        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            logger.debug("No available state. Event is ignored")
            return

        need_save = False
        # We search for the date of the event
        new_state = self._device.is_active  # new_state.state == STATE_ON
        # old_state = old_state is not None and old_state.state == STATE_ON
        if new_state and not self._old_state:
            logger.debug("The managed device becomes on - store the last_datetime_on")
            self._last_datetime_on = now()
            need_save = True

        if not new_state:
            if self._old_state and self._last_datetime_on is not None:
                logger.debug("The managed device becomes off - increment the delta time")
                self._attr_native_value += round((now() - self._last_datetime_on).total_seconds())
            self._last_datetime_on = None
            need_save = True

        # On sauvegarde le nouvel état
        if need_save:
            self._old_state = new_state
            self.update_custom_attributes()
            self.async_write_ha_state()
            self._device.set_daily_runtime(self._attr_native_value)

    @callback
    async def _on_midnight(self, _=None) -> None:
        """Called each day at midnight to reset the counter"""
        self._attr_native_value = 0

        logger.info("Call of _on_midnight to reset onTime")

        # reset _last_datetime_on to now if it was active. Here we lose the time on of yesterday but it is too late I can't do better.
        # Else you will have two point with the same date and not the same value (one with value + duration and one with 0)
        if self._last_datetime_on is not None:
            self._last_datetime_on = now()

        self.update_custom_attributes()
        self.async_write_ha_state()
        self._device.set_daily_runtime(self._attr_native_value)

    @callback
    async def _on_update_on_time(self, _=None) -> None:
        """Called priodically to update the on_time sensor"""
        logger.debug("Call of _on_update_on_time at %s", now())

        if self._last_datetime_on is not None and self._device.is_active:
            self._attr_native_value += round((now() - self._last_datetime_on).total_seconds())
            self._last_datetime_on = now()
            self.update_custom_attributes()
            self.async_write_ha_state()

            self._device.set_daily_runtime(self._attr_native_value)

    def update_custom_attributes(self):
        """Add some custom attributes to the entity"""
        self._attr_extra_state_attributes: dict(str, str) = {
            "last_datetime_on": self._last_datetime_on,
            "max_on_time_per_day_min": round(self._device.max_on_time_per_day_sec / 60),
            "max_on_time_per_day_sec": self._device.max_on_time_per_day_sec,
            "reset_time": self._coordinator.reset_time,
            "should_be_forced_offpeak": self._device.should_be_forced_offpeak,
            "offpeak_time": self._device.offpeak_time,
        }

    @property
    def icon(self) -> str | None:
        return "mdi:timer-play"

    @property
    def device_info(self) -> DeviceInfo | None:
        # Retournez des informations sur le périphérique associé à votre entité
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._device.name)},
            name="PV Excess Manager-" + self._device.name,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL,
        )

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return SensorDeviceClass.DURATION

    @property
    def state_class(self) -> SensorStateClass | None:
        return SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str | None:
        return UnitOfTime.SECONDS

    @property
    def suggested_display_precision(self) -> int | None:
        """Return the suggested number of decimal digits for display."""
        return 0

    @property
    def last_datetime_on(self) -> datetime | None:
        """Returns the last_datetime_on"""
        return self._last_datetime_on

    @property
    def get_attr_extra_state_attributes(self):
        """Get the extra state attributes for the entity"""
        return self._attr_extra_state_attributes

    async def service_reset_on_time(self):
        """Called by a service call:
        service: sensor.reset_on_time
        data:
        target:
            entity_id: pv_excess_manager.on_time_today_pv_excess_manager_<device name>
        """
        logger.info("%s - Calling service_reset_on_time", self)
        await self._on_midnight()
