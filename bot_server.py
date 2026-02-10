# В файле bot_server.py
import threading
from bot import run_bot  # предполагая, что код выше находится в bot.py

# Запускаем бота в отдельном потоке при старте FastAPI
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
