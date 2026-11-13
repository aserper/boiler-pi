# Dashboard installation

`dashboard-ready.yaml` follows the supplied dark monitoring reference using native Home Assistant cards. It requires Home Assistant 2026.10.1+ and version 0.2.0+ of this integration. Choose a dark theme in Home Assistant for the intended appearance. No custom frontend cards are required.

1. Install and configure the integration.
2. On its device page, find the entity ID for each sensor. The YAML uses placeholders such as `sensor.ALTA_SUPPLY_TEMPERATURE`; replace each with the corresponding entity. Home Assistant includes the device name in generated IDs, and may append suffixes. Do not assume the integration's internal key equals the final entity ID.
3. Create a separate dashboard and paste the substituted configuration into its raw configuration editor. Do not replace another dashboard without a backup.
4. Verify the temperature units, missing-data states, demand indicators and diagnostics. History fills as recorder collects readings.

Heat-demand and pump flags translate only documented 0/1 values to On/Off; other values remain Unknown. Burner and demand-source codes remain numeric because manufacturer documents disagree about some enum labels. Counter word order is provisional; raw words are retained on the entities. A missing DHW temperature is not the same as no DHW demand.

CH1 and CH2 do not necessarily identify individual thermostats if an external zone panel combines their calls. Confirm the installation's mapping before assigning room or floor names.

The two-column and three-column cards in the original image are approximated with responsive native Sections. This is not a pixel-identical custom frontend. Unknown diagnostics never produce a normal-status banner. No boiler controls or automatic setpoint changes are included.
