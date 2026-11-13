# Set up the dashboard

The [styled dashboard](dashboard-styled.json) uses button-card, card-mod and apexcharts-card. Install those frontend cards through HACS and refresh your browser. The boiler integration needs to be version 0.3.0 or newer.

1. Open the boiler's device page and find its entity IDs.
2. In `dashboard-styled.json`, replace each `sensor.ALTA_<KEY>` placeholder with the matching entity ID. Home Assistant generates IDs from device and entity names, so yours may differ from someone else's.
3. Create a dashboard and open its raw configuration editor. Save a copy of any existing config before replacing it.
4. Paste the edited JSON. The editor accepts JSON as well as YAML.

## Names and readings

Change the CH1, CH2 and DHW names in the integration's **Configure** screen. The dashboard picks them up from the demand sensors. If you renamed an entity in its settings, that override takes precedence. Clear the override to use the plugin's name.

The chart shows six hours of recorded supply and return temperatures. A new installation will have little history at first.

On/Off badges accept only the documented 0/1 values. Missing readings show as Unknown or Unavailable. A green diagnostic message means the current hold and lockout codes are both zero. It does not establish that the boiler is safe or fault-free. [Register notes](registers.md) explain the state labels and counter assumptions.

The dashboard has no boiler controls or enabled automations. It keeps Home Assistant's sidebar and rearranges the panels for smaller screens.

## Without custom cards

Use [dashboard-ready.yaml](dashboard-ready.yaml) for a simpler layout made from built-in cards. Replace its entity placeholders the same way.
