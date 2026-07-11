from pathlib import Path
import csv
import gzip
import hashlib
import json
import sys
from types import ModuleType

import httpx
import pytest

import compare
import main
from models import FelEvidence, FelRelease


@pytest.fixture(autouse=True)
def isolate_ai_credentials(monkeypatch):
    dotenv = ModuleType("dotenv")

    def load_dotenv():
        return None

    dotenv.load_dotenv = load_dotenv
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "THECLAWBAY_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def release(title: str, year: str, source_url: str, quote: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=quote,
            evidence_type="fixture",
        ),
    )


def test_write_comparison_outputs_source_and_match_percent(tmp_path: Path):
    ai_candidates = [
        compare.FoundCandidate(
            title="The Matrix",
            year="1999",
            source_url="https://forum.example.test/thread",
            evidence="The Matrix (1999) FEL",
            extraction_method="ai",
        ),
        compare.FoundCandidate(
            title="Only AI",
            year="2024",
            source_url="https://forum.example.test/thread",
            evidence="Only AI (2024) FEL",
            extraction_method="ai",
        ),
    ]
    py_releases = [
        release(
            "The Matrix",
            "1999",
            "https://forum.example.test/thread",
            "The Matrix is confirmed Profile 7 FEL",
        ),
        release(
            "Only Python",
            "2020",
            "https://forum.example.test/other",
            "Only Python is confirmed Profile 7 FEL",
        ),
    ]

    summary = compare.write_comparison_outputs(
        ai_candidates=ai_candidates,
        py_releases=py_releases,
        output_dir=tmp_path,
    )

    assert summary == {
        "AI_found": 2,
        "PY_found": 2,
        "overlap": 1,
        "AI_only": 1,
        "PY_only": 1,
    }
    ai_rows = list(csv.DictReader((tmp_path / "AI_found.csv").open()))
    py_rows = list(csv.DictReader((tmp_path / "PY_found.csv").open()))
    assert ai_rows[0]["source_url"] == "https://forum.example.test/thread"
    assert ai_rows[0]["match_percent"] == "100"
    assert ai_rows[1]["match_percent"] != "100"
    assert py_rows[0]["source_url"] == "https://forum.example.test/thread"
    assert py_rows[0]["match_percent"] == "100"
    assert (tmp_path / "AI_found.txt").read_text(encoding="utf-8").splitlines() == [
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread",
        "Only AI (2024) | match=50% | source=https://forum.example.test/thread",
    ]
    assert (tmp_path / "AI_PY_overlap.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread"
    ]
    assert (tmp_path / "AI_only.txt").read_text(encoding="utf-8").splitlines() == [
        "Only AI (2024) | match=50% | source=https://forum.example.test/thread"
    ]
    assert (tmp_path / "PY_only.txt").read_text(encoding="utf-8").splitlines() == [
        "Only Python (2020) | match=50% | source=https://forum.example.test/other"
    ]


def test_ai_client_loads_env_without_printing_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test/codex")

    settings = compare.AISettings.from_env()

    assert settings.api_key == "secret-token"
    assert settings.base_url == "https://api.example.test/codex"
    assert settings.model == "gpt-5.5"
    assert settings.reasoning_effort == "xhigh"


@pytest.mark.parametrize(
    "credentials,expected",
    [
        (
            {
                "OPENAI_API_KEY": "openai-token",
                "CODEX_API_KEY": "codex-token",
                "THECLAWBAY_API_KEY": "legacy-token",
            },
            "openai-token",
        ),
        (
            {
                "CODEX_API_KEY": "codex-token",
                "THECLAWBAY_API_KEY": "legacy-token",
            },
            "codex-token",
        ),
        ({"CODEX_API_KEY": "codex-token"}, "codex-token"),
        ({"THECLAWBAY_API_KEY": "legacy-token"}, "legacy-token"),
    ],
)
def test_ai_settings_credential_precedence(monkeypatch, credentials, expected):
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    assert compare.AISettings.from_env().api_key == expected


@pytest.mark.parametrize(
    "credentials,expected",
    [
        (
            {
                "OPENAI_API_KEY": "  openai-token\n",
                "CODEX_API_KEY": "codex-token",
                "THECLAWBAY_API_KEY": "legacy-token",
            },
            "openai-token",
        ),
        (
            {
                "OPENAI_API_KEY": " \t",
                "CODEX_API_KEY": "  codex-token ",
                "THECLAWBAY_API_KEY": "legacy-token",
            },
            "codex-token",
        ),
        (
            {
                "OPENAI_API_KEY": " ",
                "CODEX_API_KEY": "\n",
                "THECLAWBAY_API_KEY": "  legacy-token  ",
            },
            "legacy-token",
        ),
    ],
)
def test_ai_settings_strips_credentials_before_precedence(
    monkeypatch, credentials, expected
):
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    assert compare.AISettings.from_env().api_key == expected


def test_ai_settings_missing_key_error_names_supported_credentials():
    with pytest.raises(
        RuntimeError,
        match="OPENAI_API_KEY.*CODEX_API_KEY.*THECLAWBAY_API_KEY",
    ):
        compare.AISettings.from_env()


def test_ai_settings_warns_generically_when_dotenv_loading_fails(monkeypatch, capsys):
    import dotenv

    secret = "dotenv-parser-secret"

    def fail_dotenv_load():
        raise ValueError(secret)

    monkeypatch.setattr(dotenv, "load_dotenv", fail_dotenv_load)
    monkeypatch.setenv("OPENAI_API_KEY", "  usable-token  ")

    assert compare.AISettings.from_env().api_key == "usable-token"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dotenv" in captured.err.lower()
    assert secret not in captured.err
    assert "ValueError" not in captured.err


def test_ai_extraction_prompt_rejects_list_ordinals():
    assert "Do not include list numbering" in compare.AI_EXTRACTION_SYSTEM_PROMPT
    assert "281 Nobody" in compare.AI_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "title,evidence,reason",
    [
        ("Up", "Setup (2019) Profile 7 FEL", "title-not-bound"),
        ("Dune", "Dune (2021) Profile 7 MEL", "excluded-format"),
        ("Dune", "Dune (2021) Profile 7 MEL", "excluded-format"),
        ("Dune", "Dune (2021) Profile 7 FEL: No", "negated-fel"),
        ("Dune", "Dune (2021) Profile 7 REMUX", "excluded-format"),
    ],
)
def test_validate_ai_candidates_rejects_adversarial_evidence(title, evidence, reason):
    candidate = compare.FoundCandidate(
        title, "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == [reason]


def test_validate_ai_candidates_rejects_competing_years_in_excerpt():
    evidence = "Dune (1984), Dune (2021) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["ambiguous-year"]


def test_validate_ai_candidates_rejects_same_year_cross_release_excerpt():
    evidence = "Alpha (2024) Profile 7 FEL; Beta (2024) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_binds_markers_to_subject_after_semicolon():
    cross_release_evidence = "Alpha (2024); Beta Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", cross_release_evidence, "ai"
    )
    diagnostics: list[str] = []

    assert (
        compare.validate_ai_candidates([candidate], cross_release_evidence, diagnostics)
        == []
    )
    assert diagnostics == ["cross-release-evidence"]

    bound_evidence = "Alpha (2024); Profile 7 FEL"
    bound_candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", bound_evidence, "ai"
    )
    assert compare.validate_ai_candidates([bound_candidate], bound_evidence) == [
        bound_candidate
    ]


