from telegram import Update, InputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          filters, ContextTypes, ConversationHandler)
from telegram.request import HTTPXRequest
from agent import run_agent, suggest_topics
from tools.storage import get_stats, find_matching_query, get_papers_by_topic
from export import export_to_csv
from dotenv import load_dotenv
import asyncio
import logging
import io
import os

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

ASK_QUERY, ASK_LIMIT, ASK_YEARS, ASK_YEAR_FROM, ASK_YEAR_TO, ASK_SORT, ASK_TYPE = range(7)
SEARCH_TIMEOUT = 90  # секунд

BTN_SEARCH = "🔍 Найти статьи"
BTN_REFINE = "🔎 Уточнить поиск"
BTN_ANALYZE = "📊 Оценить тему"
BTN_SUGGEST = "💡 Смежные темы"
BTN_SUMMARY = "📝 Обзор статей"
BTN_EXPORT = "📥 Выгрузить CSV"
BTN_DB = "📚 Моя база"
BTN_HELP = "❓ Помощь"

BUTTON_HINTS = {
    BTN_REFINE: (
        "Напиши уточнение поиска и при желании — количество статей.\n\n"
        "Пример: platform labor 10"
    ),
    BTN_ANALYZE: (
        "Напиши тему для анализа актуальности.\n\n"
        "Пример: platform capitalism"
    ),
}

PENDING_ACTION_KEY = "pending_action"
PENDING_REFINE = "refine"
PENDING_SUGGEST = "suggest"
PENDING_ANALYZE = "analyze"
PENDING_SUMMARY = "summary"
PENDING_EXPORT = "export"


async def safe_edit(msg, text: str, fallback=None):
    """Редактирует сообщение. Если не удаётся — отправляет через fallback или reply_text."""
    try:
        await msg.edit_text(text)
    except Exception as e:
        logger.warning("[safe_edit] %s: %s", type(e).__name__, e)
        target = fallback if fallback else msg.reply_text
        try:
            await target(text)
        except Exception as e2:
            logger.warning("[safe_edit] fallback failed: %s", e2)


def get_user_id(update: Update) -> str:
    return str(update.effective_user.id)


def get_main_keyboard():
    buttons = [
        [KeyboardButton(BTN_SEARCH), KeyboardButton(BTN_REFINE)],
        [KeyboardButton(BTN_ANALYZE), KeyboardButton(BTN_SUGGEST)],
        [KeyboardButton(BTN_SUMMARY), KeyboardButton(BTN_EXPORT)],
        [KeyboardButton(BTN_DB), KeyboardButton(BTN_HELP)],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE, button: str):
    if button in BUTTON_HINTS:
        if button == BTN_REFINE:
            context.user_data[PENDING_ACTION_KEY] = PENDING_REFINE
        elif button == BTN_ANALYZE:
            context.user_data[PENDING_ACTION_KEY] = PENDING_ANALYZE
        else:
            context.user_data.pop(PENDING_ACTION_KEY, None)
        await update.message.reply_text(BUTTON_HINTS[button], reply_markup=get_main_keyboard())
        return
    context.user_data.pop(PENDING_ACTION_KEY, None)
    if button == BTN_SEARCH:
        await search_start(update, context)
    elif button == BTN_SUGGEST:
        await suggest_command(update, context)
    elif button == BTN_SUMMARY:
        await summary_command(update, context)
    elif button == BTN_EXPORT:
        await export_command(update, context)
    elif button == BTN_DB:
        await db_command(update, context)
    elif button == BTN_HELP:
        await help_command(update, context)


def get_yes_no_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Да"), KeyboardButton("Нет")]],
        resize_keyboard=True, one_time_keyboard=True
    )


def get_sort_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📈 По релевантности")],
        [KeyboardButton("📅 По дате (сначала новые)")],
        [KeyboardButton("🏆 По цитированиям (классика)")],
    ], resize_keyboard=True, one_time_keyboard=True)


def get_type_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📄 Все статьи")],
        [KeyboardButton("📋 Только обзорные (survey/review)")],
    ], resize_keyboard=True, one_time_keyboard=True)


