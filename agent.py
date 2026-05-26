from langchain_gigachat import GigaChat
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
import os

from tools.search import search_papers
from tools.analyze import analyze_topic
from tools.storage import save_papers, search_local, get_stats, get_user_papers

load_dotenv()


def _get_llm():
    return GigaChat(
        credentials=os.getenv("GIGACHAT_CREDENTIALS"),
        verify_ssl_certs=False,
        scope="GIGACHAT_API_PERS",
    )


def _parse_tool_input(raw: str) -> dict:
    parts = [p.strip() for p in raw.split("|")]
    result = {
        "query": parts[0],
        "limit": 5,
        "year_from": None,
        "year_to": None,
        "sort_by": "relevance",
        "search_type": "default"
    }
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            key, val = key.strip(), val.strip()
            if key == "year_from":
                try:
                    result["year_from"] = int(val)
                except ValueError:
                    pass
            elif key == "year_to":
                try:
                    result["year_to"] = int(val)
                except ValueError:
                    pass
            elif key == "sort_by" and val in ("relevance", "date", "citations"):
                result["sort_by"] = val
            elif key == "search_type" and val in ("default", "survey"):
                result["search_type"] = val
        else:
            try:
                result["limit"] = max(1, min(int(part), 50))
            except ValueError:
                pass
    return result


def _make_tools(user_id: str):

    @tool
    def tool_search_papers(query: str) -> str:
        """Search scientific papers on arXiv and OpenAlex, save to personal database.
        Input format: 'topic | limit | year_from=YEAR | year_to=YEAR | sort_by=relevance/date/citations | search_type=default/survey'
        Examples:
          'digital capitalism | 20 | year_from=2020 | sort_by=date'
          'Kant metaphysics | 15 | sort_by=citations'
          'LLM agents | 10 | search_type=survey'
        """
        params = _parse_tool_input(query)
        papers = search_papers(
            query=params["query"],
            limit=params["limit"],
            year_from=params["year_from"],
            year_to=params["year_to"],
            sort_by=params["sort_by"],
            search_type=params["search_type"]
        )
        if not papers or "error" in papers[0]:
            return f"Статьи не найдены: {papers[0].get('error', '') if papers else ''}"

        saved = save_papers(papers, query=params["query"], user_id=user_id)

        filters = []
        if params["year_from"]:
            filters.append(f"с {params['year_from']}")
        if params["year_to"]:
            filters.append(f"по {params['year_to']}")
        sort_label = {"relevance": "по релевантности", "date": "по дате", "citations": "по цитированиям"}
        filters.append(sort_label.get(params["sort_by"], ""))
        if params["search_type"] == "survey":
            filters.append("только обзорные")

        total_found = len(papers)
        result = [f"Найдено: {total_found} статей ({', '.join(f for f in filters if f)}), сохранено: {saved}\n"]

        # Для большого списка — компактный формат без аннотаций
        compact = total_found > 15
        for i, p in enumerate(papers, 1):
            cite_info = f" | {p.get('citations', 0)} цит." if params["sort_by"] == "citations" else ""
            if compact:
                result.append(
                    f"{i}. [{p.get('source')}] {p['title']} ({p['year']}){cite_info}\n"
                    f"   Ссылка: {p['link']}"
                )
            else:
                result.append(
                    f"{i}. [{p.get('source')}] {p['title']} ({p['year']}){cite_info}\n"
                    f"   Авторы: {', '.join(p['authors'])}\n"
                    f"   Аннотация: {p['abstract'][:100]}...\n"
                    f"   Ссылка: {p['link']}"
                )
        return "\n\n".join(result)

    @tool
    def tool_analyze_topic(query: str) -> str:
        """Analyze how well-studied a research topic is using arXiv and OpenAlex.
        Also saves found papers to personal database for future /suggest.
        Input must be in English."""
        # Сохраняем статьи в базу для последующего /suggest
        papers = search_papers(query=query, limit=20, sort_by="relevance")
        if papers and "error" not in papers[0]:
            save_papers(papers, query=query, user_id=user_id)

        result = analyze_topic(query)
        signals = "\n".join(f"  • {s}" for s in result["signals"]) or "  • признаков не выявлено"
        return (
            f"Тема: {result['query']}\n"
            f"Источник: {result['source']}\n"
            f"Всего публикаций: {result['total_papers']}\n"
            f"За последние 5 лет: {result['papers_last_5_years']}\n"
            f"За последний год: {result['papers_last_year']}\n"
            f"Обзорных статей: {result['survey_papers']}\n\n"
            f"Признаки:\n{signals}\n\n"
            f"Оценка: {result['score']}/8\n"
            f"Вывод: {result['verdict']}"
        )

    @tool
    def tool_search_local(query: str) -> str:
        """Search saved papers in personal database using Multi-Query semantic search.
        Input in Russian or English."""
        papers = search_local(query, n_results=10, user_id=user_id)
        if not papers:
            return "Личная база знаний пуста. Сначала найди статьи через tool_search_papers."
        result = [f"Найдено в личной базе: {len(papers)} статей\n"]
        for i, p in enumerate(papers, 1):
            result.append(
                f"{i}. [{p['relevance']}%] {p['title']} ({p['year']})\n"
                f"   Авторы: {p['authors']}\n"
                f"   {p['source']} | {p['link']}"
            )
        return "\n\n".join(result)

    @tool
    def tool_database_stats(query: str) -> str:
        """Show statistics about personal papers database. Input: any string."""
        stats = get_stats(user_id=user_id)
        if stats["total"] == 0:
            return "Личная база пуста."
        queries = "\n".join(f"  • {q}" for q in stats["queries"]) or "нет данных"
        sources = ", ".join(f"{s}: {n}" for s, n in stats["sources"].items())
        return (
            f"📚 Всего статей: {stats['total']}\n"
            f"Источники: {sources}\n\n"
            f"Темы:\n{queries}"
        )

    @tool
    def tool_summarize_collection(query: str) -> str:
        """Generate a literature review from all papers in personal database. Input: topic or 'all'."""
        papers = get_user_papers(user_id=user_id)
        if not papers:
            return "Личная база пуста."
        papers_text = "\n".join([
            f"- {p['title']} ({p['year']}, {p['source']})"
            for p in papers[:20]
        ])
        return (
            f"Список статей ({len(papers)} шт.):\n\n{papers_text}\n\n"
            f"Составь краткий обзор литературы на русском: "
            f"выдели основные темы, тенденции и пробелы."
        )

    return [
        tool_search_papers,
        tool_analyze_topic,
        tool_search_local,
        tool_database_stats,
        tool_summarize_collection
    ]