@pytest.mark.parametrize(
    "separator",
    [", ", ": ", "; ", " / ", " | ", " & ", " + ", " and "],
)
def test_validate_ai_candidates_rejects_markers_borrowed_from_delimited_subject(
    separator,
):
    evidence = f"Alpha (2024) is listed{separator}Beta is Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_rejects_year_borrowed_from_trailing_subject():
    evidence = "Alpha; Profile 7 FEL; Beta (2024)"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_rejects_trailing_yearless_subject():
    evidence = "Alpha Profile 7 FEL; Beta"
    candidate = compare.FoundCandidate(
        "Alpha", "Unknown", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize(
    "evidence",
    [
        "Alpha (2024) Profile 7; FEL",
        "Alpha (2024); Profile 7; FEL",
        "Alpha (2024) Profile 7, FEL",
        "Alpha (2024) Profile 7: FEL",
        "Alpha (2024) Profile 7 / FEL",
        "Alpha (2024) Profile 7 | FEL",
        "Alpha (2024) Profile 7 & FEL",
        "Alpha (2024) Profile 7 + FEL",
        "Alpha (2024) Profile 7 and FEL",
    ],
)
def test_validate_ai_candidates_rejects_profile_and_fel_split_across_clauses(
    evidence,
):
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize(
    "evidence",
    [
        "Alpha (2024) is, parenthetically, Profile 7 FEL",
        "Alpha (2024) is Profile 7 FEL, confirmed by disc scan",
        "Alpha (2024): Profile 7 FEL",
    ],
)
def test_validate_ai_candidates_accepts_subject_continuations(evidence):
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "adverb", ["probably", "possibly", "allegedly", "supposedly", "reportedly"]
)
def test_validate_ai_candidates_rejects_uncertain_adverb_residue(adverb):
    evidence = f"Alpha (2024) is, {adverb}, Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize(
    "statement",
    [
        "is Profile 7 full enhancement layer (FEL)",
        "is Profile 7 with full enhancement layer FEL",
    ],
)
def test_validate_ai_candidates_accepts_full_enhancement_layer_fel(statement):
    evidence = f"Alpha (2024) {statement}"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "metadata", ["Dolby Vision", "DV", "video", "Dolby Vision video"]
)
def test_validate_ai_candidates_accepts_marker_clause_with_release_metadata(metadata):
    evidence = f"Alpha (2024); {metadata} Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "evidence,year",
    [
        ("Alpha (2024); Video Nasty Profile 7 FEL", "2024"),
        ("Alpha Profile 7 FEL; Release Me (2024)", "Unknown"),
        ("Alpha Profile 7 FEL; Also Alice", "Unknown"),
    ],
)
def test_validate_ai_candidates_rejects_titles_prefixed_by_metadata_words(
    evidence, year
):
    candidate = compare.FoundCandidate(
        "Alpha", year, "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_rejects_title_made_of_metadata_words():
    evidence = "Alpha (2024); The Release Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize("title,year", [("Mel", "1998"), ("Remux", "2024")])
def test_validate_ai_candidates_accepts_title_that_is_a_format_token(title, year):
    evidence = f"{title} ({year}) Profile 7 FEL"
    candidate = compare.FoundCandidate(title, year, "https://src.test", evidence, "ai")

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "title,year,format_token", [("Mel", "1998", "MEL"), ("Remux", "2024", "REMUX")]
)
def test_validate_ai_candidates_keeps_later_format_token_after_title_binding(
    title, year, format_token
):
    evidence = f"{title} ({year}) Profile 7 FEL; {format_token}"
    candidate = compare.FoundCandidate(title, year, "https://src.test", evidence, "ai")
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["excluded-format"]


def test_validate_ai_candidates_keeps_later_release_metadata_after_title_binding():
    evidence = "Release (2024), release date 2024, Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Release", "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


def test_validate_ai_candidates_requires_markers_outside_candidate_title():
    evidence = "Mel Profile 7 (2024) FEL"
    candidate = compare.FoundCandidate(
        "Mel Profile 7", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["missing-affirmative-fel"]


@pytest.mark.parametrize(
    "evidence",
    [
        "Dune (2021) has no Dolby Vision Profile 7 FEL",
        "Dune (2021) has no Profile 7 FEL",
        "Dune (2021) is not Profile 7 FEL",
        "Dune (2021) is without a Profile 7 FEL layer",
    ],
)
def test_validate_ai_candidates_rejects_qualified_leading_fel_denial(evidence):
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["negated-fel"]


@pytest.mark.parametrize(
    "denial",
    ["is not confirmed as Profile 7 FEL", "is never confirmed Profile 7 FEL"],
)
def test_validate_ai_candidates_rejects_explicit_confirmation_denial(denial):
    evidence = f"Dune (2021) {denial}"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["negated-fel"]


@pytest.mark.parametrize(
    "evidence",
    [
        "Dune (2021), no doubt, is Profile 7 FEL",
        "Dune (2021) is not only Profile 7 FEL",
    ],
)
def test_validate_ai_candidates_accepts_non_denial_assertion_phrases(evidence):
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "other_subject", ["1917 (2019)", "eXistenZ (1999)", "Élite (2024)"]
)
def test_validate_ai_candidates_rejects_non_ascii_case_or_numeric_subject_after_slash(
    other_subject,
):
    evidence = f"Alpha / {other_subject} Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "Unknown", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_rejects_adjacent_mixed_case_title_year():
    evidence = "Alpha (2024) eXistenZ (2024) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_accepts_numeric_title_year():
    evidence = "1917 (2019) — 1917 is confirmed Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "1917", "2019", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], evidence)[0].year == "2019"


def test_validate_ai_candidates_accepts_repeated_contextual_year():
    evidence = "Dune (2021), released in 2021, is Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], evidence)[0].year == "2021"


def test_validate_ai_candidates_accepts_standalone_bound_year():
    evidence = "Dune 2021 Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


def test_validate_ai_candidates_rejects_competing_standalone_years_as_ambiguous():
    evidence = "Dune 1984; Dune 2021 Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["ambiguous-year"]


def test_validate_ai_candidates_rejects_additional_title_year_label():
    evidence = "Alpha / Beta (2024) are Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize("separator", ["&", ",", "and", "+", "•"])
def test_validate_ai_candidates_rejects_coordinated_title_year_label(separator):
    evidence = f"Alpha {separator} Beta (2024) are Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_rejects_adjacent_title_year_label():
    evidence = "Alpha (2024) Beta (2024) are Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


@pytest.mark.parametrize("separator", ["&", ",", "and"])
def test_validate_ai_candidates_accepts_exact_compound_title(separator):
    title = f"Alpha {separator} Beta"
    evidence = f"{title} (2024) is Profile 7 FEL"
    candidate = compare.FoundCandidate(
        title, "2024", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], evidence)[0].year == "2024"


@pytest.mark.parametrize("separator", ["/", "+", "•", ";"])
def test_validate_ai_candidates_accepts_exact_compound_title_with_strong_separator(
    separator,
):
    title = f"Alpha {separator} Beta"
    evidence = f"{title} (2024) is Profile 7 FEL"
    candidate = compare.FoundCandidate(
        title, "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "source",
    [
        "Alpha (2024) Profile 7\nFEL confirmed",
        "<table><tr><td>Alpha (2024) Profile 7</td></tr>"
        "<tr><td>FEL confirmed</td></tr></table>",
        "<p>Alpha (2024) Profile 7<br>FEL confirmed</p>",
        "Alpha (2024) Profile 7<br>FEL confirmed",
    ],
)
def test_validate_ai_candidates_rejects_evidence_joined_across_records(source):
    evidence = "Alpha (2024) Profile 7 FEL confirmed"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], source, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_accepts_evidence_within_one_table_row():
    source = (
        "<table><tr><td>Alpha (2024)</td><td>Profile 7 FEL confirmed</td></tr></table>"
    )
    evidence = "Alpha (2024) Profile 7 FEL confirmed"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], source)[0].year == "2024"


