# bot_server.py - объединенная версия
import os
import logging
import sqlite3
import asyncio
import threading
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DB_NAME = 'breaks.db'

# ... ВСЯ ЛОГИКА ВАШЕГО БОТА ИЗ bot.py ...

# ==================== FASTAPI СЕРВЕР ====================
app = FastAPI(title="Telegram Bot Server", version="1.0.0")

bot_started = False
bot_thread = None

def run_bot():
    """Функция для запуска бота в отдельном потоке"""
    # Устанавливаем новый event loop для этого потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # ... ВАША ЛОГИКА ЗАПУСКА БОТА ...
    
@app.on_event("startup")
async def startup_event():
    """Запускаем бота при старте сервера"""
    global bot_started, bot_thread
    
    if not bot_started:
        logger.info("🚀 Запускаем Telegram бота в фоновом режиме...")
        
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        bot_started = True
        logger.info("✅ Telegram бот запущен в фоновом режиме")

@app.get("/")
async def root():
    return {"message": "Telegram Bot Server is running"}

@app.get("/health")
async def health_check():
    return JSONResponse(
        content={"status": "healthy", "bot_running": bot_started},
        status_code=200
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
