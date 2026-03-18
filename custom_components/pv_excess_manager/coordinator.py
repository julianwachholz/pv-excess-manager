"""The main coordinator class."""

import logging
from datetime import time, timedelta
from typing import TYPE_CHECKING, Any, Self

from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import const
from .algorithm import PVExcessManagerAlgorithm
from .util import get_power_state

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

    from .managed_device import ManagedDevice


logger = logging.getLogger(__name__)


class PVExcessManagerCoordinator(DataUpdateCoordinator):
    """The manager that will enable and disable devices."""

    hass: HomeAssistant

    grid_consumption_entity_id: str
    power_production_entity_id: str
    subscribe_to_events: bool = False
    battery_soc_entity_id: str | None
    battery_consumption_entity_id: str | None

    reset_time: time = const.DEFAULT_RESET_TIME

    def __init__(self, hass: HomeAssistant, config):
        PVExcessManagerCoordinator.hass = hass
        self._devices: list[ManagedDevice] = []

        self._unsubscribe_events = None

        self._last_production = 0.0
        self._main_config_done = False

        super().__init__(hass, logger, name=const.NAME)

        self.config = config

    async def configure(self, config: ConfigEntry) -> None:
        """Configure the coordinator from configEntry of the integration."""
        self.grid_consumption_entity_id = str(config.data.get(const.CONF_GRID_CONSUMPTION_ENTITY_ID))
        self.power_production_entity_id = str(config.data.get(const.CONF_POWER_PRODUCTION_ENTITY_ID))
        self.subscribe_to_events = bool(config.data.get(const.CONF_SUBSCRIBE_TO_EVENTS))

        if self._unsubscribe_events is not None:
            self._unsubscribe_events()
            self._unsubscribe_events = None

        if self.subscribe_to_events:
            self._unsubscribe_events = async_track_state_change_event(
                self.hass,
                [
                    self.grid_consumption_entity_id,
                    self.power_production_entity_id,
                    # TODO: Check if battery should also be tracked
                ],
                self._async_on_change,
            )

        self.battery_soc_entity_id = config.data.get(const.CONF_BATTERY_SOC_ENTITY_ID)
        self.battery_consumption_entity_id = config.data.get(const.CONF_BATTERY_CONSUMPTION_ENTITY_ID)

        if time_str := config.data.get(const.CONF_RESET_TIME):
            self.reset_time = time.fromisoformat(time_str)

        self._main_config_done = True

        refresh_period = config.data.get(const.CONF_REFRESH_PERIOD_SEC) or const.DEFAULT_REFRESH_PERIOD_SEC
        self.update_interval = timedelta(seconds=refresh_period)
        self._schedule_refresh()

    def maybe_unsubscribe_events(self):
        """Unsubscribe from events if we are currently subscribed."""
        if self._unsubscribe_events is not None:
            self._unsubscribe_events()
            self._unsubscribe_events = None

    async def on_ha_started(self, _) -> None:
        """Listen the homeassistant_started event to initialize the first calculation."""
        logger.info("First initialization of PV Excess Manager")

    async def _async_on_change(self, event: Event[EventStateChangedData]) -> None:
        await self.async_refresh()
        self._schedule_refresh()

    async def _async_update_data(self):
        logger.info("Refreshing PV Excess Manager calculation")

        result = {}

        # Add a device state attributes
        for device in self.devices:
            # Initialize current power depending or reality
            device.update_current_power()

        result["grid_consumption"] = get_power_state(self.hass, self.grid_consumption_entity_id)
        if result["grid_consumption"] is None:
            logger.warning(
                "Grid consumption (%s) is not available. PV Excess Manager will be disabled.",
                self.grid_consumption_entity_id,
            )
            return None

        if self.power_production_entity_id:
            result["power_production"] = get_power_state(self.hass, self.power_production_entity_id)
            if result["power_production"] is None:
                logger.warning(
                    "Power production (%s) is not available but highly recommended.",
                    self.power_production_entity_id,
                )

        if self.battery_soc_entity_id and self.battery_consumption_entity_id:
            result["battery_soc"] = get_power_state(self.hass, self.battery_soc_entity_id)
            result["battery_consumption"] = get_power_state(self.hass, self.battery_consumption_entity_id)

        target_action, total_requested, virtual_excess = PVExcessManagerAlgorithm.run_calculation(
            self.devices,
            result["grid_consumption"],
            result.get("power_production"),
            result.get("battery_consumption"),
            result.get("battery_soc"),
        )

        result["managed_power"] = total_requested
        result["virtual_excess_power"] = virtual_excess

        if target_action is None:
            logger.info("No action to be taken.")
            return result

        unique_id, requested_power = target_action
        result["target_device"] = unique_id
        result["target_power"] = requested_power
        result[unique_id] = device

        device = self.get_device_by_unique_id(unique_id)

        if not device:
            logger.warning("Device with unique_id %s not found. Action cannot be taken.", unique_id)
            return result

        if requested_power == 0:
            logger.debug("Deactivating %s", device.name)
            await device.deactivate()
        elif not device.is_active:
            logger.debug("Activating %s with %s W", device.name, requested_power)
            await device.activate(requested_power)
        else:
            logger.debug("Change power of %s to %s W", device.name, requested_power)
            await device.change_requested_power(requested_power)

        logger.info("Coordinator result: %s", result)
        return result

    @classmethod
    def get_coordinator(cls) -> Self:
        """Get the coordinator from the hass.data."""
        if (
            not hasattr(PVExcessManagerCoordinator, "hass")
            or PVExcessManagerCoordinator.hass is None
            or PVExcessManagerCoordinator.hass.data[const.DOMAIN] is None
        ):
            return None

        return PVExcessManagerCoordinator.hass.data[const.DOMAIN]["coordinator"]

    @classmethod
    def reset(cls) -> Any:
        """Reset the coordinator from the hass.data."""
        if (
            not hasattr(PVExcessManagerCoordinator, "hass")
            or PVExcessManagerCoordinator.hass is None
            or PVExcessManagerCoordinator.hass.data[const.DOMAIN] is None
        ):
            return

        if coordinator := PVExcessManagerCoordinator.hass.data[const.DOMAIN].get("coordinator"):
            coordinator.maybe_unsubscribe_events()
        PVExcessManagerCoordinator.hass.data[const.DOMAIN]["coordinator"] = None

    @property
    def is_main_config_done(self) -> bool:
        """Return True if the main config is done."""
        return self._main_config_done

    @property
    def devices(self) -> list[ManagedDevice]:
        """Get all the managed device."""
        return self._devices

    def get_device_by_name(self, name: str) -> ManagedDevice | None:
        """Return the device which name is given in argument."""
        for _, device in enumerate(self.devices):
            if device.name == name:
                return device
        return None

    def get_device_by_unique_id(self, uid: str) -> ManagedDevice | None:
        """Return the device which name is given in argument."""
        for _, device in enumerate(self.devices):
            if device.unique_id == uid:
                return device
        return None

    def add_device(self, device: ManagedDevice):
        """Add a new device to the list of managed device."""
        # Append or replace the device
        for i, dev in enumerate(self._devices):
            if dev.unique_id == device.unique_id:
                self._devices[i] = device
                return
        self._devices.append(device)

    def remove_device(self, unique_id: str):
        """Remove a device from the list of managed device."""
        for i, dev in enumerate(self._devices):
            if dev.unique_id == unique_id:
                self._devices.pop(i)
                return