def test_validate_ai_candidates_rejects_nested_list_ancestor_joining_child_records():
    source = (
        "<ul><li>Releases<ul>"
        "<li>Alpha (2024) Profile 7</li>"
        "<li>FEL confirmed</li>"
        "</ul></li></ul>"
    )
    evidence = "Alpha (2024) Profile 7 FEL confirmed"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], source, diagnostics) == []
    assert diagnostics == ["cross-release-evidence"]


def test_validate_ai_candidates_keeps_table_row_with_semantic_cell_markup():
    source = (
        "<table><tr>"
        "<td><p>Alpha (2024)</p></td>"
        "<td><p>Profile 7 FEL</p></td>"
        "</tr></table>"
    )
    evidence = "Alpha (2024) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Alpha", "2024", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], source) == [candidate]


@pytest.mark.parametrize(
    "fel_statement", ["FEL? No.", "FEL — not present", "FEL is not present"]
)
def test_validate_ai_candidates_rejects_trailing_fel_denial(fel_statement):
    evidence = f"Dune (2021) Profile 7 {fel_statement}"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["negated-fel"]


@pytest.mark.parametrize("status", ["absent", "unsupported", "not supported"])
def test_validate_ai_candidates_rejects_explicit_fel_absence_status(status):
    evidence = f"Dune (2021) Profile 7 FEL {status}"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["negated-fel"]


def test_validate_ai_candidates_accepts_explicit_mel_absence_status():
    evidence = "Dune (2021) Profile 7 FEL, MEL absent"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


def test_validate_ai_candidates_rejects_not_only_mel():
    evidence = "Dune (2021) is not only Profile 7 MEL but also Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["excluded-format"]


def test_validate_ai_candidates_rejects_misleading_no_before_mel():
    evidence = "Dune (2021) Profile 7 FEL; no doubt this is MEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["excluded-format"]


def test_validate_ai_candidates_accepts_lowercase_contextual_year_label():
    evidence = "Dune (2021), released theatrically (2021), Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], evidence)[0].year == "2021"


def test_validate_ai_candidates_accepts_lowercase_release_metadata_with_named_label():
    evidence = "Dune (2021), released by Criterion Collection (2021), Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )

    assert compare.validate_ai_candidates([candidate], evidence) == [candidate]


@pytest.mark.parametrize(
    "mel_statement",
    [
        "not MEL",
        "not Dolby Vision MEL",
        "MEL: No",
        "MEL - no",
        "MEL = false",
        "MEL? No",
        "MEL—not present",
        "MEL - not present",
        "no MEL",
        "without MEL",
    ],
)
def test_validate_ai_candidates_accepts_negated_mel(mel_statement):
    evidence = f"Dune (2021) Profile 7 FEL, {mel_statement}"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    assert compare.validate_ai_candidates([candidate], evidence)[0].year == "2021"


def test_validate_ai_candidates_invalid_year_becomes_unknown():
    evidence = "Dune Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "TBD", "https://src.test", evidence, "ai"
    )
    accepted = compare.validate_ai_candidates([candidate], evidence)
    assert accepted[0].year == "Unknown"


@pytest.mark.parametrize(
    "candidate_year,evidence",
    [
        ("Unknown", "Dune (2021) Profile 7 FEL"),
        ("TBD", "Dune 2021 Profile 7 FEL"),
    ],
)
def test_validate_ai_candidates_recovers_immediate_title_bound_year(
    candidate_year, evidence
):
    candidate = compare.FoundCandidate(
        "Dune", candidate_year, "https://src.test", evidence, "ai"
    )

    accepted = compare.validate_ai_candidates([candidate], evidence)

    assert [(item.title, item.year) for item in accepted] == [("Dune", "2021")]


def test_validate_ai_candidates_recovers_year_for_numeric_title():
    evidence = "1917 (2019) — 1917 is confirmed Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "1917", "Unknown", "https://src.test", evidence, "ai"
    )

    accepted = compare.validate_ai_candidates([candidate], evidence)

    assert [(item.title, item.year) for item in accepted] == [("1917", "2019")]


def test_validate_ai_candidates_rejects_unbound_year_for_yearless_candidate():
    evidence = "Dune Profile 7 FEL, released in 2021"
    candidate = compare.FoundCandidate(
        "Dune", "Unknown", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["year-not-in-evidence"]


def test_validate_ai_candidates_keeps_ambiguous_year_rejection_when_year_unknown():
    evidence = "Dune (1984); Dune (2021) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "Unknown", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["ambiguous-year"]


def test_validate_ai_candidates_rejects_year_absent_from_evidence():
    evidence = "Dune Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], evidence, diagnostics) == []
    assert diagnostics == ["year-not-in-evidence"]


def test_validate_ai_candidates_rejects_missing_affirmative_fel():
    candidate = compare.FoundCandidate("Dune", "2021", "https://src.test", "Dune", "ai")
    diagnostics: list[str] = []
    assert compare.validate_ai_candidates([candidate], "Dune", diagnostics) == []
    assert diagnostics == ["missing-affirmative-fel"]


def test_validate_ai_candidates_rejects_evidence_not_found_in_source():
    evidence = "Dune (2021) Profile 7 FEL"
    candidate = compare.FoundCandidate(
        "Dune", "2021", "https://src.test", evidence, "ai"
    )
    diagnostics: list[str] = []

    assert (
        compare.validate_ai_candidates([candidate], "Different source", diagnostics)
        == []
    )
    assert diagnostics == ["evidence-not-found"]


_AI_SOURCE_URL = "https://forum.example.test/thread"
_AI_ITEM = {
    "title": "The Matrix",
    "year": "1999",
    "evidence": "Profile 7 FEL",
}


def _ai_client_for_body(
    body: str | bytes,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> compare.AIClient:
    client = compare.AIClient(
        compare.AISettings(
            api_key="secret-token",
            base_url="https://api.example.test/codex",
        )
    )
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                content=body,
                headers=headers,
                request=request,
            )
        )
    )
    return client


_INVALID_CANDIDATE_FIELD_VALUES = [
    {"unexpected": "object"},
    ["unexpected", "list"],
    7,
    False,
    None,
]
_MALFORMED_RESPONSE_SHAPES = [
    None,
    7,
    True,
    "scalar",
    [],
    [None, "bad-entry"],
    {"output": None},
    {"output": "not-a-list"},
    {"output": {"content": []}},
    {"output": [None, "bad-entry"]},
    {"output": [{"content": None}]},
    {"output": [{"content": "not-a-list"}]},
    {"output": [{"content": {"text": "not-a-list"}}]},
    {"output": [{"content": [None, "bad-entry"]}]},
    {"output": [{"content": [{"type": "output_text", "text": None}]}]},
]


@pytest.mark.parametrize("response_shape", _MALFORMED_RESPONSE_SHAPES)
def test_candidate_api_returns_empty_for_malformed_success_shape(response_shape):
    assert (
        compare._candidates_from_ai_response_text(
            json.dumps(response_shape),
            _AI_SOURCE_URL,
        )
        == []
    )


@pytest.mark.parametrize("response_shape", _MALFORMED_RESPONSE_SHAPES)
def test_completion_api_returns_empty_for_malformed_success_shape(response_shape):
    assert compare._extract_response_text(json.dumps(response_shape)) == ""


