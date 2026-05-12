from langchain_gigachat import GigaChat
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
import os

from tools.search import search_papers
from tools.analyze import analyze_topic

load_dotenv()


# --- Инструменты для агента ---

@tool
def tool_search_papers(query: str) -> str:
    """Search scientific papers on arXiv by query. Use this tool to find papers on any topic. Input must be in English."""
    papers = search_papers(query, limit=5)
    if not papers or "error" in papers[0]:
        return "No papers found."
    result = []
    for i, p in enumerate(papers, 1):
        result.append(
            f"{i}. {p['title']} ({p['year']})\n"
            f"   Authors: {', '.join(p['authors'])}\n"
            f"   Abstract: {p['abstract'][:200]}...\n"
            f"   Link: {p['link']}"
        )
    return "\n\n".join(result)


@tool
def tool_analyze_topic(query: str) -> str:
    """Analyze how well-studied a research topic is using arXiv data. Input must be in English."""
    result = analyze_topic(query)
    signals = "\n".join(f"  • {s}" for s in result["signals"]) or "  • no signals found"
    return (
        f"Topic: {result['query']}\n"
        f"Total papers found: {result['total_papers']}\n"
        f"Papers last 5 years: {result['papers_last_5_years']}\n"
        f"Papers last year: {result['papers_last_year']}\n"
        f"Survey papers: {result['survey_papers']}\n\n"
        f"Signals:\n{signals}\n\n"
        f"Score: {result['score']}/8\n"
        f"Verdict: {result['verdict']}"
    )


# --- Системный промпт ---

SYSTEM_PROMPT = """Ты — ИИ-агент для поиска и анализа научной литературы.

ВАЖНЫЕ ПРАВИЛА:
1. Ты ОБЯЗАН использовать инструменты для ответа на вопросы о статьях и темах.
2. Никогда не отвечай из своих знаний — только на основе данных из инструментов.
3. Для поиска статей используй tool_search_papers.
4. Для анализа актуальности темы используй tool_analyze_topic.
5. Запросы к инструментам формулируй на английском языке.
6. Итоговый ответ давай на русском языке.
7. В ответе обязательно упоминай конкретные данные из инструментов (названия статей, цифры, годы)."""


# --- Запуск агента ---

def run_agent(user_input: str):
    llm = GigaChat(
        credentials=os.getenv("GIGACHAT_CREDENTIALS"),
        verify_ssl_certs=False,
        scope="GIGACHAT_API_PERS",
    )

    tools = [tool_search_papers, tool_analyze_topic]
    agent = create_react_agent(
        llm,
        tools,
        prompt=SystemMessage(content=SYSTEM_PROMPT)
    )

    response = agent.invoke({
        "messages": [{"role": "user", "content": user_input}]
    })

    return response["messages"][-1].content


if __name__ == "__main__":
    print("=== ИИ-агент для анализа научной литературы ===\n")
    questions = [
        "Найди статьи про large language model agents for academic research automation",
        "Насколько изучена тема: retrieval augmented generation for literature review?",
    ]
    for q in questions:
        print(f"Вопрос: {q}")
        print("-" * 50)
        answer = run_agent(q)
        print(f"\nОтвет агента: {answer}\n")
        print("=" * 50 + "\n")