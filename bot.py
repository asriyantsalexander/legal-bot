"""
Telegram-бот: редактор русского юридического текста.
Находит орфографические и пунктуационные ошибки в загруженных файлах.
"""
 
import os
import io
import logging
import asyncio
import requests
 
from telegram import Update, Document
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
 
# ── Настройка логов ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
 
# ── Ключи из переменных окружения ────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
 
# ── Системный промпт ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — опытный редактор русского юридического текста.
 
Твоя задача: найти только орфографические и пунктуационные ошибки в переданном тексте.
 
Строгие правила:
- Не меняй правовой смысл текста ни при каких обстоятельствах.
- Не упрощай юридическую терминологию без необходимости.
- Не переформулируй предложения — исправляй только конкретные ошибки.
- Если ошибок нет — так и скажи.
 
Формат ответа (строго):
 
НАЙДЕННЫЕ ОШИБКИ:
 
Ошибка N:
- Исходный фрагмент: «...»
- Исправленный фрагмент: «...»
- Пояснение: ...
 
ПОЛНЫЙ ИСПРАВЛЕННЫЙ ТЕКСТ:
[Весь текст целиком с внесёнными исправлениями]
 
Если ошибок не найдено:
НАЙДЕННЫЕ ОШИБКИ:
Ошибок не обнаружено.
 
ПОЛНЫЙ ИСПРАВЛЕННЫЙ ТЕКСТ:
[Исходный текст без изменений]
"""
 
# ── Утилиты извлечения текста ────────────────────────────────────────────────
 
def extract_text_from_docx(file_bytes: bytes) -> str:
    try:
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать .docx: {e}")
 
 
def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать .pdf: {e}")
 
 
def extract_text_from_txt(file_bytes: bytes) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("Не удалось определить кодировку файла.")
 
 
# ── Обращение к Claude API через requests ────────────────────────────────────
 
def check_text(text: str) -> str:
    if len(text) > 50_000:
        text = text[:50_000] + "\n\n[... текст обрезан ...]"
 
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Проверь следующий юридический текст:\n\n{text}",
                }
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]
 
 
# ── Разбивка длинных сообщений ────────────────────────────────────────────────
 
async def send_long_message(update: Update, text: str):
    MAX = 4096
    for i in range(0, len(text), MAX):
        await update.message.reply_text(text[i : i + MAX])
        await asyncio.sleep(0.3)
 
 
# ── Обработчики команд ────────────────────────────────────────────────────────
 
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я — редактор русского юридического текста.\n\n"
        "Пришли мне файл (.docx, .pdf, .txt) или напечатай текст прямо в чат.\n\n"
        "Я найду орфографические и пунктуационные ошибки, "
        "не меняя правовой смысл, и верну полный исправленный текст."
    )
 
 
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь файл .docx / .pdf / .txt или напечатай текст.\n"
        "Дождись ответа (10–30 секунд).\n"
        "Получи список ошибок и исправленный текст."
    )
 
 
# ── Обработчик файлов ─────────────────────────────────────────────────────────
 
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    fname = doc.file_name or ""
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
 
    if ext not in ("docx", "pdf", "txt"):
        await update.message.reply_text("⚠️ Поддерживаемые форматы: .docx, .pdf, .txt")
        return
 
    await update.message.reply_text("⏳ Читаю файл…")
 
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось скачать файл: {e}")
        return
 
    try:
        if ext == "docx":
            text = extract_text_from_docx(file_bytes)
        elif ext == "pdf":
            text = extract_text_from_pdf(file_bytes)
        else:
            text = extract_text_from_txt(file_bytes)
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
        return
 
    if not text.strip():
        await update.message.reply_text("⚠️ Файл пустой или не содержит текста.")
        return
 
    await update.message.reply_text("🔍 Проверяю текст…")
 
    try:
        result = check_text(text)
    except Exception as e:
        logger.error("Ошибка API: %s", e)
        await update.message.reply_text(f"❌ Ошибка AI: {e}")
        return
 
    await send_long_message(update, result)
 
 
# ── Обработчик обычного текста ────────────────────────────────────────────────
 
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 10:
        await update.message.reply_text("Текст слишком короткий.")
        return
 
    await update.message.reply_text("🔍 Проверяю текст…")
 
    try:
        result = check_text(text)
    except Exception as e:
        logger.error("Ошибка API: %s", e)
        await update.message.reply_text(f"❌ Ошибка AI: {e}")
        return
 
    await send_long_message(update, result)
 
 
# ── Запуск ────────────────────────────────────────────────────────────────────
 
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True)
 
 
if __name__ == "__main__":
    main()
