"""Add a research resource to the dissertation Research Resources database.

The pipeline is deliberately split so that Claude never authors a fact:

* Every factual field (title, authors, year, DOI, abstract, PDF link, open
  access status, citation count) comes from Crossref or OpenAlex — two
  authoritative registries.
* Claude only picks values from closed enumerations that already exist in the
  Notion schema, plus a one-sentence takeaway drawn from the abstract.

Three verification gates run before anything is written to Notion:

1. ``verify_provenance`` — every factual field in the payload must be
   byte-identical to the registry value it claims to come from.
2. ``verify_corroboration`` — OpenAlex must agree with Crossref on title,
   year, and author surnames.
3. ``verify_enums`` — every model-produced value must be a member of the live
   Notion option list; non-members are dropped, never coerced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, cast

import httpx

from notion_automations.notion import get_notion_client

RESOURCES_DS_ID = "349d2b5d-d268-4ffc-871a-16e47b91b540"
AUTHORS_DS_ID = "7fab2ea4-3b88-405d-b9c9-220fb3cf2356"

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"
USER_AGENT = "notion-automations (mailto:namitdeb739@gmail.com)"

# Corroboration thresholds. Titles are compared after normalisation, so a
# genuine match sits well above 0.9; author sets tolerate one registry listing
# a collaboration or omitting a late addition.
TITLE_SIMILARITY_FLOOR = 0.90
AUTHOR_OVERLAP_FLOOR = 0.70
# Shortest prefix that counts as a subtitle-split match rather than a
# coincidence — long enough that two unrelated titles won't collide.
_TITLE_PREFIX_FLOOR = 15

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_ARXIV_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.IGNORECASE)
_CITATION_DOI_META = re.compile(
    r'<meta[^>]+name=["\']citation_doi["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Trailing punctuation that URL paths pick up but DOIs never end with.
_DOI_TRAILING_JUNK = ".,;)]}>'\"/"

# OpenAlex oa_status -> the Notion "Open Access" option. Diamond journals are
# gold OA without author fees; the distinction does not matter for reading.
_OA_STATUS_MAP = {
    "gold": "Gold",
    "diamond": "Gold",
    "green": "Green",
    "hybrid": "Hybrid",
    "bronze": "Bronze",
    "closed": "Closed",
}

# Notion rejects rich_text values longer than this.
_RICH_TEXT_LIMIT = 2000


class ResearchError(RuntimeError):
    """Raised when the pipeline cannot produce a trustworthy record."""


@dataclass(frozen=True)
class Author:
    """One author, exactly as an authoritative registry reported them."""

    given: str
    family: str

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".strip()


@dataclass(frozen=True)
class Record:
    """Bibliographic facts sourced from Crossref. Never model-authored."""

    doi: str
    title: str
    authors: tuple[Author, ...]
    year: int | None
    container: str
    event: str
    url: str
    abstract: str
    crossref_type: str = ""

    @property
    def author_string(self) -> str:
        """Verbatim, ordered author list — the citation source of truth."""
        return ", ".join(a.display for a in self.authors)


@dataclass(frozen=True)
class Enrichment:
    """Extra facts from OpenAlex. Also registry-sourced, never model-authored."""

    pdf_url: str = ""
    oa_status: str = ""
    citations: int | None = None
    abstract: str = ""


@dataclass(frozen=True)
class OpenAlexResult:
    """OpenAlex serves two roles: independent corroborator and enrichment."""

    record: Record
    enrichment: Enrichment


@dataclass(frozen=True)
class Classification:
    """The only fields Claude is allowed to produce."""

    venue: str | None = None
    resource_type: str | None = None
    topics: tuple[str, ...] = ()
    key_takeaway: str = ""


@dataclass
class VerificationReport:
    """Outcome of the three gates. ``ok`` gates the Notion write."""

    provenance: list[str] = field(default_factory=list)
    corroboration: list[str] = field(default_factory=list)
    dropped_enums: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Dropped enums are a downgrade, not a falsehood — they don't block."""
        return not self.provenance and not self.corroboration


