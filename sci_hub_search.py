"""
Paper resolution with a legal-first fallback chain.

For any DOI the resolver tries, in order:

    1. Unpaywall  - publisher/repository open-access copies
    2. OpenAlex   - broader OA index, catches records Unpaywall misses
    3. Sci-Hub    - last resort, only if ENABLE_SCIHUB is on

The first tier that yields a usable PDF link wins. Every attempt is recorded in
the returned dict under 'resolution_log' so callers can see what was tried.

Configuration (environment variables):
    UNPAYWALL_EMAIL   Required by the Unpaywall API. Without it tier 1 is skipped.
    ENABLE_SCIHUB     "true"/"1"/"yes" to allow tier 3. Default: true.
"""

import os
import requests

CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2"
OPENALEX_API = "https://api.openalex.org/works"

# Unpaywall requires an email address on every request. OpenAlex and CrossRef
# don't require one, but supplying it puts you in their "polite pool" and gets
# you more generous rate limits.
CONTACT_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "").strip()

ENABLE_SCIHUB = os.environ.get("ENABLE_SCIHUB", "true").strip().lower() in ("1", "true", "yes")

TIMEOUT = 30
USER_AGENT = f"sci-hub-mcp/1.0 (mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else "sci-hub-mcp/1.0"

EMPTY_METADATA = {'title': '', 'author': '', 'year': ''}


