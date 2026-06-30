"""
tests/test_pipeline.py
========================
Integration tests for the merge / confidence / projection / validation stages.
Uses small synthetic IntermediateRecords rather than real files, so each test
is fast and isolates exactly one behavior.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.models import (
    IntermediateRecord, ExperienceEntry, EducationEntry,
    LocationData, LinksData,
)
from src.pipeline.merger import merge_records
from src.pipeline.confidence import compute_confidence
from src.pipeline.projector import project, ProjectionError
from src.pipeline.validator import validate_output, ValidationError


def make_rec(source_name, weight, **kwargs):
    rec = IntermediateRecord(source_name=source_name, source_weight=weight)
    for k, v in kwargs.items():
        setattr(rec, k, v)
    return rec


class TestMergeIdentityMatching(unittest.TestCase):
    """
    Core principle under test: candidate matching must use the ordered
    fallback chain (email -> phone -> name+company -> name+phone) and must
    NEVER accidentally merge two different people, nor split one person
    into two candidates when a shared identity key exists.
    """

    def test_two_sources_same_email_merge_into_one(self):
        r1 = make_rec("ats_json", 0.90, full_name="Priya Sharma",
                      emails=["priya@x.com"])
        r2 = make_rec("recruiter_csv", 0.75, full_name="Priya Sharma",
                      emails=["priya@x.com"], phones=["+919876543210"])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 1)
        canon, contributing = pairs[0]
        self.assertEqual(set(r.source_name for r in contributing),
                         {"ats_json", "recruiter_csv"})

    def test_different_emails_stay_separate(self):
        r1 = make_rec("ats_json", 0.90, full_name="Person A", emails=["a@x.com"])
        r2 = make_rec("ats_json", 0.90, full_name="Person B", emails=["b@x.com"])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 2)

    def test_no_email_falls_back_to_phone(self):
        r1 = make_rec("recruiter_csv", 0.75, full_name="Vikram",
                      phones=["+919988776655"])
        r2 = make_rec("recruiter_notes", 0.50, full_name="Vikram",
                      phones=["+919988776655"])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 1)

    def test_no_email_or_phone_falls_back_to_name_plus_company(self):
        r1 = make_rec("recruiter_csv", 0.75, full_name="Jane Doe",
                      experience=[ExperienceEntry(company="Acme Corp", title="Engineer")])
        r2 = make_rec("resume_pdf", 0.70, full_name="Jane Doe",
                      experience=[ExperienceEntry(company="Acme Corp", title="Engineer")])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 1)

    def test_records_with_zero_identity_signal_never_falsely_merge(self):
        """
        Edge case: two completely empty/garbage records (no name, no email,
        no phone) must NOT be merged into the same candidate just because
        neither has any identity key — each becomes its own bucket.
        """
        r1 = make_rec("ats_json", 0.0)
        r2 = make_rec("ats_json", 0.0)
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 2)
        ids = {canon.candidate_id for canon, _ in pairs}
        self.assertEqual(len(ids), 2, "garbage records must not share a candidate_id")

    def test_shared_name_and_company_does_not_merge_records_with_unique_emails(self):
        """
        Regression test for a real bug found via 5000-row scale testing:
        two genuinely DIFFERENT people who happen to share the same name
        and work at the same company must stay separate candidates as
        long as each has its own unique email. The weak name+company
        fallback key must never override a strong email match — it may
        only be used when a record has no email AND no phone at all.
        """
        r1 = make_rec("recruiter_csv", 0.75, full_name="Arjun Joshi",
                      emails=["arjun.joshi.1@example.com"],
                      experience=[ExperienceEntry(company="TechCorp", title="Engineer")])
        r2 = make_rec("recruiter_csv", 0.75, full_name="Arjun Joshi",
                      emails=["arjun.joshi.2@example.com"],
                      experience=[ExperienceEntry(company="TechCorp", title="Engineer")])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 2,
                         "two different people sharing name+company must not merge "
                         "when both have unique emails")

    def test_shared_name_and_company_does_not_merge_records_with_unique_phones(self):
        """Same regression as above, but for unique phones instead of emails."""
        r1 = make_rec("recruiter_csv", 0.75, full_name="Arjun Joshi",
                      phones=["+919876500001"],
                      experience=[ExperienceEntry(company="TechCorp", title="Engineer")])
        r2 = make_rec("recruiter_csv", 0.75, full_name="Arjun Joshi",
                      phones=["+919876500002"],
                      experience=[ExperienceEntry(company="TechCorp", title="Engineer")])
        pairs = merge_records([r1, r2])
        self.assertEqual(len(pairs), 2,
                         "two different people sharing name+company must not merge "
                         "when both have unique phones")


class TestMergeConflictResolution(unittest.TestCase):

    def test_scalar_highest_priority_wins(self):
        r_ats = make_rec("ats_json", 0.90, full_name="ATS Name",
                         emails=["x@y.com"])
        r_csv = make_rec("recruiter_csv", 0.75, full_name="CSV Name",
                         emails=["x@y.com"])
        pairs = merge_records([r_csv, r_ats])   # order shouldn't matter
        canon, _ = pairs[0]
        self.assertEqual(canon.full_name, "ATS Name")

    def test_arrays_union_with_dedup(self):
        r1 = make_rec("ats_json", 0.90, emails=["a@x.com"], skills=["python", "go"])
        r2 = make_rec("recruiter_csv", 0.75, emails=["a@x.com"], skills=["python", "react"])
        pairs = merge_records([r1, r2])
        canon, _ = pairs[0]
        self.assertEqual(set(canon.skills), {"python", "go", "react"})

    def test_experience_merges_by_company_title_not_date(self):
        """
        Same job reported with dates by ATS and without dates by CSV must
        merge into ONE entry (gap-filled), not produce a duplicate.
        """
        r_ats = make_rec("ats_json", 0.90, emails=["x@y.com"],
                         experience=[ExperienceEntry(
                             company="TechCorp", title="Engineer",
                             start="2021-03", end="2023-01", summary="Did stuff",
                         )])
        r_csv = make_rec("recruiter_csv", 0.75, emails=["x@y.com"],
                         experience=[ExperienceEntry(
                             company="TechCorp", title="Engineer",
                         )])
        pairs = merge_records([r_ats, r_csv])
        canon, _ = pairs[0]
        self.assertEqual(len(canon.experience), 1)
        self.assertEqual(canon.experience[0].start, "2021-03")
        self.assertEqual(canon.experience[0].summary, "Did stuff")

    def test_gap_fill_from_lower_priority_source(self):
        r_ats = make_rec("ats_json", 0.90, emails=["x@y.com"],
                         location=LocationData(country="IN"))
        r_csv = make_rec("recruiter_csv", 0.75, emails=["x@y.com"],
                         location=LocationData(city="Pune", country="IN"))
        pairs = merge_records([r_ats, r_csv])
        canon, _ = pairs[0]
        self.assertEqual(canon.location.city, "Pune")     # gap-filled from CSV
        self.assertEqual(canon.location.country, "IN")    # kept from ATS


class TestConfidenceScoring(unittest.TestCase):

    def test_agreement_gives_higher_confidence_than_single_source(self):
        r1 = make_rec("ats_json", 0.90, emails=["x@y.com"], full_name="Priya")
        r2 = make_rec("recruiter_csv", 0.75, emails=["x@y.com"], full_name="Priya")
        pairs = merge_records([r1, r2])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)
        # Single-source (no agreement) candidate for comparison
        r3 = make_rec("recruiter_csv", 0.75, emails=["z@y.com"], full_name="Solo")
        pairs2 = merge_records([r3])
        canon2, contributing2 = pairs2[0]
        compute_confidence(canon2, contributing2)
        self.assertGreater(canon.confidence["full_name"], canon2.confidence["full_name"])

    def test_no_source_gives_zero_confidence(self):
        r1 = make_rec("ats_json", 0.90, emails=["x@y.com"])  # no headline at all
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)
        self.assertEqual(canon.confidence["headline"], 0.0)

    def test_confidence_scoped_per_candidate_not_global(self):
        """
        Critical correctness test: candidate A's confidence must not be
        influenced by candidate B's sources, even when both are processed
        in the same pipeline run.
        """
        a1 = make_rec("ats_json", 0.90, emails=["a@x.com"], full_name="A")
        a2 = make_rec("recruiter_csv", 0.75, emails=["a@x.com"], full_name="A")
        b1 = make_rec("ats_json", 0.90, emails=["b@x.com"], full_name="B")
        # B has only ONE contributing source -> should NOT get the
        # 2-source agreement bonus that A legitimately earns.
        pairs = merge_records([a1, a2, b1])
        by_email = {canon.emails[0]: (canon, contributing) for canon, contributing in pairs}

        canon_a, contrib_a = by_email["a@x.com"]
        canon_b, contrib_b = by_email["b@x.com"]
        compute_confidence(canon_a, contrib_a)
        compute_confidence(canon_b, contrib_b)

        self.assertEqual(len(contrib_a), 2)
        self.assertEqual(len(contrib_b), 1)
        self.assertGreater(canon_a.confidence["full_name"], canon_b.confidence["full_name"])


class TestProjection(unittest.TestCase):

    def test_field_selection_and_rename(self):
        r1 = make_rec("ats_json", 0.90, full_name="Priya", emails=["p@x.com"])
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {
            "fields": [
                {"path": "name", "from": "full_name", "type": "string"},
                {"path": "primary_email", "from": "emails[0]", "type": "string"},
            ],
            "include_confidence": False,
            "include_provenance": False,
            "include_metadata": False,
        }
        out = project(canon, config)
        self.assertEqual(out["name"], "Priya")
        self.assertEqual(out["primary_email"], "p@x.com")
        self.assertNotIn("confidence", out)
        self.assertNotIn("full_name", out)   # original field name not present

    def test_array_spread_projection(self):
        r1 = make_rec("ats_json", 0.90, emails=["p@x.com"], skills=["python", "go"])
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {"fields": [{"path": "skills", "from": "skills[]", "type": "string[]"}]}
        out = project(canon, config)
        self.assertEqual(out["skills"], ["python", "go"])

    def test_on_missing_null_default(self):
        r1 = make_rec("ats_json", 0.90, emails=["p@x.com"])  # no headline
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {"fields": [{"path": "headline", "from": "headline", "type": "string"}]}
        out = project(canon, config)
        self.assertIsNone(out["headline"])

    def test_on_missing_omit(self):
        r1 = make_rec("ats_json", 0.90, emails=["p@x.com"])
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {
            "fields": [{"path": "headline", "from": "headline", "type": "string"}],
            "on_missing": "omit",
        }
        out = project(canon, config)
        self.assertNotIn("headline", out)

    def test_on_missing_error_raises(self):
        r1 = make_rec("ats_json", 0.90, emails=["p@x.com"])   # no full_name
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {
            "fields": [{"path": "full_name", "from": "full_name",
                       "type": "string", "required": True}],
            "on_missing": "error",
        }
        with self.assertRaises(ProjectionError):
            project(canon, config)

    def test_normalize_e164_override_at_projection_time(self):
        r1 = make_rec("ats_json", 0.90, emails=["p@x.com"], phones=["9876543210"])
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)

        config = {"fields": [{"path": "phone", "from": "phones[0]",
                              "type": "string", "normalize": "E164"}]}
        out = project(canon, config)
        # already not E164-clean (no leading +), projector should re-attempt
        self.assertTrue(out["phone"].startswith("+"))


class TestValidation(unittest.TestCase):

    def test_default_schema_valid_output_passes(self):
        r1 = make_rec("ats_json", 0.90, full_name="Priya", emails=["p@x.com"])
        pairs = merge_records([r1])
        canon, contributing = pairs[0]
        compute_confidence(canon, contributing)
        out = project(canon, {})
        is_valid, errors = validate_output(out)
        self.assertTrue(is_valid, errors)

    def test_strict_mode_raises_on_required_field_missing(self):
        config = {"fields": [{"path": "x", "type": "string", "required": True}]}
        bad_output = {"candidate_id": "abc"}   # missing required 'x'
        with self.assertRaises(ValidationError):
            validate_output(bad_output, config, strict=True)

    def test_non_strict_mode_returns_errors_without_raising(self):
        config = {"fields": [{"path": "x", "type": "string", "required": True}]}
        bad_output = {"candidate_id": "abc"}
        is_valid, errors = validate_output(bad_output, config, strict=False)
        self.assertFalse(is_valid)
        self.assertTrue(len(errors) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)