"""Helper functions."""

import logging
import math
from typing import TYPE_CHECKING

from homeassistant.const import UnitOfPower
from homeassistant.helpers.template import Template, is_template_string
from homeassistant.util.unit_conversion import PowerConverter
from slugify import slugify

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


logger = logging.getLogger(__name__)


def name_to_unique_id(name: str) -> str:
    """Convert a name to a unique id. Replace ' ' by '_'."""
    return slugify(name).replace("-", "_")


def get_power_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """
    Get a safe power state value for an entity.

    Return None if entity is not available.

    """
    if not entity_id:
        return None

    state = hass.states.get(entity_id)

    if not state or state.state in {"unknown", "unavailable"}:
        return None

    value = float(state.state)

    if "device_class" in state.attributes and state.attributes["device_class"] == "power":
        value = PowerConverter.convert(
            value,
            state.attributes["unit_of_measurement"],
            UnitOfPower.WATT,
        )
    return value if math.isfinite(value) else None


def get_template_or_value(value):
    """Get the template or the value."""
    if isinstance(value, Template):
        return value.async_render(context={})
    return value


def convert_to_template_or_value(hass: HomeAssistant, value):
    """Convert the value to a template or a value."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None

    if isinstance(value, (bool, type(None))):
        return value

    if isinstance(value, str) and is_template_string(value):
        return Template(value, hass)

    constants = {
        "None": None,
        "True": True,
        "False": False,
    }
    return constants.get(str(value).strip())
