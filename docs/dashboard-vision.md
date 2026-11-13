# Boiler dashboard direction

The owner's reference is a dark Home Assistant dashboard for **read-only monitoring**, with a clear online/offline indicator and these areas:

- Overview: supply, return, delta-T, target temperature and burner state.
- Status: demand source, flame signal, fan speed and hold/lockout status.
- Pumps and heat demand: individual status indicators.
- Diagnostics: lockout and hold codes and an alert summary.
- Lifetime statistics: burner runtime and cycles; pump cycles if available.
- Temperature history: supply and return trends.
- Optional automation ideas: short cycling, excessive delta-T and lockout notifications.

This is a design target, not a list of confirmed data. Provide a dashboard example only as supported entities become available. Never substitute invented data or display a normal status when diagnostics are unavailable. Validate register mapping, units, counter word order and enum values against the boiler before presenting them as facts. Do not add controls or boiler-setting writes. Automation thresholds are user-selected suggestions, not boiler safety limits.