def timeout_tip(sort_by: str, limit: int) -> str:
    """Подсказка что сделать если таймаут."""
    tips = ["• Уменьши количество статей до 5–10"]
    if sort_by == "citations":
        tips.append("• Попробуй сортировку по релевантности вместо цитирований")
    if limit > 15:
        tips.append("• Убери фильтр по периоду")
    return "\n".join(tips)


async def run_with_timeout(coro, timeout: int):
    """Запускает корутину с таймаутом."""
    return await asyncio.wait_for(coro, timeout=timeout)


# --- Диалог поиска ---

async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🔍 Новый поиск\n\nВведи тему (на английском для лучших результатов):",
        reply_markup=ReplyKeyboardRemove()
    )
    return ASK_QUERY


async def search_got_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["query"] = update.message.text.strip()
    await update.message.reply_text(
        "Сколько статей найти?",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("5"), KeyboardButton("10"),
              KeyboardButton("20"), KeyboardButton("30")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    return ASK_LIMIT


async def search_got_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = max(1, min(int(update.message.text.strip()), 50))
    except ValueError:
        limit = 10
    context.user_data["limit"] = limit
    await update.message.reply_text(
        "Ограничить по периоду публикаций?",
        reply_markup=get_yes_no_keyboard()
    )
    return ASK_YEARS


async def search_got_years_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == "да":
        await update.message.reply_text(
            "С какого года?",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("2020"), KeyboardButton("2022"),
                  KeyboardButton("2024"), KeyboardButton("Пропустить")]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
        return ASK_YEAR_FROM
    else:
        context.user_data["year_from"] = None
        context.user_data["year_to"] = None
        await update.message.reply_text("Как сортировать?", reply_markup=get_sort_keyboard())
        return ASK_SORT


async def search_got_year_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["year_from"] = None if text == "Пропустить" else int(text) if text.isdigit() else None
    await update.message.reply_text(
        "По какой год?",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("2022"), KeyboardButton("2023"),
              KeyboardButton("2024"), KeyboardButton("Пропустить")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    return ASK_YEAR_TO


async def search_got_year_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["year_to"] = None if text == "Пропустить" else int(text) if text.isdigit() else None
    await update.message.reply_text("Как сортировать?", reply_markup=get_sort_keyboard())
    return ASK_SORT


async def search_got_sort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "дате" in text:
        context.user_data["sort_by"] = "date"
    elif "цитирован" in text:
        context.user_data["sort_by"] = "citations"
    else:
        context.user_data["sort_by"] = "relevance"
    await update.message.reply_text("Тип статей:", reply_markup=get_type_keyboard())
    return ASK_TYPE


async def search_got_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["search_type"] = "survey" if "обзорн" in text.lower() else "default"

    d = context.user_data
    query = d.get("query", "")
    limit = d.get("limit", 10)
    year_from = d.get("year_from")
    year_to = d.get("year_to")
    sort_by = d.get("sort_by", "relevance")
    search_type = d.get("search_type", "default")

    period = ""
    if year_from and year_to:
        period = str(year_from) + "-" + str(year_to)
    elif year_from:
        period = "с " + str(year_from)
    elif year_to:
        period = "до " + str(year_to)

    sort_labels = {"relevance": "по релевантности", "date": "по дате", "citations": "по цитированиям"}
    type_labels = {"default": "все статьи", "survey": "только обзорные"}

    warn = ""
    if sort_by == "citations" and limit > 10:
        warn = "\n⚠️ Сортировка по цитированиям может занять до 2 минут."

    msg = await update.message.reply_text(
        "🔍 Параметры поиска:\n"
        "  Тема: " + query + "\n"
        "  Количество: " + str(limit) + "\n"
        "  Период: " + (period or "любой") + "\n"
        "  Сортировка: " + sort_labels[sort_by] + "\n"
        "  Тип: " + type_labels[search_type] + "\n\n"
        "Ищу, подожди..." + warn,
        reply_markup=ReplyKeyboardRemove()
    )

    user_id = get_user_id(update)

    # Вызываем search_papers напрямую — без агента
    from tools.search import search_papers
    from tools.storage import save_papers

    loop = asyncio.get_event_loop()
    try:
        papers = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: search_papers(
                query=query,
                limit=limit,
                year_from=year_from,
                year_to=year_to,
                sort_by=sort_by,
                search_type=search_type
            )),
            timeout=SEARCH_TIMEOUT
        )

        if not papers or "error" in papers[0]:
            err = papers[0].get("error", "неизвестная ошибка") if papers else "нет результатов"
            result_text = "❌ Статьи не найдены: " + err
        else:
            # Сохраняем в базу
            saved = save_papers(papers, query=query, user_id=user_id)

            # Формируем список
            filters = []
            if year_from:
                filters.append("с " + str(year_from))
            if year_to:
                filters.append("по " + str(year_to))
            filters.append(sort_labels[sort_by])
            if search_type == "survey":
                filters.append("только обзорные")

            lines = ["Найдено: " + str(len(papers)) + " статей (" + ", ".join(f for f in filters if f) + "), сохранено: " + str(saved) + "\n"]

            compact = len(papers) > 15
            for i, p in enumerate(papers, 1):
                cite_info = " | " + str(p.get("citations", 0)) + " цит." if sort_by == "citations" else ""
                if compact:
                    lines.append(str(i) + ". [" + p.get("source", "") + "] " + p["title"] + " (" + str(p["year"]) + ")" + cite_info + "\n   " + p["link"])
                else:
                    lines.append(
                        str(i) + ". [" + p.get("source", "") + "] " + p["title"] + " (" + str(p["year"]) + ")" + cite_info + "\n"
                        "   Авторы: " + ", ".join(p["authors"]) + "\n"
                        "   " + p["abstract"][:100] + "...\n"
                        "   " + p["link"]
                    )

            result_text = "\n\n".join(lines)

        if len(result_text) > 3800:
            result_text = result_text[:3800] + "\n\n📥 Полный список — /export"

        result_text += "\n\n💡 Уточни: /refine <запрос>"

        try:
            await msg.edit_text(result_text)
        except Exception:
            await update.message.reply_text(result_text)

    except asyncio.TimeoutError:
        t = "⏱ Поиск занял слишком долго.\n\n" + timeout_tip(sort_by, limit)
        try:
            await msg.edit_text(t)
        except Exception:
            await update.message.reply_text(t)
    except Exception as e:
        t = "Ошибка: " + str(e)
        try:
            await msg.edit_text(t)
        except Exception:
            await update.message.reply_text(t)

    await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Поиск отменён.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


