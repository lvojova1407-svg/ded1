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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    keyboard = [
        [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
        [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для записи на перерывы в офисе.\n\n"
        "👇 Выберите действие:",
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
        "⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
        f"🕐 **Текущее время:** {datetime.now().strftime('%H:%M')}\n"
        "📅 **Показываем слоты на ближайшие 2 часа**\n\n"
        "**Легенда:**\n"
        "🟢 - свободно\n"
        "🟡 - 1 место свободно\n"
        "🔴 - занят\n\n"
        "👇 Нажмите на слот для записи:",
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
            await query.edit_message_text(
                text="✅ **Вы успешно записались!**\n\n"
                     f"🎯 Выбранный слот: {get_slot_time(slot_num)}\n"
                     "📝 Ваше имя будет отображаться в списке.\n\n"
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
            "⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
            f"🕐 **Текущее время:** {datetime.now().strftime('%H:%M')}\n"
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

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
def main():
    """Запуск бота"""
    init_db()
    
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен не найден!")
        logger.error("Добавьте TELEGRAM_BOT_TOKEN в переменные окружения")
        return
    
    # Создаем Application
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("=" * 50)
    logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
    logger.info("=" * 50)
    logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
    logger.info("=" * 50)
    logger.info("🚀 Бот запускается...")
    
    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
