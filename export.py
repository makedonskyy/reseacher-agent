import csv
import io
from tools.storage import get_user_papers, get_papers_by_topic


def export_to_csv(user_id: str, topic: str = "all") -> tuple[bytes, int]:
    """
    Экспортирует статьи пользователя в CSV.
    topic='all' — все статьи, иначе — только по теме.
    """
    if topic == "all":
        papers = get_user_papers(user_id=user_id)
    else:
        papers = get_papers_by_topic(topic=topic, user_id=user_id)

    if not papers:
        return b"", 0

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["title", "authors", "year", "source", "link", "query"],
        extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(papers)

    return output.getvalue().encode("utf-8-sig"), len(papers)