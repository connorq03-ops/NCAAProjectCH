"""
Tests for golf_course_profiles.py
"""

import pytest
from golf.golf_course_profiles import (
    COURSES,
    get_course_profile,
    get_major_courses,
    get_all_courses,
    get_courses_by_archetype,
    get_course_by_tournament,
    calc_course_fit_score,
)


class TestCourseData:
    """Tests for the COURSES data structure."""

    def test_sg_weights_sum_to_one(self):
        """All courses must have sg_weights that sum to approximately 1.0."""
        for course_id, course in COURSES.items():
            weights = course["sg_weights"]
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.01, (
                f"{course_id}: sg_weights sum to {total}, expected ~1.0"
            )

    def test_major_courses_have_18_holes(self):
        """All major courses with holes data must have exactly 18 entries."""
        for course_id, course in COURSES.items():
            if course.get("is_major") and course.get("holes") is not None:
                assert len(course["holes"]) == 18, (
                    f"{course_id}: has {len(course['holes'])} holes, expected 18"
                )

    def test_hole_entries_have_required_keys(self):
        """Each hole entry must have required keys."""
        required_keys = {"hole", "par", "yardage", "difficulty_rank"}
        for course_id, course in COURSES.items():
            if course.get("holes") is not None:
                for i, hole in enumerate(course["holes"]):
                    missing = required_keys - set(hole.keys())
                    assert not missing, (
                        f"{course_id} hole {i+1}: missing keys {missing}"
                    )

    def test_all_courses_have_required_fields(self):
        """All courses must have the core required fields."""
        required = {
            "course_id", "name", "location", "par", "yardage",
            "archetype", "sg_weights", "tournament_name", "is_major",
        }
        for course_id, course in COURSES.items():
            missing = required - set(course.keys())
            assert not missing, (
                f"{course_id}: missing required fields {missing}"
            )

    def test_at_least_16_courses(self):
        """There should be at least 16 courses in the database."""
        assert len(COURSES) >= 16


class TestHelperFunctions:
    """Tests for the helper functions."""

    def test_get_course_profile_returns_course(self):
        """get_course_profile returns a dict for a known course."""
        course = get_course_profile("augusta_national")
        assert course is not None
        assert course["name"] == "Augusta National Golf Club"

    def test_get_course_profile_returns_none_for_unknown(self):
        """get_course_profile returns None for unknown course_id."""
        assert get_course_profile("nonexistent_course") is None

    def test_get_major_courses_returns_at_least_4(self):
        """get_major_courses returns at least 4 courses."""
        majors = get_major_courses()
        assert len(majors) >= 4
        for course in majors:
            assert course["is_major"] is True

    def test_get_all_courses(self):
        """get_all_courses returns all courses."""
        all_courses = get_all_courses()
        assert len(all_courses) == len(COURSES)

    def test_get_courses_by_archetype(self):
        """get_courses_by_archetype returns correct results."""
        parklands = get_courses_by_archetype("parkland")
        assert len(parklands) > 0
        for c in parklands:
            assert c["archetype"] == "parkland"

    def test_get_course_by_tournament_exact(self):
        """get_course_by_tournament matches exact tournament names."""
        course = get_course_by_tournament("The Masters")
        assert course is not None
        assert course["course_id"] == "augusta_national"

    def test_get_course_by_tournament_fuzzy(self):
        """get_course_by_tournament does fuzzy matching."""
        course = get_course_by_tournament("Masters")
        assert course is not None
        assert course["course_id"] == "augusta_national"


class TestCourseFitScore:
    """Tests for the calc_course_fit_score function."""

    def test_aligned_player_scores_higher(self):
        """A player whose best SG matches course's highest weight scores higher
        than a player whose best category is the course's lowest weight."""
        # Augusta: sg_ott=0.30, sg_app=0.30, sg_arg=0.25, sg_putt=0.15
        augusta = get_course_profile("augusta_national")

        # Player A: strong in OTT (matches Augusta's high weight)
        player_a = {"sg_ott": 1.5, "sg_app": 0.5, "sg_arg": 0.0, "sg_putt": 0.0}

        # Player B: strong in putting (Augusta's lowest weight)
        player_b = {"sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.5, "sg_putt": 1.5}

        score_a = calc_course_fit_score(player_a, augusta)
        score_b = calc_course_fit_score(player_b, augusta)

        assert score_a > score_b, (
            f"Aligned player ({score_a:.3f}) should score higher than "
            f"misaligned player ({score_b:.3f})"
        )

    def test_all_zero_sg_returns_zero(self):
        """A player with all-zero SG should get ~0.0 course fit score."""
        augusta = get_course_profile("augusta_national")
        player = {"sg_ott": 0.0, "sg_app": 0.0, "sg_arg": 0.0, "sg_putt": 0.0}
        score = calc_course_fit_score(player, augusta)
        assert abs(score) < 0.01

    def test_strong_player_positive_score(self):
        """A player with positive SG across the board should get positive score."""
        augusta = get_course_profile("augusta_national")
        player = {"sg_ott": 1.0, "sg_app": 1.0, "sg_arg": 1.0, "sg_putt": 1.0}
        score = calc_course_fit_score(player, augusta)
        assert score > 0

    def test_elite_bonus_applied(self):
        """When player's best SG matches course's top weight, elite bonus is added."""
        # Torrey Pines: sg_ott=0.35 is highest
        torrey = get_course_profile("torrey_pines_south")

        # Player with OTT as best — should get elite bonus
        player_match = {"sg_ott": 2.0, "sg_app": 0.5, "sg_arg": 0.5, "sg_putt": 0.5}
        # Player with same total SG but OTT not the best
        player_nomatch = {"sg_ott": 0.5, "sg_app": 2.0, "sg_arg": 0.5, "sg_putt": 0.5}

        score_match = calc_course_fit_score(player_match, torrey)
        score_nomatch = calc_course_fit_score(player_nomatch, torrey)

        # The match player gets the elite bonus (alignment + higher weighted base)
        assert score_match > score_nomatch
