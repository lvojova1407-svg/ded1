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
                  max_people INTEGER DEFAULT 3,
                  status TEXT DEFAULT 'free')''')
    
    # Таблица бронирований
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  slot_id INTEGER,
                  booking_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(user_id),
                  FOREIGN KEY (slot_id) REFERENCES slots(slot_id))''')
    
    # Создаем начальные слоты если их нет
    for hour in range(8, 20):  # С 8:00 до 20:00
        for minute in [0, 15, 30, 45]:
            start_time = f"{hour:02d}:{minute:02d}"
            end_minute = minute + 15
            end_hour = hour
            if end_minute >= 60:
                end_minute -= 60
                end_hour += 1
            end_time = f"{end_hour:02d}:{end_minute:02d}"
            
            c.execute('''INSERT OR IGNORE INTO slots (start_time, end_time) 
                         VALUES (?, ?)''', (start_time, end_time))
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_user_id(telegram_id, username, full_name):
    """Получает или создает пользователя"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''INSERT OR IGNORE INTO users (telegram_id, username, full_name) 
                 VALUES (?, ?, ?)''', (telegram_id, username, full_name))
    
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (telegram_id,))
    user_id = c.fetchone()[0]
    
    conn.commit()
    conn.close()
    return user_id

def get_available_slots():
    """Получает доступные слоты на ближайшие 2 часа"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    current_time = datetime.now().strftime("%H:%M")
    
    c.execute('''SELECT s.slot_id, s.start_time, s.end_time, 
                        COUNT(b.booking_id) as booked_count,
                        s.max_people
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 WHERE s.start_time >= ?
                 GROUP BY s.slot_id
                 ORDER BY s.start_time
                 LIMIT 8''', (current_time,))
    
    slots = c.fetchall()
    conn.close()
    return slots

