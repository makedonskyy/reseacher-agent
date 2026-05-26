import chromadb
from chromadb.utils import embedding_functions
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "chroma_db")
OPENALEX_EMAIL = "agent@research.bot"


def _get_collection():
    """Возвращает коллекцию ChromaDB."""
    client = chromadb.PersistentClient(path=DB_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    return client.get_or_create_collection(
        name="papers",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )


def save_papers(papers: list[dict], query: str, user_id: str = "default") -> int:
    """Сохраняет статьи в ChromaDB с привязкой к пользователю."""
    collection = _get_collection()
    saved = 0

    for paper in papers:
        link = paper.get("link", "")
        if not link:
            continue

        # Уникальный ID = user_id + ссылка (дедупликация по пользователю)
        doc_id = f"{user_id}_{link.split('/')[-1]}"
        text = f"{paper.get('title', '')}. {paper.get('abstract', '')}"

        metadata = {
            "title": paper.get("title", "")[:500],
            "year": str(paper.get("year", "")),
            "authors": ", ".join(paper.get("authors", []))[:300],
            "link": link,
            "query": query[:200],
            "user_id": user_id,
            "source": paper.get("source", "unknown")
        }

        try:
            collection.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata]
            )
            saved += 1
        except Exception as e:
            print(f"Ошибка сохранения {doc_id}: {e}")

    return saved


def search_local(query: str, n_results: int = 5, user_id: str = "default") -> list[dict]:
    """Multi-Query семантический поиск по базе пользователя."""
    collection = _get_collection()

    # Фильтруем только статьи этого пользователя
    all_docs = collection.get(where={"user_id": user_id}, include=["metadatas"])
    user_count = len(all_docs["ids"])

    if user_count == 0:
        return []

    # Multi-Query: формируем несколько вариантов запроса
    queries = [
        query,
        f"research about {query}",
        f"{query} analysis study"
    ]

    seen_ids = set()
    all_results = []

    for q in queries:
        try:
            n = min(n_results, user_count)
            results = collection.query(
                query_texts=[q],
                n_results=n,
                where={"user_id": user_id}
            )
            for i, doc_id in enumerate(results["ids"][0]):
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    meta = results["metadatas"][0][i]
                    distance = results["distances"][0][i]
                    relevance = round((1 - distance) * 100, 1)
                    all_results.append({
                        "title": meta.get("title", ""),
                        "year": meta.get("year", ""),
                        "authors": meta.get("authors", ""),
                        "link": meta.get("link", ""),
                        "query": meta.get("query", ""),
                        "source": meta.get("source", ""),
                        "relevance": relevance
                    })
        except Exception:
            continue

    # Сортируем по релевантности и берём топ
    all_results.sort(key=lambda x: x["relevance"], reverse=True)
    return all_results[:n_results]


def get_user_papers(user_id: str = "default") -> list[dict]:
    """Возвращает все статьи пользователя для генерации резюме."""
    collection = _get_collection()
    try:
        result = collection.get(
            where={"user_id": user_id},
            include=["metadatas"]
        )
        papers = []
        for meta in result["metadatas"]:
            papers.append({
                "title": meta.get("title", ""),
                "year": meta.get("year", ""),
                "authors": meta.get("authors", ""),
                "link": meta.get("link", ""),
                "query": meta.get("query", ""),
                "source": meta.get("source", "")
            })
        return papers
    except Exception:
        return []


def get_stats(user_id: str = "default") -> dict:
    """Статистика базы знаний пользователя."""
    collection = _get_collection()

    try:
        result = collection.get(
            where={"user_id": user_id},
            include=["metadatas"]
        )
        count = len(result["ids"])
        if count == 0:
            return {"total": 0, "queries": [], "sources": {}}

        queries = list(set(m.get("query", "") for m in result["metadatas"]))
        sources = {}
        for m in result["metadatas"]:
            s = m.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1

        return {
            "total": count,
            "queries": queries[:10],
            "sources": sources
        }
    except Exception:
        return {"total": 0, "queries": [], "sources": {}}


def get_papers_by_topic(topic: str, user_id: str = "default") -> list[dict]:
    """Возвращает статьи пользователя по конкретной теме поиска."""
    collection = _get_collection()
    try:
        result = collection.get(
            where={"$and": [{"user_id": user_id}, {"query": topic}]},
            include=["metadatas"]
        )
        papers = []
        for meta in result["metadatas"]:
            papers.append({
                "title": meta.get("title", ""),
                "year": meta.get("year", ""),
                "authors": meta.get("authors", ""),
                "link": meta.get("link", ""),
                "query": meta.get("query", ""),
                "source": meta.get("source", "")
            })
        return papers
    except Exception:
        return []


def find_matching_query(topic: str, user_id: str = "default") -> str | None:
    """Ищет похожую тему в истории запросов пользователя."""
    stats = get_stats(user_id=user_id)
    queries = stats.get("queries", [])

    # Точное совпадение
    topic_lower = topic.lower().strip()
    for q in queries:
        if q.lower().strip() == topic_lower:
            return q

    # Частичное совпадение
    for q in queries:
        if topic_lower in q.lower() or q.lower() in topic_lower:
            return q

    return None


if __name__ == "__main__":
    # Тест персональных коллекций
    test_papers = [
        {
            "title": "RAG for Knowledge-Intensive NLP",
            "abstract": "We explore retrieval-augmented generation.",
            "year": "2020",
            "authors": ["Patrick Lewis"],
            "link": "https://arxiv.org/abs/2005.11401",
            "source": "arXiv"
        },
        {
            "title": "LLM Agents for Literature Review",
            "abstract": "Agent that automates literature search.",
            "year": "2024",
            "authors": ["John Smith"],
            "link": "https://arxiv.org/abs/2401.00001",
            "source": "arXiv"
        }
    ]

    print("Сохраняю для user_1...")
    n = save_papers(test_papers, query="RAG literature", user_id="user_1")
    print(f"Сохранено: {n}\n")

    print("Multi-Query поиск для user_1:")
    results = search_local("language models retrieval", user_id="user_1")
    for r in results:
        print(f"  {r['relevance']}% | {r['title'][:55]}")

    print("\nСтатистика user_1:")
    stats = get_stats(user_id="user_1")
    print(f"  Статей: {stats['total']}, Источники: {stats['sources']}")

    print("\nСтатистика user_2 (пустая):")
    stats2 = get_stats(user_id="user_2")
    print(f"  Статей: {stats2['total']}")