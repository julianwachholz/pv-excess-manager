"""Error definitions."""

from homeassistant.exceptions import HomeAssistantError


class ConfigurationError(Exception):
    """An error in the configuration."""

    def __init__(self, message: str):
        super().__init__(message)


class UnknownEntity(HomeAssistantError):
    """Error to indicate there is an unknown entity_id given."""


class InvalidTime(HomeAssistantError):
    """Error to indicate the give time is invalid."""
