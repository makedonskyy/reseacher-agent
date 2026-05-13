from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from agent import run_agent
from dotenv import load_dotenv
import os

load_dotenv()


# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я ИИ-агент для анализа научной литературы.\n\n"
        "Команды:\n"
        "🔍 /search <запрос> — найти статьи\n"
        "📊 /analyze <тема> — оценить актуальность темы\n"
        "❓ /help — помощь\n\n"
        "Или просто напиши свой вопрос!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Примеры запросов:\n\n"
        "/search LLM agents for research automation\n"
        "/analyze retrieval augmented generation literature review\n\n"
        "Или свободный вопрос:\n"
        "«Найди статьи про применение ИИ в медицине»"
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Укажи запрос: /search <тема на английском>")
        return
    await update.message.reply_text("🔍 Ищу статьи, подожди немного...")
    try:
        answer = run_agent(f"Найди научные статьи по теме: {query}")
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Укажи тему: /analyze <тема на английском>")
        return
    await update.message.reply_text("📊 Анализирую тему, подожди немного...")
    try:
        answer = run_agent(f"Проанализируй насколько изучена тема: {query}")
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("🤔 Думаю над ответом...")
    try:
        answer = run_agent(user_input)
        # Telegram ограничивает сообщения 4096 символами
        if len(answer) > 4096:
            answer = answer[:4090] + "..."
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


# --- Запуск бота ---

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN не найден в .env")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_message))

    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()