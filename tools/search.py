import requests
import xml.etree.ElementTree as ET
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NS = {"atom": "http://www.w3.org/2005/Atom"}
OPENALEX_EMAIL = "agent@research.bot"


def _search_arxiv(query: str, limit: int = 5) -> list[dict]:
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(limit, 100),
        "sortBy": "relevance",
        "sortOrder": "descending"
    }
    for attempt in range(3):
        try:
            time.sleep(0.5 + attempt)
            r = requests.get(url, params=params, verify=False, timeout=30)
            if r.status_code != 200:
                return []
            root = ET.fromstring(r.text)
            papers = []
            for entry in root.findall("atom:entry", NS):
                title = entry.find("atom:title", NS).text.strip().replace("\n", " ")
                abstract = entry.find("atom:summary", NS).text.strip().replace("\n", " ")
                published = entry.find("atom:published", NS).text[:4]
                link = entry.find("atom:id", NS).text.strip()
                authors = [
                    a.find("atom:name", NS).text
                    for a in entry.findall("atom:author", NS)[:3]
                ]
                papers.append({
                    "title": title,
                    "year": int(published),
                    "authors": authors,
                    "abstract": abstract[:300],
                    "link": link,
                    "source": "arXiv",
                    "citations": 0
                })
            return papers
        except requests.exceptions.Timeout:
            if attempt == 2:
                return []
            continue
        except Exception:
            return []
    return []


def _search_openalex(query: str, limit: int = 5, sort: str = "relevance_score:desc",
                     year_from: int = None, year_to: int = None) -> list[dict]:
    url = "https://api.openalex.org/works"
    all_papers = []
    page = 1
    per_page = min(25, limit)

    # Фильтр по годам
    filters = []
    if year_from:
        filters.append(f"publication_year:>{year_from - 1}")
    if year_to:
        filters.append(f"publication_year:<{year_to + 1}")
    filter_str = ",".join(filters) if filters else None

    while len(all_papers) < limit:
        params = {
            "search": query,
            "per-page": per_page,
            "page": page,
            "sort": sort,
            "mailto": OPENALEX_EMAIL
        }
        if filter_str:
            params["filter"] = filter_str

        for attempt in range(3):
            try:
                time.sleep(0.5 + attempt)
                r = requests.get(url, params=params, timeout=30)
                if r.status_code != 200:
                    return all_papers
                results = r.json().get("results", [])
                if not results:
                    return all_papers
                for p in results:
                    title = p.get("title") or "Без названия"
                    year = p.get("publication_year") or 0
                    authors = [
                        a.get("author", {}).get("display_name", "")
                        for a in p.get("authorships", [])[:3]
                    ]
                    abstract = p.get("abstract") or ""
                    doi = p.get("doi") or ""
                    link = doi if doi else p.get("id", "")
                    citations = p.get("cited_by_count", 0)
                    all_papers.append({
                        "title": title,
                        "year": int(year),
                        "authors": authors,
                        "abstract": abstract[:300],
                        "link": link,
                        "source": "OpenAlex",
                        "citations": citations
                    })
                if len(results) < per_page:
                    return all_papers
                break
            except requests.exceptions.Timeout:
                if attempt == 2:
                    return all_papers
                continue
            except Exception:
                return all_papers
        page += 1

    return all_papers[:limit]


def search_papers(query: str, limit: int = 5,
                  year_from: int = None, year_to: int = None,
                  sort_by: str = "relevance",
                  search_type: str = "default") -> list[dict]:
    """
    Ищет статьи с фильтрами.
    sort_by: 'relevance' | 'citations' | 'date'
    search_type: 'default' | 'classic' | 'survey'
    """
    # Для survey добавляем ключевые слова
    if search_type == "survey":
        query = f"{query} survey review"

    # Выбираем сортировку для OpenAlex
    if sort_by == "citations":
        oa_sort = "cited_by_count:desc"
    elif sort_by == "date":
        oa_sort = "publication_date:desc"
    else:
        oa_sort = "relevance_score:desc"

    papers = []
    seen_titles = set()

    # arXiv (без фильтра по годам и цитированиям — добавим вручную)
    if sort_by != "citations":  # arXiv не умеет сортировать по цитированиям
        arxiv_papers = _search_arxiv(query, limit=min(limit, 100))
        for p in arxiv_papers:
            # Фильтр по годам
            if year_from and p["year"] < year_from:
                continue
            if year_to and p["year"] > year_to:
                continue
            key = p["title"].lower()[:60]
            if key not in seen_titles:
                seen_titles.add(key)
                papers.append(p)

    # OpenAlex — основной источник для classic/survey и гуманитарных тем
    needed = limit - len(papers)
    if needed > 0:
        oa_papers = _search_openalex(
            query, limit=needed + 20,
            sort=oa_sort,
            year_from=year_from,
            year_to=year_to
        )
        for p in oa_papers:
            key = p["title"].lower()[:60]
            if key not in seen_titles:
                seen_titles.add(key)
                papers.append(p)
            if len(papers) >= limit:
                break

    if not papers:
        return [{"error": "Статьи не найдены"}]

    # Финальная сортировка
    if sort_by == "citations":
        papers.sort(key=lambda x: x.get("citations", 0), reverse=True)
    elif sort_by == "date":
        papers.sort(key=lambda x: x.get("year", 0), reverse=True)

    return papers[:limit]


if __name__ == "__main__":
    print("=== Тест фильтров ===\n")

    print("1. Свежие статьи (с 2023):")
    r = search_papers("digital capitalism", limit=5, year_from=2023, sort_by="date")
    for p in r:
        print(f"  {p['year']} | {p['title'][:55]} [{p['source']}]")

    print("\n2. Классические (по цитированиям):")
    r = search_papers("Kant metaphysics", limit=5, sort_by="citations")
    for p in r:
        print(f"  {p['year']} | {p['citations']} цит. | {p['title'][:45]} [{p['source']}]")

    print("\n3. Только обзорные:")
    r = search_papers("LLM agents", limit=5, search_type="survey")
    for p in r:
        print(f"  {p['year']} | {p['title'][:55]} [{p['source']}]")