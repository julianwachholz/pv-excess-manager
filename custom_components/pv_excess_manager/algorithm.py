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

    @staticmethod
    def _adjust_phase_switching_power(device: ManagedDevice, requested_power: float) -> float:
        """Adjust requested power and phase target for phase-switching wallboxes."""
        if not device.is_phase_switching_wallbox or requested_power <= 0:
            device.set_requested_phases(None)
            return requested_power

        current_phase = device.get_current_phase_count()
        target_phase = device.phase_for_requested_power(requested_power)

        if not device.is_active:
            device.set_requested_phases(target_phase)
            return device.clamp_power_to_phase(requested_power, target_phase)

        if target_phase > current_phase:
            device.reset_deactivate_delay()
            if not device.is_activate_delay_passed():
                device.set_requested_phases(current_phase)
                return device.clamp_power_to_phase(requested_power, current_phase)
            device.reset_activate_delay()
        elif target_phase < current_phase:
            device.reset_activate_delay()
            if not device.is_deactivate_delay_passed():
                device.set_requested_phases(current_phase)
                return device.clamp_power_to_phase(requested_power, current_phase)
            device.reset_deactivate_delay()
        else:
            device.reset_activate_delay()
            device.reset_deactivate_delay()

        device.set_requested_phases(target_phase)
        return device.clamp_power_to_phase(requested_power, target_phase)

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

        current_import = max(0.0, grid_consumption)
        current_export = max(0.0, -grid_consumption)
        virtual_excess = current_export - current_import

        if battery_consumption is not None:
            virtual_excess -= battery_consumption

        logger.debug(
            "Virtual excess before checking devices: %s",
            virtual_excess,
        )

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

                if device.disabled_due_to_standby:
                    logger.debug(
                        "Device %s was disabled due to standby and will stay off until daily reset.",
                        device.name,
                    )
                    continue

                if device.should_be_forced_offpeak():
                    requested_power = device.power_nominal
                    if device.can_change_power:
                        # Try to get more if PV is high, otherwise guarantee at least nominal.
                        # Account for power already consumed by the device (e.g. via power sensor while inactive).
                        requested_power = max(
                            device.power_nominal,
                            cls._get_variable_power(virtual_excess + device.current_power, device),
                        )
                        requested_power = cls._adjust_phase_switching_power(device, requested_power)

                    logger.debug(
                        "Device %s is forced offpeak. Activating immediately, bypassing PV checks.",
                        device.name,
                    )
                    target_action = (device.unique_id, requested_power)
                    total_requested += requested_power
                    device.reset_activate_delay()
                    break

                if device.can_change_power:
                    # Account for power already consumed by the device (e.g. via power sensor while inactive)
                    requested_power = cls._get_variable_power(virtual_excess + device.current_power, device)
                    requested_power = cls._adjust_phase_switching_power(device, requested_power)
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
                    # Reserve only the additional power needed during activation delay
                    virtual_excess -= max(0.0, requested_power - device.current_power)
                    continue

                # Check device's nominal power to check if we can turn it on.
                # Deduct the device's current consumption (e.g. via power sensor while inactive)
                # so only the additional power required is compared against the available excess.
                additional_power_needed = max(0.0, device.power_nominal - device.current_power)
                if virtual_excess >= additional_power_needed:
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
                    # Reserve only the additional power needed during activation delay
                    virtual_excess -= additional_power_needed
                else:
                    device.reset_activate_delay()

                continue

            # ########################
            #  DEVICE IS CURRENTLY ON
            # ########################

            # Check if device is locked and must not be turned off.
            # For variable devices that are not power-locked, allow power adjustments
            # while still preventing deactivation. The on-duration lock should only
            # prevent full deactivation/activation, not power level changes.
            if device.is_locked:
                if not (device.can_change_power and not device.is_power_locked):
                    logger.debug("Device %s is locked, ignoring.", device.name)
                    virtual_excess -= device.current_power
                    total_requested += device.requested_power
                    continue
                logger.debug("Device %s is locked but can change power. Evaluating power adjustment.", device.name)

            # If an active device is below nominal draw while PV production is also below
            # the device nominal power, disable it to avoid potential grid import if it ramps up.
            if (
                power_production is not None
                and not device.is_locked
                and device.current_power < device.power_nominal
                and power_production < device.power_nominal
            ):
                logger.debug(
                    "Device %s is ON below nominal (%s < %s) while production is low (%s < %s). Turning off.",
                    device.name,
                    device.current_power,
                    device.power_nominal,
                    power_production,
                    device.power_nominal,
                )
                target_action = (device.unique_id, 0)
                break

            # Check if device must be forced off immediately (skip for locked devices)
            if not device.is_locked and not device.check_usable(check_battery=False):
                logger.debug("Device %s is no longer usable. Turning off immediately.", device.name)
                target_action = (device.unique_id, 0)
                break

            # Check if device has dropped to standby — its internal logic decided no work is needed
            # Locked devices are not deactivated due to standby; the on-duration lock takes precedence.
            if not device.is_locked and device.standby_power and device.current_power < device.standby_power:
                logger.info(
                    "Device %s is in standby (current_power=%s < standby_power=%s). Deactivating.",
                    device.name,
                    device.current_power,
                    device.standby_power,
                )
                device.disable_due_to_standby()
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
                    target_power = cls._adjust_phase_switching_power(device, target_power)
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

            # For variable power devices, account for the gap between the previously requested
            # power and the actual current consumption. When a device hasn't reached its commanded
            # level yet (e.g., due to ramp-up or hardware limits), the actual consumption
            # understates the effective virtual excess, which could cause premature deactivation
            # instead of a reduction to the appropriate lower power step.
            effective_virtual_excess = virtual_excess + max(0.0, device.requested_power - device.current_power)

            if device.can_change_power:
                potential_power = cls._get_variable_power(effective_virtual_excess, device)
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

                # For variable power devices above minimum, step down to minimum power first
                # before starting the deactivation timer. This avoids jumping straight from a
                # high power level to off when only the minimum power is no longer supportable.
                if device.can_change_power and device.requested_power > device.power_nominal:
                    logger.debug(
                        "Device %s stepping down to minimum power %s W before deactivation.",
                        device.name,
                        device.power_nominal,
                    )
                    target_action = (device.unique_id, device.power_nominal)
                    total_requested += device.power_nominal
                    break

                # Locked devices must not be deactivated; the on-duration takes precedence.
                if not device.is_locked and device.is_deactivate_delay_passed():
                    target_action = (device.unique_id, 0)
                    device.reset_deactivate_delay()
                    break

                logger.debug(
                    "Device %s pending deactivation or locked. Reserving %s W.",
                    device.name,
                    device.current_power,
                )
                virtual_excess -= device.current_power
                continue

            # Surplus is sufficient to keep this device on, check if it can stay on or should have power changed
            device.reset_deactivate_delay()

            if device.can_change_power:
                requested_power = cls._get_variable_power(effective_virtual_excess, device)
                requested_power = cls._adjust_phase_switching_power(device, requested_power)
                # Compare against device.requested_power (the previously sent command) rather
                # than device.current_power (actual measurement). This ensures a reduction is
                # triggered when the device hasn't reached its commanded level yet, even if the
                # actual consumption happens to be close to the new target.
                is_power_in_range = abs(device.requested_power - requested_power) <= device.power_step / 2
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