@pytest.mark.parametrize(
    "status",
    [
        "failed",
        "incomplete",
        "cancelled",
        "canceled",
        "error",
        "in_progress",
        None,
        7,
    ],
)
@pytest.mark.parametrize("operation", ["extract_candidates", "complete"])
def test_ai_response_rejects_explicit_noncompleted_json_status(status, operation):
    if operation == "extract_candidates":
        body = json.dumps({"status": status, "items": [_AI_ITEM]})
        assert compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL) == []
    else:
        body = json.dumps({"status": status, "output_text": "must-not-be-used"})
        assert compare._extract_response_text(body) == ""

    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            if operation == "extract_candidates":
                client.extract_candidates(_AI_SOURCE_URL, "source")
            else:
                client.complete("system", "user")
    finally:
        client.close()


def test_ai_response_accepts_explicit_completed_json_status():
    candidate_body = json.dumps({"status": "completed", "items": [_AI_ITEM]})
    completion_body = json.dumps({"status": "completed", "output_text": "done"})

    candidate_client = _ai_client_for_body(candidate_body)
    completion_client = _ai_client_for_body(completion_body)
    try:
        assert [
            candidate.title
            for candidate in candidate_client.extract_candidates(
                _AI_SOURCE_URL, "source"
            )
        ] == ["The Matrix"]
        assert completion_client.complete("system", "user") == "done"
    finally:
        candidate_client.close()
        completion_client.close()


@pytest.mark.parametrize("status", ["completed", None])
@pytest.mark.parametrize(
    "contradiction",
    [
        {"error": {"message": "private error"}},
        {"incomplete_details": {"reason": "max_output_tokens"}},
    ],
)
def test_nonstream_response_rejects_error_or_incomplete_contradiction(
    status, contradiction
):
    payload = {"output_text": "must-not-be-used", **contradiction}
    if status is not None:
        payload["status"] = status
    body = json.dumps(payload)

    assert compare._extract_response_text(body) == ""
    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


def test_nonstream_completed_response_accepts_null_error_and_incomplete_details():
    body = json.dumps(
        {
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output_text": "done",
        }
    )

    assert compare._extract_response_text(body) == "done"


def test_ai_candidate_payload_rejects_explicit_noncompleted_nested_status():
    body = json.dumps(
        {
            "status": "completed",
            "output_text": json.dumps({"status": "failed", "items": [_AI_ITEM]}),
        }
    )

    assert compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL) == []
    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.extract_candidates(_AI_SOURCE_URL, "source")
    finally:
        client.close()


@pytest.mark.parametrize(
    "payload",
    [
        [_AI_ITEM],
        {"items": [_AI_ITEM]},
        [None, "bad-entry", 7, _AI_ITEM],
    ],
)
def test_candidate_api_accepts_direct_candidate_payload(payload):
    candidates = compare._candidates_from_ai_response_text(
        json.dumps(payload),
        _AI_SOURCE_URL,
    )

    assert [(item.title, item.year, item.evidence) for item in candidates] == [
        ("The Matrix", "1999", "Profile 7 FEL")
    ]


@pytest.mark.parametrize("field", ["title", "year", "evidence"])
@pytest.mark.parametrize(
    "invalid_value",
    _INVALID_CANDIDATE_FIELD_VALUES,
    ids=["object", "list", "number", "boolean", "null"],
)
def test_candidate_api_rejects_item_with_non_string_field(field, invalid_value):
    invalid_item = dict(_AI_ITEM)
    invalid_item[field] = invalid_value

    candidates = compare._candidates_from_ai_response_text(
        json.dumps([invalid_item, _AI_ITEM]),
        _AI_SOURCE_URL,
    )

    assert [(item.title, item.year, item.evidence) for item in candidates] == [
        ("The Matrix", "1999", "Profile 7 FEL")
    ]


@pytest.mark.parametrize(
    "invalid_item",
    [
        {"year": "2024", "evidence": "Profile 7 FEL"},
        {"title": "", "year": "2024", "evidence": "Profile 7 FEL"},
        {"title": "   ", "year": "2024", "evidence": "Profile 7 FEL"},
    ],
)
def test_candidate_api_requires_nonblank_string_title(invalid_item):
    candidates = compare._candidates_from_ai_response_text(
        json.dumps([invalid_item, _AI_ITEM]),
        _AI_SOURCE_URL,
    )

    assert [item.title for item in candidates] == ["The Matrix"]


def test_candidate_api_preserves_omitted_optional_field_defaults():
    candidates = compare._candidates_from_ai_response_text(
        json.dumps([{"title": "Dune"}]),
        _AI_SOURCE_URL,
    )

    assert [(item.title, item.year, item.evidence) for item in candidates] == [
        ("Dune", "Unknown", "")
    ]


@pytest.mark.parametrize(
    "items",
    [
        [{"title": "", "year": "2024", "evidence": "Profile 7 FEL"}],
        [{"title": "   ", "year": "2024", "evidence": "Profile 7 FEL"}],
        [{"title": "Dune", "year": 2021, "evidence": "Profile 7 FEL"}],
        [None, 7, "invalid"],
    ],
)
def test_ai_client_rejects_invalid_only_nonempty_candidate_lists(items):
    body = json.dumps({"items": items})
    assert compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL) == []

    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.extract_candidates(_AI_SOURCE_URL, "source")
    finally:
        client.close()


def test_ai_client_accepts_mixed_candidate_list_and_skips_invalid_items():
    body = json.dumps(
        {
            "items": [
                None,
                {"title": " ", "year": "2024", "evidence": "invalid"},
                _AI_ITEM,
            ]
        }
    )
    client = _ai_client_for_body(body)
    try:
        assert [
            candidate.title
            for candidate in client.extract_candidates(_AI_SOURCE_URL, "source")
        ] == ["The Matrix"]
    finally:
        client.close()


def test_ai_candidate_item_limit_accepts_limit_and_rejects_limit_plus_one():
    limit = compare.MAX_AI_CANDIDATE_ITEMS
    item = {"title": "Dune", "year": "2021", "evidence": "Profile 7 FEL"}
    at_limit_client = _ai_client_for_body(json.dumps({"items": [item] * limit}))
    over_limit_client = _ai_client_for_body(json.dumps({"items": [item] * (limit + 1)}))
    try:
        assert (
            len(at_limit_client.extract_candidates(_AI_SOURCE_URL, "source")) == limit
        )
        with pytest.raises(compare.AIResponseFormatError):
            over_limit_client.extract_candidates(_AI_SOURCE_URL, "source")
    finally:
        at_limit_client.close()
        over_limit_client.close()


def test_sse_candidate_payload_uses_shared_strict_field_validation():
    invalid_item = dict(_AI_ITEM)
    invalid_item["year"] = {"unexpected": "object"}
    payload_text = json.dumps({"items": [invalid_item, _AI_ITEM]})
    body = "data: " + json.dumps(
        {
            "type": "response.output_text.done",
            "text": payload_text,
        }
    )

    candidates = compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)

    assert [(item.title, item.year, item.evidence) for item in candidates] == [
        ("The Matrix", "1999", "Profile 7 FEL")
    ]


@pytest.mark.parametrize("items", [None, {"title": "wrong-shape"}, "scalar", 7])
def test_candidate_api_returns_empty_for_non_list_items(items):
    assert (
        compare._candidates_from_ai_response_text(
            json.dumps({"items": items}),
            _AI_SOURCE_URL,
        )
        == []
    )


