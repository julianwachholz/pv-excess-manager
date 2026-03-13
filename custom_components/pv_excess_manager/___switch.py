"""A binary sensor entity that holds the state of each managed_device."""

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.components.switch.const import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import now

from . import const
from .coordinator import PVExcessManagerCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback,
    )

    from .managed_device import ManagedDevice

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the entries of type switch for each ManagedDevice."""
    coordinator: PVExcessManagerCoordinator = PVExcessManagerCoordinator.get_coordinator()

    entities = []
    if entry.data[const.CONF_DEVICE_TYPE] == const.CONF_DEVICE_MAIN:
        return

    unique_id = entry.data.get(const.CONF_UNIQUE_ID)
    device = coordinator.get_device_by_unique_id(unique_id)
    if device is None:
        logger.error(
            "Calling switch.async_setup_entry in error cause device with unique_id %s not found",
            unique_id,
        )
        return

    entity = ManagedDeviceSwitch(coordinator, hass, device)
    entities.append(entity)
    entity = ManagedDeviceEnable(hass, device)
    entities.append(entity)

    async_add_entities(entities)


class ManagedDeviceSwitch(CoordinatorEntity, SwitchEntity):
    """The entity holding the algorithm calculation."""

    _entity_component_unrecorded_attributes = SwitchEntity._entity_component_unrecorded_attributes.union(
        frozenset(
            {
                "is_enabled",
                "is_active",
                "is_waiting",
                "is_usable",
                "can_change_power",
                "duration_sec",
                "duration_power_sec",
                "power_min",
                "power_max",
                "next_date_available",
                "next_date_available_power",
                "battery_soc_threshold",
                "battery_soc",
            }
        )
    )

    def __init__(self, coordinator, hass, device: ManagedDevice):
        logger.debug("Adding ManagedDeviceSwitch for %s", device.name)
        idx = device.unique_id
        super().__init__(coordinator, context=idx)
        self._hass: HomeAssistant = hass
        self._device = device
        self.idx = idx
        self._attr_has_entity_name = True
        self.entity_id = f"{SWITCH_DOMAIN}.pv_excess_manager_{idx}"
        self._attr_name = "Active"
        self._attr_unique_id = "pv_excess_manager_active_" + idx
        self._entity_id = device.entity_id
        self._attr_is_on = device.is_active

    async def async_added_to_hass(self) -> None:
        """The entity have been added to hass, listen to state change of the underlying entity"""
        await super().async_added_to_hass()

        # Arme l'écoute de la première entité
        listener_cancel = async_track_state_change_event(
            self.hass,
            [self._entity_id],
            self._on_state_change,
        )
        # desarme le timer lors de la destruction de l'entité
        self.async_on_remove(listener_cancel)

        # desarme le timer lors de la destruction de l'entité
        self.async_on_remove(
            self._hass.bus.async_listen(
                event_type=const.EVENT_PV_EXCESS_MANAGER_ENABLE_STATE_CHANGE,
                listener=self._on_enable_state_change,
            )
        )

        self.update_custom_attributes(self._device)

    @callback
    async def _on_enable_state_change(self, event: Event) -> None:
        """Triggered when the ManagedDevice enable state have change"""

        # is it for me ?
        if not event.data or (device_id := event.data.get("device_unique_id")) != self.idx:
            return

        # search for coordinator and device
        if not self.coordinator or not (device := self.coordinator.get_device_by_unique_id(device_id)):
            return

        logger.info("Changing enabled state for %s to %s", device_id, device.is_enabled)

        self.update_custom_attributes(device)
        self.async_write_ha_state()

    @callback
    async def _on_state_change(self, event: Event) -> None:
        """The entity have change its state"""
        logger.info("Appel de on_state_change à %s avec l'event %s", now(), event)

        if not event.data:
            return

        # search for coordinator and device
        if not self.coordinator or not (device := self.coordinator.get_device_by_unique_id(self.idx)):
            return

        new_state: State = event.data.get("new_state")

        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            logger.debug("Pas d'état disponible. Evenement ignoré")
            return

        # On recherche la date de l'event pour la stocker dans notre état
        new_state = self._device.is_active  # new_state.state == STATE_ON
        if new_state == self._attr_is_on:
            return

        self._attr_is_on = new_state
        # On sauvegarde le nouvel état
        self.update_custom_attributes(device)
        self.async_write_ha_state()

    def update_custom_attributes(self, device):
        """Add some custom attributes to the entity"""
        self._attr_extra_state_attributes: dict[str, str] = {
            "is_enabled": device.is_enabled,
            "is_active": device.is_active,
            "is_waiting": device.is_waiting,
            "is_usable": device.is_usable,
            "can_change_power": device.can_change_power,
            "current_power": device.current_power,
            "requested_power": device.requested_power,
            "duration_sec": device.duration_sec,
            "duration_power_sec": device.duration_power_sec,
            "power_min": device.power_min,
            "power_max": device.power_max,
            "next_date_available": device.next_date_available.isoformat(),
            "next_date_available_power": device.next_date_available_power.isoformat(),
            "battery_soc_threshold": device.battery_soc_threshold,
            "battery_soc": device.battery_soc,
            "device_name": device.name,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        logger.debug("Calling _handle_coordinator_update for %s", self._attr_name)

        if not self.coordinator or not self.coordinator.data:
            logger.debug("No coordinator found or no data...")
            return

        device: ManagedDevice = self.coordinator.data.get(self.idx)
        if not device:
            # it is possible to not have device in coordinator update (if device is not enabled)
            logger.debug("No device %s found ...", self.idx)
            return

        self._attr_is_on = device.is_active
        self.update_custom_attributes(device)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self.hass.async_create_task(self.async_turn_on(**kwargs))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        logger.info("Turn_on PV Excess Manager switch %s", self._attr_name)
        # search for coordinator and device
        if not self.coordinator or not (device := self.coordinator.get_device_by_unique_id(self.idx)):
            return

        if not self._attr_is_on:
            await device.activate()
            self._attr_is_on = True
            self.update_custom_attributes(device)
            self.async_write_ha_state()
            logger.debug("Turn_on PV Excess Manager switch %s ok", self._attr_name)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        # We cannot call async_turn_off from a sync context so we call the async_turn_off in a task
        self.hass.async_create_task(self.async_turn_off(**kwargs))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        logger.info("Turn_off PV Excess Manager switch %s", self._attr_name)
        # search for coordinator and device
        if not self.coordinator or not (device := self.coordinator.get_device_by_unique_id(self.idx)):
            return

        if self._attr_is_on:
            logger.debug("Will deactivate %s", self._attr_name)
            await device.deactivate()
            self._attr_is_on = False
            self.update_custom_attributes(device)
            self.async_write_ha_state()
            logger.debug("Turn_ff PV Excess Manager switch %s ok", self._attr_name)
        else:
            logger.debug("Not active %s", self._attr_name)

    @property
    def device_info(self) -> DeviceInfo | None:
        # Retournez des informations sur le périphérique associé à votre entité
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name="PV Excess Manager-" + self._device.name,
        )

    @property
    def get_attr_extra_state_attributes(self):
        """Get the extra state attributes for the entity"""
        return self._attr_extra_state_attributes


class ManagedDeviceEnable(SwitchEntity, RestoreEntity):
    """The that enables the ManagedDevice optimisation with"""

    _device: ManagedDevice

    def __init__(self, hass: HomeAssistant, device: ManagedDevice):
        name = device.unique_id
        self._hass: HomeAssistant = hass
        self._device = device
        self._attr_has_entity_name = True
        self.entity_id = f"{SWITCH_DOMAIN}.enable_pv_excess_manager_{name}"
        self._attr_name = "Enable"
        self._attr_unique_id = "pv_excess_manager_enable_" + name
        self._attr_is_on = True

    @property
    def device_info(self) -> DeviceInfo | None:
        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, self._device.name)},
            name="PV Excess Manager-" + self._device.name,
        )

    @property
    def icon(self) -> str | None:
        return "mdi:check"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        # Récupérer le dernier état sauvegardé de l'entité
        last_state = await self.async_get_last_state()

        # Si l'état précédent existe, vous pouvez l'utiliser
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON
        else:
            # Si l'état précédent n'existe pas, initialisez l'état comme vous le souhaitez
            self._attr_is_on = True

        # this breaks the start of integration
        self.update_device_enabled()

    @callback
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self.turn_on(**kwargs)

    @callback
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        # We cannot call async_turn_off from a sync context so we call the async_turn_off in a task
        self.turn_off(**kwargs)

    def update_device_enabled(self) -> None:
        """Update the device is enabled flag."""
        if not self._device:
            return

        self._device.set_enable(self._attr_is_on)

    def turn_off(self, **kwargs: Any):
        self._attr_is_on = False
        self.async_write_ha_state()
        self.update_device_enabled()

    def turn_on(self, **kwargs: Any):
        self._attr_is_on = True
        self.async_write_ha_state()
        self.update_device_enabled()