# --- Остальные команды ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, {name}! Я ИИ-агент для анализа научной литературы.\n\n"
        "Выбери действие кнопками внизу:\n"
        f"{BTN_SEARCH} — пошаговый поиск статей\n"
        f"{BTN_REFINE} — уточнить предыдущий поиск\n"
        f"{BTN_ANALYZE} — оценить актуальность темы\n"
        f"{BTN_SUGGEST} — найти перспективные смежные темы\n"
        f"{BTN_SUMMARY} — обзор сохранённых статей\n"
        f"{BTN_EXPORT} — выгрузить всё в CSV\n"
        f"{BTN_DB} — статистика личной базы",
        reply_markup=get_main_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Типичный сценарий:\n\n"
        "1. /analyze platform capitalism\n"
        "   → анализ + статьи сохраняются в базу\n\n"
        "2. /suggest\n"
        "   → топ-3 смежные незанятые темы\n\n"
        "3. /search → ищешь по выбранной теме\n\n"
        "4. /export → скачиваешь CSV\n\n"
        "⚠️ Советы по скорости:\n"
        "• 5–10 статей работает быстро\n"
        "• Сортировка по цитированиям медленнее\n"
        "• Если завис — /cancel и попробуй снова",
        reply_markup=get_main_keyboard()
    )


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else ""
    if not query:
        context.user_data[PENDING_ACTION_KEY] = PENDING_ANALYZE
        await update.message.reply_text(
            "Укажи тему: /analyze <тема>\n\nПример: /analyze platform capitalism",
            reply_markup=get_main_keyboard()
        )
        return

    context.user_data.pop(PENDING_ACTION_KEY, None)
    await run_analyze(update, context, query)


