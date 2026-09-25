# Native host packages

Production runs on Ubuntu 22.04 without Docker. The Python environment is the project virtualenv (`uv` or `pip install -e .`). These apt packages are separate from the Python dependencies.

## Required for media commands

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

`ffmpeg` is used for gif conversion, voice notes, mp3 extraction, and YouTube audio extraction. If it is missing, those commands reply that ffmpeg is not installed. YouTube video download still runs. Text-to-speech falls back to an audio file instead of a voice note.

## Optional OCR

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara
```

`.استخراج` / `.ocr` checks for the `tesseract` binary. Without it, the command explains that OCR is unavailable. The Docker image does not install tesseract.

## Inline control panel

`.اللوحة` posts buttons through the control bot's inline mode. Enable it once per bot, from the Telegram account that owns the bot:

1. Open `@BotFather`.
2. Send `/setinline`.
3. Choose the control bot.
4. Send a short placeholder such as `لوحة`.

No package install is required for this. If inline mode is off, the account replies with the same steps.

## Not required

Downloads use the `yt-dlp` Python package. Stickers use `Pillow`. Speech uses `gTTS`. Translation uses `httpx` against MyMemory, or LibreTranslate when `TRANSLATE_URL` is set. None of those need an extra apt package.

Keep `MEDIA_WORKERS` at `1` on the 2-CPU host. See the main README.