def test_response_shape_skips_bad_entries_and_retains_later_valid_content():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    body = json.dumps(
        {
            "output": [
                None,
                "bad-output",
                {
                    "content": [
                        None,
                        "bad-content",
                        {"text": payload_text},
                    ]
                },
            ]
        }
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        item.title
        for item in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


_OUTPUT_CONTENT_FAILURE_CONTRADICTIONS = [
    {"status": "incomplete"},
    {"status": "failed"},
    {"error": {"message": "private error"}},
    {"incomplete_details": {"reason": "max_output_tokens"}},
]
_OUTPUT_CONTENT_FAILURE_IDS = [
    "incomplete-status",
    "failed-status",
    "error-details",
    "incomplete-details",
]


@pytest.mark.parametrize(
    "content",
    [
        {"type": "output_text", "text": "done"},
        {
            "type": "output_text",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "text": "done",
        },
    ],
    ids=["missing-status", "completed-with-null-details"],
)
def test_output_text_content_helper_accepts_valid_status_shapes(content):
    assert compare._parse_output_text_content(content) == ("done", True)


@pytest.mark.parametrize(
    "contradiction",
    _OUTPUT_CONTENT_FAILURE_CONTRADICTIONS,
    ids=_OUTPUT_CONTENT_FAILURE_IDS,
)
def test_output_text_content_helper_rejects_status_or_failure_contradiction(
    contradiction,
):
    content = {"type": "output_text", "text": "must-not-count", **contradiction}

    assert compare._parse_output_text_content(content) == ("", False)


@pytest.mark.parametrize(
    "contradiction",
    _OUTPUT_CONTENT_FAILURE_CONTRADICTIONS,
    ids=_OUTPUT_CONTENT_FAILURE_IDS,
)
def test_nonstream_completed_response_rejects_content_failure_contradiction(
    contradiction,
):
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "must-not-count",
                            **contradiction,
                        }
                    ],
                }
            ],
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize(
    "contradiction",
    _OUTPUT_CONTENT_FAILURE_CONTRADICTIONS,
    ids=_OUTPUT_CONTENT_FAILURE_IDS,
)
def test_ai_client_rejects_content_status_or_failure_contradiction(contradiction):
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "must-not-count",
                            **contradiction,
                        }
                    ],
                }
            ],
        }
    )
    client = _ai_client_for_body(body)

    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


@pytest.mark.parametrize(
    "item_status", ["incomplete", "failed", "in_progress", None, 7]
)
def test_nonstream_completed_response_rejects_noncompleted_output_item_status(
    item_status,
):
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": item_status,
                    "content": [{"type": "output_text", "text": "must-not-count"}],
                }
            ],
        }
    )

    assert compare._extract_response_text(body) == ""
    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


@pytest.mark.parametrize("content_type", ["input_text", "reasoning", "custom_part"])
def test_nonstream_response_rejects_non_output_content_only(content_type):
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [{"type": content_type, "text": "must-not-count"}],
                }
            ],
        }
    )

    assert compare._extract_response_text(body) == ""


def test_nonstream_response_rejects_non_output_item_only():
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "must-not-count"}],
                }
            ],
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize(
    "output",
    [
        {
            "type": "message",
            "status": "completed",
            "content": [{"type": "output_text", "text": "done"}],
        },
        {"content": [{"text": "done"}]},
    ],
    ids=["official-types", "legacy-omissions"],
)
def test_nonstream_response_accepts_output_text_and_legacy_type_omissions(output):
    assert compare._extract_response_text(json.dumps({"output": [output]})) == "done"


def test_nonstream_response_skips_non_output_item_when_valid_message_follows():
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "status": "completed"},
                {
                    "type": "message",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            ],
        }
    )

    assert compare._extract_response_text(body) == "done"


def test_candidates_from_ai_response_accepts_responses_sse():
    body = "\n".join(
        [
            "event: response.created",
            'data: {"type":"response.created","response":{"status":"in_progress"}}',
            "",
            "event: response.output_text.done",
            (
                'data: {"type":"response.output_text.done","text":'
                '"{\\"items\\":[{\\"title\\":\\"The Matrix\\",'
                '\\"year\\":\\"1999\\",\\"evidence\\":\\"Profile 7 FEL\\"}]}" }'
            ),
            "",
            "event: response.content_part.done",
            (
                'data: {"type":"response.content_part.done","part":{"text":'
                '"{\\"items\\":[{\\"title\\":\\"The Matrix\\",'
                '\\"year\\":\\"1999\\",\\"evidence\\":\\"Profile 7 FEL\\"}]}" }}'
            ),
            "",
        ]
    )

    candidates = compare._candidates_from_ai_response_text(
        body,
        "https://forum.example.test/thread",
    )

    assert candidates == [
        compare.FoundCandidate(
            title="The Matrix",
            year="1999",
            source_url="https://forum.example.test/thread",
            evidence="Profile 7 FEL",
            extraction_method="ai",
        )
    ]


def test_data_only_sse_skips_bad_records_and_uses_safe_payload_type():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    body = "\n\n".join(
        [
            "data: not-json",
            "data: null",
            "data: []",
            'data: {"type":null,"text":"must-not-be-used"}',
            (
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_item.done",
                        "item": {"content": None},
                    }
                )
            ),
            (
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_text.done",
                        "text": payload_text,
                    }
                )
            ),
            "data: [DONE]",
        ]
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        item.title
        for item in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


def test_sse_buffers_multiline_data_until_record_boundary():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    event_json = json.dumps(
        {"type": "response.output_text.done", "text": payload_text},
        separators=(",", ":"),
    )
    split_at = event_json.index(',"text"')
    body = "\n".join(
        [
            "event: response.output_text.done",
            f"data: {event_json[:split_at]}",
            f"data: {event_json[split_at:]}",
        ]
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        item.title
        for item in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


def test_sse_concatenates_ordered_distinct_output_text_done_parts():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    split_at = len(payload_text) // 2

    def done_event(text: str, content_index: int) -> str:
        return "\n".join(
            [
                "event: response.output_text.done",
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_text.done",
                        "item_id": "message-1",
                        "output_index": 0,
                        "content_index": content_index,
                        "text": text,
                    }
                ),
            ]
        )

    body = "\n\n".join(
        [
            done_event(payload_text[:split_at], 0),
            done_event(payload_text[:split_at], 0),
            done_event(payload_text[split_at:], 1),
        ]
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        item.title
        for item in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


def test_sse_keeps_equal_done_text_from_distinct_official_coordinates():
    def event(content_index: int) -> str:
        return "data: " + json.dumps(
            {
                "type": "response.output_text.done",
                "item_id": "message-1",
                "output_index": 0,
                "content_index": content_index,
                "text": "same",
            }
        )

    assert (
        compare._extract_response_text("\n\n".join([event(0), event(1)])) == "samesame"
    )


def test_sse_does_not_dedupe_equal_done_text_without_official_coordinates():
    event = "data: " + json.dumps({"type": "response.output_text.done", "text": "same"})

    assert compare._extract_response_text(f"{event}\n\n{event}") == "samesame"


def test_sse_dedupes_equivalent_direct_and_nested_item_coordinates():
    nested_coordinate = "data: " + json.dumps(
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"id": "message-1", "content": [{"text": "same"}]},
        }
    )
    direct_coordinate = "data: " + json.dumps(
        {
            "type": "response.output_item.done",
            "item_id": "message-1",
            "output_index": 0,
            "item": {"content": [{"text": "same"}]},
        }
    )

    assert (
        compare._extract_response_text(f"{nested_coordinate}\n\n{direct_coordinate}")
        == "same"
    )


def test_sse_concatenates_all_output_item_content_text():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    split_at = len(payload_text) // 2
    body = "\n".join(
        [
            "event: response.output_item.done",
            "data: "
            + json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "content": [
                            {"type": "output_text", "text": payload_text[:split_at]},
                            {"type": "output_text", "text": payload_text[split_at:]},
                        ]
                    },
                }
            ),
        ]
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        item.title
        for item in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