# --------------------------------------------------------------------------
# Stage 0 — identifier resolution (pure string handling, no model involved)
# --------------------------------------------------------------------------


def _clean_doi(raw: str) -> str:
    return raw.strip().rstrip(_DOI_TRAILING_JUNK).lower()


def extract_doi(url: str) -> str | None:
    """Pull a DOI straight out of the URL, if one is embedded in it."""
    match = _DOI_PATTERN.search(url)
    return _clean_doi(match.group(0)) if match else None


def resolve_doi(url: str, *, client: httpx.Client | None = None) -> str:
    """Resolve a pasted link to a DOI, fetching the landing page if needed."""
    doi = extract_doi(url)
    if doi:
        return doi

    arxiv = _ARXIV_PATTERN.search(url)
    if arxiv:
        return f"10.48550/arxiv.{arxiv.group(1)}"

    owns_client = client is None
    http = client or _new_client()
    try:
        response = http.get(url)
        response.raise_for_status()
        body = response.text
    except httpx.HTTPError as exc:
        raise ResearchError(
            f"No DOI in the URL and the page could not be fetched: {exc}"
        ) from exc
    finally:
        if owns_client:
            http.close()

    meta = _CITATION_DOI_META.search(body)
    if meta:
        return _clean_doi(meta.group(1))

    embedded = _DOI_PATTERN.search(body)
    if embedded:
        return _clean_doi(embedded.group(0))

    raise ResearchError(
        f"Could not resolve a DOI for {url}. Pass --doi explicitly to continue."
    )


# --------------------------------------------------------------------------
# Stage 1 — authoritative metadata
# --------------------------------------------------------------------------


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def _get_json(url: str, client: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = client is None
    http = client or _new_client()
    try:
        response = http.get(url)
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())
    finally:
        if owns_client:
            http.close()


def _strip_jats(text: str) -> str:
    """Crossref abstracts are JATS XML; keep the prose."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def fetch_crossref(doi: str, *, client: httpx.Client | None = None) -> Record:
    """Fetch the authoritative record. Raises if the DOI is unknown."""
    try:
        payload = _get_json(f"{CROSSREF_API}/{doi}", client)
    except httpx.HTTPStatusError as exc:
        raise ResearchError(f"Crossref has no record for DOI {doi}") from exc

    message = payload["message"]
    titles = message.get("title") or []
    if not titles:
        raise ResearchError(f"Crossref record for {doi} has no title")

    # ACM and several other publishers register the subtitle separately, so
    # `title` alone is truncated at the colon.
    subtitles = message.get("subtitle") or []
    full_title = ": ".join(
        [titles[0].strip(), *(s.strip() for s in subtitles[:1] if s.strip())]
    )

    authors = tuple(
        Author(given=a.get("given", "").strip(), family=a.get("family", "").strip())
        for a in message.get("author", [])
        if a.get("family")
    )
    issued = message.get("issued", {}).get("date-parts") or [[]]
    year = issued[0][0] if issued[0] else None
    containers = message.get("container-title") or []

    return Record(
        doi=doi,
        title=full_title,
        authors=authors,
        year=int(year) if year else None,
        container=containers[0] if containers else "",
        event=(message.get("event") or {}).get("name", ""),
        url=message.get("URL", f"https://doi.org/{doi}"),
        abstract=_strip_jats(message.get("abstract", "")),
        crossref_type=message.get("type", ""),
    )


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as a word -> positions inverted index."""
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, spots in inverted.items():
        for spot in spots:
            positions[spot] = word
    return " ".join(positions[i] for i in sorted(positions))


