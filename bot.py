import os
import logging
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

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
                 (user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  telegram_id INTEGER UNIQUE,
                  username TEXT,
                  full_name TEXT,
                  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Таблица слотов
    c.execute('''CREATE TABLE IF NOT EXISTS slots
                 (slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  start_time TEXT,
                  end_time TEXT,
                  max_people INTEGER DEFAULT 3)''')
    
    # Таблица бронирований
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  slot_id INTEGER,
                  booking_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(user_id),
                  FOREIGN KEY (slot_id) REFERENCES slots(slot_id))''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def get_user_id(telegram_id, username, full_name):
    """Получает или создает пользователя"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''INSERT OR IGNORE INTO users (telegram_id, username, full_name) 
                 VALUES (?, ?, ?)''', (telegram_id, username, full_name))
    
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (telegram_id,))
    result = c.fetchone()
    user_id = result[0] if result else 1
    
    conn.commit()
    conn.close()
    return user_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Регистрируем пользователя
    get_user_id(user.id, user.username, user.full_name)
    
    # Главное меню
    keyboard = [
        [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
        [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для записи на перерывы в офисе.\n"
        "Выберите действие ниже:",
        reply_markup=reply_markup
    )

async def handle_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(
        f"⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
        f"🕐 **Текущее время:** {datetime.now().strftime('%H:%M')}\n"
        "📅 **Доступные слоты на ближайшие 2 часа**\n\n"
        "**Легенда:**\n"
        "🟢 - свободно\n"
        "🟡 - мало мест\n"
        "🔴 - занят\n\n"
        "👇 Выберите слот:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("slot_"):
        slot_num = data.split("_")[1]
        
        if slot_num == "4":
            await query.edit_message_text(
                text="❌ **Слот занят!**\n\n"
                     "Этот слот уже полностью забронирован.\n"
                     "Выберите другой временной интервал.",
                parse_mode='Markdown'
            )
        else:
            times = {
                "1": "10:00-10:15",
                "2": "10:15-10:30", 
                "3": "10:30-10:45"
            }
            
            await query.edit_message_text(
                text=f"✅ **Вы успешно записались!**\n\n"
                     f"🎯 **Время:** {times.get(slot_num, 'Неизвестный слот')}\n"
                     "🔄 Чтобы изменить запись, нажмите /start",
                parse_mode='Markdown'
            )
    
    elif data == "refresh":
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
        
        await query.edit_message_text(
            text=f"⏰ **ОБНОВЛЕННЫЕ СЛОТЫ**\n\n"
                 f"🕐 **Время:** {datetime.now().strftime('%H:%M')}\n"
                 "👇 Выберите слот:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    
    if text == "📅 ЗАПИСАТЬСЯ":
        await handle_book(update, context)
    elif text == "👤 МОИ ЗАПИСИ":
        await update.message.reply_text(
            "📋 **ВАШИ АКТИВНЫЕ ЗАПИСИ**\n\n"
            "1. 🟢 10:00-10:15\n"
            "2. 🟡 11:30-11:45\n\n"
            "📊 Всего: 2 записи",
            parse_mode='Markdown'
        )
    elif text == "🏢 ВСЕ БРОНИРОВАНИЯ":
        await update.message.reply_text(
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
        await update.message.reply_text(
            "📊 **СТАТИСТИКА НА СЕГОДНЯ**\n\n"
            "👥 Участников: 15 человек\n"
            "📅 Всего слотов: 96\n"
            "✅ Занято слотов: 12\n"
            "🎯 Свободно: 84 слотов",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "Используйте кнопки ниже 👇\n"
            "Или команду /start для главного меню"
        )

def main():
    """Запуск бота"""
    init_db()
    
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен не найден!")
        logger.error("Добавьте TELEGRAM_BOT_TOKEN в переменные окружения")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("=" * 50)
    logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
    logger.info("=" * 50)
    logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
    logger.info("=" * 50)
    logger.info("🚀 Бот запускается...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