async def run_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    user_id = get_user_id(update)
    msg = await update.message.reply_text("📊 Анализирую тему и сохраняю статьи в базу...")

    from tools.analyze import analyze_topic
    from tools.search import search_papers
    from tools.storage import save_papers

    loop = asyncio.get_event_loop()
    try:
        def _do_analyze():
            result = analyze_topic(query)

            papers = search_papers(query=query, limit=20, sort_by="relevance")
            saved = 0
            if papers and "error" not in papers[0]:
                saved = save_papers(papers, query=query, user_id=user_id)

            signals = "\n".join("  - " + s for s in result["signals"]) or "  - признаков не выявлено"

            text = (
                "📊 Анализ темы: " + result["query"] + "\n\n"
                "Источник данных: " + result["source"] + "\n"
                "Всего публикаций: " + str(result["total_papers"]) + "\n"
                "За последние 5 лет: " + str(result["papers_last_5_years"]) + "\n"
                "За последний год: " + str(result["papers_last_year"]) + "\n"
                "Обзорных статей: " + str(result["survey_papers"]) + "\n\n"
                "Признаки:\n" + signals + "\n\n"
                "Оценка: " + str(result["score"]) + "/8\n"
                "Вывод: " + result["verdict"] + "\n\n"
                "Сохранено в базу: " + str(saved) + " статей"
            )
            return text

        answer = await asyncio.wait_for(
            loop.run_in_executor(None, _do_analyze),
            timeout=SEARCH_TIMEOUT
        )

        try:
            await msg.edit_text(answer)
        except Exception:
            await update.message.reply_text(answer)

        stats = get_stats(user_id=user_id)
        if stats["total"] > 0:
            await update.message.reply_text(
                "✅ Статьи сохранены в базу (" + str(stats["total"]) + " шт.)\n\n"
                "💡 Хочешь найти смежные незанятые ниши? Нажми /suggest",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())

    except asyncio.TimeoutError:
        try:
            await msg.edit_text("⏱ Превышено время ожидания. Попробуй снова.")
        except Exception:
            await update.message.reply_text("⏱ Превышено время ожидания. Попробуй снова.")
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        try:
            await msg.edit_text("Ошибка: " + str(e))
        except Exception:
            await update.message.reply_text("Ошибка: " + str(e))
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args or []).strip()
    stats = get_stats(user_id=get_user_id(update))

    if stats["total"] == 0:
        await update.message.reply_text(
            "💡 База пуста.\n\nСначала /analyze <тема> — сохранит статьи в базу.",
            reply_markup=get_main_keyboard()
        )
        return

    if not topic:
        queries = stats.get("queries", [])
        ql = "\n".join("  - " + q for q in queries)
        text = "Укажи тему или all:\n\n/suggest all - по всем статьям\n/suggest <тема> - по теме\n\nТемы в базе:\n" + ql
        context.user_data[PENDING_ACTION_KEY] = PENDING_SUGGEST
        await update.message.reply_text(text, reply_markup=get_main_keyboard())
        return

    context.user_data.pop(PENDING_ACTION_KEY, None)
    await run_suggest(update, context, topic)


async def run_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)

    if topic == "all":
        papers = None
        count = stats["total"]
        label = "всем статьям"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=30, user_id=user_id)
        if not papers:
            queries = stats.get("queries", [])
            ql = "\n".join("  - " + q for q in queries)
            text = "По теме '" + topic + "' ничего не найдено.\n\nДоступные темы:\n" + ql
            await update.message.reply_text(text, reply_markup=get_main_keyboard())
            return
        count = len(papers)
        label = topic

    msg = await update.message.reply_text(
        "💡 Анализирую " + str(count) + " статей по теме: " + label + "\nИщу смежные темы с пробелами.\n\nЭто займёт ~30-60 секунд."
    )
    loop = asyncio.get_event_loop()
    try:
        answer = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: suggest_topics(user_id=user_id, papers=papers, topic=label)),
            timeout=180
        )
        if len(answer) > 4096:
            answer = answer[:4090] + "..."
        try:
            await msg.edit_text(answer)
        except Exception:
            await update.message.reply_text(answer)
        await update.message.reply_text(
            "Заинтересовала тема? Нажми /search и введи название темы из списка выше.\n\nНапример: /search Algorithmic Bias and Fairness in AI Platforms 15",
            reply_markup=get_main_keyboard()
        )
    except asyncio.TimeoutError:
        try:
            await msg.edit_text("⏱ Превышено время ожидания. Попробуй снова.")
        except Exception:
            await update.message.reply_text("⏱ Превышено время ожидания. Попробуй снова.")
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        try:
            await msg.edit_text("Ошибка: " + str(e))
        except Exception:
            await update.message.reply_text("Ошибка: " + str(e))
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def refine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Укажи уточнение: /refine <запрос> [кол-во]\n\nПример: /refine platform labor 10",
            reply_markup=get_main_keyboard()
        )
        return
    try:
        limit = int(args[-1])
        limit = max(1, min(limit, 50))
        query = " ".join(args[:-1])
    except ValueError:
        limit = 10
        query = " ".join(args)

    await run_refine(update, context, query, limit)


