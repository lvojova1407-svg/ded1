import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    MessageHandler,
    Filters,
    ConversationHandler
)

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DB_NAME = 'breaks.db'

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  telegram_id INTEGER UNIQUE,
                  username TEXT,
                  full_name TEXT,
                  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# ==================== КОМАНДЫ БОТА ====================
def start(update: Update, context: CallbackContext):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Простое меню
    keyboard = [
        [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
        [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для записи на перерывы в офисе.\n\n"
        "👇 Выберите действие:",
        reply_markup=reply_markup
    )

def handle_book(update: Update, context: CallbackContext):
    """Обработчик кнопки ЗАПИСАТЬСЯ"""
    keyboard = [
        [
            InlineKeyboardButton("10:00-10:15 🟢", callback_data="slot_1"),
            InlineKeyboardButton("10:15-10:30 🟢", callback_data="slot_2")
        ],
        [
            InlineKeyboardButton("10:30-10:45 🟡", callback_data="slot_3"),
            InlineKeyboardButton("10:45-11:00 🔴", callback_data="slot_4")
        ],
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        "⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
        "🕐 **Текущее время:** " + datetime.now().strftime("%H:%M") + "\n"
        "📅 **Показываем слоты на ближайшие 2 часа**\n\n"
        "**Легенда:**\n"
        "🟢 - свободно\n"
        "🟡 - 1 место свободно\n"
        "🔴 - занят\n\n"
        "👇 Нажмите на слот для записи:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

def button_handler(update: Update, context: CallbackContext):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    
    if data.startswith("slot_"):
        # Бронирование слота
        slot_num = data.split("_")[1]
        
        if slot_num == "4":
            query.edit_message_text(
                text="❌ **Слот занят!**\n\n"
                     "Этот слот уже полностью забронирован.\n"
                     "Выберите другой временной интервал.",
                parse_mode='Markdown'
            )
        else:
            query.edit_message_text(
                text="✅ **Вы успешно записались!**\n\n"
                     f"🎯 Выбранный слот: {get_slot_time(slot_num)}\n"
                     "📝 Ваше имя будет отображаться в списке.\n\n"
                     "🔄 Чтобы изменить запись, нажмите /start",
                parse_mode='Markdown'
            )
    elif data == "refresh":
        # Обновление слотов
        keyboard = [
            [
                InlineKeyboardButton("11:00-11:15 🟢", callback_data="slot_5"),
                InlineKeyboardButton("11:15-11:30 🟢", callback_data="slot_6")
            ],
            [
                InlineKeyboardButton("11:30-11:45 🟡", callback_data="slot_7"),
                InlineKeyboardButton("11:45-12:00 🔴", callback_data="slot_8")
            ],
            [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
            "🕐 **Текущее время:** " + datetime.now().strftime("%H:%M") + "\n"
            "📅 **Обновленные слоты**\n\n"
            "👇 Нажмите на слот для записи:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

def get_slot_time(slot_num):
    """Возвращает время слота по номеру"""
    times = {
        "1": "10:00-10:15",
        "2": "10:15-10:30", 
        "3": "10:30-10:45",
        "4": "10:45-11:00",
        "5": "11:00-11:15",
        "6": "11:15-11:30",
        "7": "11:30-11:45",
        "8": "11:45-12:00"
    }
    return times.get(slot_num, "Неизвестный слот")

def handle_message(update: Update, context: CallbackContext):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    
    if text == "📅 ЗАПИСАТЬСЯ":
        handle_book(update, context)
    elif text == "👤 МОИ ЗАПИСИ":
        update.message.reply_text(
            "📋 **ВАШИ АКТИВНЫЕ ЗАПИСИ**\n\n"
            "1. 🟢 10:00-10:15\n"
            "2. 🟡 11:30-11:45\n\n"
            "📊 Всего: 2 записи",
            parse_mode='Markdown'
        )
    elif text == "🏢 ВСЕ БРОНИРОВАНИЯ":
        update.message.reply_text(
            "🏢 **ВСЕ БРОНИРОВАНИЯ**\n\n"
            "🟢 10:00-10:15 - свободно\n"
            "🟢 10:15-10:30 - свободно\n"
            "🟡 10:30-10:45 - 1 место свободно\n"
            "🔴 10:45-11:00 - занят\n"
            "🟢 11:00-11:15 - свободно\n\n"
            "📊 Итого: 1 слот занят",
            parse_mode='Markdown'
        )
    elif text == "📊 СТАТИСТИКА":
        update.message.reply_text(
            "📊 **СТАТИСТИКА НА СЕГОДНЯ**\n\n"
            "👥 Участников: 15 человек\n"
            "📅 Всего слотов: 96\n"
            "✅ Занято слотов: 12\n"
            "🎯 Свободно: 84 слотов",
            parse_mode='Markdown'
        )
    else:
        update.message.reply_text(
            "Используйте кнопки ниже 👇\n"
            "Или команду /start для главного меню"
        )

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
def main():
    """Запуск бота"""
    # Инициализация БД
    init_db()
    
    # Проверка токена
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен не найден!")
        logger.error("Добавьте TELEGRAM_BOT_TOKEN в переменные окружения")
        return
    
    # Создаем updater
    updater = Updater(TOKEN, use_context=True)
    
    # Получаем диспетчер
    dp = updater.dispatcher
    
    # Регистрируем обработчики
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Логирование запуска
    logger.info("=" * 50)
    logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
    logger.info("=" * 50)
    logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
    logger.info("=" * 50)
    logger.info("🚀 Бот запускается...")
    
    # Запускаем бота
    updater.start_polling()
    
    # Запускаем ping сервис в фоне (для предотвращения сна на Render)
    if os.environ.get('RENDER'):
        logger.info("🌐 Запускаю ping сервис для Render...")
        # В отдельном потоке будем пинговать себя
        import threading
        
        def ping_service():
            """Сервис для пинга"""
            import requests
            import random
            
            # Ждем запуска бота
            time.sleep(10)
            
            render_url = os.environ.get('RENDER_EXTERNAL_URL', '')
            if not render_url:
                logger.warning("❌ RENDER_EXTERNAL_URL не найден")
                return
            
            logger.info(f"🌐 Ping сервис запущен для URL: {render_url}")
            
            while True:
                try:
                    # Ждем случайное время от 8 до 12 минут
                    sleep_time = random.randint(480, 720)
                    logger.info(f"😴 Следующий пинг через {sleep_time//60} минут...")
                    time.sleep(sleep_time)
                    
                    # Делаем ping
                    response = requests.get(render_url, timeout=10)
                    logger.info(f"✅ Ping успешен: статус {response.status_code}")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка ping: {e}")
                    time.sleep(60)  # При ошибке ждем минуту
        
        # Запускаем ping сервис в отдельном потоке
        ping_thread = threading.Thread(target=ping_service, daemon=True)
        ping_thread.start()
    
    # Бот работает до остановки
    updater.idle()

if __name__ == '__main__':
    main()
