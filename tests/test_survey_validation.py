"""Verify the survey CSV is readable and the mapping covers our channels."""
from pathlib import Path

from app.services import reference, survey_loader


def test_survey_csv_is_present():
    assert survey_loader.DEFAULT_CSV.exists(), (
        "Sample CSV missing — drop ccs_taiwan_2025_export.csv into app/data/samples/"
    )


def test_survey_rows_parse():
    rows = survey_loader.load_rows()
    assert len(rows) > 1000, f"Expected a large CSV, only parsed {len(rows)} rows"
    # a known anchor from the training material: TV live viewing
    tv = next(r for r in rows if r.variable == "Z5_R1_1")
    assert tv.agreement_level > 0
    assert tv.header == "媒體接觸率"


def test_channel_overrides_are_within_bounds():
    overrides = survey_loader.channel_penetration_overrides()
    assert overrides, "Mapping returned no overrides — check channel_survey_mapping.json"
    for channel_id, pct in overrides.items():
        assert 0 <= pct <= 100, f"{channel_id}: {pct}% out of bounds"


def test_every_mocked_channel_either_maps_or_is_explicitly_skipped():
    overrides = survey_loader.channel_penetration_overrides()
    metrics = reference.channel_metrics()
    mapped = set(overrides.keys())
    # Niche variants without a direct Z5_R1_* touchpoint. These keep their
    # hand-tuned mock values. Add new ids here when a channel is intentionally
    # left unmapped.
    allowed_unmapped = {
        "tv_program_sponsorship",
        "sponsorship_of_movies",
        "viral_youtube",
    }
    missing = set(metrics.keys()) - mapped - allowed_unmapped
    assert not missing, (
        "These channels have no survey mapping — add them to "
        "channel_survey_mapping.json or allowed_unmapped: "
        + ", ".join(sorted(missing))
    )
