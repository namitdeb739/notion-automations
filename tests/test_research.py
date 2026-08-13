from typing import Any

import httpx
import pytest

from notion_automations.research import (
    Author,
    Classification,
    Enrichment,
    Record,
    ResearchError,
    build_properties,
    classify_by_rules,
    extract_doi,
    fetch_crossref,
    fetch_openalex,
    resolve_doi,
    verify_corroboration,
    verify_enums,
    verify_provenance,
)

OPTIONS: dict[str, list[str]] = {
    "Venue": ["SenSys", "ENSsys", "Other"],
    "Type": ["Conference", "Workshop", "Journal"],
    "Topics": ["SMFC", "Backscatter", "Energy harvesting"],
}

ENTS = Record(
    doi="10.1145/3774906.3802780",
    title="ENTS: Experiences in Co-Designed Environmental Sensing",
    authors=(Author("John", "Madden"), Author("Pat", "Pannuto")),
    year=2026,
    container="Proceedings of SenSys",
    event="SenSys '26",
    url="https://doi.org/10.1145/3774906.3802780",
    abstract="A deployment paper.",
)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Stage 0: identifier resolution ---------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://dl.acm.org/doi/10.1145/3631410", "10.1145/3631410"),
        (
            "https://dl.acm.org/doi/epdf/10.1145/3722572.3727929",
            "10.1145/3722572.3727929",
        ),
        ("https://doi.org/10.1145/3631410", "10.1145/3631410"),
        ("10.1145/3631410", "10.1145/3631410"),
        ("https://example.com/paper", None),
    ],
)
def test_extract_doi(url: str, expected: str | None) -> None:
    assert extract_doi(url) == expected


def test_extract_doi_strips_trailing_punctuation() -> None:
    assert extract_doi("(see 10.1145/3631410).") == "10.1145/3631410"


def test_resolve_doi_from_arxiv_url() -> None:
    assert (
        resolve_doi("https://arxiv.org/abs/2401.12345") == "10.48550/arxiv.2401.12345"
    )


def test_resolve_doi_reads_citation_meta_tag() -> None:
    body = '<meta name="citation_doi" content="10.1145/3631410">'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    with _client(handler) as client:
        assert resolve_doi("https://patpannuto.com/pubs/x.pdf", client=client) == (
            "10.1145/3631410"
        )


def test_resolve_doi_raises_when_page_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no identifiers here</html>")

    with (
        _client(handler) as client,
        pytest.raises(ResearchError, match="Could not resolve"),
    ):
        resolve_doi("https://example.com/paper", client=client)


# --- Stage 1: registry retrieval ------------------------------------------


