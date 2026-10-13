import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("pdf-converter")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_QUEUED_IMAGES = int(os.getenv("MAX_QUEUED_IMAGES", "10"))

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".docx", ".txt", ".xlsx", ".pptx"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def safe_filename(name: str) -> str:
    name = Path(name or "upload").name
    cleaned = "".join(c for c in name if c.isalnum() or c in "._- ")
    return cleaned.strip(" .")[:120] or "upload"


def office_to_pdf(source: Path, output_dir: Path) -> Path:
    """Convert DOCX/XLSX/PPTX using LibreOffice in headless mode."""
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        raise RuntimeError(
            "Office conversion engine is missing. Install LibreOffice or deploy with the provided Dockerfile."
        )
    result = subprocess.run(
        [
            soffice, "--headless", "--convert-to", "pdf",
            "--outdir", str(output_dir), str(source)
        ],
        capture_output=True, text=True, timeout=90
    )
    expected = output_dir / f"{source.stem}.pdf"
    if result.returncode != 0 or not expected.exists() or expected.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "No PDF output was created").strip()
        raise RuntimeError(f"LibreOffice conversion failed: {detail[:500]}")
    return expected


def image_to_pdf(source: Path, destination: Path) -> None:
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.save(destination, "PDF", resolution=150.0)


def text_to_pdf(source: Path, destination: Path) -> None:
    # Simple readable text layout; wraps long lines and paginates.
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.lib.units import mm

    page_w, page_h = A4
    margin = 18 * mm
    font_name, font_size = "Helvetica", 9
    line_height = 12
    c = canvas.Canvas(str(destination), pagesize=A4)
    c.setTitle(source.stem)
    c.setFont(font_name, font_size)
    x, y = margin, page_h - margin
    max_width = page_w - 2 * margin

    try:
        text = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"Could not read text file: {exc}") from exc

    for original_line in text.splitlines() or [""]:
        words = original_line.expandtabs(4).split(" ")
        current = ""
        wrapped = []
        for word in words:
            candidate = word if not current else current + " " + word
            if stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
            else:
                if current:
                    wrapped.append(current)
                # Break very long tokens into manageable chunks.
                chunk = ""
                for ch in word:
                    if stringWidth(chunk + ch, font_name, font_size) > max_width and chunk:
                        wrapped.append(chunk)
                        chunk = ch
                    else:
                        chunk += ch
                current = chunk
        wrapped.append(current)

        for line in wrapped:
            if y < margin:
                c.showPage()
                c.setFont(font_name, font_size)
                y = page_h - margin
            c.drawString(x, y, line)
            y -= line_height
    c.save()


