"""Constants."""

from datetime import time

from homeassistant.const import Platform

NAME = "PV Excess Manager"
AUTHOR = "Julian Wachholz"
DOMAIN = PV_EXCESS_MANAGER_DOMAIN = "pv_excess_manager"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
]

CONFIG_VERSION = 1
CONFIG_MINOR_VERSION = 1

DEFAULT_REFRESH_PERIOD_SEC = 30
DEFAULT_RESET_TIME = time(5)

# Main configuration
CONF_REFRESH_PERIOD_SEC = "refresh_period_sec"

CONF_GRID_CONSUMPTION_ENTITY_ID = "grid_consumption_entity_id"
CONF_POWER_PRODUCTION_ENTITY_ID = "power_production_entity_id"
CONF_SUBSCRIBE_TO_EVENTS = "subscribe_to_events"

CONF_BATTERY_SOC_ENTITY_ID = "battery_soc_entity_id"
CONF_BATTERY_CONSUMPTION_ENTITY_ID = "battery_consumption_entity_id"

CONF_RESET_TIME = "reset_time"


# Device types for config flow
CONF_DEVICE_TYPE = "device_type"
CONF_DEVICE_MAIN = "main"
CONF_DEVICE_BASIC = "basic"
CONF_DEVICE_VARIABLE = "variable"
CONF_DEVICE_PHASE_SWITCHING_WALLBOX = "phase_switching_wallbox"
CONF_ALL_CONFIG_TYPES = [
    CONF_DEVICE_MAIN,
    CONF_DEVICE_BASIC,
    CONF_DEVICE_VARIABLE,
    CONF_DEVICE_PHASE_SWITCHING_WALLBOX,
]
CONF_DEVICE_TYPES = [
    CONF_DEVICE_BASIC,
    CONF_DEVICE_VARIABLE,
    CONF_DEVICE_PHASE_SWITCHING_WALLBOX,
]


# Device parameters
CONF_NAME = "name"
CONF_UNIQUE_ID = "unique_id"

# Entity that represents the ON/OFF state of the managed device
CONF_ENTITY_ID = "entity_id"

# Priority of a device in the cascade algorithm (lower value = higher priority)
CONF_PRIORITY = "priority"
DEFAULT_PRIORITY = 100

# Default power when a device is first turned on
CONF_NOMINAL_POWER = "nominal_power"
# Actual power consumption sensor
CONF_POWER_SENSOR_ENTITY_ID = "power_sensor_entity_id"

# Logic to check if the device is usable
CONF_CHECK_USABLE_TEMPLATE = "check_usable_template"

# Minimum duration a device must remain in its on or off state once switched
CONF_ONTIME_DURATION_MIN = "ontime_duration_min"
CONF_OFFTIME_DURATION_MIN = "offtime_duration_min"
DEFAULT_ONTIME_DURATION_MIN = 30

# How is a device switched on or off?
CONF_ACTIVATE_ACTIONS = "activate_actions"
CONF_DEACTIVATE_ACTIONS = "deactivate_actions"

# Variable power settings
CONF_POWER_ENTITY_ID = "power_entity_id"
CONF_POWER_MAX = "power_max"
CONF_POWER_STEP = "power_step"
DEFAULT_POWER_STEP = 230

CONF_DURATION_POWER_MIN = "duration_power_min"
CONF_POWER_DIVIDE_FACTOR = "power_divide_factor"

# Phase-switching wallbox settings
CONF_MIN_CURRENT = "min_current"
CONF_MAX_CURRENT = "max_current"
DEFAULT_MIN_CURRENT = 6
DEFAULT_MAX_CURRENT = 16

CONF_CURRENT_PHASES_ENTITY_ID = "current_phases_entity_id"
CONF_VOLTAGE = "voltage"
DEFAULT_VOLTAGE = 230

# Battery must have at least this SOC to allow use of this device
CONF_BATTERY_MIN_SOC = "battery_min_soc"

# If the device is ON and its measured power drops below this threshold, deactivate it immediately
CONF_STANDBY_POWER = "standby_power"

# Delay thresholds to enable or disable a device
CONF_DELAY_ACTIVATE_MIN = "delay_activate_min"
CONF_DELAY_DEACTIVATE_MIN = "delay_deactivate_min"

# Duration a device may run in a given day (resets at CONF_RESET_TIME)
CONF_MIN_DAILY_RUNTIME = "min_daily_runtime"
CONF_MAX_DAILY_RUNTIME = "max_daily_runtime"

# Time to enable a device if its minimum ON-time for today has not yet been achieved
CONF_OFFPEAK_TIME = "offpeak_time"


# Service that resets the runtime for all devices
SERVICE_RESET_RUNTIME = "reset_device_runtime"

# Event fired when a device's managed state changes
EVENT_PV_EXCESS_MANAGER_MANAGED_STATE_CHANGE = "pv_excess_manager_managed_state_change"