def _get(url, params=None):
    """GET returning parsed JSON, or None on any non-200 / transport failure."""
    try:
        response = requests.get(
            url,
            params=params,
            headers={'User-Agent': USER_AGENT},
            timeout=TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Metadata (CrossRef)
# --------------------------------------------------------------------------

def parse_crossref_item(item):
    """Pull title / author / year out of a CrossRef work record."""
    titles = item.get('title') or ['']
    title = titles[0] if titles else ''

    authors = []
    for a in item.get('author', []):
        name = ' '.join(p for p in (a.get('given'), a.get('family')) if p)
        authors.append(name or a.get('name', ''))
    author = ', '.join(n for n in authors if n)

    year = ''
    for key in ('published-print', 'published-online', 'issued', 'created'):
        date_parts = (item.get(key) or {}).get('date-parts') or []
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = str(date_parts[0][0])
            break

    return {'title': title, 'author': author, 'year': year}


def get_crossref_metadata(doi):
    """Look up a DOI's metadata on CrossRef."""
    # The DOI is a path segment here, and CrossRef expects its slash unencoded,
    # so it cannot be moved into params.
    data = _get(f"{CROSSREF_API}/{doi}")
    if data:
        try:
            return parse_crossref_item(data['message'])
        except (KeyError, TypeError):
            pass
    return dict(EMPTY_METADATA)


# --------------------------------------------------------------------------
# Tier 1: Unpaywall
# --------------------------------------------------------------------------

def _parse_unpaywall_authors(record):
    authors = []
    for a in record.get('z_authors') or []:
        name = ' '.join(p for p in (a.get('given'), a.get('family')) if p)
        authors.append(name or a.get('name', ''))
    return ', '.join(n for n in authors if n)


def resolve_via_unpaywall(doi):
    """Tier 1. Returns a resolution dict, or None if no OA copy is indexed."""
    if not CONTACT_EMAIL:
        return None

    record = _get(f"{UNPAYWALL_API}/{doi}", params={'email': CONTACT_EMAIL})
    if not record:
        return None

    location = record.get('best_oa_location') or {}
    if not location:
        # Fall back to any other OA location Unpaywall knows about.
        locations = record.get('oa_locations') or []
        location = locations[0] if locations else {}

    pdf_url = location.get('url_for_pdf') or location.get('url')
    if not pdf_url:
        return None

    return {
        'pdf_url': pdf_url,
        'landing_page_url': location.get('url_for_landing_page', ''),
        'source': 'unpaywall',
        'is_oa': bool(record.get('is_oa')),
        'oa_status': record.get('oa_status', ''),
        'license': location.get('license') or '',
        'version': location.get('version') or '',
        'host_type': location.get('host_type') or '',
        'title': record.get('title') or '',
        'author': _parse_unpaywall_authors(record),
        'year': str(record.get('year')) if record.get('year') else '',
    }


# --------------------------------------------------------------------------
# Tier 2: OpenAlex
# --------------------------------------------------------------------------

def resolve_via_openalex(doi):
    """Tier 2. Returns a resolution dict, or None if no OA copy is indexed."""
    params = {'mailto': CONTACT_EMAIL} if CONTACT_EMAIL else None
    record = _get(f"{OPENALEX_API}/doi:{doi}", params=params)
    if not record:
        return None

    open_access = record.get('open_access') or {}
    location = record.get('best_oa_location') or {}

    # pdf_url is the direct file; fall back to the landing page, then to the
    # top-level oa_url, which is populated even when best_oa_location is null.
    pdf_url = (
        location.get('pdf_url')
        or location.get('landing_page_url')
        or open_access.get('oa_url')
    )
    if not pdf_url:
        return None

    authors = []
    for authorship in record.get('authorships') or []:
        name = (authorship.get('author') or {}).get('display_name')
        if name:
            authors.append(name)

    return {
        'pdf_url': pdf_url,
        'landing_page_url': location.get('landing_page_url', ''),
        'source': 'openalex',
        'is_oa': bool(open_access.get('is_oa')),
        'oa_status': open_access.get('oa_status', ''),
        'license': location.get('license') or '',
        'version': location.get('version') or '',
        'host_type': '',
        'title': record.get('title') or record.get('display_name') or '',
        'author': ', '.join(authors),
        'year': str(record['publication_year']) if record.get('publication_year') else '',
    }


# --------------------------------------------------------------------------
# Tier 3: Sci-Hub
# --------------------------------------------------------------------------

def create_scihub_instance():
    """Create and configure a SciHub instance."""
    from scihub import SciHub  # imported lazily so tiers 1-2 work without it
    sh = SciHub()
    sh.timeout = TIMEOUT
    return sh


def resolve_via_scihub(doi):
    """Tier 3. Returns a resolution dict, or None. Sci-Hub supplies no metadata."""
    if not ENABLE_SCIHUB:
        return None
    try:
        result = create_scihub_instance().fetch(doi)
        pdf_url = (result or {}).get('url')
        if not pdf_url:
            return None
        return {
            'pdf_url': pdf_url,
            'landing_page_url': '',
            'source': 'scihub',
            'is_oa': False,
            'oa_status': 'closed',
            'license': '',
            'version': '',
            'host_type': '',
            'title': '',
            'author': '',
            'year': '',
        }
    except Exception:
        return None


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------

RESOLVERS = [
    ('unpaywall', resolve_via_unpaywall),
    ('openalex', resolve_via_openalex),
    ('scihub', resolve_via_scihub),
]


def search_paper_by_doi(doi, metadata=None):
    """
    Resolve a DOI to a PDF link, trying Unpaywall, then OpenAlex, then Sci-Hub.

    Returns a dict with status 'success' or 'not_found'. On success it includes
    pdf_url, source, is_oa, license, title, author, year, and resolution_log.
    """
    log = []

    if not CONTACT_EMAIL:
        log.append('unpaywall: skipped (UNPAYWALL_EMAIL not set)')
    if not ENABLE_SCIHUB:
        log.append('scihub: disabled (ENABLE_SCIHUB is off)')

    resolution = None
    for name, resolver in RESOLVERS:
        if name == 'unpaywall' and not CONTACT_EMAIL:
            continue
        if name == 'scihub' and not ENABLE_SCIHUB:
            continue
        try:
            resolution = resolver(doi)
        except Exception as e:
            log.append(f'{name}: error ({e})')
            continue
        if resolution:
            log.append(f'{name}: hit')
            break
        log.append(f'{name}: no copy found')

    if not resolution:
        return {'doi': doi, 'status': 'not_found', 'resolution_log': log}

    # Fill any metadata gaps from CrossRef (always needed for the Sci-Hub tier).
    if metadata is None and not (resolution['title'] and resolution['author']):
        metadata = get_crossref_metadata(doi)
    metadata = metadata or EMPTY_METADATA

    return {
        'doi': doi,
        'status': 'success',
        'pdf_url': resolution['pdf_url'],
        'landing_page_url': resolution['landing_page_url'],
        'source': resolution['source'],
        'is_oa': resolution['is_oa'],
        'oa_status': resolution['oa_status'],
        'license': resolution['license'],
        'version': resolution['version'],
        'title': resolution['title'] or metadata['title'],
        'author': resolution['author'] or metadata['author'],
        'year': resolution['year'] or metadata['year'],
        'resolution_log': log,
    }


def search_paper_by_title(title):
    """Find a paper by title via CrossRef, then resolve its DOI through the chain."""
    data = _get(CROSSREF_API, params={'query.title': title, 'rows': 1})
    if data:
        items = data.get('message', {}).get('items') or []
        if items:
            item = items[0]
            return search_paper_by_doi(item['DOI'], metadata=parse_crossref_item(item))

    return {'title': title, 'status': 'not_found'}


def search_papers_by_keyword(keyword, num_results=10):
    """Search CrossRef by keyword, then resolve each DOI through the chain."""
    papers = []
    data = _get(CROSSREF_API, params={'query': keyword, 'rows': num_results})
    if data:
        for item in data.get('message', {}).get('items') or []:
            doi = item.get('DOI')
            if doi:
                result = search_paper_by_doi(doi, metadata=parse_crossref_item(item))
                if result['status'] == 'success':
                    papers.append(result)
    return papers


def download_paper(pdf_url, output_path, source=None):
    """
    Download a PDF. Open-access URLs stream directly; Sci-Hub URLs go through
    the scihub library, which handles its mirror quirks.

    'source' is auto-detected from the URL when not supplied, so existing
    callers that pass only (pdf_url, output_path) still route correctly.
    """
    if source is None and 'sci-hub' in pdf_url.lower():
        source = 'scihub'
    try:
        if source == 'scihub':
            create_scihub_instance().download(pdf_url, output_path)
            return True

        response = requests.get(
            pdf_url,
            headers={'User-Agent': USER_AGENT},
            timeout=TIMEOUT,
            stream=True,
        )
        if response.status_code != 200:
            print(f"Download error: HTTP {response.status_code}")
            return False

        # A landing page instead of the file is the common failure here.
        content_type = response.headers.get('Content-Type', '')
        if 'pdf' not in content_type.lower():
            print(f"Download error: expected a PDF, got '{content_type}' ({pdf_url})")
            return False

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"Download error: {str(e)}")
        return False


