"""Algorithm for PV excess management."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .managed_device import ManagedDevice

logger = logging.getLogger(__name__)


class PVExcessManagerAlgorithm:
    """Algorithm that devices which actions will be made for managed devices."""

    @staticmethod
    def _nominal_power(device: ManagedDevice) -> float:
        if device.can_change_power:
            return float(max(device.power_nominal, 0))
        return float(max(device.power_max, 0))

    @staticmethod
    def _variable_requested_power(available_power: float, device: ManagedDevice) -> float | None:
        power_min = float(max(device.power_nominal, 0))
        power_max = float(max(device.power_max, power_min))
        requested = float(max(power_min, min(power_max, available_power)))
        return requested if requested >= power_min else None

    @classmethod
    def run_calculation(
        cls,
        devices: list[ManagedDevice],
        grid_consumption: float,
        power_production: float | None = None,
        battery_consumption: float | None = None,
        battery_soc: float | None = None,
    ) -> tuple[list[dict], float]:
        """Evaluate devices with a strict deterministic priority cascade."""
        if len(devices) <= 0 or grid_consumption is None:
            logger.info("Missing inputs for calculation. Calculation is abandoned.")
            return [], -1

        current_import = max(0.0, grid_consumption)
        current_export = max(0.0, -grid_consumption)

        virtual_surplus = current_export - current_import

        best_solution: list[dict] = []

        by_name: dict[str, dict] = {}

        for device in devices:
            if not device.is_enabled:
                continue
            device.battery_soc = battery_soc or 0
            force_state = (
                False
                if device.is_active and ((not device.is_usable and not device.is_waiting) or device.current_power <= 0)
                else device.is_active
            )
            equipment = {
                "power_nominal": device.power_nominal,
                "power_max": device.power_max,
                "power_step": device.power_step,
                "current_power": device.current_power,
                "requested_power": device.current_power if force_state else 0,
                "name": device.name,
                "state": force_state,
                "is_usable": device.is_usable,
                "is_waiting": device.is_waiting,
                "can_change_power": device.can_change_power,
                "priority": device.priority,
            }
            best_solution.append(equipment)
            by_name[device.name] = equipment
            if force_state:
                virtual_surplus += device.current_power

        sorted_devices = sorted(
            [device for device in devices if device.is_enabled],
            key=lambda item: (item.priority, item.name),
        )

        for device in sorted_devices:
            equipment = by_name[device.name]
            nominal_power = cls._nominal_power(device)
            is_active = equipment["state"]

            if not is_active:
                if not equipment["is_usable"]:
                    continue
                if device.can_change_power:
                    requested_power = cls._variable_requested_power(virtual_surplus, device)
                    if requested_power is None:
                        continue
                    equipment["state"] = True
                    equipment["requested_power"] = requested_power
                    break

                if virtual_surplus >= nominal_power:
                    equipment["state"] = True
                    equipment["requested_power"] = nominal_power
                    break
                continue
            if not device.should_be_forced_offpeak and not equipment["is_waiting"] and virtual_surplus < nominal_power:
                equipment["state"] = False
                equipment["requested_power"] = 0
                break
            if device.can_change_power and equipment["is_usable"]:
                requested_power = cls._variable_requested_power(virtual_surplus, device)
                if requested_power is not None and requested_power != device.current_power:
                    equipment["requested_power"] = requested_power
                    break
            virtual_surplus -= device.current_power

        total_power = sum(equipment["requested_power"] for equipment in best_solution if equipment["state"])
        return best_solution, total_power