async def run_refine(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, limit: int):
    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)
    msg = await update.message.reply_text(
        f"🔎 Уточняю: «{query}» ({limit} статей)\n"
        f"Статей в базе: {stats.get('total', 0)}"
    )

    from tools.search import search_papers
    from tools.storage import save_papers

    loop = asyncio.get_event_loop()
    try:
        papers = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: search_papers(
                query=query,
                limit=limit,
                sort_by="relevance",
            )),
            timeout=SEARCH_TIMEOUT
        )

        if not papers or "error" in papers[0]:
            err = papers[0].get("error", "неизвестная ошибка") if papers else "нет результатов"
            result_text = f"❌ Статьи не найдены: {err}"
            added = 0
        else:
            save_papers(papers, query=query, user_id=user_id)
            new_stats = get_stats(user_id=user_id)
            added = new_stats.get("total", 0) - stats.get("total", 0)

            lines = [f"Найдено: {len(papers)} статей, новых в базе: +{added}\n"]
            compact = len(papers) > 15
            for i, p in enumerate(papers, 1):
                if compact:
                    lines.append(
                        f"{i}. [{p.get('source', '')}] {p['title']} ({p['year']})\n"
                        f"   {p['link']}"
                    )
                else:
                    lines.append(
                        f"{i}. [{p.get('source', '')}] {p['title']} ({p['year']})\n"
                        f"   Авторы: {', '.join(p['authors'])}\n"
                        f"   {p['abstract'][:100]}...\n"
                        f"   {p['link']}"
                    )
            result_text = "\n\n".join(lines)

        if len(result_text) > 3500:
            result_text = result_text[:3500] + "\n\n📥 Полный список — /export"

        new_stats = get_stats(user_id=user_id)
        await safe_edit(
            msg,
            f"{result_text}\n\n📊 Добавлено: +{added} (всего {new_stats.get('total', 0)})\n📥 /export",
            fallback=update.message.reply_text,
        )
        await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    except asyncio.TimeoutError:
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй с меньшим количеством статей.", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        await safe_edit(msg, f"Ошибка: {e}", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip() if context.args else ""
    stats = get_stats(user_id=get_user_id(update))

    if stats["total"] == 0:
        await update.message.reply_text("📚 База пуста. Сначала /search или /analyze.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        queries_list = "\n".join(f"  • {q}" for q in queries)
        context.user_data[PENDING_ACTION_KEY] = PENDING_SUMMARY
        await update.message.reply_text(
            "Укажи тему или all:\n\n"
            "/summary all — обзор всех статей\n"
            "/summary <тема> — обзор по теме\n\n"
            f"Темы в базе:\n{queries_list}",
            reply_markup=get_main_keyboard()
        )
        return

    context.user_data.pop(PENDING_ACTION_KEY, None)
    await run_summary(update, context, topic)


async def run_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)

    if topic == "all":
        from tools.storage import get_user_papers
        papers = get_user_papers(user_id=user_id)
        label = "все темы"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=20, user_id=user_id)
        label = topic

    if not papers:
        queries = stats.get("queries", [])
        queries_list = "\n".join(f"  • {q}" for q in queries)
        await update.message.reply_text(
            f"❌ По теме '{topic}' ничего не найдено в базе.\n\nДоступные темы:\n{queries_list}\n\nИли используй /summary all",
            reply_markup=get_main_keyboard()
        )
        return

    count = len(papers)
    shown = papers[:30]
    papers_text = "\n".join(
        f"- {p['title']} ({p['year']}, {p['source']})"
        for p in shown
    )
    if count > len(shown):
        papers_text += f"\n\n(... и ещё {count - len(shown)} статей)"

    prompt = (
        f"Вот {count} статей из личной базы по теме «{label}»:\n\n"
        f"{papers_text}\n\n"
        f"Составь краткий обзор на русском ТОЛЬКО на основе этих статей: "
        f"выдели основные темы, тенденции и пробелы. Не добавляй информацию вне списка."
    )

    msg = await update.message.reply_text(f"📝 Генерирую обзор {count} статей...")
    from agent import _get_llm
    loop = asyncio.get_event_loop()
    try:
        def _summarize():
            llm = _get_llm()
            response = llm.invoke(prompt)
            return response.content

        answer = await asyncio.wait_for(
            loop.run_in_executor(None, _summarize),
            timeout=SEARCH_TIMEOUT
        )
        if len(answer) > 4096:
            answer = answer[:4090] + "..."
        try:
            await msg.edit_text(answer)
        except Exception:
            await update.message.reply_text(answer)
        await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    except asyncio.TimeoutError:
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй снова.", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        print(f"[summary] exception: {e}")
        await safe_edit(msg, f"Ошибка: {e}", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip() if context.args else ""
    stats = get_stats(user_id=get_user_id(update))

    if stats["total"] == 0:
        await update.message.reply_text("База пуста. Сначала /search.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        ql = chr(10).join(f"  - {q}" for q in queries)
        context.user_data[PENDING_ACTION_KEY] = PENDING_EXPORT
        await update.message.reply_text(
            "Укажи тему или all:\n\n"
            "all — все статьи\n"
            "<тема> — только по выбранной теме\n\n"
            "Темы в базе:\n" + ql,
            reply_markup=get_main_keyboard()
        )
        return

    context.user_data.pop(PENDING_ACTION_KEY, None)
    await run_export(update, context, topic)


async def run_export(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)

    if topic == "all":
        from tools.storage import get_user_papers
        papers = get_user_papers(user_id=user_id)
        filename = "literature_all.csv"
        caption_topic = "все темы"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=100, user_id=user_id)
        if not papers:
            queries = stats.get("queries", [])
            ql = chr(10).join(f"  - {q}" for q in queries)
            await update.message.reply_text(
                f"По теме '{topic}' ничего не найдено.\n\nДоступные темы:\n" + ql,
                reply_markup=get_main_keyboard()
            )
            return
        filename = f"literature_{topic[:20].replace(' ', '_')}.csv"
        caption_topic = topic

    count = len(papers)

    preview = [f"Найдено {count} статей по теме: {caption_topic}"]
    preview.append("Превью первых 5:")
    for i, p in enumerate(papers[:5], 1):
        title = (p.get("title") or "")[:60]
        year = p.get("year", "")
        source = p.get("source", "")
        preview.append(f"{i}. {title} ({year}, {source})")
    if count > 5:
        preview.append(f"...и ещё {count - 5} статей в файле")

    await update.message.reply_text(chr(10).join(preview))

    import csv, io
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["title", "authors", "year", "source", "link", "query"],
        extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(papers)
    csv_bytes = output.getvalue().encode("utf-8-sig")
    csv_file = io.BytesIO(csv_bytes)

    await update.message.reply_document(
        document=InputFile(csv_file, filename=filename),
        caption=f"{count} статей - {caption_topic}"
    )
    await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())


