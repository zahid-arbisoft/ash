"""Deterministic estimate repair (decision #29).

The PM agent asks the LLM to fill four estimate fields per ticket (traditional text + decimal days,
and the LLM-assisted text + decimal days). LLMs are unreliable at the arithmetic — they leave days
fields blank/zero, or set an `llm_estimate_days` that isn't actually smaller than the traditional
one. In *compact mode* (small `LLM_MAX_TOKENS`) the per-ticket elaboration pass is skipped entirely,
so the numbers come from a single token-starved call and are usually garbage.

These pure functions take whatever the LLM produced and normalize it: parse the traditional text
estimate into person-days, derive the LLM-assisted days from a configurable speedup factor when the
model didn't give a sane value, and keep the text/decimal fields consistent. No I/O — unit-testable.
"""

from __future__ import annotations

import re

from ash.schemas import Spec, Ticket

# T-shirt size codes → person-days (lower-cased lookup).
_SIZE_DAYS: dict[str, float] = {
    "xs": 0.25,
    "s": 0.5,
    "m": 2.0,
    "l": 5.0,
    "xl": 10.0,
}

# "3d" / "1.5w" / "8h" / "0.5d" → (value, unit). Unit defaults to days for a bare number.
_NUM_UNIT = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([hdw]?)\s*$", re.IGNORECASE)
_UNIT_TO_DAYS = {"h": 1.0 / 8.0, "d": 1.0, "w": 5.0, "": 1.0}


def parse_estimate_days(text: str | None) -> float | None:
    """Parse a free-text estimate into decimal person-days, or None if unparseable.

    Accepts T-shirt sizes (XS/S/M/L/XL), `Nh`/`Nd`/`Nw` (hours=/8, weeks*5), and a bare number
    (treated as days). Case-insensitive; surrounding whitespace ignored.
    """
    if not text:
        return None
    t = text.strip().lower()
    if t in _SIZE_DAYS:
        return _SIZE_DAYS[t]
    m = _NUM_UNIT.match(t)
    if m is None:
        return None
    value = float(m.group(1))
    return round(value * _UNIT_TO_DAYS[m.group(2).lower()], 4)


def format_days(days: float) -> str:
    """Render decimal person-days as a compact estimate string (e.g. 0.5 → '0.5d', 5 → '5d')."""
    if days <= 0:
        return ""
    if days >= 5 and days % 5 == 0:
        weeks = int(days // 5)
        return f"{weeks}w"
    if days < 1:
        return f"{days:.1f}d"
    if days == int(days):
        return f"{int(days)}d"
    return f"{days:.1f}d"


def repair_ticket_estimates(ticket: Ticket, *, speedup: float) -> Ticket:
    """Return a copy of `ticket` with its four estimate fields normalized deterministically.

    - `estimate_days`: filled from the traditional text when missing/<=0.
    - `llm_estimate_days`: set to `estimate_days / speedup` when missing/<=0 OR not strictly less
      than the traditional days (LLMs often emit a value >= traditional, which is wrong).
    - `llm_estimate` / `estimate` text: formatted from the decimal days when blank.
    - Always enforces `llm_estimate_days < estimate_days` (strict) when traditional days are known.
    """
    t = ticket.model_copy(deep=True)
    factor = speedup if speedup and speedup > 1 else 6.0

    # 1) Traditional days from the text if the model left it blank/zero.
    if not t.estimate_days or t.estimate_days <= 0:
        parsed = parse_estimate_days(t.estimate)
        if parsed is not None and parsed > 0:
            t.estimate_days = parsed

    # 2) LLM-assisted days: derive from the factor when missing/zero or not strictly smaller.
    if t.estimate_days and t.estimate_days > 0:
        llm_days = t.llm_estimate_days
        if not llm_days or llm_days <= 0 or llm_days >= t.estimate_days:
            llm_days = round(t.estimate_days / factor, 4)
        # Guard against rounding making it 0 for tiny traditional estimates.
        if llm_days <= 0:
            llm_days = round(t.estimate_days / factor, 4) or 0.1
        t.llm_estimate_days = llm_days

    # 3) Backfill the human-readable text fields from the decimals when blank.
    if not t.estimate.strip() and t.estimate_days and t.estimate_days > 0:
        t.estimate = format_days(t.estimate_days)
    if not t.llm_estimate.strip() and t.llm_estimate_days and t.llm_estimate_days > 0:
        t.llm_estimate = format_days(t.llm_estimate_days)

    return t


def repair_spec_estimates(spec: Spec, *, speedup: float) -> Spec:
    """Apply :func:`repair_ticket_estimates` to every ticket; returns the same spec object."""
    spec.tickets = [repair_ticket_estimates(t, speedup=speedup) for t in spec.tickets]
    return spec