@pytest.mark.parametrize(
    "item_status", ["incomplete", "failed", "in_progress", None, 7]
)
def test_sse_output_item_done_rejects_noncompleted_item_status(item_status):
    body = "data: " + json.dumps(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "status": item_status,
                "content": [{"type": "output_text", "text": "must-not-count"}],
            },
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize("content_type", ["input_text", "reasoning", "custom_part"])
def test_sse_output_item_done_rejects_non_output_content_only(content_type):
    body = "data: " + json.dumps(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "status": "completed",
                "content": [{"type": content_type, "text": "must-not-count"}],
            },
        }
    )

    assert compare._extract_response_text(body) == ""


def test_sse_output_item_done_rejects_non_output_item_type():
    body = "data: " + json.dumps(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "status": "completed",
                "content": [{"type": "output_text", "text": "must-not-count"}],
            },
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize("part_type", ["input_text", "reasoning", "custom_part"])
def test_sse_content_part_done_rejects_non_output_part_type(part_type):
    body = "data: " + json.dumps(
        {
            "type": "response.content_part.done",
            "part": {"type": part_type, "text": "must-not-count"},
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize(
    "contradiction",
    _OUTPUT_CONTENT_FAILURE_CONTRADICTIONS,
    ids=_OUTPUT_CONTENT_FAILURE_IDS,
)
def test_sse_content_part_done_rejects_status_or_failure_contradiction(
    contradiction,
):
    body = "data: " + json.dumps(
        {
            "type": "response.content_part.done",
            "part": {
                "type": "output_text",
                "text": "must-not-count",
                **contradiction,
            },
        }
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize(
    "part",
    [
        {"type": "output_text", "text": "done"},
        {"text": "done"},
    ],
    ids=["official-output-text", "legacy-type-omission"],
)
def test_sse_content_part_done_accepts_output_text_and_legacy_type_omission(part):
    body = "data: " + json.dumps({"type": "response.content_part.done", "part": part})

    assert compare._extract_response_text(body) == "done"


def test_sse_skips_header_payload_type_mismatch_and_keeps_later_valid_event():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    valid_event = "\n".join(
        [
            "event: response.output_text.done",
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": payload_text}),
        ]
    )
    mismatched_event = "\n".join(
        [
            "event: response.created",
            "data: "
            + json.dumps(
                {
                    "type": "response.output_text.done",
                    "text": "must-not-replace-valid-output",
                }
            ),
        ]
    )

    body = f"{valid_event}\n\n{mismatched_event}"

    assert compare._extract_response_text(body) == payload_text


def test_sse_done_output_takes_precedence_over_delta_and_fallback_output():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.delta", "delta": payload_text}),
            "data: "
            + json.dumps(
                {
                    "type": "response.content_part.done",
                    "part": {"text": "fallback-must-not-be-appended"},
                }
            ),
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": payload_text}),
        ]
    )

    assert compare._extract_response_text(body) == payload_text


def test_sse_completed_response_is_authoritative_fallback_before_delta():
    payload_text = json.dumps({"items": [_AI_ITEM]})
    split_at = len(payload_text) // 2
    body = "\n\n".join(
        [
            "data: "
            + json.dumps(
                {"type": "response.output_text.delta", "delta": "stale-delta"}
            ),
            "event: response.completed\n"
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "content": [
                                    {"text": payload_text[:split_at]},
                                    {"text": payload_text[split_at:]},
                                ]
                            }
                        ],
                    },
                }
            ),
        ]
    )

    assert compare._extract_response_text(body) == payload_text
    assert [
        candidate.title
        for candidate in compare._candidates_from_ai_response_text(body, _AI_SOURCE_URL)
    ] == ["The Matrix"]


def test_sse_done_output_precedes_completed_response_fallback():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "done-output"}),
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output_text": "completed-fallback",
                    },
                }
            ),
        ]
    )

    assert compare._extract_response_text(body) == "done-output"


@pytest.mark.parametrize(
    "terminal_type",
    [
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "response.canceled",
        "response.error",
        "error",
    ],
)
def test_sse_terminal_failure_invalidates_prior_text(terminal_type):
    body = "\n\n".join(
        [
            "data: "
            + json.dumps(
                {"type": "response.output_text.done", "text": "private-output"}
            ),
            "data: " + json.dumps({"type": terminal_type, "error": "private-body"}),
        ]
    )

    assert compare._extract_response_text(body) == ""
    client = _ai_client_for_body(body)
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


@pytest.mark.parametrize(
    "terminal_header",
    [
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "error",
    ],
)
@pytest.mark.parametrize(
    "terminal_data",
    [
        "not-json",
        "[DONE]",
        json.dumps({"type": "response.output_text.done", "text": "mismatched-output"}),
    ],
    ids=["malformed-json", "done-sentinel", "mismatched-payload-type"],
)
def test_sse_terminal_failure_header_is_fatal_before_payload_parsing(
    terminal_header, terminal_data
):
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior-output"}),
            f"event: {terminal_header}\ndata: {terminal_data}",
        ]
    )

    assert compare._extract_response_text(body) == ""


def test_ai_client_rejects_stream_with_mismatched_terminal_failure_header():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior-output"}),
            "event: response.failed\ndata: "
            + json.dumps({"type": "response.output_text.done", "text": "mismatch"}),
        ]
    )
    client = _ai_client_for_body(body)

    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


@pytest.mark.parametrize(
    "terminal_data",
    [
        "not-json",
        "[DONE]",
        "null",
        "[]",
        json.dumps({"type": "response.output_text.done", "text": "mismatched-output"}),
        json.dumps(
            {
                "type": "response.completed",
                "response": {"status": "failed", "output_text": "invalid"},
            }
        ),
    ],
    ids=[
        "malformed-json",
        "done-sentinel",
        "null-payload",
        "list-payload",
        "mismatched-payload-type",
        "invalid-nested-response",
    ],
)
def test_sse_completed_header_is_fatal_when_terminal_payload_is_invalid(
    terminal_data,
):
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior"}),
            f"event: response.completed\ndata: {terminal_data}",
        ]
    )

    assert compare._extract_response_text(body) == ""


def test_sse_completed_header_without_data_invalidates_prior_text():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior"}),
            "event: response.completed",
        ]
    )

    assert compare._extract_response_text(body) == ""


def test_sse_payload_failure_is_fatal_before_header_type_mismatch_filtering():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior"}),
            "event: response.created\ndata: "
            + json.dumps({"type": "response.failed", "error": "private"}),
        ]
    )

    assert compare._extract_response_text(body) == ""


def test_ai_client_rejects_malformed_completed_terminal_header():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior"}),
            "event: response.completed\ndata: [DONE]",
        ]
    )
    client = _ai_client_for_body(body)

    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


def test_sse_terminal_failure_header_without_data_is_fatal():
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior-output"}),
            "event: response.failed",
        ]
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize("status", ["failed", "incomplete", None, 7])
def test_sse_completed_event_requires_completed_response_status(status):
    body = "\n\n".join(
        [
            "data: "
            + json.dumps(
                {"type": "response.output_text.done", "text": "private-output"}
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {"status": status, "output_text": "must-not-win"},
                }
            ),
        ]
    )

    assert compare._extract_response_text(body) == ""


@pytest.mark.parametrize(
    "nested_response",
    [
        {"output_text": "missing-status"},
        {
            "status": "completed",
            "error": {"message": "private error"},
            "output_text": "must-not-win",
        },
        {
            "status": "completed",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": "must-not-win",
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "incomplete",
                    "content": [{"type": "output_text", "text": "must-not-win"}],
                }
            ],
        },
        None,
        [],
    ],
    ids=[
        "missing-status",
        "non-null-error",
        "incomplete-details",
        "incomplete-output-item",
        "null-response",
        "non-dict-response",
    ],
)
def test_sse_completed_event_requires_consistent_completed_response(nested_response):
    body = "\n\n".join(
        [
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "prior-output"}),
            "data: "
            + json.dumps({"type": "response.completed", "response": nested_response}),
        ]
    )

    assert compare._extract_response_text(body) == ""