def suggest_topics(user_id: str, papers: list = None, topic: str = None) -> str:
    """
    Отдельная функция (не tool) — смотрит на статьи в базе,
    генерирует смежные подтемы через GigaChat, проверяет каждую
    через analyze_topic() и возвращает рекомендации.
    papers: если передан — использует их, иначе берёт все статьи пользователя.
    topic: тема для контекста в промпте.
    """
    if papers is None:
        papers = get_user_papers(user_id=user_id)
    if not papers:
        return "База знаний пуста. Сначала выполни /analyze или /search."

    print("[suggest] шаг 1: собираем заголовки")
    # Собираем заголовки для GigaChat
    titles = "\n".join(
        f"- {p['title']} ({p['year']})"
        for p in papers[:30]
    )

    topic_hint = f"Пользователь изучает тему: '{topic}'.\n\n" if topic else ""

    print("[suggest] шаг 2: вызываем GigaChat")
    # Шаг 1: GigaChat анализирует заголовки и предлагает подтемы
    llm = _get_llm()
    prompt = (
        f"{topic_hint}"
        f"Вот список научных статей по этой теме:\n\n{titles}\n\n"
        f"Предложи ровно 5 смежных подтем, которые:\n"
        f"1. Строго связаны с темой '{topic or 'из списка статей'}'\n"
        f"2. Вероятно менее изучены чем основная тема\n"
        f"3. Сформулированы на английском языке (для поиска в базах)\n"
        f"4. Являются узкими нишами внутри основной темы, а не другими темами\n\n"
        f"Ответь ТОЛЬКО списком из 5 подтем, каждая на отдельной строке, без нумерации и пояснений."
    )

    response = llm.invoke(prompt)
    subtopics_raw = response.content.strip().split("\n")
    subtopics = [s.strip().strip("-•").strip() for s in subtopics_raw if s.strip()][:5]

    if not subtopics:
        return "Не удалось сгенерировать подтемы. Попробуй сначала добавить больше статей."

    print(f"[suggest] шаг 3: подтемы={subtopics}")
    # Шаг 2: Проверяем только топ-3 подтемы через analyze_topic() с быстрым лимитом
    results = []
    for subtopic in subtopics[:3]:  # только 3 вместо 5
        try:
            # Используем уменьшенный лимит для скорости
            from tools.analyze import _arxiv_search, _openalex_search
            import datetime
            current_year = datetime.datetime.now().year
            recent_years = [current_year - 1, current_year]

            all_papers = _arxiv_search(subtopic, limit=10)  # 10 вместо 50
            source = "arXiv"
            if not all_papers:
                all_papers = _openalex_search(subtopic, limit=10)
                source = "OpenAlex"

            total = len(all_papers)
            recent = len([p for p in all_papers if p["year"] in recent_years])

            # Быстрая оценка
            score = 0
            if total < 5:
                score += 3
            elif total < 10:
                score += 1
            if recent >= 2:
                score += 2

            results.append({
                "topic": subtopic,
                "score": score,
                "total": total,
                "recent": recent,
                "source": source
            })
        except Exception:
            continue

    if not results:
        return "Не удалось проанализировать подтемы. Попробуй позже."

    print(f"[suggest] шаг 4: результатов={len(results)}")
    # Шаг 3: Сортируем — чем выше score, тем менее изучена (интереснее)
    results.sort(key=lambda x: x["score"], reverse=True)

    print("[suggest] шаг 5: финальные рекомендации")
    # Шаг 4: GigaChat формулирует финальные рекомендации
    analysis_text = "\n".join([
        f"- {r['topic']}: оценка {r['score']}/8, {r['total']} публикаций, "
        f"{r['recent']} за последний год, источник: {r['source']}"
        for r in results
    ])

    recommendation_prompt = (
        f"Вот результаты анализа смежных подтем (чем выше оценка — тем менее изучена тема):\n\n"
        f"{analysis_text}\n\n"
        f"Напиши рекомендации на русском языке:\n"
        f"1. Выдели топ-3 наиболее перспективные темы для исследования\n"
        f"2. Для каждой объясни в 1-2 предложениях почему она интересна\n"
        f"3. Предложи как можно сузить тему для оригинального вклада\n"
        f"Будь конкретным и практичным."
    )

    final = llm.invoke(recommendation_prompt)

    # Формируем полный ответ
    header = "🔎 Анализ смежных подтем:\n\n"
    table = "\n".join([
        f"{'🟢' if r['score'] >= 4 else '🟡' if r['score'] >= 2 else '🔴'} "
        f"{r['topic']}\n"
        f"   Публикаций: {r['total']} | За год: {r['recent']} | Оценка: {r['score']}/8"
        for r in results
    ])
    recommendations = f"\n\n💡 Рекомендации:\n\n{final.content}"

    print("[suggest] готово, возвращаем ответ")
    return header + table + recommendations


