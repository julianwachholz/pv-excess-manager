# PV Excess Manager

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that intelligently manages your smart devices based on your realtime power consumption and production, **maximizing** your solar **self-consumption**.

## Requirements

- Home Assistant version `2026.3.1` or later
- HACS version `2.0.5` or later (for installation via HACS)

## Installation

### Via HACS (Recommended)

1. Open HACS in Home Assistant
2. Click **Integrations**
3. Click **Custom repositories**
4. Paste the repository : `julianwachholz/pv-excess-manager`
5. Select **Integration** as the category
6. Click **Create**
7. Find **PV Excess Manager** in the list and click **Install**
8. Restart Home Assistant

### Manual Installation

1. Clone or download this repository
2. Copy the `custom_components/pv_excess_manager` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Setup

After installation, add the integration to Home Assistant:

1. Go to **Settings** > **Devices & Services** > **Integrations**
2. Click **Create Integration** and search for "PV Excess Manager"
3. The integration will ask for your main configuration the first time you add it:
   - Grid consumption entity (required)
   - Power production entity (optional)
   - Battery settings (optional)
   - Refresh period and reset time
4. Once main configuration is complete, add devices:
   - Click **Add device** and select **Basic** or **Variable Power**
   - Configure each device's power settings, delays, and actions
   - Set device priority (lower number = higher priority)

## How It Works

The PV Excess Manager runs a deterministic algorithm every refresh cycle (default: 30 seconds) to decide which devices should be active and at what power level.

### Virtual Excess Power

The algorithm first calculates **virtual excess power**:

```
Virtual Excess = Current Grid Export + Power Used by All Managed Devices
```

- If your current grid consumption is _negative_, you're exporting power
- If your current grid consumption is _positive_, you're importing power
- Managed device power is added back because it's already accounted for in the grid meter

### Priority Cascade (Bucket Overflow)

Devices are sorted by their **priority value** (1 = highest). The algorithm processes them in order:

1. **Highest-priority device** gets allocated power from virtual excess first
   - If enough excess available: device may turn ON or power level increases
   - If insufficient excess: device waits or turns OFF
2. Remaining virtual excess "overflows" to the **next-priority device**
3. Process repeats until all devices are evaluated or excess is exhausted

### Deterministic, Single-Action Cycles

- **One action per cycle maximum**: activate one device, deactivate one, or adjust one device's power
- **No randomness**: same conditions always produce the same decision
- **Delays prevent thrashing**: minimum ON/OFF durations and activation/deactivation delays prevent rapid oscillations
- **Safety locks**: devices respect minimum battery charge (SOC), standby power thresholds (with daily re-enable lock), and daily runtime limits

## Configuration Reference

### Main Configuration

Configure once per Home Assistant instance. These settings control the grid monitoring and algorithm timing.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **Refresh period** | Number (s) | 30 | How often (in seconds) the algorithm re-evaluates device states. |
| **Grid consumption entity** | Entity (Power) | — | Sensor reporting current grid import/export power, negative means exporting to the grid. |
| **Power production entity** | Entity (Power) | — | Sensor reporting total PV production power, must always be positive. Leave empty if not available. |
| **Subscribe to state-change events** | Boolean | False | When enabled, the algorithm also reacts immediately to sensor state-change events instead of only polling. |
| **Battery state of charge entity** | Entity (%) | — | Sensor reporting the battery state of charge in percent. Leave empty if no battery is present. |
| **Battery consumption entity** | Entity (Power) | — | Sensor reporting current battery discharge power, negative means the battery is charging. Leave empty if no battery is present. |
| **Daily reset time** | Time | 05:00 | Time of day at which daily runtimes are reset. |

### Basic Device Configuration