async def db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)
    if stats["total"] == 0:
        await update.message.reply_text("📚 База пуста. Сначала /search.", reply_markup=get_main_keyboard())
        return
    queries = "\n".join(f"  • {q}" for q in stats["queries"]) or "нет данных"
    sources = "\n".join(f"  • {s}: {n} статей" for s, n in stats["sources"].items())
    await update.message.reply_text(
        f"📚 Личная база знаний\n\n"
        f"Всего статей: {stats['total']}\n\n"
        f"Источники:\n{sources}\n\n"
        f"Темы поиска:\n{queries}\n\n"
        f"📥 Скачать: {BTN_EXPORT}",
        reply_markup=get_main_keyboard()
    )


MENU_BUTTONS = frozenset({
    BTN_SEARCH, BTN_REFINE, BTN_ANALYZE, BTN_SUGGEST,
    BTN_SUMMARY, BTN_EXPORT, BTN_DB, BTN_HELP,
})


async def free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    user_input = update.message.text.strip()

    if user_input in MENU_BUTTONS:
        await handle_menu_button(update, context, user_input)
        return

    if context.user_data.get(PENDING_ACTION_KEY) == PENDING_REFINE:
        context.user_data.pop(PENDING_ACTION_KEY, None)
        parts = user_input.split()
        try:
            limit = int(parts[-1])
            limit = max(1, min(limit, 50))
            query = " ".join(parts[:-1]).strip()
        except (ValueError, IndexError):
            limit = 10
            query = user_input

        if not query:
            await update.message.reply_text(
                "Укажи уточнение в формате: topic [кол-во].\nПример: platform labor 10",
                reply_markup=get_main_keyboard()
            )
            return

        await run_refine(update, context, query, limit)
        return

    if context.user_data.get(PENDING_ACTION_KEY) == PENDING_SUGGEST:
        context.user_data.pop(PENDING_ACTION_KEY, None)
        await run_suggest(update, context, user_input)
        return

    if context.user_data.get(PENDING_ACTION_KEY) == PENDING_ANALYZE:
        context.user_data.pop(PENDING_ACTION_KEY, None)
        if len(user_input) < 3:
            await update.message.reply_text(
                "Слишком короткая тема. Пример: platform capitalism",
                reply_markup=get_main_keyboard()
            )
            return
        await run_analyze(update, context, user_input)
        return

    if context.user_data.get(PENDING_ACTION_KEY) == PENDING_SUMMARY:
        context.user_data.pop(PENDING_ACTION_KEY, None)
        await run_summary(update, context, user_input)
        return

    if context.user_data.get(PENDING_ACTION_KEY) == PENDING_EXPORT:
        context.user_data.pop(PENDING_ACTION_KEY, None)
        await run_export(update, context, user_input)
        return

    if len(user_input) < 3:
        await update.message.reply_text(
            "Слишком короткий запрос. Используй кнопки меню внизу.",
            reply_markup=get_main_keyboard()
        )
        return

    command_words = ["search", "analyze", "suggest", "refine", "local", "summary", "export", "db", "help"]
    if user_input.lower() in command_words:
        await update.message.reply_text(
            f"Используй команду с /: /{user_input.lower()}",
            reply_markup=get_main_keyboard()
        )
        return

    msg = await update.message.reply_text("🤔 Думаю над ответом...")
    loop = asyncio.get_event_loop()
    try:
        answer = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: run_agent(user_input, user_id=user_id)),
            timeout=SEARCH_TIMEOUT
        )
        if len(answer) > 4096:
            answer = answer[:4090] + "..."
        await safe_edit(msg, answer, fallback=update.message.reply_text)
        await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    except asyncio.TimeoutError:
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй снова или используй кнопки меню.", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        await safe_edit(msg, f"Ошибка: {e}", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN не найден в .env")

    request = HTTPXRequest(connect_timeout=120, read_timeout=120)
    app = ApplicationBuilder().token(token).request(request).build()

    search_conv = ConversationHandler(
        entry_points=[
            CommandHandler("search", search_start),
            MessageHandler(filters.Regex(f"^{BTN_SEARCH}$"), search_start),
        ],
        states={
            ASK_QUERY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_query)],
            ASK_LIMIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_limit)],
            ASK_YEARS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_years_choice)],
            ASK_YEAR_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_year_from)],
            ASK_YEAR_TO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_year_to)],
            ASK_SORT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_sort)],
            ASK_TYPE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, search_got_type)],
        },
        fallbacks=[CommandHandler("cancel", search_cancel)],
        allow_reentry=True
    )

    app.add_handler(search_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("suggest", suggest_command))
    app.add_handler(CommandHandler("refine", refine_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("db", db_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_message))

    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()