# ==================== КОМАНДЫ БОТА ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Регистрируем пользователя
    user_id = get_user_id(user.id, user.username, user.full_name)
    
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
            "❌ **На ближайшие 2 часа нет доступных слотов**\n\n"
            "Пожалуйста, попробуйте позже.",
            parse_mode='Markdown'
        )
        return
    
    # Создаем кнопки для слотов
    keyboard = []
    row = []
    
    for i, slot in enumerate(slots):
        slot_id, start_time, end_time, booked_count, max_people = slot
        free_slots = max_people - booked_count
        
        # Определяем статус
        if free_slots >= max_people:
            status = "🟢"
        elif free_slots > 0:
            status = "🟡"
        else:
            status = "🔴"
        
        button_text = f"{start_time}-{end_time} {status}"
        callback_data = f"book_{slot_id}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        
        # Каждые 2 кнопки в ряд
        if (i + 1) % 2 == 0 or i == len(slots) - 1:
            keyboard.append(row)
            row = []
    
    # Добавляем кнопку обновления
    keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_slots")])
    
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
    
    user = query.from_user
    data = query.data
    
    if data.startswith("book_"):
        # Бронирование слота
        slot_id = int(data.split("_")[1])
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # Проверяем доступность
        c.execute('''SELECT s.max_people, COUNT(b.booking_id) as booked_count
                     FROM slots s
                     LEFT JOIN bookings b ON s.slot_id = b.slot_id
                     WHERE s.slot_id = ?
                     GROUP BY s.slot_id''', (slot_id,))
        
        result = c.fetchone()
        max_people, booked_count = result if result else (3, 0)
        
        # Получаем время слота
        c.execute('''SELECT start_time, end_time FROM slots WHERE slot_id = ?''', (slot_id,))
        start_time, end_time = c.fetchone()
        
        if booked_count >= max_people:
            await query.edit_message_text(
                text=f"❌ **Слот {start_time}-{end_time} уже занят!**\n\n"
                     "Этот слот уже полностью забронирован.\n"
                     "Выберите другой временной интервал.",
                parse_mode='Markdown'
            )
        else:
            # Регистрируем пользователя
            user_id = get_user_id(user.id, user.username, user.full_name)
            
            # Создаем бронирование
            c.execute('''INSERT INTO bookings (user_id, slot_id) 
                         VALUES (?, ?)''', (user_id, slot_id))
            
            conn.commit()
            conn.close()
            
            await query.edit_message_text(
                text=f"✅ **Вы успешно записались!**\n\n"
                     f"🎯 **Время:** {start_time}-{end_time}\n"
                     f"👤 **Имя:** {user.full_name or user.username or 'Пользователь'}\n"
                     f"📊 **Место:** {booked_count + 1}/{max_people}\n\n"
                     "🔄 Чтобы изменить запись, нажмите /start",
                parse_mode='Markdown'
            )
    
    elif data == "refresh_slots":
        # Обновление слотов
        slots = get_available_slots()
        
        if not slots:
            await query.edit_message_text(
                text="❌ **На ближайшие 2 часа нет доступных слотов**",
                parse_mode='Markdown'
            )
            return
        
        keyboard = []
        row = []
        
        for i, slot in enumerate(slots):
            slot_id, start_time, end_time, booked_count, max_people = slot
            free_slots = max_people - booked_count
            
            if free_slots >= max_people:
                status = "🟢"
            elif free_slots > 0:
                status = "🟡"
            else:
                status = "🔴"
            
            button_text = f"{start_time}-{end_time} {status}"
            callback_data = f"book_{slot_id}"
            
            row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
            
            if (i + 1) % 2 == 0 or i == len(slots) - 1:
                keyboard.append(row)
                row = []
        
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_slots")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=f"⏰ **ОБНОВЛЕННЫЕ СЛОТЫ**\n\n"
                 f"🕐 **Время:** {datetime.now().strftime('%H:%M')}\n"
                 "👇 Выберите слот:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def handle_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать мои записи"""
    user = update.effective_user
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Получаем ID пользователя
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (user.id,))
    result = c.fetchone()
    
    if not result:
        await update.message.reply_text("📭 У вас нет активных записей.")
        conn.close()
        return
    
    user_id = result[0]
    
    # Получаем бронирования
    c.execute('''SELECT b.booking_id, s.start_time, s.end_time, b.booking_time
                 FROM bookings b
                 JOIN slots s ON b.slot_id = s.slot_id
                 WHERE b.user_id = ?
                 ORDER BY s.start_time''', (user_id,))
    
    bookings = c.fetchall()
    conn.close()
    
    if not bookings:
        await update.message.reply_text("📭 У вас нет активных записей.")
        return
    
    response = "📋 **ВАШИ ЗАПИСИ**\n\n"
    for i, booking in enumerate(bookings, 1):
        booking_id, start_time, end_time, booking_time = booking
        response += f"{i}. 🕐 {start_time}-{end_time}\n"
    
    response += f"\n📊 **Всего записей:** {len(bookings)}"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все бронирования"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Получаем слоты на сегодня
    c.execute('''SELECT s.start_time, s.end_time, 
                        COUNT(b.booking_id) as booked_count,
                        s.max_people,
                        GROUP_CONCAT(u.full_name, ', ') as users
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 LEFT JOIN users u ON b.user_id = u.user_id
                 WHERE s.start_time >= ?
                 GROUP BY s.slot_id
                 ORDER BY s.start_time
                 LIMIT 10''', (datetime.now().strftime("%H:%M"),))
    
    slots = c.fetchall()
    conn.close()
    
    if not slots:
        await update.message.reply_text("🏢 **На сегодня нет бронирований**", parse_mode='Markdown')
        return
    
    response = "🏢 **ВСЕ БРОНИРОВАНИЯ**\n\n"
    
    total_booked = 0
    total_slots = 0
    
    for slot in slots:
        start_time, end_time, booked_count, max_people, users = slot
        total_slots += 1
        if booked_count > 0:
            total_booked += 1
        
        if booked_count == 0:
            status = "🟢 свободно"
        elif booked_count < max_people:
            status = f"🟡 {booked_count}/{max_people}"
        else:
            status = f"🔴 {booked_count}/{max_people}"
        
        response += f"• {start_time}-{end_time}: {status}\n"
        if users:
            response += f"  👥 {users}\n"
    
    response += f"\n📊 **Статистика:** {total_booked}/{total_slots} слотов занято"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Общая статистика
    c.execute('''SELECT COUNT(*) FROM users''')
    total_users = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM slots''')
    total_slots = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM bookings WHERE DATE(booking_time) = DATE('now')''')
    today_bookings = c.fetchone()[0]
    
    conn.close()
    
    response = (
        "📊 **СТАТИСТИКА НА СЕГОДНЯ**\n\n"
        f"👥 **Пользователей:** {total_users}\n"
        f"📅 **Всего слотов:** {total_slots}\n"
        f"✅ **Записей сегодня:** {today_bookings}\n"
        f"🎯 **Свободно:** {total_slots - today_bookings} слотов"
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
    
    # Создаем Application
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
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
