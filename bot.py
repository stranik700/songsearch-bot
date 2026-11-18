import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from yt_dlp import YoutubeDL

# Загружаем токен из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Ошибка: не найден BOT_TOKEN в файле .env")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временное хранилище результатов поиска пользователей
search_cache = {}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def search_tracks(query: str):
    """Ищет 5 вариантов треков (оригиналы, ремиксы) без скачивания."""
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch5:{query} audio", download=False)
        results = []
        for entry in info.get("entries", []):
            if entry:
                duration = entry.get("duration") or 0
                mins, secs = divmod(int(duration), 60)
                results.append({
                    "id": entry.get("id"),
                    "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                    "title": entry.get("title", "Без названия"),
                    "duration": f"{mins}:{secs:02d}" if duration else "?:??"
                })
        return results


def download_audio(video_url: str, user_id: int) -> tuple[str, str]:
    """Скачивает аудио в лучшем качестве (m4a/mp3) и возвращает путь и название."""
    output_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "noplaylist": True,
        "max_filesize": 45 * 1024 * 1024,  # Ограничение 45 МБ для Telegram
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)
        title = info.get("title", "Track")
        return filename, title


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    text = (
        "🎧 **Привет! Я музыкальный бот.**\n\n"
        "Напиши мне название песни или имя исполнителя (можно добавить *remix*, *slowed* и т.д.).\n"
        "Я найду **5 вариантов** и скину выбранный трек прямо в чат! 🎶"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text)
async def handle_search(message: types.Message):
    query = message.text.strip()
    if not query:
        return

    wait_msg = await message.answer("🔍 Ищу варианты треков...")

    try:
        # Запускаем поиск в отдельном потоке, чтобы бот не зависал
        results = await asyncio.to_thread(search_tracks, query)
    except Exception as e:
        logging.error(f"Ошибка поиска: {e}")
        await wait_msg.edit_text("❌ Произошла ошибка при поиске. Попробуй другой запрос.")
        return

    if not results:
        await wait_msg.edit_text("😔 По твоему запросу ничего не нашлось. Попробуй написать иначе.")
        return

    user_id = message.from_user.id
    search_cache[user_id] = results

    # Формируем текст со списком найденных треков
    lines = [f"🎵 **Результаты по запросу:** *{query}*\n"]
    buttons = []

    for idx, track in enumerate(results, start=1):
        lines.append(f"**{idx}.** {track['title']} `[{track['duration']}]`")
        buttons.append(InlineKeyboardButton(text=f"⬇️ {idx}", callback_data=f"dl_{idx-1}"))

    lines.append("\n👇 Выбери номер трека для скачивания:")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    await wait_msg.edit_text("\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("dl_"))
async def handle_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    track_idx = int(callback.data.split("_")[1])

    user_results = search_cache.get(user_id)
    if not user_results or track_idx >= len(user_results):
        await callback.answer("⚠️ Список устарел. Напиши название песни заново.", show_alert=True)
        return

    track = user_results[track_idx]
    await callback.answer("⏳ Начинаю загрузку...")
    status_msg = await callback.message.answer(f"⏳ Скачиваю: **{track['title']}**...", parse_mode="Markdown")

    file_path = None
    try:
        file_path, title = await asyncio.to_thread(download_audio, track["url"], user_id)

        # Отправляем аудиофайл в плеер Telegram
        audio_file = FSInputFile(file_path, filename=f"{title}.m4a")
        await callback.message.answer_audio(
            audio=audio_file,
            title=title,
            caption="🎧 Найдено через @songsearch_aligator_bot"
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Ошибка скачивания: {e}")
        await status_msg.edit_text("❌ Не удалось скачать этот вариант (возможно, файл слишком большой). Попробуй выбрать другой номер.")
    finally:
        # Обязательно удаляем файл с диска после отправки
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


async def main():
    logging.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())