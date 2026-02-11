"""
🤖 Telegram Bot для записи на перерывы - ИСПРАВЛЕННАЯ ВЕРСИЯ для Render
🚀 С авто-пингом для 24/7 работы
"""
import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

# FastAPI
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes
)

# --- Конфигурация ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Токен бота не найден! Установите TELEGRAM_BOT_TOKEN")

PORT = int(os.getenv("PORT", 10000))
DATABASE_URL = os.getenv("DATABASE_URL", "breaks.db")

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- FastAPI приложение ---
app = FastAPI(
    title="Telegram Bot для записи на перерывы",
    description="Бот для организации перерывов с авто-пингом для 24/7 работы",
    version="2.0"
)

# Глобальные переменные
bot_app: Optional[Application] = None
startup_time = datetime.now(timezone.utc)

# --- БАЗА ДАННЫХ ---
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица записей на перерывы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS breaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            break_time TEXT,
            break_date DATE,
            registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_moscow_time() -> str:
    """Получить текущее время по Москве"""
    moscow_tz = timezone(timedelta(hours=3))
    return datetime.now(moscow_tz).strftime("%H:%M")

def get_current_date() -> str:
    """Получить текущую дату в формате YYYY-MM-DD"""
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")

def get_break_times() -> List[str]:
    """Возвращает список доступных времен для перерывов"""
    return ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", 
            "13:00", "13:30", "14:00", "14:30", "15:00", "15:30"]

def save_user_to_db(user_id: int, username: str, first_name: str, last_name: str):
    """Сохраняет пользователя в базу данных"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name))
    
    conn.commit()
    conn.close()

def save_break_to_db(user_id: int, break_time: str, break_date: str):
    """Сохраняет запись на перерыв в базу данных"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Проверяем, не записан ли уже пользователь на это время
    cursor.execute('''
        SELECT COUNT(*) FROM breaks 
        WHERE user_id = ? AND break_date = ? AND break_time = ?
    ''', (user_id, break_date, break_time))
    
    count = cursor.fetchone()[0]
    
    if count > 0:
        conn.close()
        return False  # Уже записан
    
    # Сохраняем запись
    cursor.execute('''
        INSERT INTO breaks (user_id, break_time, break_date)
        VALUES (?, ?, ?)
    ''', (user_id, break_time, break_date))
    
    conn.commit()
    conn.close()
    return True

def get_user_breaks(user_id: int, break_date: str) -> List[str]:
    """Получает перерывы пользователя на указанную дату"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT break_time FROM breaks 
        WHERE user_id = ? AND break_date = ?
        ORDER BY break_time
    ''', (user_id, break_date))
    
    breaks = [row[0] for row in cursor.fetchall()]
    conn.close()
    return breaks

def get_all_breaks(break_date: str) -> Dict[str, List[str]]:
    """Получает все записи на перерывы на указанную дату"""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.username, b.break_time 
        FROM breaks b
        JOIN users u ON b.user_id = u.user_id
        WHERE b.break_date = ?
        ORDER BY b.break_time
    ''', (break_date,))
    
    breaks = {}
    for username, break_time in cursor.fetchall():
        if break_time not in breaks:
            breaks[break_time] = []
        breaks[break_time].append(username or "Аноним")
    
    conn.close()
    return breaks

# --- КОМАНДЫ TELEGRAM БОТА ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Сохраняем пользователя в БД
    save_user_to_db(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    welcome_text = f"""
    👋 Привет, {user.first_name}!

    🤖 Я бот для записи на перерывы.
    
    📅 *Сегодня:* {get_current_date()}
    ⏰ *Время по Москве:* {get_moscow_time()}
    
    *Доступные команды:*
    /start - Начало работы
    /breaks - Записаться на перерыв
    /my_breaks - Мои записи
    /today - Расписание на сегодня
    /help - Помощь
    
    Выберите действие:
    """
    
    keyboard = [
        [InlineKeyboardButton("📅 Записаться на перерыв", callback_data="show_breaks")],
        [InlineKeyboardButton("👤 Мои записи", callback_data="my_breaks")],
        [InlineKeyboardButton("📋 Расписание на сегодня", callback_data="today_schedule")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
    🤖 *Помощь по боту*
    
    *Основные команды:*
    /start - Начало работы с ботом
    /breaks - Записаться на перерыв
    /my_breaks - Посмотреть свои записи
    /today - Расписание на сегодня
    /help - Эта справка
    
    *Как записаться:*
    1. Нажмите "Записаться на перерыв"
    2. Выберите удобное время
    3. Подтвердите запись
    
    *Как отменить запись:*
    Нажмите на время, на которое записаны, чтобы отменить
    
    *Время работы:*
    Бот работает круглосуточно!
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def breaks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /breaks - показывает доступные перерывы"""
    await show_breaks_menu(update, context)

