from app.domain.sanctions import CLEAR, POTENTIAL_MATCH, screen_name


def test_clear_name():
    assert screen_name("Alice Smith") == CLEAR


def test_potential_match_exact():
    assert screen_name("John Doe") == POTENTIAL_MATCH
    assert screen_name("Jane Roe") == POTENTIAL_MATCH
    assert screen_name("Viktor Alergi") == POTENTIAL_MATCH


def test_case_and_whitespace_insensitive():
    assert screen_name("  JOHN DOE\t") == POTENTIAL_MATCH
    assert screen_name("jane roe") == POTENTIAL_MATCH


def test_extra_names_merged():
    assert screen_name("Bad Actor", extra_names="Bad Actor") == POTENTIAL_MATCH
    assert screen_name("Bad Actor") == CLEAR


def test_partial_match_is_clear():
    assert screen_name("john") == CLEAR
    assert screen_name("john doe junior") == CLEAR