def fetch_openalex(
    doi: str, *, client: httpx.Client | None = None
) -> OpenAlexResult | None:
    """Fetch the same work from OpenAlex. ``None`` when it isn't indexed."""
    try:
        payload = _get_json(f"{OPENALEX_API}/doi:{doi}", client)
    except httpx.HTTPError:
        return None

    authors: list[Author] = []
    for entry in payload.get("authorships") or []:
        name = (entry.get("author") or {}).get("display_name", "").strip()
        if not name:
            continue
        given, _, family = name.rpartition(" ")
        authors.append(Author(given=given, family=family))

    source = (payload.get("primary_location") or {}).get("source") or {}
    best_oa = payload.get("best_oa_location") or {}
    open_access = payload.get("open_access") or {}

    record = Record(
        doi=doi,
        title=(payload.get("title") or "").strip(),
        authors=tuple(authors),
        year=payload.get("publication_year"),
        container=source.get("display_name") or "",
        event="",
        url=payload.get("doi") or f"https://doi.org/{doi}",
        abstract="",
    )
    enrichment = Enrichment(
        pdf_url=best_oa.get("pdf_url") or "",
        oa_status=_OA_STATUS_MAP.get(open_access.get("oa_status") or "", ""),
        citations=payload.get("cited_by_count"),
        abstract=_reconstruct_abstract(payload.get("abstract_inverted_index")),
    )
    return OpenAlexResult(record=record, enrichment=enrichment)


# --------------------------------------------------------------------------
# Gate 2 — independent corroboration
# --------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def verify_corroboration(primary: Record, secondary: Record | None) -> list[str]:
    """Gate 2. Two independent registries must agree on the hard facts."""
    if secondary is None:
        return []

    problems: list[str] = []

    if secondary.title:
        left, right = _normalise(primary.title), _normalise(secondary.title)
        # Registries disagree on whether the subtitle belongs in the title, so
        # one being a prefix of the other is agreement, not a mismatch.
        shorter, longer = sorted((left, right), key=len)
        prefix_match = len(shorter) >= _TITLE_PREFIX_FLOOR and longer.startswith(
            shorter
        )
        similarity = SequenceMatcher(None, left, right).ratio()
        if not prefix_match and similarity < TITLE_SIMILARITY_FLOOR:
            problems.append(
                f"Title mismatch ({similarity:.2f} similarity): "
                f"Crossref {primary.title!r} vs OpenAlex {secondary.title!r}"
            )

    if primary.year and secondary.year and primary.year != secondary.year:
        problems.append(
            f"Year mismatch: Crossref {primary.year} vs OpenAlex {secondary.year}"
        )

    primary_families = {_normalise(a.family) for a in primary.authors if a.family}
    secondary_families = {_normalise(a.family) for a in secondary.authors if a.family}
    if primary_families and secondary_families:
        overlap = len(primary_families & secondary_families) / len(
            primary_families | secondary_families
        )
        if overlap < AUTHOR_OVERLAP_FLOOR:
            problems.append(
                f"Author set mismatch ({overlap:.2f} overlap): "
                f"only in Crossref {sorted(primary_families - secondary_families)}, "
                f"only in OpenAlex {sorted(secondary_families - primary_families)}"
            )

    return problems


# --------------------------------------------------------------------------
# Notion schema introspection — the live enum allowlist
# --------------------------------------------------------------------------


def fetch_select_options(ds_id: str = RESOURCES_DS_ID) -> dict[str, list[str]]:
    """Read the live select/multi-select options straight from the schema."""
    notion = get_notion_client()
    schema = cast(
        "dict[str, Any]",
        notion.request(path=f"data_sources/{ds_id}", method="GET"),
    )
    options: dict[str, list[str]] = {}
    for name, prop in schema.get("properties", {}).items():
        kind = prop.get("type")
        if kind in {"select", "multi_select"}:
            options[name] = [o["name"] for o in prop[kind].get("options", [])]
    return options


# --------------------------------------------------------------------------
# Stage 2a — rules-based classification (the default; no model, no API cost)
# --------------------------------------------------------------------------

