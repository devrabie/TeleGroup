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

## Not required

Downloads use the `yt-dlp` Python package. Stickers use `Pillow`. Speech uses `gTTS`. Translation uses `httpx` against MyMemory, or LibreTranslate when `TRANSLATE_URL` is set. None of those need an extra apt package.

Keep `MEDIA_WORKERS` at `1` on the 2-CPU host. See the main README.
