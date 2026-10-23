import json
from gui.models import AppState

def test_map_payload_state_is_layer_scoped(tmp_path):
    state = AppState(project_root=tmp_path)
    state.map_geojson[("part2_step1", "result_inductive")] = json.dumps(
        {"type": "FeatureCollection", "features": []}
    )
    state.map_bounds[("part2_step1", "result_inductive")] = (4.0, 51.0, 5.0, 52.0)
    assert ("part2_step1", "result_inductive") in state.map_geojson
    assert state.map_bounds[("part2_step1", "result_inductive")][0] == 4.0
