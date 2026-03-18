"""A managed device may be controlled by our excess manager."""

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.light.const import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import Template
from homeassistant.util.dt import now
from slugify import slugify

from . import const
from .exceptions import ConfigurationError
from .util import convert_to_template_or_value, get_template_or_value

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


async def enable_entity(hass: HomeAssistant, entity_id: str):
    """Enable an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    await hass.services.async_call(
        domain,
        "turn_on",
        {"entity_id": entity_id},
        blocking=False,
    )


async def disable_entity(hass: HomeAssistant, entity_id: str):
    """Disable an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    await hass.services.async_call(
        domain,
        "turn_off",
        {"entity_id": entity_id},
        blocking=False,
    )


async def set_entity_value(hass: HomeAssistant, entity_id: str, value: float):
    """Set the value of an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    if domain in {NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN}:
        await hass.services.async_call(
            domain,
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=False,
        )
    else:
        logger.warning(
            "Cannot automatically change power on device %s. Please define custom actions!",
            entity_id,
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
    _power_step: float | Template = 0
    power_divide_factor: float = 1
    power_entity_id: str | None

    _battery_min_soc: float | Template = 0

    current_power: float = 0
    requested_power: float = 0

    duration_ontime: timedelta = timedelta(0)
    duration_power: timedelta = timedelta(0)
    duration_offtime: timedelta = timedelta(0)

    activate_delay: timedelta = timedelta(0)
    deactivate_delay: timedelta = timedelta(0)
    _pending_activate: datetime | None = None
    _pending_deactivate: datetime | None = None

    is_managed: bool = True
    check_usable_template: Template | None = None

    priority: int = const.DEFAULT_PRIORITY

    # Device must not be changed before this time stamp
    locked_until: datetime
    power_locked_until: datetime

    activate_actions: list
    deactivate_actions: list
    change_power_service: str

    battery_soc: float = 0

    _daily_runtime: float = 0
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
        self.activate_delay = timedelta(minutes=device_config.get(const.CONF_DELAY_ACTIVATE_MIN) or 0)

        self.duration_power = duration
        power_minutes = device_config.get(const.CONF_DURATION_POWER_MIN)
        if power_minutes is not None:
            self.duration_power = timedelta(minutes=power_minutes)

        self.duration_offtime = duration
        if offtime_minutes := device_config.get(const.CONF_OFFTIME_DURATION_MIN):
            self.duration_offtime = timedelta(minutes=offtime_minutes)
        self.deactivate_delay = timedelta(minutes=device_config.get(const.CONF_DELAY_DEACTIVATE_MIN) or 0)

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
            self.requested_power = self.power_nominal

        self.is_managed = True

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
        return slugify(self.name).replace("-", "_")

    async def _apply_action(self, action_type: str, requested_power: float | None = None):
        """
        Apply an action to a managed device.

        This method is a generic method for activate, deactivate, change_requested_power

        """
        logger.debug(
            "Applying action %s for %s. requested_power=%s",
            action_type,
            self.name,
            requested_power,
        )

        if requested_power is None:
            requested_power = self.requested_power

        self.requested_power = requested_power

        action_context = Context()
        script_variables = {
            "requested_power": requested_power,
            "current_power": self.current_power,
            "power_divide_factor": self.power_divide_factor,
        }

        if action_type == ACTION_ACTIVATE:
            if not self.activate_actions:
                await enable_entity(self.hass, self.entity_id)
            else:
                logger.debug("Executing custom activate_actions for %s", self.name)
                script = Script(
                    self.hass,
                    sequence=self.activate_actions,
                    name=f"Activate: {self.name}",
                    domain=const.DOMAIN,
                )
                await script.async_run(
                    run_variables=script_variables,
                    context=action_context,
                )

            self.reset_next_date_available(action_type)
            if self.can_change_power:
                self.reset_next_date_available_power()

        elif action_type == ACTION_DEACTIVATE:
            if not self.deactivate_actions:
                await disable_entity(self.hass, self.entity_id)
            else:
                logger.debug("Executing custom deactivate_actions for %s", self.name)
                script = Script(
                    self.hass,
                    sequence=self.deactivate_actions,
                    name=f"Deactivate: {self.name}",
                    domain=const.DOMAIN,
                )
                await script.async_run(
                    run_variables=script_variables,
                    context=action_context,
                )

            self.reset_next_date_available(action_type)

        elif action_type == ACTION_CHANGE_POWER:
            if not self.can_change_power:
                msg = f"Device {self.name} cannot change its power!"
                raise RuntimeError(msg)

            if self.activate_actions:
                logger.debug("Executing custom activate_actions for %s", self.name)
                script = Script(
                    self.hass,
                    sequence=self.activate_actions,
                    name=f"Change Power: {self.name}",
                    domain=const.DOMAIN,
                )
                await script.async_run(
                    run_variables=script_variables,
                    context=action_context,
                )
            elif self.power_entity_id:
                requested_amps = requested_power / self.power_divide_factor
                await set_entity_value(self.hass, self.power_entity_id, requested_amps)
            else:
                logger.warning(
                    "Cannot change power on device %s!",
                    self.name,
                )

            self.reset_next_date_available_power()

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
        self.power_locked_until = now() + self.duration_power
        logger.debug(
            "Next availability date for power change for %s is %s",
            self.name,
            self.power_locked_until,
        )

        # When changing power levels, if _next_date_available does not comply with
        # the minimum power time, it is updated to ensure a minimum time at the
        # new power level before a cut can be authorized.
        if self.locked_until <= self.power_locked_until:
            self.locked_until = self.power_locked_until
            logger.info(
                "Updated next availability date for %s to %s to comply with power change minimum time",
                self.name,
                self.locked_until,
            )

    def update_current_power(self):
        """Set the current power according to the real device state."""
        if self.power_sensor_entity_id is not None:
            # Always use real power sensor if available
            power_entity_state = self.hass.states.get(self.power_sensor_entity_id)
            if power_entity_state and power_entity_state.state not in [None, STATE_UNKNOWN, STATE_UNAVAILABLE]:
                self.current_power = float(power_entity_state.state)
                logger.debug(
                    "Set current_power to %s for device %s based on power sensor %s",
                    self.current_power,
                    self.name,
                    self.power_sensor_entity_id,
                )
                return

            logger.warning(
                "Power sensor entity %s for device %s is not available. Falling back to nominal power.",
                self.power_sensor_entity_id,
                self.name,
            )

        if not self.is_active:
            self.current_power = 0
            logger.debug("Set current_power to 0 for device %s because it's not active", self.name)
            return

        if not self.can_change_power:
            self.current_power = self.power_nominal
            logger.debug(
                "Set current_power to %s for device %s because it's active and cannot change power",
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
                "Set current_power to %s for device %s because can_change_power but state is %s",
                self.current_power,
                self.name,
                power_entity_state,
            )
            return

        if self.power_entity_id is not None and self.power_entity_id.startswith(POWERED_ENTITY_DOMAINS_NEED_ATTR):
            msg = (f"Device {self.name} uses variable power but power entity domain isn't supported yet.",)
            raise NotImplementedError(msg)
        else:
            power_entity_value = power_entity_state.state

        self.current_power = float(power_entity_value) * self.power_divide_factor
        logger.debug(
            "Set current_power to %s for device %s cause can_change_power and amps is %s",
            self.current_power,
            self.name,
            power_entity_value,
        )

    def set_managed(self, *, is_managed: bool):
        """Enable or disable the ManagedDevice for PV Excess Manager."""
        logger.info("%s - set managed=%s", self.name, is_managed)
        self.is_managed = is_managed

    @property
    def is_active(self) -> bool:
        """Check if device is active by getting the underlying state of the device."""
        device_active: bool = False

        if not self.is_managed:
            return False

        if self.entity_id:
            device_state = self.hass.states.get(self.entity_id)
            device_active = device_state and device_state.state == STATE_ON

        return device_active

    def check_usable(self, *, check_battery: bool = True) -> bool:
        """Check if the device is usable. The battery is checked optionally."""
        if self.daily_runtime >= self.max_daily_runtime:
            # TODO: Allow change if device is ON here; it means it should be turned off
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

        if self.can_change_power and self.is_power_locked:
            logger.debug("%s is not usable due to power lock until %s", self.name, self.power_locked_until)
            return False

        if self.is_locked:
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

    def should_be_forced_offpeak(self) -> bool:
        """Check if device must be enabled at offpeak time."""
        if not self.check_usable(check_battery=False) or self.offpeak_time is None:
            return False

        if self.daily_runtime >= self.max_daily_runtime or self.daily_runtime > self.min_daily_runtime:
            # Maximum exceeded or minimum already reached, do not force offpeak
            return False

        time = now().time()
        if self.offpeak_time >= self.coordinator.reset_time:
            # e.g. offpeak=20:00 >= 05:00, offpeak is from 20:00 to 05:00,
            # we are offpeak if time is after 20:00 or before 05:00
            return time >= self.offpeak_time or time < self.coordinator.reset_time

        # e.g. offpeak=02:00 < 05:00, offpeak is from 02:00 to 05:00,
        # we are offpeak if time is after 02:00 and before 05:00
        return time >= self.offpeak_time and time < self.coordinator.reset_time

    def is_activate_delay_passed(self) -> bool:
        """Check if the device may be activated after waiting for the activate delay."""
        if not self.activate_delay:
            return True

        if self._pending_activate is None:
            self._pending_activate = now()  # Start the timer
            return False

        return (now() - self._pending_activate) >= self.activate_delay

    def reset_activate_delay(self):
        """Reset the activate delay timer."""
        self._pending_activate = None

    def is_deactivate_delay_passed(self) -> bool:
        """Check if the device may be deactivated after waiting for the deactivate delay."""
        if not self.deactivate_delay:
            return True

        if self._pending_deactivate is None:
            self._pending_deactivate = now()  # Start the timer
            return False

        return (now() - self._pending_deactivate) >= self.deactivate_delay

    def reset_deactivate_delay(self):
        """Reset the deactivate delay timer."""
        self._pending_deactivate = None

    @property
    def is_locked(self) -> bool:
        """Check if the device is locked."""
        return now() < self.locked_until

    @property
    def is_power_locked(self) -> bool:
        """Check if the device's power is locked."""
        return now() < self.power_locked_until

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
    def power_step(self) -> float:
        """The power step of the managed device."""
        return get_template_or_value(self._power_step)

    @power_step.setter
    def power_step(self, value: float | Template):
        self._power_step = convert_to_template_or_value(self.hass, value) or 0

    @property
    def battery_min_soc(self) -> float:
        """Minimum battery SOC before this device may be enabled."""
        return get_template_or_value(self._battery_min_soc)

    @battery_min_soc.setter
    def battery_min_soc(self, value: float | Template):
        self._battery_min_soc = convert_to_template_or_value(self.hass, value) or 0

    @property
    def daily_runtime(self) -> timedelta:
        """Daily runtime of this device."""
        return timedelta(seconds=self._daily_runtime)

    @daily_runtime.setter
    def daily_runtime(self, seconds: float):
        """Daily runtime of this device."""
        self._daily_runtime = seconds

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
