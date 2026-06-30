"""
tests/test_normalizers.py
==========================
Unit tests for the normalizers package.
Run with: python3 -m pytest tests/ -v   (or python3 tests/test_normalizers.py)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.normalizers import (
    normalize_phone, normalize_date, normalize_country,
    normalize_email, normalize_skill, normalize_url, normalize_name,
    make_candidate_id, infer_country_from_city_mention,
)


class TestPhoneNormalization(unittest.TestCase):

    def test_indian_local_number(self):
        self.assertEqual(normalize_phone("9876543210", "IN"), "+919876543210")

    def test_already_e164(self):
        self.assertEqual(normalize_phone("+919876543210"), "+919876543210")

    def test_us_number_with_formatting(self):
        self.assertEqual(normalize_phone("(415) 555-2671", "US"), "+14155552671")

    def test_leading_zero_stripped(self):
        # UK-style local number with trunk-prefix 0
        result = normalize_phone("07911 123456", "GB")
        self.assertTrue(result.startswith("+44"))

    def test_invalid_too_short(self):
        self.assertIsNone(normalize_phone("12345"))

    def test_invalid_garbage(self):
        self.assertIsNone(normalize_phone("not-a-phone"))

    def test_empty_input(self):
        self.assertIsNone(normalize_phone(""))
        self.assertIsNone(normalize_phone(None))


class TestDateNormalization(unittest.TestCase):

    def test_iso_date(self):
        self.assertEqual(normalize_date("2023-06-15"), "2023-06")

    def test_month_name_year(self):
        self.assertEqual(normalize_date("June 2023"), "2023-06")

    def test_abbreviated_month(self):
        self.assertEqual(normalize_date("Jun 2023"), "2023-06")

    def test_mm_yyyy(self):
        self.assertEqual(normalize_date("06/2023"), "2023-06")

    def test_year_only(self):
        self.assertEqual(normalize_date("2023"), "2023-01")

    def test_present_keyword(self):
        from datetime import date
        expected = date.today().strftime("%Y-%m")
        self.assertEqual(normalize_date("present"), expected)
        self.assertEqual(normalize_date("Present"), expected)
        self.assertEqual(normalize_date("current"), expected)

    def test_unparseable(self):
        self.assertIsNone(normalize_date("sometime last year"))

    def test_empty(self):
        self.assertIsNone(normalize_date(None))


class TestCountryNormalization(unittest.TestCase):

    def test_full_name(self):
        self.assertEqual(normalize_country("India"), "IN")

    def test_alpha2_passthrough(self):
        self.assertEqual(normalize_country("in"), "IN")
        self.assertEqual(normalize_country("US"), "US")

    def test_common_alias(self):
        self.assertEqual(normalize_country("USA"), "US")
        self.assertEqual(normalize_country("UK"), "GB")

    def test_unknown_country(self):
        self.assertIsNone(normalize_country("Atlantis"))

    def test_city_inference(self):
        self.assertEqual(infer_country_from_city_mention("Based in Hyderabad"), "IN")
        self.assertEqual(infer_country_from_city_mention("Living in London now"), "GB")
        self.assertIsNone(infer_country_from_city_mention("No city mentioned here"))


class TestEmailNormalization(unittest.TestCase):

    def test_lowercase_and_strip(self):
        self.assertEqual(normalize_email("  John.Doe@EXAMPLE.com  "), "john.doe@example.com")

    def test_invalid_email(self):
        self.assertIsNone(normalize_email("not-an-email"))
        self.assertIsNone(normalize_email("missing@domain"))

    def test_empty(self):
        self.assertIsNone(normalize_email(""))


class TestSkillNormalization(unittest.TestCase):

    def test_alias_resolution(self):
        self.assertEqual(normalize_skill("JS"), "javascript")
        self.assertEqual(normalize_skill("k8s"), "kubernetes")
        self.assertEqual(normalize_skill("React.js"), "react")

    def test_unknown_skill_passthrough(self):
        self.assertEqual(normalize_skill("FastAPI"), "fastapi")

    def test_ci_cd_preserved(self):
        self.assertEqual(normalize_skill("CI/CD"), "ci/cd")

    def test_empty(self):
        self.assertIsNone(normalize_skill(""))
        self.assertIsNone(normalize_skill(None))


class TestUrlNormalization(unittest.TestCase):

    def test_adds_https(self):
        self.assertEqual(normalize_url("linkedin.com/in/test"), "https://linkedin.com/in/test")

    def test_preserves_existing_scheme(self):
        self.assertEqual(normalize_url("http://example.com"), "http://example.com")

    def test_empty(self):
        self.assertIsNone(normalize_url(""))


class TestNameNormalization(unittest.TestCase):

    def test_title_case(self):
        self.assertEqual(normalize_name("priya sharma"), "Priya Sharma")

    def test_collapse_whitespace(self):
        self.assertEqual(normalize_name("  Priya   Sharma  "), "Priya Sharma")


class TestCandidateId(unittest.TestCase):

    def test_deterministic_same_email(self):
        id1 = make_candidate_id("test@example.com")
        id2 = make_candidate_id("test@example.com")
        self.assertEqual(id1, id2)

    def test_case_insensitive_email(self):
        id1 = make_candidate_id("Test@Example.com")
        id2 = make_candidate_id("test@example.com")
        self.assertEqual(id1, id2)

    def test_different_emails_different_ids(self):
        id1 = make_candidate_id("a@example.com")
        id2 = make_candidate_id("b@example.com")
        self.assertNotEqual(id1, id2)

    def test_distinct_bucket_keys_give_distinct_ids(self):
        """
        Edge case: two records with NO identity signal at all must not
        collapse onto the same candidate_id when they belong to different
        merge buckets (bucket_key disambiguates them).
        """
        id1 = make_candidate_id(None, None, None, bucket_key="unknown_0")
        id2 = make_candidate_id(None, None, None, bucket_key="unknown_1")
        self.assertNotEqual(id1, id2)

    def test_no_bucket_key_falls_back_to_unknown(self):
        id1 = make_candidate_id(None, None, None)
        id2 = make_candidate_id(None, None, None)
        self.assertEqual(id1, id2)   # both fall back to the same constant


if __name__ == "__main__":
    unittest.main(verbosity=2)