import requests
import xml.etree.ElementTree as ET
import time
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NS = {"atom": "http://www.w3.org/2005/Atom"}
OPENALEX_EMAIL = "agent@research.bot"


def _arxiv_search(query: str, limit: int = 50) -> list[dict]:
    """Поиск через arXiv."""
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
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
                published = entry.find("atom:published", NS).text[:4]
                title = entry.find("atom:title", NS).text.strip()
                papers.append({"year": int(published), "title": title})
            return papers
        except requests.exceptions.Timeout:
            if attempt == 2:
                return []
            continue
        except Exception:
            return []
    return []


def _openalex_search(query: str, limit: int = 50) -> list[dict]:
    """Поиск через OpenAlex — все науки, без ключа."""
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per-page": min(limit, 100),
        "mailto": OPENALEX_EMAIL
    }
    for attempt in range(3):
        try:
            time.sleep(0.5 + attempt)
            r = requests.get(url, params=params, timeout=30)
            if r.status_code != 200:
                return []
            results = r.json().get("results", [])
            papers = []
            for p in results:
                year = p.get("publication_year")
                title = p.get("title") or ""
                if year and title:
                    papers.append({"year": int(year), "title": title})
            return papers
        except requests.exceptions.Timeout:
            if attempt == 2:
                return []
            continue
        except Exception:
            return []
    return []


def _semantic_scholar_search(query: str, limit: int = 50) -> list[dict]:
    """Фолбэк через Semantic Scholar (медленный, rate limit)."""
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "fields": "title,year",
        "limit": min(limit, 100)
    }
    for attempt in range(3):
        try:
            time.sleep(10 + attempt * 5)
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                if attempt == 2:
                    return []
                continue
            if r.status_code != 200:
                return []
            data = r.json().get("data", [])
            papers = []
            for p in data:
                if p.get("year") and p.get("title"):
                    papers.append({"year": int(p["year"]), "title": p["title"]})
            return papers
        except requests.exceptions.Timeout:
            if attempt == 2:
                return []
            continue
        except Exception:
            return []
    return []


def analyze_topic(query: str) -> dict:
    """Анализирует актуальность темы: arXiv → OpenAlex → Semantic Scholar."""
    current_year = datetime.now().year
    recent_years = [current_year - 1, current_year]
    old_years_start = current_year - 5

    # 1. arXiv
    all_papers = _arxiv_search(query, limit=50)
    source = "arXiv"

    # 2. OpenAlex если arXiv пустой
    if len(all_papers) == 0:
        all_papers = _openalex_search(query, limit=50)
        source = "OpenAlex"

    # 3. Semantic Scholar как последний резерв
    if len(all_papers) == 0:
        all_papers = _semantic_scholar_search(query, limit=50)
        source = "Semantic Scholar"

    total = len(all_papers)
    recent = [p for p in all_papers if p["year"] in recent_years]
    last_5 = [p for p in all_papers if p["year"] >= old_years_start]

    # Обзорные статьи — arXiv и OpenAlex
    survey_papers = _arxiv_search(f"{query} survey review", limit=10)
    if len(survey_papers) == 0:
        survey_papers = _openalex_search(f"{query} survey review", limit=10)
    survey_count = len(survey_papers)

    signals = []
    score = 0

    if total < 20:
        signals.append("мало публикаций по теме в целом")
        score += 2
    elif total < 50:
        signals.append("умеренное число публикаций")
        score += 1

    if len(last_5) < 10:
        signals.append("мало работ за последние 5 лет")
        score += 2

    if len(recent) >= 3:
        signals.append("резкий рост публикаций в последний год")
        score += 2

    if survey_count < 3:
        signals.append("нет обзорных статей (survey/review)")
        score += 2

    if total == 0:
        verdict = "Тема не найдена ни в одном источнике — попробуй переформулировать запрос на английском"
    elif score >= 6:
        verdict = "Тема слабо изучена — отличный выбор для исследования"
    elif score >= 3:
        verdict = "Тема умеренно изучена — есть пространство для вклада"
    else:
        verdict = "Тема хорошо изучена — нужна узкая специализация"

    return {
        "query": query,
        "source": source,
        "total_papers": total,
        "papers_last_5_years": len(last_5),
        "papers_last_year": len(recent),
        "survey_papers": survey_count,
        "signals": signals,
        "score": score,
        "verdict": verdict
    }


if __name__ == "__main__":
    for topic in ["digital capitalism", "LLM agents scientific literature"]:
        print(f"Анализирую: {topic}")
        result = analyze_topic(topic)
        print(f"  Источник: {result['source']}")
        print(f"  Публикаций: {result['total_papers']}")
        print(f"  Вывод: {result['verdict']}\n")