Configure for on/off devices (switches, boolean entities).

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **Name** | String | — | A friendly name for this device. |
| **Controlled entity** | Entity | — | The switch, boolean, or similar entity that physically turns this device on or off. |
| **Nominal power** | Number (W) | — | Expected power consumption when the device is turned on (watts). |
| **Power sensor entity** | Entity (Power) | — | Sensor reporting the actual power consumption of this device. Used instead of nominal power once the device is on. |
| **Check usable template** | Template | — | A template that evaluates to true when this device is eligible to be switched on. |
| **Activation delay** | Number (min) | 0 | Minimum time (minutes) of sufficient excess power before the device is turned on. |
| **Minimum ON time** | Number (min) | 30 | Minimum time (minutes) the device must stay on after being activated. |
| **Deactivation delay** | Number (min) | 0 | Minimum time (minutes) of insufficient excess power before the device is turned off. |
| **Minimum OFF time** | Number (min) | 30 | Minimum time (minutes) the device must stay off after being deactivated. |
| **Activation actions** | Actions | — | Custom actions to execute when the device is activated. Will use `turn_on` service on the controlled entity by default. |
| **Deactivation actions** | Actions | — | Custom actions to execute when the device is deactivated. Will use `turn_off` service on the controlled entity by default. |
| **Minimum battery SOC** | Number (%) | 0 | The battery must be at or above this state of charge (%) before this device may be switched on. |
| **Standby power threshold** | Number (W) | 0 | If the device is on and its measured power drops below this threshold (watts), it is immediately deactivated and not re-enabled until the next daily reset time. |
| **Minimum daily runtime** | Number (min) | 0 | Force the device on at the off-peak time if it has not yet accumulated this many minutes of runtime today. |
| **Maximum daily runtime** | Number (min) | 1440 | Switch the device off once it has accumulated this many minutes of runtime today. |
| **Off-peak fallback time** | Time | — | Time of day at which the device is force-started if its minimum daily runtime has not been reached. |

### Variable Power Device (Additional Options)

For devices that support power adjustment (immersion heaters, pool pumps, EV chargers, etc.), add these options to the basic configuration above.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| **Power control entity** | Entity (Number) | — | The number or input_number entity used to set the current power level of this device. |
| **Maximum power** | Number (W) | — | Maximum allowed power for this device (watts). |
| **Power step** | Number (W) | 230 | Increment by which the power is raised or lowered each cycle (watts). |
| **Minimum duration per power level** | Number (min) | 30 | Minimum time (minutes) the device must hold a power level before it is changed again. |
| **Power divide factor** | Number | 1 | Divide the power by this factor before applying it to the power control entity (e.g. convert to ampere for wallboxes). |

### Custom Actions

You can define custom actions to be executed when changing a mangaged device's state. By default, it will try to use the `turn_on` or `turn_off` services on your primary entity.
If you define any custom actions, you must ensure that it will toggle the state of your entity for the algorithm to correctly detect its new state.
I recommend using template sensors or switches in case you're trying to control an entity that doesn't directly fit into a binary switch state.

#### Custom actions with variable power

When a variable power device should change its requested power, it will use the `set_number` service of your power control entity.
If custom activation actions are defined, it will use those instead. The activation and deactivation actions receive script variables that allow the power level control:

| Variable | Type | Description |
|----------|------|-------------|
| `requested_power` | Number (W) | Equal to nominal power for basic devices; different power level for variable devices. **Already divided** by the power divide factor! |
| `current_power` | Number (W) | Amount of power being drawn by the device right now. |
| `power_divide_factor` | Number | Your defined multiplication / division factor to convert power to e.g. amps or something similar. |

## Created Entities

### Integration-Level Sensors

Per Home Assistant instance (created once when you add the main configuration):

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.pv_excess_manager_managed_power` | Power (W) | Total actual power consumed by all managed devices. |
| `sensor.pv_excess_manager_virtual_excess_power` | Power (W) | Virtual surplus power available for devices (grid export + managed device power). |

### Per-Device Entities

For each device you add, the integration creates these entities:

| Entity | Type | Description |
|--------|------|-------------|
| `switch.pv_excess_manager_{device}_active` | Switch | Reflects the current ON/OFF state of the device. Is synchronized back to the device. |
| `switch.pv_excess_manager_{device}_managed` | Switch | Toggle between managed mode (ON) and manual mode (OFF). When manual, the algorithm will not control this device. |
| `input_number.pv_excess_manager_{device}_priority` | Number (1–100) | Device priority in the cascade. Lower values = higher priority. Adjust to reorder which devices fill with excess power first. |
| `sensor.pv_excess_manager_{device}_daily_runtime` | Duration (min) | Today's cumulative ON time for this device (resets daily at the configured reset time). |

## Services

### Reload

**Service:** `pv_excess_manager.reload`

Reload the PV Excess Manager configuration without restarting Home Assistant.

### Reset Devices ON-Time

**Service:** `pv_excess_manager.reset_device_runtime`

Reset a device's ON-time to zero. Useful if you want to force the off-peak activation logic immediately.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report issues and contribute improvements.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
