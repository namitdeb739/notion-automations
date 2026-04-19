"""Tests for GPA projection logic."""

import pytest

from notion_automations.gpa_project import GRADE_POINTS, PENDING_GRADES, project_gpa


def test_project_gpa_no_pending() -> None:
    assert project_gpa(20.0, 5.0, []) == pytest.approx(4.0)


def test_project_gpa_with_a_grade() -> None:
    # 20 WGP / 5 MCs = 4.0 current; add A (5.0) * 4 MCs
    result = project_gpa(20.0, 5.0, [("A", 4.0)])
    assert result == pytest.approx(40.0 / 9.0)


def test_project_gpa_su_not_counted() -> None:
    assert project_gpa(20.0, 5.0, [("CS", 4.0)]) == pytest.approx(4.0)
    assert project_gpa(20.0, 5.0, [("CU", 4.0)]) == pytest.approx(4.0)


def test_project_gpa_zero_mcs() -> None:
    assert project_gpa(0.0, 0.0, []) == 0.0


def test_project_gpa_f_grade() -> None:
    # F = 0.0 grade points, still counts in denominator
    result = project_gpa(20.0, 5.0, [("F", 4.0)])
    assert result == pytest.approx(20.0 / 9.0)


def test_grade_points_a_plus_equals_a() -> None:
    assert GRADE_POINTS["A+"] == GRADE_POINTS["A"] == 5.0


def test_pending_grades_contains_expected() -> None:
    assert "IP" in PENDING_GRADES
    assert "Planned" in PENDING_GRADES
    assert "A" not in PENDING_GRADES
