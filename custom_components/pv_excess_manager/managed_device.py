"""A managed device may be controlled by our excess manager."""

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.light.const import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context
from homeassistant.helpers import config_validation as cv
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


async def enable_entity(hass: HomeAssistant, entity_id: str, requested_power: float | None = None):
    """Enable an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    await hass.services.async_call(
        domain,
        "turn_on",
        {"entity_id": entity_id},
        blocking=False,
    )


async def disable_entity(hass: HomeAssistant, entity_id: str, requested_power: float | None = None):
    """Disable an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    await hass.services.async_call(
        domain,
        "turn_off",
        {"entity_id": entity_id},
        blocking=False,
    )


async def set_entity_value(hass: HomeAssistant, entity_id: str, requested_power: float):
    """Set the value of an entity by calling the appropriate service based on its domain."""
    domain = entity_id.split(".", maxsplit=1)[0]
    if domain in {NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN}:
        await hass.services.async_call(
            domain,
            "set_value",
            {"entity_id": entity_id, "value": requested_power},
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
    entity_id: str
    unique_id: str

    _power_nominal: float = 0
    power_sensor_entity_id: str | None = None

    _power_max: float | Template = 0
    _power_step: float | Template = 0
    power_divide_factor: float = 1
    power_entity_id: str | None = None
    device_type: str = const.CONF_DEVICE_BASIC

    # Phase-switching wallbox specific attributes
    min_current: float = const.DEFAULT_MIN_CURRENT
    max_current: float = const.DEFAULT_MAX_CURRENT
    current_phases_entity_id: str | None = None
    voltage: float = const.DEFAULT_VOLTAGE
    _requested_phases: int | None = None

    _battery_min_soc: float | Template = 0
    standby_power: float = 0
    disabled_due_to_standby: bool = False

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
    locked_until: datetime = None
    power_locked_until: datetime = None

    activate_actions: list = None
    deactivate_actions: list = None

    battery_soc: float = 0

    _daily_runtime: float = 0
    _min_daily_runtime: float | Template = 0
    _max_daily_runtime: float | Template = 24 * 60
    offpeak_time: time | None = None

    def __init__(  # noqa: PLR0912, PLR0915
        self,
        hass: HomeAssistant,
        device_config: dict,
        coordinator: PVExcessManagerCoordinator,
    ):
        """Initialize a manageable device."""
        self.hass = hass
        self.coordinator = coordinator

        self.name = cv.string(device_config.get(const.CONF_NAME))
        self.unique_id = cv.string(device_config.get(const.CONF_UNIQUE_ID))
        self.entity_id = cv.entity_id_or_uuid(device_config.get(const.CONF_ENTITY_ID))
        self.device_type = cv.string(device_config.get(const.CONF_DEVICE_TYPE) or const.CONF_DEVICE_BASIC)

        if self.is_phase_switching_wallbox:
            self.min_current = cv.positive_float(
                device_config.get(const.CONF_MIN_CURRENT) or const.DEFAULT_MIN_CURRENT
            )
            self.max_current = cv.positive_float(
                device_config.get(const.CONF_MAX_CURRENT) or const.DEFAULT_MAX_CURRENT
            )
            if self.min_current > self.max_current:
                msg = (
                    f"Device {self.name} configuration is incorrect. "
                    f"min_current ({self.min_current!r}) must be <= max_current ({self.max_current!r})."
                )
                raise ConfigurationError(msg)

            phases_entity = device_config.get(const.CONF_CURRENT_PHASES_ENTITY_ID)
            if phases_entity is None:
                msg = f"Device {self.name} requires current_phases_entity_id."
                raise ConfigurationError(msg)
            self.current_phases_entity_id = cv.entity_id_or_uuid(phases_entity)
            self.voltage = cv.positive_float(device_config.get(const.CONF_VOLTAGE) or const.DEFAULT_VOLTAGE)
            self.power_nominal = self.min_power_for_phase(self.min_supported_phase)
        else:
            self.power_nominal = cv.positive_float(device_config.get(const.CONF_NOMINAL_POWER) or 0)

        if self.power_nominal <= 0:
            msg = f"Device {self.name} nominal power ({self.power_nominal!r}) must be > 0."
            raise ConfigurationError(msg)

        if power_sensor_entity_id := device_config.get(const.CONF_POWER_SENSOR_ENTITY_ID):
            self.power_sensor_entity_id = cv.entity_id_or_uuid(power_sensor_entity_id)

        # Attributes for variable power devices
        if self.is_phase_switching_wallbox:
            self.power_max = self.max_power_for_phase(self.max_supported_phase)
            self.power_step = 1
            self.power_divide_factor = 1
        else:
            self.power_max = cv.positive_float(device_config.get(const.CONF_POWER_MAX) or 0)
            self.power_step = cv.positive_float(device_config.get(const.CONF_POWER_STEP) or 0)
            self.power_divide_factor = cv.positive_float(device_config.get(const.CONF_POWER_DIVIDE_FACTOR) or 1)
        if power_entity_id := device_config.get(const.CONF_POWER_ENTITY_ID):
            self.power_entity_id = cv.entity_id_or_uuid(power_entity_id)

        # Duration control
        duration = timedelta(minutes=device_config.get(const.CONF_ONTIME_DURATION_MIN) or 1)
        self.duration_ontime = cv.positive_timedelta(duration)
        self.activate_delay = cv.positive_timedelta(
            timedelta(minutes=device_config.get(const.CONF_DELAY_ACTIVATE_MIN) or 0)
        )

        self.duration_power = timedelta(0)
        if power_minutes := device_config.get(const.CONF_DURATION_POWER_MIN):
            self.duration_power = cv.positive_timedelta(timedelta(minutes=power_minutes))

        self.duration_offtime = timedelta(0)
        if offtime_minutes := device_config.get(const.CONF_OFFTIME_DURATION_MIN):
            self.duration_offtime = cv.positive_timedelta(timedelta(minutes=offtime_minutes))
        self.deactivate_delay = cv.positive_timedelta(
            timedelta(minutes=device_config.get(const.CONF_DELAY_DEACTIVATE_MIN) or 0)
        )

        if template := device_config.get(const.CONF_CHECK_USABLE_TEMPLATE):
            self.check_usable_template = Template(template, hass)

        self.locked_until = self.power_locked_until = now()

        self.activate_actions = cv.SCRIPT_SCHEMA(device_config.get(const.CONF_ACTIVATE_ACTIONS))
        self.deactivate_actions = cv.SCRIPT_SCHEMA(device_config.get(const.CONF_DEACTIVATE_ACTIONS))

        self.battery_min_soc = cv.positive_int(device_config.get(const.CONF_BATTERY_MIN_SOC) or 0)
        self.standby_power = cv.positive_float(device_config.get(const.CONF_STANDBY_POWER) or 0)

        self.min_daily_runtime = cv.positive_int(device_config.get(const.CONF_MIN_DAILY_RUNTIME) or 0)
        self.max_daily_runtime = cv.positive_int(device_config.get(const.CONF_MAX_DAILY_RUNTIME) or 24 * 60)
        if offpeak_time := device_config.get(const.CONF_OFFPEAK_TIME):
            self.offpeak_time = cv.time(offpeak_time)

        self.priority = cv.positive_int(device_config.get(const.CONF_PRIORITY) or const.DEFAULT_PRIORITY)

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

    @property
    def is_phase_switching_wallbox(self) -> bool:
        """Return whether this device is configured as a phase-switching wallbox."""
        return self.device_type == const.CONF_DEVICE_PHASE_SWITCHING_WALLBOX

    @property
    def min_supported_phase(self) -> int:
        """Return the minimum supported phase count."""
        return min(self.supported_phase_counts())

    @property
    def max_supported_phase(self) -> int:
        """Return the maximum supported phase count."""
        return max(self.supported_phase_counts())

    def supported_phase_counts(self) -> list[int]:
        """Return supported phase counts from configured phase entity."""
        if not self.is_phase_switching_wallbox:
            return [1]

        default = [1, 3]
        if self.current_phases_entity_id is None:
            return default

        state = self.hass.states.get(self.current_phases_entity_id)
        if state is None:
            return default

        options = state.attributes.get("options")
        if not isinstance(options, list):
            return default

        parsed_options: set[int] = set()
        for option in options:
            if parsed := self._parse_phase_count(option):
                parsed_options.add(parsed)
        return sorted(parsed_options) if parsed_options else default

    def _parse_phase_count(self, value) -> int | None:
        """Parse a phase count value into an integer in the range 1..3."""
        try:
            if isinstance(value, str):
                digits = "".join(char for char in value if char.isdigit())
                phase = int(digits or value.strip())
            else:
                phase = int(float(value))
        except TypeError, ValueError:
            return None

        if phase in {1, 2, 3}:
            return phase
        return None

    def get_current_phase_count(self) -> int:
        """Return the current active phase count."""
        if not self.is_phase_switching_wallbox or self.current_phases_entity_id is None:
            return 1

        phase_state = self.hass.states.get(self.current_phases_entity_id)
        if phase_state is None:
            return self.min_supported_phase

        parsed_phase = self._parse_phase_count(phase_state.state)
        if parsed_phase is None:
            return self.min_supported_phase
        return parsed_phase

    def get_voltage(self) -> float:
        """Return voltage configured for the wallbox."""
        if not self.is_phase_switching_wallbox:
            return const.DEFAULT_VOLTAGE
        return self.voltage

    def min_power_for_phase(self, phase_count: int) -> float:
        """Return minimum wallbox power for a given phase count."""
        return self.min_current * self.get_voltage() * phase_count

    def max_power_for_phase(self, phase_count: int) -> float:
        """Return maximum wallbox power for a given phase count."""
        return self.max_current * self.get_voltage() * phase_count

    def phase_for_requested_power(self, requested_power: float) -> int:
        """Return the best phase count for a requested power."""
        if not self.is_phase_switching_wallbox:
            return 1

        current_phase = self.get_current_phase_count()
        supported_phases = self.supported_phase_counts()

        if requested_power <= 0 or len(supported_phases) == 1:
            return current_phase

        for phase_count in supported_phases:
            min_power = self.min_power_for_phase(phase_count)
            max_power = self.max_power_for_phase(phase_count)
            if min_power <= requested_power <= max_power:
                return phase_count

        current_min = self.min_power_for_phase(current_phase)
        current_max = self.max_power_for_phase(current_phase)
        if current_min <= requested_power <= current_max:
            return current_phase

        if requested_power < current_min:
            lower_phases = [phase for phase in supported_phases if phase < current_phase]
            return max(lower_phases) if lower_phases else current_phase

        higher_phases = [phase for phase in supported_phases if phase > current_phase]
        return min(higher_phases) if higher_phases else current_phase

    def clamp_power_to_phase(self, requested_power: float, phase_count: int) -> float:
        """Clamp requested power to limits of a specific phase."""
        if not self.is_phase_switching_wallbox:
            return requested_power

        if requested_power <= 0:
            return 0

        min_power = self.min_power_for_phase(phase_count)
        max_power = self.max_power_for_phase(phase_count)
        return max(min_power, min(requested_power, max_power))

    def set_requested_phases(self, phase_count: int | None) -> None:
        """Set target phase count for next action."""
        self._requested_phases = phase_count

    async def apply_phase_switch(self, phase_count: int) -> None:
        """Write phase count target to the configured phase entity."""
        if not self.is_phase_switching_wallbox or self.current_phases_entity_id is None:
            return

        domain = self.current_phases_entity_id.split(".", maxsplit=1)[0]
        service_data = {"entity_id": self.current_phases_entity_id}

        if domain in {NUMBER_DOMAIN, INPUT_NUMBER_DOMAIN}:
            service_data["value"] = phase_count
            await self.hass.services.async_call(domain, "set_value", service_data, blocking=False)
            return

        if domain == SELECT_DOMAIN:
            phase_state = self.hass.states.get(self.current_phases_entity_id)
            option = str(phase_count)
            if phase_state:
                options = phase_state.attributes.get("options") or []
                matching_option = next(
                    (candidate for candidate in options if self._parse_phase_count(candidate) == phase_count),
                    None,
                )
                if matching_option is not None:
                    option = matching_option
            service_data["option"] = option
            await self.hass.services.async_call(domain, "select_option", service_data, blocking=False)

    async def _apply_action(self, action_type: str, requested_power: float):  # noqa: PLR0912
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

        requested_phases = self._requested_phases or self.get_current_phase_count()
        requested_power_for_service = requested_power
        if self.is_phase_switching_wallbox and requested_power > 0:
            requested_power_for_service = self.clamp_power_to_phase(requested_power, requested_phases)

        self.requested_power = requested_power_for_service

        action_context = Context()
        script_variables = {
            "requested_power": requested_power_for_service / self.power_divide_factor,
            "requested_phases": requested_phases,
            "current_power": self.current_power,
            "power_divide_factor": self.power_divide_factor,
        }

        target_entity = self.entity_id

        if action_type == ACTION_ACTIVATE:
            actions = self.activate_actions
            default_action = enable_entity

        elif action_type == ACTION_DEACTIVATE:
            actions = self.deactivate_actions
            default_action = disable_entity

        elif action_type == ACTION_CHANGE_POWER:
            if not self.can_change_power:
                msg = f"Device {self.name} cannot change its power!"
                raise RuntimeError(msg)

            actions = self.activate_actions
            default_action = set_entity_value

            if self.power_entity_id:
                requested_power = requested_power_for_service / self.power_divide_factor
                target_entity = self.power_entity_id
                if self.is_phase_switching_wallbox and requested_power_for_service > 0:
                    voltage = self.get_voltage()
                    requested_power = requested_power_for_service / (voltage * requested_phases)
                    requested_power = max(self.min_current, min(requested_power, self.max_current))
                    script_variables["requested_power"] = requested_power
            elif not actions:
                msg = f"Device {self.name} cannot change power because no actions and no power_entity_id are defined."
                raise RuntimeError(msg)

        if actions:
            logger.debug("Executing custom %s actions for %s", action_type, self.name)
            script = Script(
                self.hass,
                sequence=actions,
                name=f"{action_type}: {self.name}",
                domain=const.DOMAIN,
            )
            await script.async_run(
                run_variables=script_variables,
                context=action_context,
            )
        else:
            if self.is_phase_switching_wallbox and requested_phases != self.get_current_phase_count():
                await self.apply_phase_switch(requested_phases)
            if self.can_change_power and action_type == ACTION_ACTIVATE:
                await set_entity_value(self.hass, target_entity, requested_power)
            await default_action(self.hass, target_entity, requested_power)

        self._requested_phases = None

        if action_type in {ACTION_ACTIVATE, ACTION_DEACTIVATE}:
            self.reset_next_date_available(action_type)
        else:
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

        if self.can_change_power:
            self.reset_next_date_available_power()

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
        power_entity_value = power_entity_state.state

        if self.is_phase_switching_wallbox:
            phase_count = self.get_current_phase_count()
            self.current_power = float(power_entity_value) * self.get_voltage() * phase_count
        else:
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

    def disable_due_to_standby(self):
        """Disable re-activation for the current day after standby deactivation."""
        self.disabled_due_to_standby = True
        logger.info("%s disabled due to standby detection.", self.name)

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
                "%s not usable: daily_runtime %s >= %s max_daily_runtime",
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

        logger.debug(
            "Checking if %s should be forced offpeak. daily_runtime=%s, min_daily_runtime=%s, max_daily_runtime=%s",
            self.name,
            self.daily_runtime,
            self.min_daily_runtime,
            self.max_daily_runtime,
        )

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
    def power_nominal(self) -> float:
        """The nominal (minimum) power of the managed device."""
        if self.is_phase_switching_wallbox:
            return self.min_power_for_phase(self.min_supported_phase)
        return self._power_nominal

    @power_nominal.setter
    def power_nominal(self, value: float):
        self._power_nominal = value

    @property
    def power_max(self) -> float:
        """The maximum power of the managed device."""
        if self.is_phase_switching_wallbox:
            return self.max_power_for_phase(self.max_supported_phase)
        return get_template_or_value(self._power_max)

    @power_max.setter
    def power_max(self, value: float | Template):
        self._power_max = convert_to_template_or_value(self.hass, value) or 0

    @property
    def power_step(self) -> float:
        """The power step of the managed device."""
        if self.is_phase_switching_wallbox:
            return max(1.0, self.get_voltage() * self.get_current_phase_count())
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
