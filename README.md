# Telegram File-to-PDF Converter

A Telegram bot that converts:
- JPG/JPEG and PNG images to PDF
- DOCX to PDF
- TXT to PDF
- XLSX to PDF
- PPTX to PDF
- Multiple images into a single PDF

## Important notes

- The bot uses Telegram's hosted Bot API. This project defaults to an 18 MB input limit because cloud Bot API file-download limits can constrain larger uploads.
- Office files are converted with LibreOffice, installed in the Docker image.
- Office document layout may differ from Microsoft Office, particularly for fonts, complex spreadsheets, and slide layouts.
- Never upload sensitive documents to a bot you do not control. This bot processes uploaded files temporarily and does not intentionally keep them as a permanent archive.
- Free hosting availability and sleep/usage limits change. A continuously running polling bot may require a paid worker depending on the provider's current plan. Do not assume a free tier will run it 24/7.

## 1. Create the Telegram bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Choose a display name and username.
4. Copy the token. Treat it like a password; do not commit it to GitHub.

## 2. Run locally on macOS

Install Python 3.12+ and LibreOffice:

```bash
brew install python@3.12
brew install --cask libreoffice
```

Then, from this project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Open `.env`, replace `BOT_TOKEN` with the token from BotFather, then run:

```bash
set -a
source .env
set +a
python bot.py
```

Keep the terminal running while testing. In Telegram, open your bot and send `/start`.

## 3. Use the bot

- Send a supported document as a Telegram file.
- Send images one at a time to queue them for a merged PDF.
- Press **Merge queue to PDF** or send `/makepdf`.
- Send `/clear` to clear the image queue.
- Send `/help` to see the supported formats.

## 4. Deploy with Docker on a compatible host

1. Push this folder to a private or public GitHub repository. Do not commit `.env`.
2. On a host that supports Docker background workers, create a new worker/service from the repository.
3. Set the start command to `python bot.py` if the host asks for one; otherwise the Dockerfile already provides it.
4. Add environment variables:
   - `BOT_TOKEN` = token from BotFather
   - `MAX_FILE_MB` = `18`
   - `MAX_QUEUED_IMAGES` = `10`
5. Deploy and check logs for `PDF converter bot is starting`.
6. Send `/start` to the bot in Telegram.

### Render / free hosting warning

This is a long-running polling worker, not a web service. Some free hosting plans do not support always-on workers or may sleep/stop them. Check the provider's current plan terms before choosing it. If the provider only offers free web services that sleep, the bot may stop receiving updates while asleep. A small VM or an always-on worker is more reliable.

## 5. Security and troubleshooting

- **Invalid token:** create/copy a fresh token in `@BotFather`, then update `BOT_TOKEN`.
- **409 Conflict:** stop every other running copy of this bot using the same token. Only one polling process should run at a time.
- **Office conversion unavailable:** ensure Docker deployment uses the included Dockerfile, or install LibreOffice locally.
- **Unsupported file:** confirm the filename ends with `.jpg`, `.jpeg`, `.png`, `.docx`, `.txt`, `.xlsx`, or `.pptx`.
- **File too large:** reduce `MAX_FILE_MB` if needed; the hosted Telegram Bot API has its own limits.
- **Large spreadsheets:** LibreOffice may take time or create a large PDF. Test with trusted sample files.
- **No conversion for legacy `.doc`, `.xls`, `.ppt`:** these are intentionally not in the allowed list; save them to modern `.docx`, `.xlsx`, `.pptx` first.

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `BOT_TOKEN` | required | Telegram bot token |
| `MAX_FILE_MB` | `18` | Maximum input file size |
| `MAX_QUEUED_IMAGES` | `10` | Max images per merged PDF |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