async def download_telegram_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_obj, filename: str) -> Path:
    file_size = getattr(file_obj, "file_size", None)
    if file_size and file_size > MAX_FILE_BYTES:
        raise ValueError(f"File is too large. Maximum supported size is {MAX_FILE_MB} MB.")
    workdir = Path(tempfile.mkdtemp(prefix="pdfbot_"))
    context.user_data["workdir"] = str(workdir)
    path = workdir / safe_filename(filename)
    tg_file = await context.bot.get_file(file_obj.file_id)
    await tg_file.download_to_drive(custom_path=str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("Telegram returned an empty file.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"File is too large. Maximum supported size is {MAX_FILE_MB} MB.")
    return path


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["image_queue"] = []
    await update.message.reply_text(
        "📄 *File → PDF Converter*\n\n"
        "Send a JPG/PNG, DOCX, TXT, XLSX or PPTX file and I’ll convert it to PDF.\n\n"
        "🖼️ *Merge images:* send multiple JPG/PNG images, then use /makepdf.\n"
        "Use /clear to empty the image queue and /help for instructions.\n\n"
        f"Maximum input file size: {MAX_FILE_MB} MB.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Supported input formats:\n"
        "• JPG / JPEG / PNG → PDF\n"
        "• DOCX → PDF\n• TXT → PDF\n• XLSX → PDF\n• PPTX → PDF\n\n"
        "To merge images: send each image, then press /makepdf.\n"
        f"Maximum file size: {MAX_FILE_MB} MB per file.\n\n"
        "Office conversion uses LibreOffice. Complex layouts may differ slightly from Microsoft Office."
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["image_queue"] = []
    await update.message.reply_text("🧹 Image queue cleared.")


async def convert_one(update: Update, context: ContextTypes.DEFAULT_TYPE, source: Path) -> None:
    ext = source.suffix.lower()
    out = source.parent / f"{source.stem}.pdf"
    if ext in IMAGE_EXTENSIONS:
        image_to_pdf(source, out)
    elif ext == ".txt":
        text_to_pdf(source, out)
    elif ext in {".docx", ".xlsx", ".pptx"}:
        out = office_to_pdf(source, source.parent)
    else:
        raise ValueError("Unsupported format. Send JPG, PNG, DOCX, TXT, XLSX or PPTX.")

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Conversion did not produce a PDF.")
    if out.stat().st_size > 49 * 1024 * 1024:
        raise RuntimeError("The resulting PDF is over Telegram's typical 50 MB send limit.")
    with out.open("rb") as pdf:
        await update.message.reply_document(
            document=pdf,
            filename=out.name,
            caption=f"✅ Converted successfully: {out.name}",
            read_timeout=120, write_timeout=120, connect_timeout=30,
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    doc = msg.document
    if not doc:
        return
    filename = safe_filename(doc.file_name or "upload")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        await msg.reply_text("❌ Unsupported file type. Send JPG, PNG, DOCX, TXT, XLSX or PPTX.")
        return
    status = await msg.reply_text("⏳ File received. Converting to PDF…")
    try:
        source = await download_telegram_file(update, context, doc, filename)
        if ext in IMAGE_EXTENSIONS:
            queue = context.user_data.setdefault("image_queue", [])
            if len(queue) < MAX_QUEUED_IMAGES:
                queue.append(str(source))
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("➕ Add more images", callback_data="queue_more"),
                    InlineKeyboardButton("📄 Merge queue to PDF", callback_data="make_pdf"),
                ]])
                await status.edit_text(
                    f"🖼️ Image added to merge queue ({len(queue)}/{MAX_QUEUED_IMAGES}).\n"
                    "Send more images or merge them into one PDF.",
                    reply_markup=keyboard,
                )
                return
            await status.edit_text("Image queue is full; converting this image separately…")
        await convert_one(update, context, source)
        await status.delete()
    except Exception as exc:
        log.exception("Document conversion failed")
        await status.edit_text(f"❌ Conversion failed: {str(exc)[:700]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg.photo:
        return
    status = await msg.reply_text("⏳ Image received. Adding it to your PDF queue…")
    try:
        photo = msg.photo[-1]
        source = await download_telegram_file(update, context, photo, f"image_{photo.file_unique_id}.jpg")
        queue = context.user_data.setdefault("image_queue", [])
        if len(queue) >= MAX_QUEUED_IMAGES:
            await status.edit_text(f"❌ Queue limit reached ({MAX_QUEUED_IMAGES} images). Use /makepdf or /clear.")
            return
        queue.append(str(source))
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Add more images", callback_data="queue_more"),
            InlineKeyboardButton("📄 Merge queue to PDF", callback_data="make_pdf"),
        ]])
        await status.edit_text(
            f"🖼️ Image added ({len(queue)}/{MAX_QUEUED_IMAGES}). Send more images or merge now.",
            reply_markup=keyboard,
        )
    except Exception as exc:
        log.exception("Photo processing failed")
        await status.edit_text(f"❌ Could not add image: {str(exc)[:500]}")


async def make_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    queue = context.user_data.get("image_queue", [])
    paths = [Path(p) for p in queue if Path(p).exists()]
    if not paths:
        await msg.reply_text("No images queued. Send one or more JPG/PNG images first.")
        return
    status = await msg.reply_text(f"⏳ Merging {len(paths)} image(s) into one PDF…")
    try:
        workdir = Path(tempfile.mkdtemp(prefix="merged_pdf_"))
        out = workdir / "merged_images.pdf"
        images = []
        for path in paths:
            with Image.open(path) as im:
                images.append(ImageOps.exif_transpose(im).convert("RGB").copy())
        images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=150.0)
        if out.stat().st_size > 49 * 1024 * 1024:
            raise RuntimeError("Merged PDF is over Telegram's typical 50 MB send limit.")
        with out.open("rb") as pdf:
            await msg.reply_document(
                document=pdf, filename="merged_images.pdf",
                caption=f"✅ Merged {len(images)} image(s) into one PDF.",
                read_timeout=120, write_timeout=120, connect_timeout=30,
            )
        context.user_data["image_queue"] = []
        await status.delete()
    except Exception as exc:
        log.exception("Image merge failed")
        await status.edit_text(f"❌ Could not create merged PDF: {str(exc)[:500]}")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "queue_more":
        await query.edit_message_text("Send another JPG/PNG image, or press /makepdf to merge the queued images.")
    elif query.data == "make_pdf":
        await make_pdf(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled bot error", exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing. Add your token in the hosting environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("makepdf", make_pdf))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)
    log.info("PDF converter bot is starting; maximum input size=%s MB", MAX_FILE_MB)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
