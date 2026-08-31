"""Does this document resemble a screenplay?

The importer decides whether a document is a
screenplay at all and to explain the verdict. A parser will happily turn a
novel, an invoice, or a mailing list archive into "action" units, so the check
runs on the parse result rather than the raw text: structure is the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ripple.adapters.base import ImportWarning, ParsedScene, UnitType

# A screenplay under three scenes is either a fragment or a false positive.
MIN_SCENES = 3
# Screenplays are dialogue-heavy. Below this share of dialogue units a document
# is more likely prose that happens to contain capitalised lines.
MIN_DIALOGUE_SHARE = 0.08
# Action paragraphs in a screenplay are short. Long ones mean prose.
MAX_MEAN_ACTION_CHARS = 700


@dataclass(frozen=True)
class ScreenplayVerdict:
    """Outcome of the structural check, with the evidence that produced it."""

    is_screenplay: bool
    confidence: float
    reasons: tuple[str, ...]

    @property
    def needs_review(self) -> bool:
        """True when the document parsed but the evidence is thin."""
        return self.is_screenplay and self.confidence < 0.6


def assess(scenes: list[ParsedScene]) -> ScreenplayVerdict:
    """Judge a parse result on structure alone.

    Returns a verdict rather than raising, so the caller decides between
    rejection and a needs_review import.
    """
    if not scenes:
        return ScreenplayVerdict(False, 0.0, ("No scenes were found.",))

    units = [unit for scene in scenes for unit in scene.units]
    if not units:
        return ScreenplayVerdict(
            False, 0.0, ("Scene headings were found but no content follows them.",)
        )

    counts = {unit_type: 0 for unit_type in UnitType}
    for unit in units:
        counts[unit.unit_type] += 1

    action_lengths = [
        len(unit.text) for unit in units if unit.unit_type is UnitType.ACTION
    ]
    mean_action = sum(action_lengths) / len(action_lengths) if action_lengths else 0.0
    dialogue_share = counts[UnitType.DIALOGUE] / len(units)

    reasons: list[str] = []
    score = 0.0

    if len(scenes) >= MIN_SCENES:
        score += 0.35
    else:
        reasons.append(
            f"Only {len(scenes)} scene heading(s) found; a screenplay normally "
            f"has at least {MIN_SCENES}."
        )

    if counts[UnitType.CHARACTER] and counts[UnitType.DIALOGUE]:
        score += 0.35
    else:
        reasons.append("No character cue followed by dialogue was found.")

    if dialogue_share >= MIN_DIALOGUE_SHARE:
        score += 0.2
    else:
        reasons.append(
            f"Dialogue is {dialogue_share:.0%} of content; screenplays are "
            f"normally above {MIN_DIALOGUE_SHARE:.0%}."
        )

    if action_lengths and mean_action <= MAX_MEAN_ACTION_CHARS:
        score += 0.1
    elif action_lengths:
        reasons.append(
            f"Action paragraphs average {mean_action:.0f} characters, which "
            "reads as prose rather than screen direction."
        )

    # Both structural pillars missing means this is not a screenplay, whatever
    # the remaining signals say.
    is_screenplay = score >= 0.5
    return ScreenplayVerdict(is_screenplay, round(score, 2), tuple(reasons))


def verdict_warnings(verdict: ScreenplayVerdict) -> list[ImportWarning]:
    """Convert a thin-evidence verdict into import warnings."""
    if verdict.is_screenplay and not verdict.reasons:
        return []
    return [
        ImportWarning(code="weak_screenplay_signal", message=reason)
        for reason in verdict.reasons
    ]
