"""Configuration and rendering checks for the custom Lovelace dashboard."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StyledDashboardTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "docs/dashboard-styled.json").read_text())
        self.card = self.config["views"][0]["cards"][0]

    def test_panel_layout_and_read_only_actions(self):
        self.assertEqual(self.config["views"][0]["type"], "panel")
        self.assertEqual(self.card["type"], "custom:button-card")
        for action in ("tap_action", "hold_action", "double_tap_action"):
            self.assertEqual(self.card[action]["action"], "none")
        self.assertEqual(len(self.card["variables"]["entities"]), 23)
        self.assertEqual(set(self.card["triggers_update"]), set(self.card["variables"]["entities"].values()))
        self.assertEqual(self.card["custom_fields"]["trend"]["card"]["type"], "custom:apexcharts-card")
        self.assertIn("@media(max-width:600px)", self.card["extra_styles"])
        self.assertNotIn("tips", self.card["custom_fields"])
        orders = {key: next(v["order"] for v in styles if "order" in v)
                  for key, styles in self.card["styles"]["custom_fields"].items()}
        self.assertLess(int(orders["trend"]), int(orders["note"]))

    @unittest.skipUnless(shutil.which("node"), "Node needed to evaluate frontend templates")
    def test_live_missing_and_escaped_consumer_rendering(self):
        script = r'''
const fs = require('fs'), assert = require('assert');
const c = JSON.parse(fs.readFileSync(process.argv[1])).views[0].cards[0];
const render = new Function('variables','states','hass', c.variables.ui.slice(3,-3));
const variables = c.variables, states = {};
for (const id of Object.values(variables.entities)) states[id] = {state:'0', attributes:{}};
const put = (key,state,attributes={}) => states[variables.entities[key]] = {state:String(state),attributes};
put('supply_temperature',154,{unit_of_measurement:'°F'});
put('return_temperature',131,{unit_of_measurement:'°F'});
put('target_temperature',160,{unit_of_measurement:'°F'});
put('burner_control_state',8); put('demand_source',2);
put('ch1_heat_demand',1,{consumer_name:'<img src=x onerror=alert(1)>'});
put('pump_a_status',1);
let ui = render(variables,states,{locale:{language:'en'}});
assert(ui.overview.includes('154')); assert(ui.overview.includes('23'));
assert(ui.overview.includes('Modulating')); assert(ui.demand.includes('&lt;img'));
assert(!ui.demand.includes('<img')); assert(ui.pumps.includes('On'));
const renamed = render(variables,states,{locale:{language:'en'},entities:{[variables.entities.ch1_heat_demand]:{name:'Upstairs'}}});
assert(renamed.demand.includes('Upstairs'));
assert(!renamed.demand.includes('&lt;img'));
assert(ui.diagnostics.includes('Both current codes are zero'));
put('hold_code','unavailable'); put('pump_a_status','unavailable');
ui = render(variables,states,{locale:{language:'en'}});
assert(ui.diagnostics.includes('Diagnostics unavailable'));
assert(!ui.diagnostics.includes('Both current codes are zero'));
assert(ui.pumps.includes('Unknown'));
for (const id of Object.values(variables.entities)) states[id].state='unavailable';
ui = render(variables,states,{locale:{language:'en'}});
assert(ui.header.includes('Offline')); assert(ui.overview.includes('Unknown'));
assert(!ui.status.includes('Codes clear'));
'''
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "docs/dashboard-styled.json")],
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