if __name__ == "__main__":
    print("Paper resolution test (Unpaywall -> OpenAlex -> Sci-Hub)\n")
    if not CONTACT_EMAIL:
        print("WARNING: UNPAYWALL_EMAIL is not set, so Unpaywall will be skipped.\n")

    # 1. DOI search test
    print("1. Resolve a paper by DOI")
    test_doi = "10.1002/jcad.12075"
    result = search_paper_by_doi(test_doi)

    if result['status'] == 'success':
        print(f"Title: {result['title']}")
        print(f"Author: {result['author']}")
        print(f"Year: {result['year']}")
        print(f"Source: {result['source']} (open access: {result['is_oa']}, license: {result['license'] or 'n/a'})")
        print(f"PDF URL: {result['pdf_url']}")

        output_file = f"paper_{test_doi.replace('/', '_')}.pdf"
        if download_paper(result['pdf_url'], output_file, source=result['source']):
            print(f"Paper downloaded to: {output_file}")
        else:
            print("Paper download failed")
    else:
        print(f"No paper found with DOI {test_doi}")
    print(f"Tried: {result.get('resolution_log')}")

    # 2. Title search test
    print("\n2. Resolve a paper by title")
    test_title = "Choosing Assessment Instruments for Posttraumatic Stress Disorder Screening and Outcome Research"
    result = search_paper_by_title(test_title)

    if result['status'] == 'success':
        print(f"DOI: {result['doi']}")
        print(f"Author: {result['author']}")
        print(f"Year: {result['year']}")
        print(f"Source: {result['source']}")
        print(f"PDF URL: {result['pdf_url']}")
    else:
        print(f"No paper found with title '{test_title}'")

    # 3. Keyword search test
    print("\n3. Resolve papers by keyword")
    test_keyword = "artificial intelligence medicine 2023"
    papers = search_papers_by_keyword(test_keyword, num_results=3)

    for i, paper in enumerate(papers, 1):
        print(f"\nPaper {i}:")
        print(f"Title: {paper['title']}")
        print(f"DOI: {paper['doi']}")
        print(f"Author: {paper['author']}")
        print(f"Year: {paper['year']}")
        print(f"Source: {paper['source']}")
        if paper.get('pdf_url'):
            print(f"PDF URL: {paper['pdf_url']}")
