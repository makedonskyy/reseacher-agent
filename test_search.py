import requests
import xml.etree.ElementTree as ET

def test_arxiv():
    url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": "all:LLM agents literature review",
        "start": 0,
        "max_results": 3
    }
    r = requests.get(url, params=params)
    root = ET.fromstring(r.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = entry.find("atom:title", ns).text.strip()
        year = entry.find("atom:published", ns).text[:4]
        print(f"{year} | {title[:60]}")

test_arxiv()