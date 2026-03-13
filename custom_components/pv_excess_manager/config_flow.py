"""Configuration for the integration setup GUI."""

import logging
import uuid

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import const
from .config_schema import (
    basic_device_schema,
    main_schema,
    new_device_schema,
    variable_device_schema,
)
from .coordinator import PVExcessManagerCoordinator

logger = logging.getLogger(__name__)


# extends FlowHandler ?
class PVExcessManagerBaseConfigFlow:
    """Base class that defines both config flow and options flow."""

    def __init__(self, initial_data: dict | None = None) -> None:
        super().__init__()
        logger.debug("BaseConfigFlow initial_data: %s", initial_data)
        self._initial_data: dict = initial_data or {}

        # Coordinator should be initialized
        self._coordinator = PVExcessManagerCoordinator.get_coordinator()
        if not self._coordinator:
            logger.warning("Coordinator is not initialized yet. First run ?")

        self._user_inputs: dict = {}
        self._placeholders: dict = {}

    async def show_step(
        self,
        step_id: str,
        data_schema: vol.Schema,
        user_input: dict,
        next_step_function,
    ):
        """Show a config flow step."""
        logger.debug("Into ConfigFlow.async_step_%s user_input=%s", step_id, user_input)

        defaults = self._initial_data.copy()
        errors = {}

        if user_input is not None:
            self.merge_user_input(data_schema, user_input)
            # Add default values for main config flags
            logger.debug("_initial_data: %s", self._initial_data)
            return await next_step_function()

        data_schema = self.add_suggested_values_to_schema(
            data_schema=data_schema,
            suggested_values=defaults,
        )

        return self.async_show_form(
            step_id=step_id,
            data_schema=data_schema,
            errors=errors,
            description_placeholders=self._placeholders,
        )

    def merge_user_input(self, data_schema: vol.Schema, user_input: dict):
        """For each schema entry not in user_input, set or remove values in infos."""
        self._initial_data.update(user_input)
        for key, _ in data_schema.schema.items():
            if key not in user_input and isinstance(key, vol.Marker):
                logger.debug("add_empty_values_to_user_input: %s is not in user_input", key)
                if key in self._initial_data:
                    self._initial_data.pop(key)

        logger.debug("merge_user_input: _infos: %s", self._initial_data)

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the flow steps."""
        logger.debug("Into ConfigFlow.async_step_user user_input=%s", user_input)

        if not self._coordinator or not self._coordinator.is_main_config_done:
            return await self.async_step_device_main(user_input)

        return await self.show_step(
            "user",
            new_device_schema,
            user_input,
            self.async_step_finalize,
        )

    async def async_step_device_main(self, user_input: dict | None = None) -> FlowResult:
        """Handle the flow steps for main device."""
        logger.debug(
            "Into ConfigFlow.async_step_device_main user_input=%s",
            user_input,
        )

        if user_input is not None:
            user_input[const.CONF_NAME] = "Configuration"  # TODO: Translate?
            user_input[const.CONF_DEVICE_TYPE] = const.CONF_DEVICE_MAIN

        return await self.show_step(
            "device_main",
            main_schema,
            user_input,
            self.async_step_finalize,
        )

    async def async_step_device_basic(self, user_input: dict | None = None) -> FlowResult:
        """Handle the flow steps for basic device."""
        logger.debug("Into ConfigFlow.async_step_device_basic user_input=%s", user_input)

        return await self.show_step(
            "device_basic",
            basic_device_schema,
            user_input,
            self.async_step_finalize,
        )

    async def async_step_device_variable(self, user_input: dict | None = None) -> FlowResult:
        """Handle the flow steps for variable device."""
        logger.debug("Into ConfigFlow.async_step_device_variable user_input=%s", user_input)

        return await self.show_step(
            "device_variable",
            variable_device_schema,
            user_input,
            self.async_step_finalize,
        )

    async def async_step_finalize(self, user_input: dict | None = None) -> FlowResult | None:
        """Handle the flow steps for finalization."""
        msg = "async_step_finalize should be implemented in subclass"
        raise NotImplementedError(msg)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get options flow for this handler."""
        return PVExcessManagerOptionsFlow(config_entry)


class PVExcessManagerConfigFlow(PVExcessManagerBaseConfigFlow, ConfigFlow, domain=const.DOMAIN):
    """The real config flow for PV Excess Manager."""

    async def async_step_finalize(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Finalise of the ConfigEntry creation."""
        logger.debug("ConfigFlow.async_finalize")
        self._initial_data[const.CONF_UNIQUE_ID] = str(uuid.uuid4())
        return self.async_create_entry(
            title=self._initial_data[const.CONF_NAME],
            data=self._initial_data,
        )

    def is_matching(self, entry: ConfigEntry) -> bool:
        """Check if the entry matches the current flow."""
        return entry.data.get("domain") == const.DOMAIN


class PVExcessManagerOptionsFlow(PVExcessManagerBaseConfigFlow, OptionsFlow):
    """The class which enable to modified the configuration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """
        Initialise of the option flow.

        We have the existing ConfigEntry as input.

        """
        super().__init__({**config_entry.data, **config_entry.options})
        logger.debug(
            "PVExcessManagerOptionsFlow entry_id: %s",
            config_entry.entry_id,
        )

    async def async_step_init(self, user_input=None):
        """Manage options."""
        logger.debug(
            "Into OptionsFlowHandler.async_step_init user_input =%s",
            user_input,
        )

        if self._initial_data.get(const.CONF_DEVICE_TYPE) == const.CONF_DEVICE_MAIN:
            return await self.async_step_device_main(user_input)
        if self._initial_data.get(const.CONF_DEVICE_TYPE) == const.CONF_DEVICE_BASIC:
            return await self.async_step_device_basic(user_input)
        if self._initial_data.get(const.CONF_DEVICE_TYPE) == const.CONF_DEVICE_VARIABLE:
            return await self.async_step_device_variable(user_input)
        return None

    async def async_step_finalize(self, user_input: dict | None = None) -> ConfigFlowResult:
        logger.info(
            "Recreating entry %s due to configuration change. New config is: %s",
            self.config_entry.entry_id,
            self._initial_data,
        )
        name = self._initial_data.get(const.CONF_NAME)
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=self._initial_data, title=name
        )
        return self.async_create_entry(title=None, data=None)