# Notion option -> substrings that identify it in the Crossref container/event
# strings. Ordered most-specific-first: an ENSsys paper's event string also
# names SenSys, because the workshop is co-located with it.
_VENUE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ENSsys", ("enssys", "energy harvesting and energy-neutral sensing")),
    ("IMWUT / UbiComp", ("imwut", "ubicomp", "interactive, mobile, wearable")),
    ("IPSN", ("ipsn", "information processing in sensor networks")),
    ("MobiCom", ("mobicom", "mobile computing and networking")),
    ("MobiSys", ("mobisys", "mobile systems, applications")),
    (
        "SenSys",
        (
            "sensys",
            "embedded networked sensor systems",
            "embedded artificial intelligence and sensing systems",
        ),
    ),
    ("arXiv", ("arxiv",)),
)

# Crossref `type` -> Notion Type option.
_CROSSREF_TYPES = {
    "proceedings-article": "Conference",
    "journal-article": "Journal",
    "posted-content": "Preprint",
    "dissertation": "Thesis",
    "dataset": "Dataset",
    "report": "Web",
    "book-chapter": "Journal",
}

# Notion topic -> substrings to look for in the title and abstract.
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "SMFC": ("microbial fuel cell", "smfc", "soil-powered", "soil powered"),
    "Backscatter": ("backscatter",),
    "Soil moisture sensing": ("soil moisture",),
    "Energy harvesting": ("energy harvesting", "energy-neutral", "energy neutral"),
    "Low-power embedded": ("low-power", "low power", "microcontroller", "embedded"),
    "Field deployment": ("deployment", "deployed", "field study", "in situ"),
    "Environmental sensing": (
        "environmental sensing",
        "environmental monitoring",
        "wireless sensor network",
    ),
    "Batteryless": ("batteryless", "battery-free", "battery free"),
}


def _first_sentence(text: str) -> str:
    """A takeaway that is provably a quote, not a paraphrase."""
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s", text)
    return (text[: match.start()] if match else text).strip()


def classify_by_rules(
    record: Record, abstract: str, options: dict[str, list[str]]
) -> Classification:
    """Derive taxonomy placement from the Crossref record alone.

    More deterministic than a model, and free. Venue and Type come from
    structured registry fields; Topics are keyword matches over the title and
    abstract, which is the one genuinely fuzzy call — expect to correct it
    occasionally in the Notion UI.
    """
    haystack = f"{record.container} {record.event}".lower()
    venue = next(
        (
            name
            for name, aliases in _VENUE_ALIASES
            if name in options["Venue"] and any(a in haystack for a in aliases)
        ),
        None,
    )
    if venue is None and "Other" in options["Venue"]:
        venue = "Other"

    resource_type = _CROSSREF_TYPES.get(record.crossref_type)
    # ACM registers workshop papers as proceedings-articles; the container
    # string is what distinguishes them from main-track conference papers.
    if resource_type == "Conference" and "workshop" in haystack:
        resource_type = "Workshop"
    if resource_type not in options["Type"]:
        resource_type = None

    corpus = f"{record.title} {abstract}".lower()
    topics = tuple(
        topic
        for topic in options["Topics"]
        if any(k in corpus for k in _TOPIC_KEYWORDS.get(topic, ()))
    )

    return Classification(
        venue=venue,
        resource_type=resource_type,
        topics=topics,
        key_takeaway=_first_sentence(abstract),
    )


# --------------------------------------------------------------------------
# Stage 2b — Claude, restricted to closed enumerations (opt-in)
# --------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """\
You classify an academic reference into a fixed taxonomy for a dissertation \
reading database.