async def my_breaks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /my_breaks - показывает мои записи"""
    await show_my_breaks(update, context)

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /today - показывает расписание на сегодня"""
    await show_today_schedule(update, context)

# --- ОБРАБОТЧИКИ КНОПОК ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline-кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "show_breaks":
        await show_breaks_menu(update, context)
    
    elif data == "my_breaks":
        await show_my_breaks(update, context)
    
    elif data == "today_schedule":
        await show_today_schedule(update, context)
    
    elif data.startswith("select_"):
        # Выбор времени перерыва
        break_time = data.replace("select_", "")
        await confirm_break_selection(update, context, break_time)
    
    elif data.startswith("confirm_"):
        # Подтверждение записи
        break_time = data.replace("confirm_", "")
        await process_break_registration(update, context, break_time)
    
    elif data == "back_to_menu":
        await start_command(update, context)

async def show_breaks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора времени перерыва"""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    
    current_date = get_current_date()
    user_breaks = get_user_breaks(user_id, current_date)
    
    # Создаем клавиатуру с временами
    keyboard = []
    break_times = get_break_times()
    
    for i in range(0, len(break_times), 2):
        row = []
        for j in range(2):
            if i + j < len(break_times):
                time = break_times[i + j]
                # Проверяем, записан ли уже пользователь на это время
                if time in user_breaks:
                    row.append(InlineKeyboardButton(f"✅ {time}", callback_data=f"select_{time}"))
                else:
                    row.append(InlineKeyboardButton(f"🕐 {time}", callback_data=f"select_{time}"))
        keyboard.append(row)
    
    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"""
    📅 *Запись на перерыв*
    
    *Дата:* {current_date}
    *Ваши записи:* {', '.join(user_breaks) if user_breaks else 'нет'}
    
    Выберите время перерыва:
    ✅ - уже записаны
    🕐 - доступно для записи
    """
    
    if query:
        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def confirm_break_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, break_time: str):
    """Подтверждение выбора времени"""
    query = update.callback_query
    
    text = f"""
    🕐 *Подтверждение записи*
    
    *Время:* {break_time}
    *Дата:* {get_current_date()}
    
    Подтверждаете запись?
    """
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, записать", callback_data=f"confirm_{break_time}"),
            InlineKeyboardButton("❌ Нет, отменить", callback_data="show_breaks")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data="show_breaks")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def process_break_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, break_time: str):
    """Обработка записи на перерыв"""
    query = update.callback_query
    user_id = query.from_user.id
    current_date = get_current_date()
    
    # Сохраняем запись в БД
    success = save_break_to_db(user_id, break_time, current_date)
    
    if success:
        text = f"""
        ✅ *Запись подтверждена!*
        
        *Время:* {break_time}
        *Дата:* {current_date}
        
        Вы успешно записаны на перерыв!
        """
    else:
        text = f"""
        ⚠️ *Запись уже существует!*
        
        Вы уже записаны на перерыв в {break_time}
        """
    
    keyboard = [
        [InlineKeyboardButton("📅 Еще одна запись", callback_data="show_breaks")],
        [InlineKeyboardButton("👤 Мои записи", callback_data="my_breaks")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def show_my_breaks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает записи пользователя"""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    current_date = get_current_date()
    
    user_breaks = get_user_breaks(user_id, current_date)
    
    if user_breaks:
        text = f"""
        👤 *Ваши записи на сегодня*
        
        *Дата:* {current_date}
        *Время по Москве:* {get_moscow_time()}
        
        📋 *Записанные перерывы:*
        """
        for i, break_time in enumerate(user_breaks, 1):
            text += f"\n{i}. 🕐 {break_time}"
    else:
        text = f"""
        👤 *Ваши записи*
        
        *Дата:* {current_date}
        
        📭 У вас нет записей на сегодня.
        Запишитесь на перерыв!
        """
    
    keyboard = [
        [InlineKeyboardButton("📅 Записаться", callback_data="show_breaks")],
        [InlineKeyboardButton("📋 Расписание на сегодня", callback_data="today_schedule")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def show_today_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расписание на сегодня"""
    query = update.callback_query
    current_date = get_current_date()
    
    all_breaks = get_all_breaks(current_date)
    
    if all_breaks:
        text = f"""
        📋 *Расписание на сегодня*
        
        *Дата:* {current_date}
        *Время по Москве:* {get_moscow_time()}
        
        📅 *Записи:*
        """
        
        for break_time in get_break_times():
            if break_time in all_breaks:
                users = ", ".join(all_breaks[break_time])
                text += f"\n🕐 *{break_time}*: {users}"
            else:
                text += f"\n🕐 *{break_time}*: свободно"
    else:
        text = f"""
        📋 *Расписание на сегодня*
        
        *Дата:* {current_date}
        
        📭 На сегодня еще нет записей.
        Будьте первым!
        """
    
    keyboard = [
        [InlineKeyboardButton("📅 Записаться", callback_data="show_breaks")],
        [InlineKeyboardButton("👤 Мои записи", callback_data="my_breaks")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

# --- ЗАПУСК ТЕЛЕГРАМ БОТА ---
async def start_bot():
    """Запуск Telegram бота"""
    global bot_app
    
    logger.info("🤖 Инициализация Telegram бота...")
    
    # Ждем стабилизации сети на Render
    await asyncio.sleep(10)
    
    # Создаем приложение
    bot_app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики команд
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("help", help_command))
    bot_app.add_handler(CommandHandler("breaks", breaks_command))
    bot_app.add_handler(CommandHandler("my_breaks", my_breaks_command))
    bot_app.add_handler(CommandHandler("today", today_command))
    
    # Добавляем обработчик inline-кнопок
    bot_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Инициализируем и запускаем
    await bot_app.initialize()
    await bot_app.start()
    
    # Начинаем polling
    await bot_app.updater.start_polling(
        poll_interval=0.5,
        timeout=10,
        drop_pending_updates=True
    )
    
    logger.info("✅ Telegram бот успешно запущен!")
    return True

# --- ПРОСТОЙ АВТО-ПИНГ ---
import threading
import time
import requests

def start_auto_ping():
    """Запускает простой авто-пинг в отдельном потоке"""
    def ping_worker():
        # Ждем полного запуска сервера
        time.sleep(30)
        
        url = "https://ded1-8.onrender.com"
        logger.info(f"🧵 Авто-пинг запущен для {url}")
        
        while True:
            try:
                response = requests.get(f"{url}/health", timeout=10)
                if response.status_code == 200:
                    logger.info(f"✅ Авто-пинг успешен")
                else:
                    logger.warning(f"⚠️ Авто-пинг: код {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Ошибка авто-пинга: {e}")
            
            # Пинг каждые 8 минут (меньше 15-минутного лимита Render)
            time.sleep(480)
    
    thread = threading.Thread(target=ping_worker, daemon=True)
    thread.start()
    return thread

# --- FastAPI ЭНДПОИНТЫ ---
@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "message": "🤖 Telegram Bot для записи на перерывы",
        "status": "running",
        "bot": "active" if bot_app else "starting",
        "time_moscow": get_moscow_time(),
        "date": get_current_date(),
        "uptime": str(datetime.now(timezone.utc) - startup_time),
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "ping": "/ping"
        }
    }

@app.get("/health")
async def health_check():
    """Health check для Render"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_running": bool(bot_app),
        "time_moscow": get_moscow_time(),
        "version": "2.0"
    }

@app.get("/status")
async def status():
    """Статус системы"""
    return {
        "server": {
            "uptime": str(datetime.now(timezone.utc) - startup_time),
            "port": PORT,
            "startup_time": startup_time.isoformat()
        },
        "bot": {
            "initialized": bool(bot_app),
            "database": "connected"
        }
    }

@app.get("/ping")
async def ping():
    """Ручной пинг"""
    return {"ping": "pong", "timestamp": datetime.now(timezone.utc).isoformat()}

# --- ОБРАБОТЧИКИ СОБЫТИЙ ---
@app.on_event("startup")
async def startup_event():
    """Запуск при старте приложения"""
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК БОТА ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
    logger.info("=" * 50)
    
    # Инициализируем БД
    init_db()
    
    logger.info(f"✅ Токен бота: {'Найден' if TOKEN else 'Не найден'}")
    logger.info(f"⏰ Время по Москве: {get_moscow_time()}")
    logger.info(f"📅 Дата: {get_current_date()}")
    logger.info(f"🌐 Порт: {PORT}")
    logger.info("=" * 50)
    
    # Запускаем авто-пинг в отдельном потоке
    start_auto_ping()
    logger.info("🔧 Авто-пинг запущен (пинг каждые 8 минут)")
    
    # Запускаем бота
    await start_bot()
    
    logger.info("🎉 Все системы запущены и готовы к работе!")

@app.on_event("shutdown")
async def shutdown_event():
    """Остановка при завершении"""
    logger.info("🛑 Завершение работы сервера...")
    
    if bot_app:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("✅ Telegram бот остановлен")
    
    logger.info("👋 Сервер остановлен")

# --- ТОЧКА ВХОДА ---
def main():
    """Основная функция запуска"""
    logger.info(f"🌍 Запуск сервера на порту {PORT}...")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        access_log=False,  # Убираем лишние логи от Uvicorn
        log_level="info"
    )

if __name__ == "__main__":
    main()
