# -*- coding: utf-8 -*-
"""
measure_metrics.py — снятие количественных метрик для главы 3 ВКР.

Запускать ИЗ КОРНЯ проекта (там же, где bot.py), где работают API:
    python measure_metrics.py

Скрипт:
  1. Прогоняет тестовые сценарии (поиск, анализ темы, семантический поиск,
     изоляция пользователей, экспорт) по 3 повтора каждый.
  2. Замеряет время, число найденных публикаций.
  3. Сохраняет полную выдачу в results_for_relevance.txt — по ней вы
     вручную проставляете релевантность (релевантна статья теме или нет).
  4. Печатает готовую таблицу со средними значениями.

Сценарии /summary и агентный цикл (GigaChat) НЕ замеряются автоматически —
их время снимается вручную в боте, т.к. зависит от внешней LLM.
"""

import time
import statistics
from tools.search import search_papers
from tools.analyze import analyze_topic
from tools.storage import save_papers, search_local, get_stats
from export import export_to_csv

REPEATS = 3  # число повторов для усреднения времени


def log(msg):
    """Печать с немедленным сбросом буфера — прогресс виден сразу."""
    print(msg, flush=True)


def timed(fn, *args, **kwargs):
    """Запускает fn, возвращает (результат, время_сек)."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    dt = time.perf_counter() - t0
    return result, dt


def count_papers(result):
    """Считает публикации в выдаче search_papers (с учётом маркера ошибки)."""
    if not result:
        return 0
    if isinstance(result, list) and len(result) == 1 and "error" in result[0]:
        return 0
    return len(result)


def run_search_scenario(name, query, **kwargs):
    """Сценарий поиска: 3 повтора, усреднение времени, выдача для оценки."""
    log("\n→ %s | запрос: %s" % (name, query))
    times, counts, last_result = [], [], []
    for i in range(REPEATS):
        log("    повтор %d/%d ..." % (i + 1, REPEATS))
        res, dt = timed(search_papers, query, **kwargs)
        n = count_papers(res)
        log("      готово за %.2f с, найдено %d" % (dt, n))
        times.append(dt)
        counts.append(n)
        last_result = res
    return {
        "name": name,
        "query": query,
        "avg_time": statistics.mean(times),
        "count": statistics.median(counts),  # медиана числа публикаций
        "result": last_result,
    }


def main():
    rows = []
    relevance_dump = []

    log("Запуск замеров (по %d повтора на сценарий)..." % REPEATS)
    log("Скрипт обращается к arXiv и OpenAlex по сети — между запросами")
    log("есть паузы, поэтому каждый сценарий занимает несколько секунд.\n")

    # --- Сценарий 1: техническая тема ---
    rows.append(run_search_scenario("1. Техническая тема", "LLM agents", limit=10))

    # --- Сценарий 2: гуманитарная тема + фильтр по году ---
    rows.append(run_search_scenario("2. Гуманитарная тема", "digital capitalism",
                                     limit=10, year_from=2020))

    # --- Сценарий 3: классика по цитированиям ---
    rows.append(run_search_scenario("3. Классика по цитированиям", "Kant metaphysics",
                                     limit=10, sort_by="citations"))

    # --- Сценарий 4: только обзорные ---
    rows.append(run_search_scenario("4. Обзорные статьи", "transformer architecture",
                                     limit=10, search_type="survey"))

    # --- Сценарий 5: анализ актуальности темы ---
    log("\n→ 5. Анализ актуальности | запрос: LLM agents")
    log("    (внутри несколько запросов — может занять дольше) ...")
    res, dt = timed(analyze_topic, "LLM agents")
    log("      готово за %.2f с, всего публикаций %d" % (dt, res.get("total_papers", 0)))
    rows.append({
        "name": "5. Анализ актуальности", "query": "LLM agents",
        "avg_time": dt, "count": res.get("total_papers", 0),
        "result": [{"verdict": res.get("verdict"), "score": res.get("score"),
                    "signals": res.get("signals")}],
    })

    # --- Сценарий 6: сохранение + семантический поиск (Multi-Query) ---
    log("\n→ 6. Семантический поиск (Multi-Query) | запрос: neural attention")
    log("    сначала собираю базу по 'attention mechanism' ...")
    base, _ = timed(search_papers, "attention mechanism", limit=10)
    save_papers(base, query="attention mechanism", user_id="metrics_user")
    res, dt = timed(search_local, "neural attention", user_id="metrics_user")
    log("      готово за %.2f с, найдено в базе %d" % (dt, len(res)))
    rows.append({
        "name": "6. Семантический поиск (Multi-Query)", "query": "neural attention",
        "avg_time": dt, "count": len(res), "result": res,
    })

    # --- Сценарий 7: изоляция пользователей ---
    log("\n→ 7. Изоляция пользователей ...")
    save_papers(base, query="attention", user_id="iso_user_A")
    a = get_stats(user_id="iso_user_A")["total"]
    b = get_stats(user_id="iso_user_B")["total"]  # должно быть 0
    log("      user_A: %d статей, user_B: %d статей (ожидается 0)" % (a, b))
    rows.append({
        "name": "7. Изоляция пользователей", "query": "—",
        "avg_time": None, "count": None,
        "result": [{"user_A_papers": a, "user_B_papers": b,
                    "isolated": b == 0}],
    })

    # --- Сценарий 10: экспорт в CSV ---
    log("\n→ 10. Экспорт в CSV ...")
    res, dt = timed(export_to_csv, "metrics_user", "all")
    csv_bytes, n_exported = res
    log("      готово за %.2f с, выгружено %d статей" % (dt, n_exported))
    rows.append({
        "name": "10. Экспорт в CSV", "query": "(коллекция п.6)",
        "avg_time": dt, "count": n_exported, "result": [],
    })

    # ---------- Печать таблицы метрик ----------
    log("\n" + "=" * 70)
    log("СВОДНАЯ ТАБЛИЦА (впишите в Таблицу Г.1)")
    log("=" * 70)
    log("%-38s %8s %8s" % ("Сценарий", "Найдено", "Время,с"))
    log("-" * 70)
    for r in rows:
        t = "%.2f" % r["avg_time"] if r["avg_time"] is not None else "н/п"
        c = r["count"] if r["count"] is not None else "н/п"
        log("%-38s %8s %8s" % (r["name"][:38], c, t))

    # ---------- Дамп выдачи для ручной оценки релевантности ----------
    for r in rows:
        relevance_dump.append("=" * 60)
        relevance_dump.append("%s | запрос: %s" % (r["name"], r["query"]))
        relevance_dump.append("=" * 60)
        for i, p in enumerate(r["result"], 1):
            if "title" in p:
                line = "%2d. [%s] %s (%s) [%s]" % (
                    i, "релев? Y/N", p.get("title", "")[:90],
                    p.get("year", ""), p.get("source", ""))
            else:
                line = "%2d. %s" % (i, str(p))
            relevance_dump.append(line)
        relevance_dump.append("")

    with open("results_for_relevance.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(relevance_dump))

    log("\nПолная выдача сохранена в results_for_relevance.txt")
    log("Проставьте Y/N в колонке 'релев?' и посчитайте долю релевантных.")


if __name__ == "__main__":
    main()