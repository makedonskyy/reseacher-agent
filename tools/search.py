import requests
import xml.etree.ElementTree as ET
import time

NS = {"atom": "http://www.w3.org/2005/Atom"}


def search_papers(query: str, limit: int = 5) -> list[dict]:
    """Ищет научные статьи по запросу через arXiv API."""
    url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
        "sortOrder": "descending"
    }
    time.sleep(1)
    r = requests.get(url, params=params)

    if r.status_code != 200:
        return [{"error": f"API вернул {r.status_code}"}]

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
            "year": published,
            "authors": authors,
            "abstract": abstract[:300],
            "link": link
        })

    return papers


if __name__ == "__main__":
    results = search_papers("LLM agents for research automation")
    for p in results:
        print(f"{p['year']} | {p['title'][:60]}")
        print(f"  Авторы: {', '.join(p['authors'])}")
        print(f"  Аннотация: {p['abstract'][:120]}...")
        print(f"  Ссылка: {p['link']}\n")