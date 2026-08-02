import main


def test_team_button_coordinate_override_can_be_saved_and_cleared(monkeypatch):
    state = {}
    monkeypatch.setattr(main.cfg, "load", lambda: dict(state))

    def update(patch):
        state.update(patch)
        return dict(state)

    monkeypatch.setattr(main.cfg, "update", update)
    api = object.__new__(main.Api)

    assert api.set_macro_coords({"team_button_x": 438, "team_button_y": 570})["saved"] == [
        "team_button_x", "team_button_y"
    ]
    coords = api.get_macro_coords()
    assert coords["team_button_x"] == 438
    assert coords["team_button_y"] == 570

    assert api.clear_macro_coord("team_button")["ok"] is True
    coords = api.get_macro_coords()
    assert coords["team_button_x"] is None
    assert coords["team_button_y"] is None
    assert api.clear_macro_coord("screen_middle")["ok"] is False