Rules:
- Choose values ONLY from the allowed lists given to you. Never invent a value.
- If nothing in a list fits, return null (or an empty array for topics).
- The key takeaway must be one sentence, drawn strictly from the supplied \
title and abstract. If there is no abstract, return an empty string.
- Never restate, correct, or infer bibliographic facts. They are already known.
"""


def _classification_schema(options: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "venue": {"type": ["string", "null"], "enum": [*options["Venue"], None]},
            "resource_type": {
                "type": ["string", "null"],
                "enum": [*options["Type"], None],
            },
            "topics": {
                "type": "array",
                "items": {"type": "string", "enum": options["Topics"]},
            },
            "key_takeaway": {"type": "string"},
        },
        "required": ["venue", "resource_type", "topics", "key_takeaway"],
        "additionalProperties": False,
    }


def classify(
    record: Record, abstract: str, options: dict[str, list[str]]
) -> Classification:
    """Ask Claude for taxonomy placement only. Output is schema-constrained."""
    import anthropic

    prompt = json.dumps(
        {
            "title": record.title,
            "container": record.container,
            "event": record.event,
            "year": record.year,
            "abstract": abstract[:4000],
            "allowed_venue": options["Venue"],
            "allowed_type": options["Type"],
            "allowed_topics": options["Topics"],
        },
        indent=2,
        sort_keys=True,
    )

    try:
        client = anthropic.Anthropic()
    except TypeError as exc:  # SDK raises this when no credential resolves
        raise ResearchError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY, run "
            "`ant auth login`, or pass --no-classify to skip taxonomy placement."
        ) from exc

    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=2000,
        system=_CLASSIFY_SYSTEM,
        output_config={
            "effort": "low",
            "format": {
                "type": "json_schema",
                "schema": _classification_schema(options),
            },
        },
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "refusal":
        raise ResearchError("Claude declined to classify this resource.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    return Classification(
        venue=data.get("venue"),
        resource_type=data.get("resource_type"),
        topics=tuple(data.get("topics") or []),
        key_takeaway=(data.get("key_takeaway") or "").strip(),
    )


# --------------------------------------------------------------------------
# Gates 1 and 3
# --------------------------------------------------------------------------


def _plain(prop: dict[str, Any] | None, kind: str) -> Any:
    """Read a scalar back out of a Notion property payload."""
    if prop is None:
        return None
    if kind == "title":
        return "".join(p["text"]["content"] for p in prop["title"])
    if kind == "rich_text":
        return "".join(p["text"]["content"] for p in prop["rich_text"])
    return prop.get(kind)


def verify_provenance(
    record: Record, enrichment: Enrichment, properties: dict[str, Any]
) -> list[str]:
    """Gate 1. Every factual field must match the registry it came from."""
    expected: list[tuple[str, str, Any]] = [
        ("Name", "title", record.title),
        ("Author String", "rich_text", record.author_string),
        ("DOI", "rich_text", record.doi),
        ("URL", "url", record.url),
        ("Year", "number", record.year),
        ("Citations", "number", enrichment.citations),
    ]
    problems = [
        f"{name}: payload has {_plain(properties.get(name), kind)!r}, "
        f"registry has {value!r}"
        for name, kind, value in expected
        if value is not None and _plain(properties.get(name), kind) != value
    ]

    # The abstract is truncated to fit Notion's rich_text limit, so compare a
    # prefix rather than the whole string.
    abstract = _plain(properties.get("Abstract"), "rich_text")
    source_abstract = record.abstract or enrichment.abstract
    if abstract and not source_abstract.startswith(abstract.rstrip("…")):
        problems.append("Abstract: payload text is not a prefix of the registry text")

    return problems


def verify_enums(
    classification: Classification, options: dict[str, list[str]]
) -> tuple[Classification, list[str]]:
    """Gate 3. Drop any model value that is not a live Notion option."""
    dropped: list[str] = []

    venue = classification.venue
    if venue is not None and venue not in options["Venue"]:
        dropped.append(f"Venue {venue!r}")
        venue = None

    resource_type = classification.resource_type
    if resource_type is not None and resource_type not in options["Type"]:
        dropped.append(f"Type {resource_type!r}")
        resource_type = None

    topics: list[str] = []
    for topic in classification.topics:
        if topic in options["Topics"]:
            topics.append(topic)
        else:
            dropped.append(f"Topic {topic!r}")

    verified = Classification(
        venue=venue,
        resource_type=resource_type,
        topics=tuple(topics),
        key_takeaway=classification.key_takeaway,
    )
    return verified, dropped


# --------------------------------------------------------------------------
# Notion writes
# --------------------------------------------------------------------------


def _title_of(page: dict[str, Any]) -> str:
    title = page.get("properties", {}).get("Name", {}).get("title", [])
    return "".join(part.get("plain_text", "") for part in title).strip()


def resolve_authors(authors: tuple[Author, ...]) -> list[str]:
    """Match each author to an existing Authors row, creating any that are new."""
    notion = get_notion_client()
    existing: dict[str, str] = {}
    cursor: str | None = None
    while True:
        page = cast(
            "dict[str, Any]",
            notion.data_sources.query(
                AUTHORS_DS_ID, page_size=100, start_cursor=cursor
            ),
        )
        for row in page["results"]:
            existing[_normalise(_title_of(row))] = row["id"]
        if not page.get("has_more"):
            break
        cursor = page["next_cursor"]

    ids: list[str] = []
    for author in authors:
        key = _normalise(author.display)
        page_id = existing.get(key)
        if page_id is None:
            created = cast(
                "dict[str, Any]",
                notion.pages.create(
                    parent={"data_source_id": AUTHORS_DS_ID},
                    properties={
                        "Name": {"title": [{"text": {"content": author.display}}]}
                    },
                ),
            )
            page_id = created["id"]
            existing[key] = page_id
        ids.append(page_id)
    return ids


def _rich_text(value: str) -> dict[str, Any]:
    if len(value) > _RICH_TEXT_LIMIT:
        value = value[: _RICH_TEXT_LIMIT - 1] + "…"
    return {"rich_text": [{"text": {"content": value}}]}


def build_properties(
    record: Record,
    enrichment: Enrichment,
    classification: Classification,
    author_ids: list[str],
    status: str = "To Read",
    code_url: str | None = None,
) -> dict[str, Any]:
    """Assemble the Notion payload. Factual values come only from the registries."""
    props: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": record.title}}]},
        "Author String": _rich_text(record.author_string),
        "DOI": _rich_text(record.doi),
        "URL": {"url": record.url},
        "Status": {"select": {"name": status}},
    }
    if record.year is not None:
        props["Year"] = {"number": record.year}
    if author_ids:
        props["Authors"] = {"relation": [{"id": i} for i in author_ids]}
        props["First Author"] = {"relation": [{"id": author_ids[0]}]}
    if classification.venue:
        props["Venue"] = {"select": {"name": classification.venue}}
    if classification.resource_type:
        props["Type"] = {"select": {"name": classification.resource_type}}
    if classification.topics:
        props["Topics"] = {"multi_select": [{"name": t} for t in classification.topics]}
    if classification.key_takeaway:
        props["Key Takeaway"] = _rich_text(classification.key_takeaway)

    abstract = record.abstract or enrichment.abstract
    if abstract:
        props["Abstract"] = _rich_text(abstract)
    if enrichment.pdf_url:
        props["PDF URL"] = {"url": enrichment.pdf_url}
    if enrichment.oa_status:
        props["Open Access"] = {"select": {"name": enrichment.oa_status}}
    if enrichment.citations is not None:
        props["Citations"] = {"number": enrichment.citations}
    if code_url:
        props["Code / Data"] = {"url": code_url}
    return props


_BODY_TEMPLATE = ["Summary", "Method", "Limitations", "Relevance to my work"]


def _body_blocks(enrichment: Enrichment) -> list[dict[str, Any]]:
    """Annotation skeleton, preceded by an inline PDF when one is available."""
    blocks: list[dict[str, Any]] = []
    if enrichment.pdf_url:
        blocks.append(
            {
                "object": "block",
                "type": "pdf",
                "pdf": {
                    "type": "external",
                    "external": {"url": enrichment.pdf_url},
                },
            }
        )
    blocks.extend(
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": heading}}]},
        }
        for heading in _BODY_TEMPLATE
    )
    return blocks


def create_resource(
    properties: dict[str, Any], enrichment: Enrichment
) -> dict[str, Any]:
    """Create the page, with the PDF embedded above the annotation skeleton."""
    notion = get_notion_client()
    return cast(
        "dict[str, Any]",
        notion.pages.create(
            parent={"data_source_id": RESOURCES_DS_ID},
            properties=properties,
            children=_body_blocks(enrichment),
        ),
    )


def find_existing(doi: str) -> str | None:
    """Return the page URL of an existing row with this DOI, if any."""
    notion = get_notion_client()
    result = cast(
        "dict[str, Any]",
        notion.data_sources.query(
            RESOURCES_DS_ID,
            filter={"property": "DOI", "rich_text": {"equals": doi}},
            page_size=1,
        ),
    )
    rows = result["results"]
    return cast("str", rows[0]["url"]) if rows else None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


@dataclass
class Prepared:
    """Everything needed to write, plus the verification evidence."""

    record: Record
    enrichment: Enrichment
    classification: Classification
    properties: dict[str, Any]
    report: VerificationReport
    corroborated_by: str | None
    duplicate_of: str | None


def prepare(
    url: str,
    doi_override: str | None = None,
    code_url: str | None = None,
    classifier: str = "rules",
) -> Prepared:
    """Read-only: resolve, retrieve, classify, and run all three gates.

    Nothing is written to Notion here — author rows are created at commit time.

    ``classifier`` is ``"rules"`` (default; derives placement from the Crossref
    record, no API cost), ``"claude"``, or ``"none"``. All three feed gate 3,
    so no path can introduce a value that isn't in the live Notion schema.
    """
    with _new_client() as http:
        doi = doi_override or resolve_doi(url, client=http)
        record = fetch_crossref(doi, client=http)
        openalex = fetch_openalex(doi, client=http)

    enrichment = openalex.enrichment if openalex else Enrichment()

    report = VerificationReport()
    report.corroboration = verify_corroboration(
        record, openalex.record if openalex else None
    )

    classification = Classification()
    dropped: list[str] = []
    if classifier != "none":
        options = fetch_select_options()
        abstract = record.abstract or enrichment.abstract
        raw = (
            classify(record, abstract, options)
            if classifier == "claude"
            else classify_by_rules(record, abstract, options)
        )
        classification, dropped = verify_enums(raw, options)
    report.dropped_enums = dropped

    properties = build_properties(
        record, enrichment, classification, [], code_url=code_url
    )
    report.provenance = verify_provenance(record, enrichment, properties)

    return Prepared(
        record=record,
        enrichment=enrichment,
        classification=classification,
        properties=properties,
        report=report,
        corroborated_by="OpenAlex" if openalex else None,
        duplicate_of=find_existing(doi),
    )


def commit(prepared: Prepared, code_url: str | None = None) -> str:
    """Resolve author rows, re-verify, and create the page. Returns its URL."""
    if not prepared.report.ok:
        raise ResearchError("Refusing to write: verification did not pass.")

    author_ids = resolve_authors(prepared.record.authors)
    properties = build_properties(
        prepared.record,
        prepared.enrichment,
        prepared.classification,
        author_ids,
        code_url=code_url,
    )

    # The relations changed the payload — re-run gate 1 on what actually ships.
    problems = verify_provenance(prepared.record, prepared.enrichment, properties)
    if problems:
        raise ResearchError(
            "Provenance check failed at write time: " + "; ".join(problems)
        )

    page = create_resource(properties, prepared.enrichment)
    return cast("str", page["url"])
