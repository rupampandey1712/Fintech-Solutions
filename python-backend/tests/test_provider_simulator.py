"""Provider simulator modes: deterministic outcomes driven by reference markers."""

from app.services.provider import (
    MARKER_DELAY,
    MARKER_DUPLICATE,
    MARKER_FAILURE,
    MARKER_PARTIAL,
    MARKER_TIMEOUT,
    MODE_DELAYED,
    MODE_DUPLICATE,
    MODE_FAILURE,
    MODE_PARTIAL,
    MODE_SUCCESS,
    MODE_TIMEOUT,
    mode_for_reference,
)


def test_plain_reference_is_a_success():
    assert mode_for_reference("Rent share") == MODE_SUCCESS
    assert mode_for_reference(None) == MODE_SUCCESS


def test_each_marker_selects_its_mode():
    assert mode_for_reference(f"Rent {MARKER_FAILURE}") == MODE_FAILURE
    assert mode_for_reference(f"Rent {MARKER_TIMEOUT}") == MODE_TIMEOUT
    assert mode_for_reference(f"Rent {MARKER_DUPLICATE}") == MODE_DUPLICATE
    assert mode_for_reference(f"Rent {MARKER_DELAY}") == MODE_DELAYED
    assert mode_for_reference(f"Rent {MARKER_PARTIAL}") == MODE_PARTIAL


def test_markers_anywhere_in_the_reference_trigger():
    assert mode_for_reference(f"{MARKER_FAILURE} please") == MODE_FAILURE
    assert mode_for_reference("invoice " + MARKER_TIMEOUT + " #42") == MODE_TIMEOUT
