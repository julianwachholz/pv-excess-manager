"""Alls constants for the PV Excess Manager integration."""

import voluptuous as vol
from homeassistant.components.climate.const import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.humidifier.const import DOMAIN as HUMIDIFIER_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.components.light.const import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch.const import DOMAIN as SWITCH_DOMAIN
from homeassistant.helpers import selector

from . import const

new_device_schema = vol.Schema(
    {
        vol.Required(const.CONF_NAME): selector.TextSelector(),
        vol.Required(
            const.CONF_DEVICE_TYPE,
            default=const.CONF_DEVICE_BASIC,
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=const.CONF_DEVICE_TYPES,
                translation_key="device_type",
                mode=selector.SelectSelectorMode.LIST,
            ),
        ),
    }
)

main_schema = vol.Schema(
    {
        vol.Required(
            const.CONF_REFRESH_PERIOD_SEC,
            default=const.DEFAULT_REFRESH_PERIOD_SEC,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=3600,
                step=5,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            ),
        ),
        vol.Required(const.CONF_GRID_CONSUMPTION_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(const.CONF_POWER_PRODUCTION_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(
            const.CONF_SUBSCRIBE_TO_EVENTS,
            default=False,
        ): selector.BooleanSelector(),
        vol.Optional(const.CONF_BATTERY_SOC_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.BATTERY,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.BATTERY,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(const.CONF_BATTERY_CONSUMPTION_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(
            const.CONF_RESET_TIME,
            default=const.DEFAULT_RESET_TIME,
        ): selector.TimeSelector(),
    }
)

basic_device_schema = vol.Schema(
    {
        vol.Required(const.CONF_NAME): str,
        vol.Optional(const.CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[
                    INPUT_BOOLEAN_DOMAIN,
                    SWITCH_DOMAIN,
                    HUMIDIFIER_DOMAIN,
                    CLIMATE_DOMAIN,
                    FAN_DOMAIN,
                    LIGHT_DOMAIN,
                    SELECT_DOMAIN,
                ]
            )
        ),
        vol.Required(const.CONF_NOMINAL_POWER): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(const.CONF_POWER_SENSOR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(
            const.CONF_CHECK_USABLE_TEMPLATE,
        ): selector.TemplateSelector(),
        vol.Optional(const.CONF_DELAY_ACTIVATE_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(
            const.CONF_ONTIME_DURATION_MIN,
            default=const.DEFAULT_ONTIME_DURATION_MIN,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_DELAY_DEACTIVATE_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_OFFTIME_DURATION_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Required(const.CONF_ACTIVATE_ACTIONS): selector.ActionSelector(),
        vol.Required(const.CONF_DEACTIVATE_ACTIONS): selector.ActionSelector(),
        vol.Optional(const.CONF_BATTERY_MIN_SOC): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            ),
        ),
        vol.Optional(const.CONF_STANDBY_POWER): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(const.CONF_MIN_DAILY_RUNTIME): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_MAX_DAILY_RUNTIME): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_OFFPEAK_TIME): selector.TimeSelector(),
    }
)

variable_device_schema = vol.Schema(
    {
        vol.Required(const.CONF_NAME): str,
        vol.Optional(const.CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[
                    INPUT_BOOLEAN_DOMAIN,
                    SWITCH_DOMAIN,
                    HUMIDIFIER_DOMAIN,
                    CLIMATE_DOMAIN,
                    FAN_DOMAIN,
                    LIGHT_DOMAIN,
                    SELECT_DOMAIN,
                ]
            )
        ),
        vol.Required(const.CONF_NOMINAL_POWER): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(const.CONF_POWER_SENSOR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        domain=SENSOR_DOMAIN,
                        device_class=SensorDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=NUMBER_DOMAIN,
                        device_class=NumberDeviceClass.POWER,
                    ),
                    selector.EntityFilterSelectorConfig(
                        domain=INPUT_NUMBER_DOMAIN,
                    ),
                ],
            ),
        ),
        vol.Optional(const.CONF_POWER_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=[
                    INPUT_NUMBER_DOMAIN,
                    NUMBER_DOMAIN,
                    FAN_DOMAIN,
                    LIGHT_DOMAIN,
                ]
            )
        ),
        vol.Required(const.CONF_POWER_MAX): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(
            const.CONF_POWER_STEP,
            default=const.DEFAULT_POWER_STEP,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(const.CONF_DURATION_POWER_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(
            const.CONF_CHECK_USABLE_TEMPLATE,
        ): selector.TemplateSelector(),
        vol.Optional(const.CONF_DELAY_ACTIVATE_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(
            const.CONF_ONTIME_DURATION_MIN,
            default=const.DEFAULT_ONTIME_DURATION_MIN,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_DELAY_DEACTIVATE_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_OFFTIME_DURATION_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Required(const.CONF_ACTIVATE_ACTIONS): selector.ActionSelector(),
        vol.Required(const.CONF_DEACTIVATE_ACTIONS): selector.ActionSelector(),
        vol.Optional(
            const.CONF_POWER_DIVIDE_FACTOR,
            default=1.0,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1.0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(const.CONF_BATTERY_MIN_SOC): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            ),
        ),
        vol.Optional(const.CONF_STANDBY_POWER): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="W",
            ),
        ),
        vol.Optional(const.CONF_MIN_DAILY_RUNTIME): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_MAX_DAILY_RUNTIME): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        vol.Optional(const.CONF_OFFPEAK_TIME): selector.TimeSelector(),
    }
)
