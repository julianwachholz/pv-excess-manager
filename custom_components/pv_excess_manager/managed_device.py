"""A managed device may be controlled by our excess manager."""

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light.const import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.template import Template
from homeassistant.util.dt import now
from slugify import slugify

from . import const
from .exceptions import ConfigurationError
from .util import (
    convert_to_template_or_value,
    get_template_or_value,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import PVExcessManagerCoordinator


logger = logging.getLogger(__name__)

ACTION_ACTIVATE = "activate"
ACTION_DEACTIVATE = "deactivate"
ACTION_CHANGE_POWER = "change_power"

# Entity domains that require attribute to change power
POWERED_ENTITY_DOMAINS_NEED_ATTR = (
    LIGHT_DOMAIN,
    FAN_DOMAIN,
)


class ManagedDevice:
    """A Managed device representation."""

    hass: HomeAssistant
    coordinator: PVExcessManagerCoordinator

    name: str
    entity_id: str | None
    unique_id: str

    power_nominal: float
    power_sensor_entity_id: str | None

    _power_max: float | Template = 0
    power_step: float = 0
    power_divide_factor: float = 1
    power_entity_id: str | None

    _battery_min_soc: float | Template = 0

    current_power: float = 0
    requested_power: float = 0

    duration_ontime: timedelta = timedelta(0)
    duration_power: timedelta = timedelta(0)
    duration_offtime: timedelta = timedelta(0)

    is_enabled: bool = True
    check_usable_template: Template | None = None

    priority: int = const.DEFAULT_PRIORITY

    # Device must not be changed before this time stamp
    locked_until: datetime
    power_locked_until: datetime

    activate_actions: list
    deactivate_actions: list
    change_power_service: str

    battery_soc: float = 0

    daily_runtime: timedelta = timedelta(0)
    _min_daily_runtime: float | Template = 0
    _max_daily_runtime: float | Template = 24 * 60
    offpeak_time: time | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        device_config: dict,
        coordinator: PVExcessManagerCoordinator,
    ):
        """Initialize a manageable device."""
        self.hass = hass
        self.coordinator = coordinator

        self.name = str(device_config.get(const.CONF_NAME))
        self.unique_id = str(device_config.get(const.CONF_UNIQUE_ID))
        self.entity_id = device_config.get(const.CONF_ENTITY_ID)

        self.power_nominal = float(device_config.get(const.CONF_NOMINAL_POWER) or 0)
        if self.power_nominal <= 0:
            msg = f"Device {self.name} nominal power ({self.power_nominal!r}) must be > 0."
            raise ConfigurationError(msg)
        self.power_sensor_entity_id = device_config.get(const.CONF_POWER_SENSOR_ENTITY_ID)

        # Attributes for variable power devices
        self.power_max = device_config.get(const.CONF_POWER_MAX) or 0
        self.power_step = device_config.get(const.CONF_POWER_STEP) or 0
        self.power_divide_factor = device_config.get(const.CONF_POWER_DIVIDE_FACTOR) or 1
        self.power_entity_id = device_config.get(const.CONF_POWER_ENTITY_ID)

        # Duration control
        duration = timedelta(minutes=device_config.get(const.CONF_ONTIME_DURATION_MIN) or 1)
        self.duration_ontime = duration

        self.duration_power = duration
        if power_minutes := device_config.get(const.CONF_DURATION_POWER_MIN):
            self.duration_power = timedelta(minutes=power_minutes)

        self.duration_offtime = duration
        if offtime_minutes := device_config.get(const.CONF_OFFTIME_DURATION_MIN):
            self.duration_offtime = timedelta(minutes=offtime_minutes)

        if template := device_config.get(const.CONF_CHECK_AVAILABLE_TEMPLATE):
            self.check_usable_template = Template(template, hass)

        self.locked_until = self.power_locked_until = now()

        self.activate_actions = device_config.get(const.CONF_ACTIVATE_ACTIONS)
        self.deactivate_actions = device_config.get(const.CONF_DEACTIVATE_ACTIONS)
        self.change_power_service = str(device_config.get(const.CONF_CHANGE_POWER_SERVICE))

        self.battery_min_soc = device_config.get(const.CONF_BATTERY_MIN_SOC) or 0

        self.min_daily_runtime = device_config.get(const.CONF_MIN_DAILY_RUNTIME) or 0
        self.max_daily_runtime = device_config.get(const.CONF_MAX_DAILY_RUNTIME) or 24 * 60
        self.offpeak_time = device_config.get(const.CONF_OFFPEAK_TIME)

        self.priority = int(device_config.get(const.CONF_PRIORITY) or const.DEFAULT_PRIORITY)

        if self.is_active:
            self.requested_power = self.current_power = self.power_max if self.can_change_power else self.power_nominal

        self.is_enabled = True

        # Validate configuration
        if self.min_daily_runtime and self.offpeak_time is None:
            msg = f"Configuration of device ${self.name} is incorrect. min_daily_runtime requires offpeak_time."
            logger.error("%s - %s", self, msg)
            raise ConfigurationError(msg)

        if self.min_daily_runtime > self.max_daily_runtime:
            msg = f"Configuration of device ${self.name} is incorrect. min daily runtime must be less than max."
            logger.error("%s - %s", self, msg)
            raise ConfigurationError(msg)

    @property
    def slug(self) -> str:
        """Get a slug for this device."""
        return slugify(self.name).replace(" ", "_")

    async def _apply_action(self, action_type: str, requested_power=None):
        """
        Apply an action to a managed device.

        This method is a generical method for activate, deactivate, change_requested_power

        """
        logger.debug(
            "Applying action %s for %s. requested_power=%s",
            action_type,
            self.name,
            requested_power,
        )

        if requested_power is None:
            requested_power = self.requested_power

        if action_type == ACTION_ACTIVATE:
            # TODO Execute these actions
            print(repr(self.activate_actions))
            self.reset_next_date_available(action_type)
            if self.can_change_power:
                self.reset_next_date_available_power()

        elif action_type == ACTION_DEACTIVATE:
            # TODO Execute these actions
            print(repr(self.deactivate_actions))
            self.reset_next_date_available(action_type)

        elif action_type == ACTION_CHANGE_POWER:
            if not self.can_change_power:
                msg = f"Equipment {self.name} cannot change its power. We should not be there."
                raise RuntimeError(msg)
            self.reset_next_date_available_power()

        self.current_power = self.requested_power

    async def activate(self, requested_power: float):
        """Activate this device."""
        return await self._apply_action(ACTION_ACTIVATE, requested_power)

    async def deactivate(self):
        """Deactivate this device."""
        return await self._apply_action(ACTION_DEACTIVATE, 0)

    async def change_requested_power(self, requested_power: float):
        """Change a device's variable power."""
        return await self._apply_action(ACTION_CHANGE_POWER, requested_power)

    def reset_next_date_available(self, action_type):
        """Set the next availability date to change the device state."""
        if action_type == ACTION_ACTIVATE:
            self.locked_until = now() + self.duration_ontime
        else:
            self.locked_until = now() + self.duration_offtime

        logger.debug("Next availability date for %s is %s", self.name, self.locked_until)

    def reset_next_date_available_power(self):
        """Set the next availability date to change the variable device's power."""
        self.locked_until = now() + self.duration_power
        logger.debug(
            "Next availability date for power change for %s is %s",
            self.name,
            self.locked_until,
        )
        # When changing power levels, if _next_date_available does not comply with
        # the minimum power time, it is updated to ensure a minimum time at the
        # new power level before a cut can be authorized.
        if self.locked_until <= self.locked_until:
            self.locked_until = self.locked_until
            logger.info(
                "Mise à jour de Next availability date suite au changmement de puissance %s is %s",
                self.name,
                self.locked_until,
            )

    def set_current_power_with_device_state(self):
        """Set the current power according to the real device state"""
        if not self.is_active:
            self.current_power = 0
            logger.debug("Set current_power to 0 for device %s cause not active", self.name)
            return

        if not self.can_change_power:
            self.current_power = self.power_max
            logger.debug(
                "Set current_power to %s for device %s cause active and not can_change_power",
                self.current_power,
                self.name,
            )
            return

        power_entity_state = self.hass.states.get(self.power_entity_id)
        if not power_entity_state or power_entity_state.state in [
            None,
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ]:
            self.current_power = self.power_nominal
            logger.debug(
                "Set current_power to %s for device %s cause can_change_power but state is %s",
                self.current_power,
                self.name,
                power_entity_state,
            )
            return

        if self.power_entity_id.startswith(POWERED_ENTITY_DOMAINS_NEED_ATTR):
            # TODO : move this part to device initialisation, make new instance variable
            service_name = self.change_power_service  # retrieve attribute from power service
            parties = self.change_power_service.split("/")
            if len(parties) < 2:
                msg = f"Incorrect service declaration for power entity. Service {service_name} should be formatted with: 'domain/action/attribute'"
                raise ConfigurationError(msg)
            parameter = parties[2]
            power_entity_value = power_entity_state.attributes[parameter]
        else:
            power_entity_value = power_entity_state.state

        # TODO: Automatically switch to Amps based on the entity unit?
        self.current_power = round(float(power_entity_value) * self.power_divide_factor)
        logger.debug(
            "Set current_power to %s for device %s cause can_change_power and amps is %s",
            self.current_power,
            self.name,
            power_entity_value,
        )

    def set_priority(self, priority: int):
        """Set the priority of the ManagedDevice."""
        logger.info("%s - set priority=%s", self.name, priority)
        self.priority = priority

    def set_enable(self, enable: bool):
        """Enable or disable the ManagedDevice for PV Excess Manager."""
        logger.info("%s - set enable=%s", self.name, enable)
        self.is_enabled = enable

    def set_daily_runtime(self, seconds: float):
        """Set the time the underlying device was on per day."""
        logger.info("%s - set daily_runtime=%s", self.name, seconds)
        self.daily_runtime = timedelta(seconds=seconds)

    def set_requested_power(self, requested_power: float):
        """Set the requested power of the ManagedDevice."""
        self.requested_power = requested_power

    @property
    def is_active(self) -> bool:
        """Check if device is active by getting the underlying state of the device."""
        if not self.is_enabled:
            return False

        if self.power_sensor_entity_id:
            power_sensor_state = self.hass.states.get(self.power_sensor_entity_id)
            if power_sensor_state and power_sensor_state.state not in [None, STATE_UNKNOWN, STATE_UNAVAILABLE]:
                power_value = float(power_sensor_state.state)
                device_active = power_value > 0
                logger.debug(
                    "%s - power sensor %s value is %s, active=%s",
                    self.name,
                    self.power_sensor_entity_id,
                    power_value,
                    device_active,
                )
            else:
                logger.debug(
                    "%s - power sensor %s state is %s, cannot determine active state",
                    self.name,
                    self.power_sensor_entity_id,
                    power_sensor_state,
                )
                device_active = False
        return True

    def check_usable(self, *, check_battery: bool = True) -> bool:
        """Check if the device is usable. The battery is checked optionally."""
        if self.daily_runtime >= self.max_daily_runtime:
            logger.debug(
                "%s not usable: daily_runtime %d >= %d max_daily_runtime",
                self.name,
                self.daily_runtime,
                self.max_daily_runtime,
            )
            return False

        if self.check_usable_template is not None and not self.check_usable_template.async_render():
            logger.debug("%s not usable: check_usable_template is false", self.name)
            return False

        _now = now()

        if self.can_change_power and _now < self.power_locked_until:
            logger.debug("%s is not usable due to power lock until %s", self.name, self.power_locked_until)
            return False

        if _now < self.locked_until:
            logger.debug("%s is not usable due to lock until %s", self.name, self.locked_until)
            return False

        if (
            check_battery
            and self.battery_soc is not None
            and self.battery_min_soc is not None
            and self.battery_soc < self.battery_min_soc
        ):
            logger.debug(
                "%s is not usable due to battery soc threshold (%s < %s)",
                self.name,
                self.battery_soc,
                self.battery_min_soc,
            )
            return False

        return True

    @property
    def is_usable(self) -> bool:
        """
        Check if the device can be used right now.

        A device is usable for optimisation if the check_usable_template returns true and
        if the device is not waiting for the end of its cycle and if the battery_soc_threshold is >= battery_soc
        and the _max_daily_runtime is not exceeded.
        """
        return self.check_usable(check_battery=True)

    @property
    def should_be_forced_offpeak(self) -> bool:
        """True is we are offpeak and the max_on_time is not exceeded."""
        if not self.check_usable(check_battery=False) or self.offpeak_time is None:
            return False

        time = now().time()
        if self.offpeak_time >= self.coordinator.reset_time:
            return (
                (time >= self.offpeak_time or time < self.coordinator.reset_time)
                and self.daily_runtime < self.max_daily_runtime
                and self.daily_runtime < self.min_daily_runtime
            )

        return (
            time >= self.offpeak_time
            and time < self.coordinator.reset_time
            and self.daily_runtime < self.max_daily_runtime
            and self.daily_runtime < self.min_daily_runtime
        )

    @property
    def is_waiting(self):
        """A device is waiting if the device is waiting for the end of its cycle."""
        result = now() < self.locked_until

        if result:
            logger.debug("%s is waiting", self.name)

        return result

    @property
    def can_change_power(self) -> bool:
        """Check if the device can change its power."""
        return self.power_max is not None and self.power_max > self.power_nominal

    @property
    def power_max(self) -> float:
        """The maximum power of the managed device."""
        return get_template_or_value(self._power_max)

    @power_max.setter
    def power_max(self, value: float | Template):
        self._power_max = convert_to_template_or_value(self.hass, value) or 0

    @property
    def battery_min_soc(self) -> float:
        """Minimum battery SOC before this device may be enabled."""
        return get_template_or_value(self._battery_min_soc)

    @battery_min_soc.setter
    def battery_min_soc(self, value: float | Template):
        self._battery_min_soc = convert_to_template_or_value(self.hass, value) or 0

    @property
    def min_daily_runtime(self) -> timedelta:
        """Minimum daily runtime this device requires."""
        return timedelta(minutes=get_template_or_value(self._min_daily_runtime))

    @min_daily_runtime.setter
    def min_daily_runtime(self, value: float | Template):
        self._min_daily_runtime = convert_to_template_or_value(self.hass, value) or 0

    @property
    def max_daily_runtime(self) -> timedelta:
        """Maximum daily runtime for this device."""
        return timedelta(minutes=get_template_or_value(self._max_daily_runtime))

    @max_daily_runtime.setter
    def max_daily_runtime(self, value: float | Template):
        self._max_daily_runtime = convert_to_template_or_value(self.hass, value) or 0
