from core import runner_constants as rc


def test_event_act_images_and_order_stay_in_sync():
    """_reach_event_act_selected looks an act up in EVENT_ACT_IMAGES, then
    indexes EVENT_ACT_ORDER to decide whether the card needs scrolling to. An
    act in one but not the other raises ValueError mid-navigation, so the two
    have to be edited together -- this fails the moment they drift, which is
    exactly what adding Act 4 to one and forgetting the other looks like."""
    assert set(rc.EVENT_ACT_IMAGES) == set(rc.EVENT_ACT_ORDER)


def test_event_act_scroll_index_is_within_the_order():
    assert 0 <= rc.EVENT_ACT_SCROLL_FROM_INDEX <= len(rc.EVENT_ACT_ORDER)


def test_every_event_act_has_at_least_one_candidate_crop():
    for act, images in rc.EVENT_ACT_IMAGES.items():
        candidates = (images,) if isinstance(images, str) else images
        assert candidates, f"Act {act} has no reference crop names"
        assert all(isinstance(n, str) and n for n in candidates), f"Act {act} has a bad crop name"