def test_sse_completed_event_accepts_null_error_and_incomplete_details():
    body = "data: " + json.dumps(
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output_text": "done",
            },
        }
    )

    assert compare._extract_response_text(body) == "done"


def test_sse_strips_exactly_one_leading_bom():
    event = "data: " + json.dumps(
        {"type": "response.output_text.done", "text": "result"}
    )

    assert compare._extract_response_text("\ufeff" + event) == "result"
    assert compare._extract_response_text("\ufeff\ufeff" + event) == ""


def test_sse_records_are_generated_lazily():
    records = compare._sse_records('data: {"type":"response.created"}\n')

    assert iter(records) is records
    assert list(records) == [
        ("", '{"type":"response.created"}'),
    ]


@pytest.mark.parametrize(
    "hostile_json",
    [
        "9" * 5000,
        "[" * 1500 + "]" * 1500,
    ],
    ids=["huge-integer", "deep-nesting"],
)
def test_ai_response_helpers_tolerate_hostile_json(hostile_json):
    assert compare._extract_response_text(hostile_json) == ""
    assert compare._candidates_from_ai_response_text(hostile_json, _AI_SOURCE_URL) == []


@pytest.mark.parametrize(
    "hostile_json",
    [
        "9" * 5000,
        "[" * 1500 + "]" * 1500,
    ],
    ids=["huge-integer", "deep-nesting"],
)
def test_sse_skips_hostile_json_record_and_keeps_later_valid_event(hostile_json):
    payload_text = json.dumps({"items": [_AI_ITEM]})
    body = "\n\n".join(
        [
            f"data: {hostile_json}",
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": payload_text}),
        ]
    )

    assert compare._extract_response_text(body) == payload_text


@pytest.mark.parametrize("operation", ["extract_candidates", "complete"])
def test_ai_client_malformed_http_200_raises_redacted_format_error(operation, capsys):
    response_body = "malformed-private-response-body"
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, text=response_body)

    ai_client = compare.AIClient(
        compare.AISettings(
            api_key="secret-token",
            base_url="https://api.example.test/codex",
        )
    )
    ai_client.client.close()
    ai_client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(compare.AIResponseFormatError) as exc_info:
            if operation == "extract_candidates":
                ai_client.extract_candidates(_AI_SOURCE_URL, "source text")
            else:
                ai_client.complete("system", "user")
    finally:
        ai_client.close()

    assert isinstance(exc_info.value, httpx.HTTPError)
    assert request_count == 1
    captured = capsys.readouterr()
    diagnostic_text = "\n".join(
        [str(exc_info.value), repr(exc_info.value), captured.out, captured.err]
    )
    assert response_body not in diagnostic_text
    assert "secret-token" not in diagnostic_text


@pytest.mark.parametrize("operation", ["extract_candidates", "complete"])
@pytest.mark.parametrize(
    "response_body",
    ["9" * 5000, "[" * 1500 + "]" * 1500],
    ids=["huge-integer", "deep-nesting"],
)
def test_ai_client_hostile_json_raises_format_error(operation, response_body):
    ai_client = compare.AIClient(
        compare.AISettings(
            api_key="secret-token",
            base_url="https://api.example.test/codex",
        )
    )
    ai_client.client.close()
    ai_client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=response_body)
        )
    )
    try:
        with pytest.raises(compare.AIResponseFormatError):
            if operation == "extract_candidates":
                ai_client.extract_candidates(_AI_SOURCE_URL, "source text")
            else:
                ai_client.complete("system", "user")
    finally:
        ai_client.close()


@pytest.mark.parametrize("response_kind", ["json", "sse"])
def test_ai_client_accepts_large_valid_response_at_byte_limit(response_kind):
    limit = compare.MAX_AI_RESPONSE_BYTES
    if response_kind == "json":
        base = json.dumps({"status": "completed", "output_text": "done"}).encode()
        body = base + b" " * (limit - len(base))
    else:
        base = (
            "data: "
            + json.dumps({"type": "response.output_text.done", "text": "done"})
            + "\n\n:"
        ).encode()
        body = base + b"x" * (limit - len(base))

    client = _ai_client_for_body(body)
    try:
        assert client.complete("system", "user") == "done"
    finally:
        client.close()


def test_ai_client_rejects_oversized_content_length_before_reading_stream():
    class TrackingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            yield b'{"output_text":"done"}'

    stream = TrackingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(compare.MAX_AI_RESPONSE_BYTES + 1)},
            stream=stream,
            request=request,
        )

    client = _ai_client_for_body("unused")
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()

    assert stream.iterations == 0


def test_ai_client_caps_decoded_stream_bytes_not_only_content_length(monkeypatch):
    monkeypatch.setattr(compare, "MAX_AI_RESPONSE_BYTES", 128)
    decoded = json.dumps({"output_text": "x" * 256}).encode()
    compressed = gzip.compress(decoded)
    assert len(compressed) < 128

    client = _ai_client_for_body(
        compressed,
        headers={
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        },
    )
    try:
        with pytest.raises(compare.AIResponseFormatError):
            client.complete("system", "user")
    finally:
        client.close()


@pytest.mark.parametrize(
    "status_code",
    [
        300,
        301,
        302,
        307,
        308,
        400,
        401,
        403,
        404,
        405,
        406,
        407,
        410,
        415,
        418,
        422,
        499,
        501,
        505,
        511,
    ],
)
def test_ai_client_raises_redacted_global_error_for_configuration_status(status_code):
    private_body = "private configuration response"
    client = _ai_client_for_body(private_body, status_code=status_code)
    try:
        with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
            client.complete("system", "user")
    finally:
        client.close()

    diagnostic = str(exc_info.value) + repr(exc_info.value)
    assert exc_info.value.status_code == status_code
    assert private_body not in diagnostic
    assert "secret-token" not in diagnostic


def test_global_ai_http_error_preserves_already_redacted_error():
    error = compare.AIGlobalHTTPError(401)

    assert compare.global_ai_http_error(error) is error


@pytest.mark.parametrize("status_code", [408, 409, 425, 429, 500, 502, 503, 504])
def test_retryable_http_status_is_not_classified_global(status_code):
    request = httpx.Request("POST", "https://api.example.test/responses")
    response = httpx.Response(status_code, request=request)
    error = httpx.HTTPStatusError(
        "transient",
        request=request,
        response=response,
    )

    assert compare.global_ai_http_error(error) is None


@pytest.mark.parametrize(
    "base_url",
    ["", "://bad", "http://[]", "\n"],
    ids=["blank", "unsupported-protocol", "invalid-ipv6", "invalid-character"],
)
def test_ai_client_converts_invalid_base_url_to_redacted_global_error(base_url):
    client = compare.AIClient(
        compare.AISettings(api_key="secret-key", base_url=base_url)
    )
    try:
        with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
            client.complete("system", "user")
    finally:
        client.close()

    assert exc_info.value.status_code is None
    diagnostic = str(exc_info.value) + repr(exc_info.value)
    assert "secret-key" not in diagnostic
    if stripped_base_url := base_url.strip():
        assert stripped_base_url not in diagnostic


def test_ai_client_converts_proxy_configuration_error_to_redacted_global_error(
    monkeypatch,
):
    private_proxy = "private-proxy-credential"

    def invalid_proxy_client(*args, **kwargs):
        raise ValueError(private_proxy)

    monkeypatch.setattr(compare.httpx, "Client", invalid_proxy_client)

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        compare.AIClient(compare.AISettings(api_key="secret-key"))

    diagnostic = str(exc_info.value) + repr(exc_info.value)
    assert exc_info.value.status_code is None
    assert private_proxy not in diagnostic
    assert "secret-key" not in diagnostic


