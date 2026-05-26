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


async def safe_edit(msg, text: str):
    """Редактирует сообщение, молча игнорирует если уже нельзя."""
    try:
        await safe_edit(msg, text, fallback=update.message.reply_text)
    except Exception:
        pass


def get_user_id(update: Update) -> str:
    return str(update.effective_user.id)


def get_main_keyboard():
    buttons = [
        [KeyboardButton("🔍 /search"), KeyboardButton("🔎 /refine")],
        [KeyboardButton("📊 /analyze"), KeyboardButton("💡 /suggest")],
        [KeyboardButton("📝 /summary"), KeyboardButton("📥 /export")],
        [KeyboardButton("📚 /db"), KeyboardButton("❓ /help")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


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
        period = f"{year_from}–{year_to}"
    elif year_from:
        period = f"с {year_from}"
    elif year_to:
        period = f"до {year_to}"

    sort_labels = {"relevance": "по релевантности", "date": "по дате", "citations": "по цитированиям"}
    type_labels = {"default": "все статьи", "survey": "только обзорные"}

    # Предупреждение если комбинация параметров может быть медленной
    warn = ""
    if sort_by == "citations" and limit > 10:
        warn = "\n⚠️ Сортировка по цитированиям может занять до 2 минут."

    msg = await update.message.reply_text(
        f"🔍 Параметры поиска:\n"
        f"  Тема: {query}\n"
        f"  Количество: {limit}\n"
        f"  Период: {period or 'любой'}\n"
        f"  Сортировка: {sort_labels[sort_by]}\n"
        f"  Тип: {type_labels[search_type]}\n\n"
        f"Ищу, подожди...{warn}",
        reply_markup=ReplyKeyboardRemove()
    )

    filters_parts = [f"year_from={year_from}"] if year_from else []
    if year_to:
        filters_parts.append(f"year_to={year_to}")
    filters_parts += [f"sort_by={sort_by}", f"search_type={search_type}"]
    prompt = f"Find {limit} scientific papers about: {query} | {limit} | " + " | ".join(filters_parts)

    user_id = get_user_id(update)
    loop = asyncio.get_event_loop()
    try:
        answer = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: run_agent(prompt, user_id=user_id)),
            timeout=SEARCH_TIMEOUT
        )
        if len(answer) > 3800:
            answer = answer[:3800] + "\n\n📥 Полный список — /export"
        text = answer + "\n\n💡 Уточни: /refine <запрос>"
        try:
            await msg.edit_text(text)
        except Exception:
            await update.message.reply_text(text)
    except asyncio.TimeoutError:
        text = (
            f"⏱ Поиск занял больше {SEARCH_TIMEOUT} секунд и был прерван.\n\n"
            f"Попробуй:\n{timeout_tip(sort_by, limit)}"
        )
        try:
            await msg.edit_text(text)
        except Exception:
            await update.message.reply_text(text)
    except Exception as e:
        text = f"Ошибка: {e}"
        try:
            await msg.edit_text(text)
        except Exception:
            await update.message.reply_text(text)

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
        "Команды:\n"
        "🔍 /search — найти статьи (пошаговый диалог)\n"
        "🔎 /refine <уточнение> [кол-во] — уточнить поиск\n"
        "📊 /analyze <тема> — оценить актуальность темы\n"
        "💡 /suggest — найти перспективные смежные темы\n"
        "📝 /summary — обзор сохранённых статей\n"
        "📥 /export — выгрузить всё в CSV\n"
        "📚 /db — статистика личной базы",
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
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text(
            "Укажи тему: /analyze <тема>\n\nПример: /analyze platform capitalism",
            reply_markup=get_main_keyboard()
        )
        return
    user_id = get_user_id(update)
    msg = await update.message.reply_text("📊 Анализирую тему и сохраняю статьи в базу...")
    loop = asyncio.get_event_loop()
    try:
        answer = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: run_agent(f"Analyze how well-studied this topic is: {query}", user_id=user_id)
            ),
            timeout=SEARCH_TIMEOUT
        )
        await safe_edit(msg, answer, fallback=update.message.reply_text)
        stats = get_stats(user_id=user_id)
        if stats["total"] > 0:
            await update.message.reply_text(
                f"✅ Статьи сохранены в базу ({stats['total']} шт.)\n\n"
                f"💡 Хочешь найти смежные незанятые ниши? Нажми /suggest",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    except asyncio.TimeoutError:
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй снова.", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        await safe_edit(msg, f"Ошибка: {e}", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    args = context.args or []
    topic = " ".join(args).strip()
    stats = get_stats(user_id=user_id)

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
        await update.message.reply_text(text, reply_markup=get_main_keyboard())
        return

    if topic == "all":
        papers = None  # suggest_topics возьмёт все статьи пользователя
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
            "Заинтересовала тема? Используй /search чтобы найти статьи по ней.",
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

    user_id = get_user_id(update)
    stats = get_stats(user_id=user_id)
    msg = await update.message.reply_text(
        f"🔎 Уточняю: «{query}» ({limit} статей)\n"
        f"Статей в базе: {stats.get('total', 0)}"
    )
    loop = asyncio.get_event_loop()
    try:
        prev = stats.get("queries", [])
        ctx = f"Previous searches: {', '.join(prev[:3])}. " if prev else ""
        prompt = f"{ctx}Find {limit} more papers about: {query} | {limit}"
        answer = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: run_agent(prompt, user_id=user_id)),
            timeout=SEARCH_TIMEOUT
        )
        new_stats = get_stats(user_id=user_id)
        added = new_stats.get("total", 0) - stats.get("total", 0)
        if len(answer) > 3500:
            answer = answer[:3500] + "..."
        await safe_edit(msg, f"{answer}\n\n📊 Добавлено: +{added} (всего {new_stats.get('total', 0, fallback=update.message.reply_text)})\n📥 /export"
        )
        await update.message.reply_text("Что делаем дальше?", reply_markup=get_main_keyboard())
    except asyncio.TimeoutError:
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй с меньшим количеством статей.", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())
    except Exception as e:
        await safe_edit(msg, f"Ошибка: {e}", fallback=update.message.reply_text)
        await update.message.reply_text("Попробуй ещё раз:", reply_markup=get_main_keyboard())


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    topic = " ".join(context.args).strip() if context.args else ""
    stats = get_stats(user_id=user_id)

    if stats["total"] == 0:
        await update.message.reply_text("📚 База пуста. Сначала /search или /analyze.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        queries_list = "\n".join(f"  • {q}" for q in queries)
        await update.message.reply_text(
            "Укажи тему или all:\n\n"
            "/summary all — обзор всех статей\n"
            "/summary <тема> — обзор по теме\n\n"
            f"Темы в базе:\n{queries_list}",
            reply_markup=get_main_keyboard()
        )
        return

    if topic == "all":
        count = stats["total"]
        prompt = "Сделай обзор всех моих сохранённых статей"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=20, user_id=user_id)
        if not papers:
            queries = stats.get("queries", [])
            queries_list = "\n".join(f"  • {q}" for q in queries)
            await update.message.reply_text(
                f"❌ По теме '{topic}' ничего не найдено в базе.\n\nДоступные темы:\n{queries_list}\n\nИли используй /summary all",
                reply_markup=get_main_keyboard()
            )
            return
        count = len(papers)
        papers_text = "\n".join([
            f"- {p['title']} ({p['year']}, {p['source']})"
            for p in papers
        ])
        prompt = (
            f"Вот {count} статей из базы по теме '{topic}':\n\n{papers_text}\n\nСоставь краткий обзор на русском: выдели основные темы, тенденции и пробелы."
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
    user_id = get_user_id(update)
    topic = " ".join(context.args).strip() if context.args else ""
    stats = get_stats(user_id=user_id)

    if stats["total"] == 0:
        await update.message.reply_text("База пуста. Сначала /search.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        ql = chr(10).join(f"  - {q}" for q in queries)
        await update.message.reply_text(
            "Укажи тему или all:\n\n/export all - все статьи\n/export <тема> - по теме\n\nТемы в базе:\n" + ql,
            reply_markup=get_main_keyboard()
        )
        return
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

    # Превью топ-5 в чате
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
        f"📥 Скачать: /export",
        reply_markup=get_main_keyboard()
    )


async def free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    user_input = update.message.text.strip()

    button_hints = {
        "🔍 /search": None,
        "💡 /suggest": None,
        "📝 /summary": None,
        "📥 /export": None,
        "📚 /db": None,
        "🔎 /refine": "Введи: /refine <запрос> [кол-во]\n\nПример: /refine platform labor 10",
        "📊 /analyze": "Введи: /analyze <тема>\n\nПример: /analyze platform capitalism",

    }

    if user_input in button_hints:
        hint = button_hints[user_input]
        if hint:
            await update.message.reply_text(hint, reply_markup=get_main_keyboard())
        elif user_input == "🔍 /search":
            await search_start(update, context)
        elif user_input == "💡 /suggest":
            await suggest_command(update, context)
        elif user_input == "📝 /summary":
            await summary_command(update, context)
        elif user_input == "📥 /export":
            await export_command(update, context)
        elif user_input == "📚 /db":
            await db_command(update, context)
        return

    if len(user_input) < 3:
        await update.message.reply_text(
            "Слишком короткий запрос. Используй команды из меню.",
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
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй снова или используй команды из меню.", fallback=update.message.reply_text)
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
            MessageHandler(filters.Regex("^🔍 /search$"), search_start)
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
async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    topic = " ".join(context.args).strip() if context.args else ""
    stats = get_stats(user_id=user_id)

    if stats["total"] == 0:
        await update.message.reply_text("📚 База пуста. Сначала /search или /analyze.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        queries_list = "\n".join(f"  • {q}" for q in queries)
        await update.message.reply_text(
            "Укажи тему или all:\n\n"
            "/summary all — обзор всех статей\n"
            "/summary <тема> — обзор по теме\n\n"
            f"Темы в базе:\n{queries_list}",
            reply_markup=get_main_keyboard()
        )
        return

    if topic == "all":
        count = stats["total"]
        prompt = "Сделай обзор всех моих сохранённых статей"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=20, user_id=user_id)
        if not papers:
            queries = stats.get("queries", [])
            queries_list = "\n".join(f"  • {q}" for q in queries)
            await update.message.reply_text(
                f"❌ По теме '{topic}' ничего не найдено в базе.\n\nДоступные темы:\n{queries_list}\n\nИли используй /summary all",
                reply_markup=get_main_keyboard()
            )
            return
        count = len(papers)
        papers_text = "\n".join([
            f"- {p['title']} ({p['year']}, {p['source']})"
            for p in papers
        ])
        prompt = (
            f"Вот {count} статей из базы по теме '{topic}':\n\n{papers_text}\n\nСоставь краткий обзор на русском: выдели основные темы, тенденции и пробелы."
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


async def export_command(update, context):
    user_id = get_user_id(update)
    args = context.args or []
    topic = " ".join(args).strip()
    stats = get_stats(user_id=user_id)

    if stats["total"] == 0:
        await update.message.reply_text("База пуста. Сначала /search.", reply_markup=get_main_keyboard())
        return

    if not topic:
        queries = stats.get("queries", [])
        ql = "\n".join("  - " + q for q in queries)
        text = "Укажи тему или all:\n\n/export all - все статьи\n/export <тема> - по теме\n\nТемы в базе:\n" + ql
        await update.message.reply_text(text, reply_markup=get_main_keyboard())
        return

    if topic == "all":
        from tools.storage import get_user_papers
        papers = get_user_papers(user_id=user_id)
        filename = "literature_all.csv"
        label = "все темы"
    else:
        from tools.storage import search_local
        papers = search_local(topic, n_results=100, user_id=user_id)
        if not papers:
            queries = stats.get("queries", [])
            ql = "\n".join("  - " + q for q in queries)
            text = "По теме '" + topic + "' ничего не найдено.\n\nДоступные темы:\n" + ql
            await update.message.reply_text(text, reply_markup=get_main_keyboard())
            return
        filename = "literature_" + topic[:20].replace(" ", "_") + ".csv"
        label = topic

    count = len(papers)

    preview = ["Найдено " + str(count) + " статей по теме: " + label, "Превью первых 5:"]
    for i, p in enumerate(papers[:5], 1):
        title = (p.get("title") or "")[:60]
        year = str(p.get("year", ""))
        source = p.get("source", "")
        preview.append(str(i) + ". " + title + " (" + year + ", " + source + ")")
    if count > 5:
        preview.append("...и ещё " + str(count - 5) + " статей в файле")

    await update.message.reply_text("\n".join(preview))

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
        caption=str(count) + " статей - " + label
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
        f"📥 Скачать: /export",
        reply_markup=get_main_keyboard()
    )


async def free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    user_input = update.message.text.strip()

    button_hints = {
        "🔍 /search": None,
        "💡 /suggest": None,
        "📝 /summary": None,
        "📥 /export": None,
        "📚 /db": None,
        "🔎 /refine": "Введи: /refine <запрос> [кол-во]\n\nПример: /refine platform labor 10",
        "📊 /analyze": "Введи: /analyze <тема>\n\nПример: /analyze platform capitalism",

    }

    if user_input in button_hints:
        hint = button_hints[user_input]
        if hint:
            await update.message.reply_text(hint, reply_markup=get_main_keyboard())
        elif user_input == "🔍 /search":
            await search_start(update, context)
        elif user_input == "💡 /suggest":
            await suggest_command(update, context)
        elif user_input == "📝 /summary":
            await summary_command(update, context)
        elif user_input == "📥 /export":
            await export_command(update, context)
        elif user_input == "📚 /db":
            await db_command(update, context)
        return

    if len(user_input) < 3:
        await update.message.reply_text(
            "Слишком короткий запрос. Используй команды из меню.",
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
        await safe_edit(msg, "⏱ Превышено время ожидания. Попробуй снова или используй команды из меню.", fallback=update.message.reply_text)
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
            MessageHandler(filters.Regex("^🔍 /search$"), search_start)
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