def test_fetch_crossref_maps_fields() -> None:
    payload = {
        "message": {
            "title": ["ENTS: Experiences in Co-Designed Environmental Sensing"],
            "author": [
                {"given": "John", "family": "Madden"},
                {"given": "Pat", "family": "Pannuto"},
                {"name": "Some Consortium"},
            ],
            "issued": {"date-parts": [[2026, 5]]},
            "container-title": ["Proceedings of SenSys"],
            "event": {"name": "SenSys '26"},
            "URL": "https://doi.org/10.1145/3774906.3802780",
            "abstract": "<jats:p>A deployment  paper.</jats:p>",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        record = fetch_crossref("10.1145/3774906.3802780", client=client)

    assert record.year == 2026
    # The unnamed consortium entry has no family name and is skipped.
    assert record.author_string == "John Madden, Pat Pannuto"
    assert record.abstract == "A deployment paper."


def test_fetch_crossref_raises_on_unknown_doi() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client, pytest.raises(ResearchError, match="no record"):
        fetch_crossref("10.9999/nope", client=client)


def test_fetch_openalex_reconstructs_abstract_and_enrichment() -> None:
    payload = {
        "title": "ENTS: Experiences in Co-Designed Environmental Sensing",
        "publication_year": 2026,
        "cited_by_count": 7,
        "authorships": [{"author": {"display_name": "John Madden"}}],
        "primary_location": {"source": {"display_name": "SenSys"}},
        "best_oa_location": {"pdf_url": "https://example.org/ents.pdf"},
        "open_access": {"oa_status": "green"},
        "abstract_inverted_index": {"A": [0], "deployment": [1], "paper": [2]},
        "doi": "https://doi.org/10.1145/3774906.3802780",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        result = fetch_openalex("10.1145/3774906.3802780", client=client)

    assert result is not None
    assert result.enrichment.pdf_url == "https://example.org/ents.pdf"
    assert result.enrichment.oa_status == "Green"
    assert result.enrichment.citations == 7
    assert result.enrichment.abstract == "A deployment paper"


def test_fetch_openalex_returns_none_when_not_indexed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        assert fetch_openalex("10.9999/nope", client=client) is None


# --- Gate 2: corroboration -------------------------------------------------


def test_corroboration_passes_on_agreement() -> None:
    secondary = Record(
        doi=ENTS.doi,
        title="ENTS: Experiences in Co-designed Environmental Sensing",
        authors=ENTS.authors,
        year=2026,
        container="SenSys",
        event="",
        url=ENTS.url,
        abstract="",
    )
    assert verify_corroboration(ENTS, secondary) == []


def test_corroboration_skipped_when_secondary_missing() -> None:
    assert verify_corroboration(ENTS, None) == []


def test_corroboration_flags_wrong_paper() -> None:
    secondary = Record(
        doi=ENTS.doi,
        title="Soil-Powered Computing: The Engineer's Guide",
        authors=(Author("Bill", "Yen"), Author("Laura", "Jaliff")),
        year=2024,
        container="IMWUT",
        event="",
        url=ENTS.url,
        abstract="",
    )
    problems = verify_corroboration(ENTS, secondary)
    assert any("Title mismatch" in p for p in problems)
    assert any("Year mismatch" in p for p in problems)
    assert any("Author set mismatch" in p for p in problems)


# --- Gate 3: enum grounding ------------------------------------------------


def test_verify_enums_drops_values_absent_from_schema() -> None:
    hallucinated = Classification(
        venue="NeurIPS",
        resource_type="Conference",
        topics=("SMFC", "Quantum computing"),
        key_takeaway="A takeaway.",
    )
    verified, dropped = verify_enums(hallucinated, OPTIONS)

    assert verified.venue is None
    assert verified.resource_type == "Conference"
    assert verified.topics == ("SMFC",)
    assert verified.key_takeaway == "A takeaway."
    assert dropped == ["Venue 'NeurIPS'", "Topic 'Quantum computing'"]


def test_verify_enums_keeps_valid_values() -> None:
    good = Classification(venue="SenSys", resource_type="Conference", topics=("SMFC",))
    verified, dropped = verify_enums(good, OPTIONS)
    assert verified == good
    assert dropped == []


# --- Gate 1: provenance ----------------------------------------------------


def test_provenance_passes_for_generated_payload() -> None:
    enrichment = Enrichment(citations=7, abstract="A deployment paper.")
    props = build_properties(ENTS, enrichment, Classification(), [])
    assert verify_provenance(ENTS, enrichment, props) == []


def test_provenance_catches_a_tampered_field() -> None:
    enrichment = Enrichment(citations=7)
    props = build_properties(ENTS, enrichment, Classification(), [])
    props["Year"] = {"number": 2019}
    props["Author String"] = {"rich_text": [{"text": {"content": "Someone Else"}}]}

    problems = verify_provenance(ENTS, enrichment, props)
    assert any(p.startswith("Year:") for p in problems)
    assert any(p.startswith("Author String:") for p in problems)


# --- Payload construction --------------------------------------------------


def test_build_properties_includes_enrichment_and_relations() -> None:
    enrichment = Enrichment(
        pdf_url="https://example.org/ents.pdf",
        oa_status="Green",
        citations=7,
        abstract="From OpenAlex.",
    )
    classification = Classification(
        venue="SenSys",
        resource_type="Conference",
        topics=("SMFC", "Backscatter"),
        key_takeaway="Field deployments are hard.",
    )
    props = build_properties(
        ENTS, enrichment, classification, ["id-a", "id-b"], code_url="https://git/x"
    )

    assert props["PDF URL"] == {"url": "https://example.org/ents.pdf"}
    assert props["Open Access"] == {"select": {"name": "Green"}}
    assert props["Citations"] == {"number": 7}
    assert props["Code / Data"] == {"url": "https://git/x"}
    assert props["First Author"] == {"relation": [{"id": "id-a"}]}
    assert props["Authors"] == {"relation": [{"id": "id-a"}, {"id": "id-b"}]}
    assert props["Topics"] == {
        "multi_select": [{"name": "SMFC"}, {"name": "Backscatter"}]
    }
    # Crossref's abstract wins over OpenAlex's reconstruction.
    assert props["Abstract"]["rich_text"][0]["text"]["content"] == "A deployment paper."


def test_build_properties_truncates_overlong_rich_text() -> None:
    long_abstract = "x" * 5000
    record = Record(**{**ENTS.__dict__, "abstract": long_abstract})
    props = build_properties(record, Enrichment(), Classification(), [])
    assert len(props["Abstract"]["rich_text"][0]["text"]["content"]) == 2000


def test_build_properties_omits_absent_optional_fields() -> None:
    props = build_properties(ENTS, Enrichment(), Classification(), [])
    for absent in ("PDF URL", "Open Access", "Citations", "Code / Data", "Venue"):
        assert absent not in props


def test_fetch_crossref_joins_subtitle() -> None:
    """ACM registers the subtitle separately; `title` alone is truncated."""
    payload = {
        "message": {
            "title": ["Soil-Powered Computing"],
            "subtitle": [
                "The Engineer's Guide to Practical Soil Microbial Fuel Cell Design"
            ],
            "author": [{"given": "Bill", "family": "Yen"}],
            "issued": {"date-parts": [[2023, 12]]},
            "URL": "https://doi.org/10.1145/3631410",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        record = fetch_crossref("10.1145/3631410", client=client)

    assert record.title == (
        "Soil-Powered Computing: The Engineer's Guide to "
        "Practical Soil Microbial Fuel Cell Design"
    )


def test_corroboration_allows_subtitle_split() -> None:
    """Crossref joins title+subtitle; OpenAlex often stores only the title."""
    crossref = Record(
        **{
            **ENTS.__dict__,
            "title": "Soil-Powered Computing: The Engineer's Guide to SMFC Design",
        }
    )
    openalex = Record(**{**crossref.__dict__, "title": "Soil-Powered Computing"})
    assert verify_corroboration(crossref, openalex) == []


def test_corroboration_still_flags_a_short_coincidental_prefix() -> None:
    crossref = Record(**{**ENTS.__dict__, "title": "ENTS: A Study of Sensing"})
    openalex = Record(**{**crossref.__dict__, "title": "ENTS"})
    assert any("Title mismatch" in p for p in verify_corroboration(crossref, openalex))


# --- Rules-based classification -------------------------------------------


def test_classify_by_rules_picks_workshop_over_colocated_conference() -> None:
    """An ENSsys paper's event string also names SenSys — specificity wins."""
    record = Record(
        **{
            **ENTS.__dict__,
            "container": "Proceedings of the 13th International Workshop on "
            "Energy Harvesting and Energy-Neutral Sensing Systems",
            "event": "SenSys '25: The 23rd ACM Conference on Embedded Networked "
            "Sensor Systems",
            "crossref_type": "proceedings-article",
        }
    )
    result = classify_by_rules(record, "A backscatter soil moisture tag.", OPTIONS)
    assert result.venue == "ENSsys"
    assert result.resource_type == "Workshop"
    assert result.topics == ("Backscatter",)


def test_classify_by_rules_maps_journal_article() -> None:
    record = Record(
        **{
            **ENTS.__dict__,
            "container": "Proceedings of the ACM on Interactive, Mobile, Wearable "
            "and Ubiquitous Technologies",
            "event": "",
            "crossref_type": "journal-article",
        }
    )
    assert classify_by_rules(record, "", OPTIONS).resource_type == "Journal"


def test_classify_by_rules_falls_back_to_other_venue() -> None:
    record = Record(
        **{**ENTS.__dict__, "container": "Journal of Nothing Relevant", "event": ""}
    )
    assert classify_by_rules(record, "", OPTIONS).venue == "Other"


def test_classify_by_rules_takeaway_is_a_verbatim_first_sentence() -> None:
    abstract = "SMFCs power sensors. A second sentence follows."
    result = classify_by_rules(ENTS, abstract, OPTIONS)
    assert result.key_takeaway == "SMFCs power sensors."
    assert result.key_takeaway in abstract


def test_classify_by_rules_never_invents_an_option() -> None:
    """Only options present in the live schema can be returned."""
    narrow: dict[str, list[str]] = {"Venue": [], "Type": [], "Topics": []}
    result = classify_by_rules(ENTS, "microbial fuel cell backscatter", narrow)
    assert result.venue is None
    assert result.resource_type is None
    assert result.topics == ()
