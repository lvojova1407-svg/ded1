import os
import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DB_NAME = 'breaks.db'
PORT = int(os.environ.get('PORT', 10000))  # Render автоматически установит PORT

# Московское время (UTC+3)
MOSCOW_OFFSET = timedelta(hours=3)

def get_moscow_time():
    """Возвращает текущее время по Москве"""
    utc_now = datetime.now(timezone.utc)
    moscow_time = utc_now + MOSCOW_OFFSET
    return moscow_time

def format_moscow_time():
    """Возвращает форматированное время по Москве"""
    return get_moscow_time().strftime('%H:%M')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HTTP СЕРВЕР ДЛЯ HEALTH CHECKS ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP запросов для health checks"""
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = f"""
            <html>
            <head><title>Break Booking Bot</title></head>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h1>🤖 Office Break Booking Bot</h1>
                <p><strong>Status:</strong> <span style="color: green;">✅ RUNNING</span></p>
                <p><strong>Moscow Time (UTC+3):</strong> {format_moscow_time()}</p>
                <p><strong>Telegram Bot:</strong> Active</p>
                <p><strong>Database:</strong> Initialized</p>
                <hr>
                <p><a href="/health">Health Check</a> | <a href="/ping">Ping</a> | <a href="/status">Status JSON</a></p>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
        elif self.path == '/health' or self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            import json
            status = {
                "status": "running",
                "bot": "active",
                "database": "initialized",
                "moscow_time": format_moscow_time(),
                "port": PORT,
                "service": "telegram-break-bot"
            }
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Отключаем стандартное логирование HTTP запросов"""
        pass

def run_http_server():
    """Запускает HTTP сервер для health checks"""
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")
    server.serve_forever()

def start_http_server():
    """Запускает HTTP сервер в фоновом потоке"""
    server_thread = threading.Thread(
        target=run_http_server,
        daemon=True,
        name="HTTP-Server"
    )
    server_thread.start()
    
    # Даем серверу время на запуск
    import time
    time.sleep(2)
    
    if server_thread.is_alive():
        logger.info(f"✅ HTTP сервер успешно запущен на порту {PORT}")
        logger.info(f"✅ Health check доступен по: http://0.0.0.0:{PORT}/health")
    else:
        logger.error("❌ HTTP сервер не запустился")
    
    return server_thread

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                (user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 telegram_id INTEGER UNIQUE,
                 username TEXT,
                 full_name TEXT,
                 registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS slots
                (slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 time_range TEXT UNIQUE,
                 max_people INTEGER DEFAULT 3)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                (booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER,
                 slot_id INTEGER,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (user_id) REFERENCES users(user_id),
                 FOREIGN KEY (slot_id) REFERENCES slots(slot_id))''')
    
    # Создаем слоты
    time_slots = []
    for hour in range(8, 20):
        for minute in [0, 15, 30, 45]:
            start_hour = hour
            start_minute = minute
            
            end_minute = minute + 15
            end_hour = hour
            if end_minute >= 60:
                end_minute -= 60
                end_hour += 1
            
            time_range = f"{start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d}"
            time_slots.append(time_range)
    
    for time_slot in time_slots:
        c.execute('''INSERT OR IGNORE INTO slots (time_range) VALUES (?)''', (time_slot,))
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def get_or_create_user(telegram_id, username, full_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''SELECT user_id FROM users WHERE telegram_id = ?''', (telegram_id,))
    result = c.fetchone()
    
    if result:
        user_id = result[0]
    else:
        c.execute('''INSERT INTO users (telegram_id, username, full_name) 
                    VALUES (?, ?, ?)''', (telegram_id, username, full_name))
        user_id = c.lastrowid
    
    conn.commit()
    conn.close()
    return user_id

def get_available_slots():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    current_time = get_moscow_time()
    current_hour = current_time.hour
    current_minute = current_time.minute
    current_time_str = f"{current_hour:02d}:{current_minute:02d}"
    
    c.execute('''SELECT s.slot_id, s.time_range, 
                        COUNT(b.booking_id) as booked_count,
                        s.max_people
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 WHERE s.time_range >= ?
                 GROUP BY s.slot_id
                 ORDER BY s.time_range
                 LIMIT 8''', (f"{current_time_str}-",))
    
    slots = c.fetchall()
    conn.close()
    return slots

def book_slot(user_id, slot_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute('''SELECT COUNT(*) FROM bookings WHERE slot_id = ?''', (slot_id,))
        booked_count = c.fetchone()[0]
        
        c.execute('''SELECT max_people FROM slots WHERE slot_id = ?''', (slot_id,))
        max_people = c.fetchone()[0]
        
        if booked_count >= max_people:
            return False
        
        c.execute('''INSERT INTO bookings (user_id, slot_id) VALUES (?, ?)''', 
                 (user_id, slot_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка бронирования: {e}")
        return False
    finally:
        conn.close()

def get_user_bookings(telegram_id):
    """Получает все бронирования пользователя"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''SELECT b.booking_id, s.time_range, s.slot_id
                 FROM bookings b
                 JOIN slots s ON b.slot_id = s.slot_id
                 JOIN users u ON b.user_id = u.user_id
                 WHERE u.telegram_id = ?
                 ORDER BY s.time_range''', (telegram_id,))
    
    bookings = c.fetchall()
    conn.close()
    return bookings

def cancel_booking(booking_id, telegram_id):
    """Отменяет бронирование пользователя"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Проверяем, что запись принадлежит пользователю
        c.execute('''SELECT u.telegram_id, s.time_range 
                     FROM bookings b
                     JOIN users u ON b.user_id = u.user_id
                     JOIN slots s ON b.slot_id = s.slot_id
                     WHERE b.booking_id = ?''', (booking_id,))
        
        result = c.fetchone()
        
        if not result:
            return False, "Запись не найдена"
        
        owner_telegram_id, time_range = result
        
        if owner_telegram_id != telegram_id:
            return False, "Вы можете отменять только свои записи"
        
        # Удаляем запись
        c.execute('''DELETE FROM bookings WHERE booking_id = ?''', (booking_id,))
        conn.commit()
        
        return True, f"Запись на {time_range} отменена"
    except Exception as e:
        logger.error(f"Ошибка отмены бронирования: {e}")
        return False, "Ошибка при отмене записи"
    finally:
        conn.close()

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    user_id = get_or_create_user(user.id, user.username, user.full_name)
    
    keyboard = [
        [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
        [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}!\n\nЯ бот для записи на перерывы в офисе.\nВыберите действие ниже:",
        reply_markup=reply_markup
    )

async def handle_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slots = get_available_slots()
    
    if not slots:
        await update.message.reply_text(
            "На ближайшие 2 часа нет доступных слотов.\nПопробуйте позже.",
            parse_mode='Markdown'
        )
        return
    
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
    
    moscow_time_now = format_moscow_time()
    
    await update.message.reply_text(
        f"*Выбор времени для перерыва*\n\n"
        f"*Текущее время (Москва):* {moscow_time_now}\n"
        f"*Доступные слоты на ближайшие 2 часа*\n\n"
        "*Статус слотов:*\n"
        "🟢 - свободно\n"
        "🟡 - мало мест\n"
        "🔴 - занят\n\n"
        "Выберите удобное время:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def handle_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    bookings = get_user_bookings(user.id)
    
    if not bookings:
        await update.message.reply_text(
            "📭 *У вас пока нет активных записей.*\n\n"
            "Нажмите '📅 ЗАПИСАТЬСЯ' чтобы выбрать время для перерыва.",
            parse_mode='Markdown'
        )
        return
    
    # Создаем клавиатуру с кнопками отмены
    keyboard = []
    
    for booking_id, time_range, slot_id in bookings:
        button_text = f"❌ Отменить {time_range}"
        callback_data = f"cancel_{booking_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Кнопка возврата в меню
    keyboard.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    response = "📋 *Ваши активные записи:*\n\n"
    for i, (booking_id, time_range, slot_id) in enumerate(bookings, 1):
        response += f"{i}. 🕐 {time_range}\n"
    
    response += f"\n📊 *Всего записей:* {len(bookings)}\n\n👇 *Нажмите на запись для отмены:*"
    
    await update.message.reply_text(response, parse_mode='Markdown', reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    if data.startswith("book_"):
        slot_id = int(data.split("_")[1])
        
        user_id = get_or_create_user(user.id, user.username, user.full_name)
        
        if book_slot(user_id, slot_id):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute('''SELECT time_range FROM slots WHERE slot_id = ?''', (slot_id,))
            time_range = c.fetchone()[0]
            conn.close()
            
            # Создаем клавиатуру с действиями после бронирования
            keyboard = [
                [InlineKeyboardButton("📋 Мои записи", callback_data="my_bookings")],
                [InlineKeyboardButton("📅 Записаться еще", callback_data="book_more")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=f"✅ *Вы успешно записались!*\n\n"
                     f"🎯 *Время:* {time_range}\n"
                     f"👤 *Имя:* {user.first_name or 'Пользователь'}\n\n"
                     "Вы можете посмотреть свои записи или записаться еще раз:",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                text="❌ *Этот слот уже занят!*\n\nПожалуйста, выберите другое время.",
                parse_mode='Markdown'
            )
    
    elif data == "refresh_slots":
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
        
        moscow_time_now = format_moscow_time()
        
        await query.edit_message_text(
            text=f"🔄 *Слоты обновлены*\n\n"
                 f"🕐 *Текущее время (Москва):* {moscow_time_now}\n\n"
                 "Выберите удобное время:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    elif data.startswith("cancel_"):
        # Отмена записи
        booking_id = int(data.split("_")[1])
        
        success, message = cancel_booking(booking_id, user.id)
        
        if success:
            # Показываем кнопки после отмены
            keyboard = [
                [InlineKeyboardButton("📋 Мои записи", callback_data="my_bookings")],
                [InlineKeyboardButton("📅 Записаться снова", callback_data="book_more")],
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=f"✅ *Запись отменена!*\n\n"
                     f"🗑️ {message}\n\n"
                     "Что вы хотите сделать дальше?",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                text=f"❌ *Ошибка отмены:*\n\n{message}",
                parse_mode='Markdown'
            )
    
    elif data == "my_bookings":
        # Показать записи пользователя
        bookings = get_user_bookings(user.id)
        
        if not bookings:
            keyboard = [
                [InlineKeyboardButton("📅 Записаться", callback_data="book_more")],
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text="📭 *У вас пока нет активных записей.*\n\n"
                     "Хотите записаться на перерыв?",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            keyboard = []
            
            for booking_id, time_range, slot_id in bookings:
                button_text = f"❌ Отменить {time_range}"
                callback_data = f"cancel_{booking_id}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_from_bookings")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            response = "📋 *Ваши активные записи:*\n\n"
            for i, (booking_id, time_range, slot_id) in enumerate(bookings, 1):
                response += f"{i}. 🕐 {time_range}\n"
            
            response += f"\n📊 *Всего записей:* {len(bookings)}\n\n👇 *Нажмите на запись для отмены:*"
            
            await query.edit_message_text(
                text=response,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    
    elif data == "book_more":
        # Вернуться к выбору слотов
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
        keyboard.append([InlineKeyboardButton("📋 Мои записи", callback_data="my_bookings")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        moscow_time_now = format_moscow_time()
        
        await query.edit_message_text(
            text=f"📅 *Выбор времени для перерыва*\n\n"
                 f"🕐 *Текущее время (Москва):* {moscow_time_now}\n\n"
                 "👇 Выберите удобное время:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    elif data == "back_from_bookings":
        # Вернуться к выбору слотов из списка записей
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
        
        moscow_time_now = format_moscow_time()
        
        await query.edit_message_text(
            text=f"📅 *Выбор времени для перерыва*\n\n"
                 f"🕐 *Текущее время (Москва):* {moscow_time_now}\n\n"
                 "👇 Выберите удобное время:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    elif data == "back_to_menu":
        # Возврат в главное меню
        keyboard = [
            [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
            [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await query.message.reply_text(
            "Главное меню:",
            reply_markup=reply_markup
        )
        await query.delete_message()

async def handle_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    current_time = get_moscow_time()
    current_time_str = current_time.strftime('%H:%M')
    
    c.execute('''SELECT s.time_range, 
                        COUNT(b.booking_id) as booked,
                        s.max_people,
                        GROUP_CONCAT(u.full_name, ', ') as users
                 FROM slots s
                 LEFT JOIN bookings b ON s.slot_id = b.slot_id
                 LEFT JOIN users u ON b.user_id = u.user_id
                 WHERE s.time_range >= ?
                 GROUP BY s.slot_id
                 ORDER BY s.time_range
                 LIMIT 10''', (f"{current_time_str}-",))
    
    slots = c.fetchall()
    conn.close()
    
    if not slots:
        await update.message.reply_text("🏢 На ближайшее время нет бронирований.")
        return
    
    response = "🏢 *Бронирования на ближайшее время:*\n\n"
    
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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''SELECT COUNT(*) FROM users''')
    total_users = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM slots''')
    total_slots = c.fetchone()[0]
    
    c.execute('''SELECT COUNT(*) FROM bookings''')
    total_bookings = c.fetchone()[0]
    
    # Активные бронирования на сегодня
    current_date = get_moscow_time().strftime('%Y-%m-%d')
    c.execute('''SELECT COUNT(*) FROM bookings 
                 WHERE DATE(created_at) = ?''', (current_date,))
    today_bookings = c.fetchone()[0]
    
    # Самый популярный слот
    c.execute('''SELECT s.time_range, COUNT(b.booking_id) as booking_count
                 FROM bookings b
                 JOIN slots s ON b.slot_id = s.slot_id
                 GROUP BY s.slot_id
                 ORDER BY booking_count DESC
                 LIMIT 1''')
    popular_slot = c.fetchone()
    
    conn.close()
    
    response = (
        "📊 *Статистика системы*\n\n"
        f"👥 *Участников в системе:* {total_users} человек\n"
        f"📅 *Всего временных слотов:* {total_slots}\n"
        f"✅ *Всего бронирований:* {total_bookings}\n"
        f"📈 *Бронирований сегодня:* {today_bookings}\n"
        f"🎯 *Свободных слотов:* {total_slots - total_bookings}\n"
    )
    
    if popular_slot and popular_slot[1] > 0:
        time_range, booking_count = popular_slot
        response += f"🔥 *Самый популярный слот:* {time_range} ({booking_count} записей)\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "Используйте кнопки меню ниже\n"
            "Или команду /start для главного меню"
        )

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
def main():
    """
    Главная функция запускает:
    1. Инициализацию базы данных
    2. HTTP-сервер в фоновом потоке для health checks
    3. Telegram бота на polling
    """
    
    # 1. Инициализируем базу данных
    init_db()
    
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен не найден!")
        logger.error("ℹ️ Добавьте TELEGRAM_BOT_TOKEN в переменные окружения")
        return
    
    # 2. Запускаем HTTP-сервер в фоновом потоке для health checks
    logger.info(f"🔧 Запускаю HTTP сервер на порту {PORT}...")
    http_thread = start_http_server()
    
    # Ждем, чтобы убедиться что HTTP сервер запустился
    import time
    time.sleep(3)
    
    if not http_thread.is_alive():
        logger.error(f"❌ HTTP сервер не запустился на порту {PORT}. Проверьте конфигурацию.")
    else:
        logger.info(f"✅ HTTP сервер активен, поток: {http_thread.name}")
    
    try:
        # 3. Создаем и запускаем Telegram бота
        application = Application.builder().token(TOKEN).build()
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Информация о запуске
        logger.info("=" * 60)
        logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
        logger.info("=" * 60)
        logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
        logger.info(f"🌐 Часовой пояс: Москва (UTC+3)")
        logger.info(f"⏰ Текущее время по Москве: {format_moscow_time()}")
        logger.info(f"🔌 HTTP порт: {PORT}")
        logger.info(f"📡 Health check: http://0.0.0.0:{PORT}/health")
        logger.info(f"🧵 HTTP сервер активен: {http_thread.is_alive()}")
        logger.info("=" * 60)
        logger.info("🚀 Бот запускается...")
        logger.info("=" * 60)
        
        # Запускаем polling бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    main()
