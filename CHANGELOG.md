# Changelog

## 0.5.0

### Breaking changes

- **Removed the phase-switching wallbox device type.** Controlling charging current and phase count from the PV Excess Manager oscillated too easily. Let the wallbox's own PV surplus logic handle current and phases instead.
- Existing phase-switching wallbox devices fail to load after updating, and their options dialog shows a "no longer supported" message. Delete the device and add it again as a **basic** device, for example with the wallbox's minimum start power as nominal power and its power sensor configured. If you reuse the same name, the entity ids stay the same.
