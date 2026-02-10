# bot_server.py
import os
import logging
import threading
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Импортируем функцию запуска бота из вашего файла
# Если бот находится в том же файле, используйте import
from bot import run_bot  # Или ваш_файл_бота import run_bot

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создаем FastAPI приложение
app = FastAPI(title="Telegram Bot Server", version="1.0.0")

# Флаг для отслеживания запуска бота
bot_started = False
bot_thread = None

@app.on_event("startup")
async def startup_event():
    """Запускаем бота при старте сервера"""
    global bot_started, bot_thread
    
    if not bot_started:
        logger.info("🚀 Запускаем Telegram бота в фоновом режиме...")
        
        # Запускаем бота в отдельном потоке
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        bot_started = True
        logger.info("✅ Telegram бот запущен в фоновом режиме")

@app.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "message": "Telegram Bot Server is running",
        "bot_status": "running" if bot_started else "stopped",
        "docs": "/docs",
        "health": "/health"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint для мониторинга"""
    return JSONResponse(
        content={
            "status": "healthy",
            "bot_running": bot_started,
            "service": "telegram-bot-server"
        },
        status_code=200
    )

@app.get("/bot-status")
async def bot_status():
    """Проверка статуса бота"""
    if bot_started and bot_thread and bot_thread.is_alive():
        return {"status": "running", "message": "Бот активен"}
    else:
        return {"status": "stopped", "message": "Бот не запущен"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
