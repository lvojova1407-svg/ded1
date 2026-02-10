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
    try:
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
                     time_range TEXT UNIQUE,
                     max_people INTEGER DEFAULT 3)''')
        
        # Таблица бронирований
        c.execute('''CREATE TABLE IF NOT EXISTS bookings
                    (booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     slot_id INTEGER,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY (user_id) REFERENCES users(user_id),
                     FOREIGN KEY (slot_id) REFERENCES slots(slot_id))''')
        
        # Создаем тестовые слоты
        time_slots = [
            "10:00-10:15", "10:15-10:30", "10:30-10:45", "10:45-11:00",
            "11:00-11:15", "11:15-11:30", "11:30-11:45", "11:45-12:00",
            "12:00-12:15", "12:15-12:30", "12:30-12:45", "12:45-13:00",
            "13:00-13:15", "13:15-13:30", "13:30-13:45", "13:45-14:00"
        ]
        
        for time_slot in time_slots:
            c.execute('''INSERT OR IGNORE INTO slots (time_range) VALUES (?)''', (time_slot,))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_or_create_user(telegram_id: int, username: str, full_name: str) -> int:
    """Получает или создает пользователя в БД"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Проверяем существование пользователя
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (telegram_id,))
    result = c.fetchone()
    
    if result:
        user_id = result[0]
    else:
        # Создаем нового пользователя
        c.execute('''INSERT INTO users (telegram_id, username, full_name) 
                    VALUES (?, ?, ?)''', (telegram_id, username, full_name))
        user_id = c.lastrowid
    
    conn.commit()
    conn.close()
    return user_id

def get_available_slots():
    """Получает список доступных слотов"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Получаем все слоты с количеством бронирований
    c.execute('''SELECT s.slot_id, s.time_range, 
                        COUNT(b.booking_id) as booked_count,
                        s.max_people
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 GROUP BY s.slot_id
                 ORDER BY s.time_range
                 LIMIT 8''')
    
    slots = c.fetchall()
    conn.close()
    return slots

def book_slot(user_id: int, slot_id: int) -> bool:
    """Бронирует слот для пользователя"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Проверяем доступность
        c.execute('''SELECT COUNT(*) FROM bookings WHERE slot_id = ?''', (slot_id,))
        booked_count = c.fetchone()[0]
        
        c.execute('''SELECT max_people FROM slots WHERE slot_id = ?''', (slot_id,))
        max_people = c.fetchone()[0]
        
        if booked_count >= max_people:
            return False
        
        # Создаем бронирование
        c.execute('''INSERT INTO bookings (user_id, slot_id) VALUES (?, ?)''', 
                 (user_id, slot_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка бронирования: {e}")
        return False
    finally:
        conn.close()

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Регистрируем пользователя
    user_id = get_or_create_user(user.id, user.username, user.full_name)
    
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
    slots = get_available_slots()
    
    if not slots:
        await update.message.reply_text(
            "❌ Нет доступных слотов на ближайшее время.",
            parse_mode='Markdown'
        )
        return
    
    # Создаем клавиатуру с доступными слотами
    keyboard = []
    row = []
    
    for i, slot in enumerate(slots):
        slot_id, time_range, booked_count, max_people = slot
        
        # Определяем статус слота
        if booked_count == 0:
            status = "🟢"
        elif booked_count < max_people:
            status = "🟡"
        else:
            status = "🔴"
        
        button_text = f"{time_range} {status}"
        callback_data = f"book_{slot_id}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        
        # Размещаем по 2 кнопки в ряд
        if len(row) == 2 or i == len(slots) - 1:
            keyboard.append(row)
            row = []
    
    # Добавляем кнопку обновления
    keyboard.append([InlineKeyboardButton("🔄 Обновить слоты", callback_data="refresh_slots")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⏰ **Выбор времени для перерыва**\n\n"
        f"🕐 Текущее время: {datetime.now().strftime('%H:%M')}\n\n"
        "**Статус слотов:**\n"
        "🟢 - свободно\n"
        "🟡 - есть места\n"
        "🔴 - занят\n\n"
        "👇 Выберите удобное время:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    if data.startswith("book_"):
        # Бронирование слота
        slot_id = int(data.split("_")[1])
        
        # Регистрируем пользователя
        user_id = get_or_create_user(user.id, user.username, user.full_name)
        
        # Пробуем забронировать
        if book_slot(user_id, slot_id):
            # Получаем информацию о слоте
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute('''SELECT time_range FROM slots WHERE slot_id = ?''', (slot_id,))
            time_range = c.fetchone()[0]
            conn.close()
            
            await query.edit_message_text(
                text=f"✅ **Вы успешно записались!**\n\n"
                     f"🎯 Время: {time_range}\n"
                     f"👤 Имя: {user.first_name or 'Пользователь'}\n\n"
                     "Вы можете:\n"
                     "• Посмотреть свои записи кнопкой '👤 МОИ ЗАПИСИ'\n"
                     "• Записаться ещё раз",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                text="❌ **Этот слот уже занят!**\n\n"
                     "Пожалуйста, выберите другое время.",
                parse_mode='Markdown'
            )
    
    elif data == "refresh_slots":
        # Обновление списка слотов
        slots = get_available_slots()
        
        keyboard = []
        row = []
        
        for i, slot in enumerate(slots):
            slot_id, time_range, booked_count, max_people = slot
            
            if booked_count == 0:
                status = "🟢"
            elif booked_count < max_people:
                status = "🟡"
            else:
                status = "🔴"
            
            button_text = f"{time_range} {status}"
            callback_data = f"book_{slot_id}"
            
            row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
            
            if len(row) == 2 or i == len(slots) - 1:
                keyboard.append(row)
                row = []
        
        keyboard.append([InlineKeyboardButton("🔄 Обновить слоты", callback_data="refresh_slots")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=f"🔄 **Слоты обновлены**\n\n"
                 f"🕐 Время: {datetime.now().strftime('%H:%M')}\n\n"
                 "👇 Выберите удобное время:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def handle_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать записи пользователя"""
    user = update.effective_user
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Получаем user_id
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (user.id,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("📭 У вас пока нет записей.")
        conn.close()
        return
    
    user_id = result[0]
    
    # Получаем бронирования пользователя
    c.execute('''SELECT s.time_range, b.created_at
                 FROM bookings b
                 JOIN slots s ON b.slot_id = s.slot_id
                 WHERE b.user_id = ?
                 ORDER BY s.time_range''', (user_id,))
    
    bookings = c.fetchall()
    conn.close()
    
    if not bookings:
        await update.message.reply_text("📭 У вас пока нет записей.")
        return
    
    response = "📋 **Ваши записи на перерывы:**\n\n"
    for i, (time_range, created_at) in enumerate(bookings, 1):
        response += f"{i}. 🕐 {time_range}\n"
    
    response += f"\n📊 Всего записей: {len(bookings)}"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все бронирования"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''SELECT s.time_range, 
                        COUNT(b.booking_id) as booked,
                        s.max_people,
                        GROUP_CONCAT(u.full_name, ', ') as users
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 LEFT JOIN users u ON b.user_id = u.user_id
                 GROUP BY s.slot_id
                 ORDER BY s.time_range
                 LIMIT 10''')
    
    slots = c.fetchall()
    conn.close()
    
    if not slots:
        await update.message.reply_text("🏢 На сегодня нет бронирований.")
        return
    
    response = "🏢 **Все бронирования на сегодня:**\n\n"
    
    for time_range, booked, max_people, users in slots:
        if booked == 0:
            status = "🟢 свободно"
        elif booked < max_people:
            status = f"🟡 {booked}/{max_people}"
        else:
            status = f"🔴 {booked}/{max_people}"
        
        response += f"• {time_range}: {status}\n"
        if users:
            response += f"  👥 {users}\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Статистика
    c.execute('''SELECT COUNT(*) FROM users''')
    total_users = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM slots''')
    total_slots = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM bookings''')
    total_bookings = c.fetchone()[0]
    
    conn.close()
    
    response = (
        "📊 **Статистика системы:**\n\n"
        f"👥 **Зарегистрировано пользователей:** {total_users}\n"
        f"📅 **Всего временных слотов:** {total_slots}\n"
        f"✅ **Всего бронирований:** {total_bookings}\n"
        f"🎯 **Свободных слотов:** {total_slots - total_bookings}"
    )
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    
    if text == "📅 ЗАПИСАТЬСЯ":
        await handle_book(update, context)
    elif text == "👤 МОИ ЗАПИСИ":
        await handle_my_bookings(update, context)
    elif text == "🏢 ВСЕ БРОНИРОВАНИЯ":
        await handle_all_bookings(update, context)
    elif text == "📊 СТАТИСТИКА":
        await handle_statistics(update, context)
    else:
        await update.message.reply_text(
            "Используйте кнопки меню ниже 👇\n"
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
    
    try:
        # Создаем приложение
        application = Application.builder().token(TOKEN).build()
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Логирование запуска
        logger.info("=" * 50)
        logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
        logger.info("=" * 50)
        logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
        logger.info("=" * 50)
        logger.info("🚀 Бот запускается...")
        
        # Запускаем бота
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    main()