@pytest.mark.parametrize(
    "operation,response_body",
    [
        ("extract_candidates", "[]"),
        ("extract_candidates", '{"items":[]}'),
        ("extract_candidates", '{"output":[]}'),
        ("complete", '{"output":[]}'),
        ("complete", '{"output_text":""}'),
    ],
)
def test_ai_client_accepts_structurally_valid_empty_output(operation, response_body):
    ai_client = compare.AIClient(
        compare.AISettings(
            api_key="secret-token",
            base_url="https://api.example.test/codex",
        )
    )
    ai_client.client.close()
    ai_client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=response_body)
        )
    )
    try:
        if operation == "extract_candidates":
            assert ai_client.extract_candidates(_AI_SOURCE_URL, "source text") == []
        else:
            assert ai_client.complete("system", "user") == ""
    finally:
        ai_client.close()


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (None, "AIResponseFormatError\n"),
        (503, "HTTPStatusError status=503\n"),
    ],
)
def test_compare_ai_error_diagnostic_contains_only_safe_type_and_status(
    status_code, expected, tmp_path, monkeypatch, capsys
):
    source_secret = "credential-in-source-url"
    source_url = f"https://user:{source_secret}@forum.example.test/thread?key=private"
    response_body = "private-malformed-response-body"

    class FakeFetchResult:
        text = "source text"
        error = None

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url, *, raise_on_error=True):
            return FakeFetchResult()

    class MalformedAIClient:
        def extract_candidates(self, source_url, text):
            if status_code is None:
                error = compare.AIResponseFormatError()
                error.__cause__ = ValueError(response_body)
                raise error
            request = httpx.Request("POST", source_url)
            response = httpx.Response(status_code, text=response_body, request=request)
            raise httpx.HTTPStatusError(
                response_body,
                request=request,
                response=response,
            )

    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    cache_dir = tmp_path / "cache"

    assert (
        compare._extract_ai_candidates([source_url], cache_dir, MalformedAIClient())
        == []
    )

    diagnostic = (tmp_path / "ai_compare_errors.txt").read_text(encoding="utf-8")
    emitted = diagnostic + capsys.readouterr().err
    assert diagnostic == expected
    assert response_body not in emitted
    assert source_secret not in emitted
    assert "?key=private" not in emitted


@pytest.mark.parametrize("status_code", [301, 308, 406, 407, 415, 501, 505, 511])
def test_compare_ai_extraction_stops_after_first_global_http_error(
    status_code, tmp_path, monkeypatch
):
    calls: list[str] = []
    request = httpx.Request("POST", "https://api.example.test/responses")
    response = httpx.Response(status_code, text="private body", request=request)

    class FakeFetchResult:
        text = "source"
        error = None

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url, *, raise_on_error=True):
            return FakeFetchResult()

    class AuthFailureClient:
        def extract_candidates(self, source_url, text):
            calls.append(source_url)
            raise httpx.HTTPStatusError(
                "private body",
                request=request,
                response=response,
            )

    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    source_urls = ["https://one.test", "https://two.test"]

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        compare._extract_ai_candidates(
            source_urls, tmp_path / "cache", AuthFailureClient()
        )

    assert exc_info.value.status_code == status_code
    assert calls == ["https://one.test"]


def test_candidates_from_ai_response_strips_list_ordinals():
    body = json.dumps(
        {
            "output_text": json.dumps(
                {
                    "items": [
                        {
                            "title": "281 Nobody",
                            "year": "2021",
                            "evidence": "281 Nobody (2021)",
                        }
                    ]
                }
            )
        }
    )

    candidates = compare._candidates_from_ai_response_text(
        body,
        "https://forum.example.test/thread",
    )

    assert [candidate.title for candidate in candidates] == ["Nobody"]


def test_compare_found_with_ai_fetches_sources_and_marks_origin(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    source_url = "https://forum.example.test/thread"
    sources_path.write_text(f"{source_url}\n", encoding="utf-8")

    class FakeFetchResult:
        url = source_url
        text = "The Matrix (1999) is Profile 7 FEL."
        error = None

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str, *, raise_on_error: bool = True):
            assert url == source_url
            return FakeFetchResult()

    class FakeAIClient:
        def __init__(self, settings: compare.AISettings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_candidates(self, source_url: str, text: str):
            assert text == FakeFetchResult.text
            return [
                compare.FoundCandidate(
                    title="The Matrix",
                    year="1999",
                    source_url=source_url,
                    evidence="The Matrix (1999) is Profile 7 FEL.",
                    extraction_method="ai",
                )
            ]

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(compare, "AIClient", FakeAIClient)
    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=True,
    )

    assert summary == {
        "AI_found": 1,
        "PY_found": 1,
        "overlap": 1,
        "AI_only": 0,
        "PY_only": 0,
    }
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread\n"
    )


def test_compare_found_with_ai_prefers_expanded_source_list(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    expanded_url = "https://forum.example.test/thread?page=2"
    cache_dir.parent.mkdir(parents=True)
    cache_dir.parent.joinpath("ai_expanded_urls.txt").write_text(
        f"{expanded_url}\n", encoding="utf-8"
    )
    fetched_urls = []

    class FakeFetchResult:
        def __init__(self, url: str):
            self.url = url
            self.text = "The Matrix (1999) is Profile 7 FEL."
            self.error = None

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str, *, raise_on_error: bool = True):
            fetched_urls.append(url)
            return FakeFetchResult(url)

    class FakeAIClient:
        def __init__(self, settings: compare.AISettings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_candidates(self, source_url: str, text: str):
            return [
                compare.FoundCandidate(
                    title="The Matrix",
                    year="1999",
                    source_url=source_url,
                    evidence=text,
                    extraction_method="ai",
                )
            ]

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text("[]\n", encoding="utf-8")
        return 0

    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(compare, "AIClient", FakeAIClient)
    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=True,
    )

    assert fetched_urls == [expanded_url]
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=0% | source=https://forum.example.test/thread?page=2\n"
    )


def test_compare_found_reads_legacy_ai_text_and_recovers_source_from_cache(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    source_url = "https://forum.example.test/thread?page=2"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    output_dir.mkdir()
    output_dir.joinpath("AI_found.txt").write_text(
        "The Matrix (1999)\n", encoding="utf-8"
    )
    cache_dir.mkdir(parents=True)
    cache_dir.parent.joinpath("ai_expanded_urls.txt").write_text(
        f"{source_url}\n", encoding="utf-8"
    )
    cache_key = f"public\0{source_url}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cache_dir.joinpath(f"{digest}.html").write_text(
        "<html>The Matrix (1999) is Profile 7 FEL.</html>",
        encoding="utf-8",
    )

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=False,
    )

    assert summary == {
        "AI_found": 1,
        "PY_found": 1,
        "overlap": 1,
        "AI_only": 0,
        "PY_only": 0,
    }
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread?page=2\n"
    )


def test_compare_found_falls_back_to_legacy_ai_text_when_csv_is_empty(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    source_url = "https://forum.example.test/thread"
    sources_path.write_text(f"{source_url}\n", encoding="utf-8")
    output_dir.mkdir()
    output_dir.joinpath("AI_found.csv").write_text(
        "title,year,label,match_percent,source_url,extraction_method,evidence\n",
        encoding="utf-8",
    )
    output_dir.joinpath("AI_found.txt").write_text(
        "The Matrix (1999)\n", encoding="utf-8"
    )

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=False,
    )

    assert summary["AI_found"] == 1
    assert summary["overlap"] == 1
