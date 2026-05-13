import chromadb
from chromadb.utils import embedding_functions
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "chroma_db")

def _get_collection():
    """Возвращает коллекцию ChromaDB с sentence-transformers эмбеддингами."""
    client = chromadb.PersistentClient(path=DB_PATH)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    return client.get_or_create_collection(
        name="papers",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )


def save_papers(papers: list[dict], query: str) -> int:
    """Сохраняет список статей в ChromaDB. Возвращает число сохранённых."""
    collection = _get_collection()
    saved = 0

    for paper in papers:
        link = paper.get("link", "")
        if not link:
            continue

        doc_id = link.split("/")[-1]

        text = f"{paper.get('title', '')}. {paper.get('abstract', '')}"

        metadata = {
            "title": paper.get("title", "")[:500],
            "year": str(paper.get("year", "")),
            "authors": ", ".join(paper.get("authors", []))[:300],
            "link": link,
            "query": query[:200]
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


def search_local(query: str, n_results: int = 5) -> list[dict]:
    """Семантический поиск по сохранённым статьям."""
    collection = _get_collection()

    count = collection.count()
    if count == 0:
        return []

    n_results = min(n_results, count)

    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    papers = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        relevance = round((1 - distance) * 100, 1)

        papers.append({
            "title": meta.get("title", ""),
            "year": meta.get("year", ""),
            "authors": meta.get("authors", ""),
            "link": meta.get("link", ""),
            "query": meta.get("query", ""),
            "relevance": relevance
        })

    return papers


def get_stats() -> dict:
    """Статистика базы знаний."""
    collection = _get_collection()
    count = collection.count()

    if count == 0:
        return {"total": 0, "queries": []}

    all_meta = collection.get(include=["metadatas"])
    queries = list(set(
        m.get("query", "") for m in all_meta["metadatas"]
    ))

    return {
        "total": count,
        "queries": queries[:10]
    }


if __name__ == "__main__":
    test_papers = [
        {
            "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            "abstract": "We explore a general-purpose fine-tuning recipe for retrieval-augmented generation.",
            "year": "2020",
            "authors": ["Patrick Lewis", "Ethan Perez"],
            "link": "https://arxiv.org/abs/2005.11401"
        },
        {
            "title": "LLM Agents for Scientific Literature Review",
            "abstract": "We present an agent that automates literature search and analysis using large language models.",
            "year": "2024",
            "authors": ["John Smith", "Jane Doe"],
            "link": "https://arxiv.org/abs/2401.00001"
        }
    ]

    print("Сохраняю тестовые статьи...")
    n = save_papers(test_papers, query="RAG NLP literature review")
    print(f"Сохранено: {n} статей\n")

    print("Ищу по базе: 'language model retrieval'")
    results = search_local("language model retrieval")
    for r in results:
        print(f"  {r['relevance']}% | {r['title'][:60]}")
        print(f"           {r['link']}\n")

    stats = get_stats()
    print(f"Всего в базе: {stats['total']} статей")
    print(f"Запросы: {stats['queries']}")