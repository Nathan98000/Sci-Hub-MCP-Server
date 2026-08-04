from scihub import SciHub
import urllib3
import requests

# Disable HTTPS certificate verification warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CROSSREF_API = "https://api.crossref.org/works"


def create_scihub_instance():
    """Create and configure a SciHub instance"""
    sh = SciHub()
    sh.timeout = 30  # Increase timeout to 30 seconds
    return sh


def parse_crossref_item(item):
    """Pull title / author / year out of a CrossRef work record"""
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
    """Look up a DOI's metadata on CrossRef (Sci-Hub itself returns none)"""
    try:
        # The DOI belongs in the URL path here, so it is not a query parameter.
        response = requests.get(f"{CROSSREF_API}/{doi}", timeout=30)
        if response.status_code == 200:
            return parse_crossref_item(response.json()['message'])
    except Exception as e:
        print(f"CrossRef metadata error: {str(e)}")

    return {'title': '', 'author': '', 'year': ''}


def search_paper_by_doi(doi, metadata=None):
    """Search for a paper on Sci-Hub by DOI"""
    sh = create_scihub_instance()
    if metadata is None:
        metadata = get_crossref_metadata(doi)
    try:
        result = sh.fetch(doi)
        return {
            'doi': doi,
            'pdf_url': result['url'],
            'status': 'success',
            'title': metadata['title'],
            'author': metadata['author'],
            'year': metadata['year']
        }
    except Exception as e:
        print(f"Search error: {str(e)}")
        return {
            'doi': doi,
            'status': 'not_found'
        }


def search_paper_by_title(title):
    """Search for a paper on Sci-Hub by title"""
    # The SciHub package has no search method, so we search by DOI instead.
    # First try to get the DOI from CrossRef.
    try:
        params = {'query.title': title, 'rows': 1}
        response = requests.get(CROSSREF_API, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if data['message']['items']:
                item = data['message']['items'][0]
                doi = item['DOI']
                return search_paper_by_doi(doi, metadata=parse_crossref_item(item))
    except Exception as e:
        print(f"CrossRef search error: {str(e)}")

    return {
        'title': title,
        'status': 'not_found'
    }


def search_papers_by_keyword(keyword, num_results=10):
    """Search papers by keyword and return a list of metadata"""
    # Use the CrossRef API to search
    papers = []
    try:
        params = {'query': keyword, 'rows': num_results}
        response = requests.get(CROSSREF_API, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            for item in data['message']['items']:
                doi = item.get('DOI')
                if doi:
                    result = search_paper_by_doi(doi, metadata=parse_crossref_item(item))
                    if result['status'] == 'success':
                        papers.append(result)
    except Exception as e:
        print(f"Search error: {str(e)}")

    return papers


def download_paper(pdf_url, output_path):
    """Download the paper PDF"""
    sh = SciHub()
    try:
        sh.download(pdf_url, output_path)
        return True
    except Exception as e:
        print(f"Download error: {str(e)}")
        return False


if __name__ == "__main__":
    print("Sci-Hub paper search test\n")

    # 1. DOI search test
    print("1. Search for a paper by DOI")
    test_doi = "10.1002/jcad.12075"  # an article from a counseling journal
    result = search_paper_by_doi(test_doi)

    if result['status'] == 'success':
        print(f"Title: {result['title']}")
        print(f"Author: {result['author']}")
        print(f"Year: {result['year']}")
        print(f"PDF URL: {result['pdf_url']}")

        # Try to download the paper
        output_file = f"paper_{test_doi.replace('/', '_')}.pdf"
        if download_paper(result['pdf_url'], output_file):
            print(f"Paper downloaded to: {output_file}")
        else:
            print("Paper download failed")
    else:
        print(f"No paper found with DOI {test_doi}")

    # 2. Title search test
    print("\n2. Search for a paper by title")
    test_title = "Choosing Assessment Instruments for Posttraumatic Stress Disorder Screening and Outcome Research"
    result = search_paper_by_title(test_title)

    if result['status'] == 'success':
        print(f"DOI: {result['doi']}")
        print(f"Author: {result['author']}")
        print(f"Year: {result['year']}")
        print(f"PDF URL: {result['pdf_url']}")
    else:
        print(f"No paper found with title '{test_title}'")

    # 3. Keyword search test
    print("\n3. Search for papers by keyword")
    test_keyword = "artificial intelligence medicine 2023"
    papers = search_papers_by_keyword(test_keyword, num_results=3)

    for i, paper in enumerate(papers, 1):
        print(f"\nPaper {i}:")
        print(f"Title: {paper['title']}")
        print(f"DOI: {paper['doi']}")
        print(f"Author: {paper['author']}")
        print(f"Year: {paper['year']}")
        if paper.get('pdf_url'):
            print(f"PDF URL: {paper['pdf_url']}")