SYSTEM_PROMPT = """Ты — ИИ-агент для поиска и анализа научной литературы.

ПРАВИЛА:
1. ВСЕГДА используй инструменты — не отвечай из своих знаний.
2. Для поиска статей: tool_search_papers
   Формат: 'тема | кол-во | year_from=ГОД | sort_by=relevance/date/citations | search_type=default/survey'
3. Для поиска по базе: tool_search_local
4. Для анализа актуальности: tool_analyze_topic
5. Для статистики: tool_database_stats
6. Для обзора коллекции: tool_summarize_collection
7. Отвечай на русском, всегда показывай ссылки.

КРИТИЧЕСКИ ВАЖНО при выводе результатов поиска:
- НИКОГДА не суммаризируй и не группируй статьи по темам
- ВСЕГДА выводи ПОЛНЫЙ список всех найденных статей как есть из инструмента
- Если найдено 30 статей — выведи все 30, не меньше
- Добавь только краткое вступление (1-2 предложения) и больше ничего не добавляй"""


def run_agent(user_input: str, user_id: str = "default", retries: int = 2):
    """Запускает агента с повтором при SSL/сетевых ошибках."""
    import time
    last_error = None
    for attempt in range(retries + 1):
        try:
            llm = _get_llm()
            tools = _make_tools(user_id=user_id)
            agent = create_react_agent(
                llm, tools,
                prompt=SystemMessage(content=SYSTEM_PROMPT)
            )
            response = agent.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                config={"recursion_limit": 10}
            )
            return response["messages"][-1].content
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(3 + attempt * 2)
                continue
    raise last_error


if __name__ == "__main__":
    print("=== Тест suggest_topics ===\n")
    # Сначала добавим данные через analyze
    run_agent("Analyze how well-studied: platform capitalism", user_id="test")
    # Потом получим рекомендации
    print(suggest_topics(user_id="test"))