"""GPA projection logic for the Notion School Dashboard."""

GRADE_POINTS: dict[str, float] = {
    "A+": 5.0,
    "A": 5.0,
    "A-": 4.5,
    "B+": 4.0,
    "B": 3.5,
    "B-": 3.0,
    "C+": 2.5,
    "C": 2.0,
    "D+": 1.5,
    "D": 1.0,
    "F": 0.0,
}

PENDING_GRADES: frozenset[str] = frozenset({"IP", "Planned"})


def project_gpa(
    current_weighted_gp: float,
    current_counted_mcs: float,
    hypothetical: list[tuple[str, float]],
) -> float:
    """Compute projected GPA given current rollup values and hypothetical grades.

    hypothetical: list of (grade, mcs) pairs for pending courses.
    CS/CU grades are excluded from both numerator and denominator.
    """
    added_weighted = sum(
        GRADE_POINTS[g] * m for g, m in hypothetical if g in GRADE_POINTS
    )
    added_mcs = sum(m for g, m in hypothetical if g in GRADE_POINTS)
    denom = current_counted_mcs + added_mcs
    if denom == 0.0:
        return 0.0
    return (current_weighted_gp + added_weighted) / denom
