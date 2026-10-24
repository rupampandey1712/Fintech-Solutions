"""Mock sanctions screening: case-insensitive exact name matching."""

DEFAULT_SANCTIONS_NAMES = {"john doe", "jane roe", "viktor alergi"}

CLEAR = "clear"
POTENTIAL_MATCH = "potential_match"


def sanctions_names(extra: str = "") -> set[str]:
    names = set(DEFAULT_SANCTIONS_NAMES)
    for name in extra.split(","):
        if name.strip():
            names.add(name.strip().lower())
    return names


def screen_name(full_name: str, extra_names: str = "") -> str:
    normalized = full_name.strip().lower()
    if normalized in sanctions_names(extra_names):
        return POTENTIAL_MATCH
    return CLEAR
