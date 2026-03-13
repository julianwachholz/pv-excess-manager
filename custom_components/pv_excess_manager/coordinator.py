"""The main coordinator class."""

import logging
from datetime import time, timedelta
from typing import TYPE_CHECKING, Any, Self

from homeassistant.helpers.event import (
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from . import const
from .algorithm import PVExcessManagerAlgorithm
from .util import get_power_state, name_to_unique_id

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

    reset_time: time

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

        self.reset_time = config.data.get(const.CONF_RESET_TIME) or const.DEFAULT_RESET_TIME

        self._main_config_done = True

        refresh_period = config.data.get(const.CONF_REFRESH_PERIOD_SEC) or const.DEFAULT_REFRESH_PERIOD_SEC
        self.update_interval = timedelta(seconds=refresh_period)
        self._schedule_refresh()

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
        for _, device in enumerate(self.devices):
            # Initialize current power depending or reality
            device.set_current_power_with_device_state()

        result["grid_consumption"] = get_power_state(self.hass, self.grid_consumption_entity_id)
        if result["grid_consumption"] is None:
            logger.warning("Grid consumption is not available. PV Excess Manager will be disabled.")
            return None

        if self.power_production_entity_id:
            result["power_production"] = get_power_state(self.hass, self.power_production_entity_id)
            if result["power_production"] is None:
                logger.warning("Power production is not available but highly recommended.")

        if self.battery_soc_entity_id and self.battery_consumption_entity_id:
            result["battery_soc"] = get_power_state(self.hass, self.battery_soc_entity_id)
            result["battery_consumption"] = get_power_state(self.hass, self.battery_consumption_entity_id)

        best_solution, total_power = PVExcessManagerAlgorithm.run_calculation(
            self.devices,
            result["grid_consumption"],
            result.get("power_production"),
            result.get("battery_consumption"),
            result.get("battery_soc"),
        )

        result["best_solution"] = best_solution
        result["total_power"] = total_power

        # Uses the result to turn on or off or change power
        log = logger.debug
        for _, equipment in enumerate(best_solution):
            name = equipment[const.CONF_NAME]
            requested_power = equipment.get("requested_power")
            state = equipment["state"]
            logger.debug("Dealing with best_solution for %s - %s", name, equipment)
            device = self.get_device_by_name(name)
            if not device:
                continue

            old_requested_power = device.requested_power
            is_active = device.is_active
            should_force_offpeak = device.should_be_forced_offpeak
            if should_force_offpeak:
                logger.debug("%s - we should force %s name", self, name)
            if is_active and not state and not should_force_offpeak:
                logger.debug("Deactivating %s", name)
                log = logger.info
                old_requested_power = 0
                await device.deactivate()
            elif not is_active and (state or should_force_offpeak):
                logger.debug("Activating %s", name)
                log = logger.info
                old_requested_power = requested_power
                await device.activate(requested_power)

            # Send change power if state is now on and change power is accepted and (power have change or eqt is just activated)
            if state and device.can_change_power and (device.current_power != requested_power or not is_active):
                logger.debug(
                    "Change power of %s to %s",
                    name,
                    requested_power,
                )
                log = logger.info
                await device.change_requested_power(requested_power)

            device.set_requested_power(old_requested_power)

            # Add updated data to the result
            result[name_to_unique_id(name)] = device

        result["managed_power"] = result["grid_consumption"] / 3
        result["virtual_excess_power"] = result["grid_consumption"] / 2

        log("Result: %s", result)
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
