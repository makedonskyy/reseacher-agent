import requests
import xml.etree.ElementTree as ET
import time
from datetime import datetime

NS = {"atom": "http://www.w3.org/2005/Atom"}


def _arxiv_search(query: str, limit: int = 100) -> list[dict]:
    """Внутренняя функция поиска через arXiv."""
    url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
        "sortOrder": "descending"
    }
    time.sleep(1)
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return []

    root = ET.fromstring(r.text)
    papers = []
    for entry in root.findall("atom:entry", NS):
        published = entry.find("atom:published", NS).text[:4]
        title = entry.find("atom:title", NS).text.strip()
        papers.append({"year": int(published), "title": title})
    return papers


def analyze_topic(query: str) -> dict:
    """Анализирует актуальность темы по признакам малоисследованности."""
    current_year = datetime.now().year
    recent_years = [current_year - 1, current_year]
    old_years_start = current_year - 5

    # 1. Общий поиск по теме
    print("  Загружаю публикации по теме...")
    all_papers = _arxiv_search(query, limit=100)
    total = len(all_papers)

    # 2. Публикации за последний год
    recent = [p for p in all_papers if p["year"] in recent_years]

    # 3. Публикации за последние 5 лет
    last_5 = [p for p in all_papers if p["year"] >= old_years_start]

    # 4. Поиск обзорных статей
    print("  Ищу обзорные статьи (survey/review)...")
    survey_papers = _arxiv_search(f"{query} survey review", limit=20)
    survey_count = len(survey_papers)

    # --- Оценка актуальности ---
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

    # Итоговый вердикт
    if score >= 6:
        verdict = "Тема слабо изучена — отличный выбор для исследования"
    elif score >= 3:
        verdict = "Тема умеренно изучена — есть пространство для вклада"
    else:
        verdict = "Тема хорошо изучена — нужна узкая специализация"

    return {
        "query": query,
        "total_papers": total,
        "papers_last_5_years": len(last_5),
        "papers_last_year": len(recent),
        "survey_papers": survey_count,
        "signals": signals,
        "score": score,
        "verdict": verdict
    }


if __name__ == "__main__":
    topic = "LLM agents scientific literature analysis"
    print(f"Анализирую тему: {topic}\n")
    result = analyze_topic(topic)

    print(f"\nВсего публикаций найдено:     {result['total_papers']}")
    print(f"За последние 5 лет:           {result['papers_last_5_years']}")
    print(f"За последний год:             {result['papers_last_year']}")
    print(f"Обзорных статей (survey):     {result['survey_papers']}")
    print(f"\nПризнаки малоисследованности:")
    for s in result['signals']:
        print(f"  • {s}")
    print(f"\nОценка: {result['score']}/8")
    print(f"Вывод: {result['verdict']}")