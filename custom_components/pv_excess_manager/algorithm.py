"""Algorithm for PV excess management."""

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .managed_device import ManagedDevice

logger = logging.getLogger(__name__)


class PVExcessManagerAlgorithm:
    """Algorithm that devices which actions will be made for managed devices."""

    @staticmethod
    def _get_variable_power(available_power: float, device: ManagedDevice) -> float:
        """Calculate maximum power a device may use."""
        power_min = max(device.power_nominal, 0)
        power_max = max(device.power_max, power_min)
        power_step = device.power_step or 1

        if available_power < power_min:
            return 0.0

        requested = min(available_power, power_max)

        # Quantize to device step size (from power_min upward)
        if power_step > 0:
            steps = math.floor((requested - power_min) / power_step)
            requested = power_min + (steps * power_step)
        return float(requested)

    @classmethod
    def run_calculation(  # noqa: PLR0915, PLR0912
        cls,
        devices: list[ManagedDevice],
        grid_consumption: float,
        power_production: float | None = None,
        battery_consumption: float | None = None,
        battery_soc: float | None = None,
    ) -> tuple[tuple[str, float] | None, float, float]:
        """
        Evaluate devices with a strict deterministic priority cascade.

        Priority 1 is highest and will be evaluated first.
        Virtual surplus power is calculated as if all managed devices were off.
        Using real power usage on devices that have a power sensor defined.

        Returns a pair:
         - a single action in the form or (unique_id, requested_power) to be taken
           on this iteration, or None if nothing should be changed.
         - total requested power by all managed devices
         - remaining virtual excess power

        """
        if grid_consumption is None:
            logger.warning("Missing grid consumption input for calculation. Calculation is abandoned.")
            return (None, 0, 0)
        if len(devices) < 0:
            logger.info("Missing devices for calculation.")
            return (None, 0, 0)

        logger.debug(
            "Run algo: grid_consumption: %s, power_production: %s, battery_consumption: %s, battery_soc: %s",
            grid_consumption,
            power_production,
            battery_consumption,
            battery_soc,
        )

        # TODO: power_production, battery_consumption aren't used in the algo yet

        current_import = max(0.0, grid_consumption)
        current_export = max(0.0, -grid_consumption)

        virtual_excess = current_export - current_import

        # Build list of currently managed devices
        managed_devices = []

        for device in devices:
            if not device.is_managed:
                continue

            device.battery_soc = battery_soc or 0
            managed_devices.append(device)

            if device.is_active:
                virtual_excess += device.current_power

        logger.debug("Virtual excess after checking devices: %s", virtual_excess)
        available_virtual_excess = virtual_excess

        sorted_devices: list[ManagedDevice] = sorted(
            managed_devices,
            key=lambda item: (item.priority, item.name),
        )

        target_action = None
        total_requested = 0

        for device in sorted_devices:
            logger.debug(
                "Evaluating device %s. Current power: %s, requested power: %s",
                device.name,
                device.current_power,
                device.requested_power,
            )

            # #########################
            #  DEVICE IS CURRENTLY OFF
            # #########################

            # Check if we can turn on this device with the current virtual excess power
            if not device.is_active:
                device.reset_deactivate_delay()

                if not device.is_usable:
                    # Device is locked, unusable by template or max daily runtime reached
                    continue

                if device.should_be_forced_offpeak():
                    requested_power = device.power_nominal
                    if device.can_change_power:
                        # Try to get more if PV is high, otherwise guarantee at least nominal
                        requested_power = max(device.power_nominal, cls._get_variable_power(virtual_excess, device))

                    logger.debug(
                        "Device %s is forced offpeak. Activating immediately, bypassing PV checks.",
                        device.name,
                    )
                    target_action = (device.unique_id, requested_power)
                    total_requested += requested_power
                    device.reset_activate_delay()
                    break

                if device.can_change_power:
                    requested_power = cls._get_variable_power(virtual_excess, device)
                    if requested_power == 0:
                        device.reset_activate_delay()
                        continue

                    logger.debug(
                        "Device %s should be turned on with variable power. Requested: %s, virtual_excess: %s",
                        device.name,
                        requested_power,
                        virtual_excess,
                    )
                    if device.is_activate_delay_passed():
                        target_action = (device.unique_id, requested_power)
                        total_requested += requested_power
                        device.reset_activate_delay()
                        break

                    logger.debug(
                        "Device %s cannot be turned on due to activation delay. Requested: %s, virtual_excess: %s",
                        device.name,
                        requested_power,
                        virtual_excess,
                    )
                    # Reserve the requested power during activation delay
                    virtual_excess -= requested_power
                    continue

                # Check device's nominal power to check if we can turn it on
                if virtual_excess >= device.power_nominal:
                    # Virtual excess is available, check activation delay
                    logger.debug(
                        "Device %s should be turned on with nominal power. Requested: %s, virtual_excess: %s",
                        device.name,
                        device.power_nominal,
                        virtual_excess,
                    )
                    if device.is_activate_delay_passed():
                        target_action = (device.unique_id, device.power_nominal)
                        total_requested += device.power_nominal
                        device.reset_activate_delay()
                        break

                    logger.debug(
                        "Device %s cannot be turned on due to activation delay. Requested: %s, virtual_excess: %s",
                        device.name,
                        device.power_nominal,
                        virtual_excess,
                    )
                    # Reserve the nominal power during activation delay
                    virtual_excess -= device.power_nominal
                else:
                    device.reset_activate_delay()

                continue

            # ########################
            #  DEVICE IS CURRENTLY ON
            # ########################

            # Check if device is locked and must not be turned off
            if device.is_locked:
                logger.debug("Device %s is locked, ignoring.", device.name)
                virtual_excess -= device.current_power
                total_requested += device.requested_power
                continue

            # Check if device must be forced off immediately
            if not device.check_usable(check_battery=False):
                logger.debug("Device %s is no longer usable. Turning off immediately.", device.name)
                target_action = (device.unique_id, 0)
                break

            if device.should_be_forced_offpeak():
                logger.debug(
                    "Device %s is forced offpeak. Keeping it ON regardless of PV surplus.",
                    device.name,
                )
                device.reset_deactivate_delay()

                # If variable power, ensure it runs at least at nominal power during cheap tariffs
                if device.can_change_power:
                    target_power = max(device.power_nominal, cls._get_variable_power(virtual_excess, device))
                    is_power_in_range = abs(device.current_power - target_power) <= device.power_step / 2

                    if target_power > 0 and not is_power_in_range:
                        logger.debug("Adjusting forced offpeak device %s to %s W", device.name, target_power)
                        target_action = (device.unique_id, target_power)
                        total_requested += target_power
                        break

                virtual_excess -= device.current_power
                total_requested += device.current_power

                # Skip additional PV excess checks
                continue

            is_surplus_insufficient = False

            if device.can_change_power:
                potential_power = cls._get_variable_power(virtual_excess, device)
                is_surplus_insufficient = potential_power == 0
            else:
                is_surplus_insufficient = virtual_excess < device.current_power

            if is_surplus_insufficient:
                logger.debug(
                    "Device %s has insufficient surplus. Current: %s, virtual_excess: %s",
                    device.name,
                    device.current_power,
                    virtual_excess,
                )

                if device.is_deactivate_delay_passed():
                    target_action = (device.unique_id, 0)
                    device.reset_deactivate_delay()
                    break

                logger.debug("Device %s pending deactivation. Reserving %s W.", device.name, device.current_power)
                virtual_excess -= device.current_power
                continue

            # Surplus is sufficient to keep this device on, check if it can stay on or should have power changed
            device.reset_deactivate_delay()

            if device.can_change_power:
                requested_power = cls._get_variable_power(virtual_excess, device)
                is_power_in_range = abs(device.current_power - requested_power) <= device.power_step / 2
                logger.debug(
                    "Device %s can change power. Requested: %s, virtual_excess: %s, is_power_in_range: %s",
                    device.name,
                    requested_power,
                    virtual_excess,
                    is_power_in_range,
                )

                if requested_power > 0 and requested_power != device.current_power and not is_power_in_range:
                    logger.debug(
                        "Device %s changing power. Current: %s, requested: %s",
                        device.name,
                        device.current_power,
                        requested_power,
                    )
                    target_action = (device.unique_id, requested_power)
                    total_requested += requested_power
                    break

            total_requested += device.requested_power
            virtual_excess -= device.current_power

        return target_action, total_requested, available_virtual_excess
