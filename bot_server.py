import os
import logging
import sqlite3
import asyncio
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DB_NAME = 'breaks.db'
RENDER_APP_NAME = os.environ.get('RENDER_APP_NAME', '')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', '')

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

# ==================== СИСТЕМА АВТО-ПИНГА ====================
class KeepAliveSystem:
    """Система автоматического поддержания активности сервера"""
    
    def __init__(self):
        self.ping_count = 0
        self.last_ping_time = None
        self.is_running = False
        self.ping_thread = None
        
    def start(self):
        """Запускает систему авто-пинга"""
        if self.is_running:
            return
            
        self.is_running = True
        self.ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
        self.ping_thread.start()
        logger.info("🚀 Система авто-пинга запущена")
        
    def stop(self):
        """Останавливает систему авто-пинга"""
        self.is_running = False
        if self.ping_thread:
            self.ping_thread.join(timeout=5)
        logger.info("🛑 Система авто-пинга остановлена")
        
    def _ping_loop(self):
        """Основной цикл пинга"""
        while self.is_running:
            try:
                self._perform_ping()
                self.ping_count += 1
                self.last_ping_time = datetime.now(timezone.utc)
                
                # Логируем каждые 10 пингов
                if self.ping_count % 10 == 0:
                    logger.info(f"🔁 Авто-пинг #{self.ping_count} выполнен")
                
                # Ждем 8 минут (меньше чем 15 минут сна Render)
                time_to_sleep = 480  # 8 минут в секундах
                
                # Если есть внешний URL, пингуем его тоже
                if RENDER_EXTERNAL_URL:
                    try:
                        response = requests.get(f"{RENDER_EXTERNAL_URL}/health", timeout=10)
                        if response.status_code == 200:
                            logger.debug("✅ Внешний пинг успешен")
                    except:
                        pass
                
                # Спим
                for _ in range(time_to_sleep):
                    if not self.is_running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"❌ Ошибка в системе авто-пинга: {e}")
                time.sleep(60)  # Ждем минуту при ошибке
    
    def _perform_ping(self):
        """Выполняет пинг сервера"""
        try:
            # Пингуем сами себя через localhost
            response = requests.get("http://localhost:8000/health", timeout=5)
            
            if response.status_code == 200:
                logger.debug("✅ Авто-пинг выполнен успешно")
                return True
            else:
                logger.warning(f"⚠️ Авто-пинг: статус {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError:
            # Сервер может быть в процессе запуска
            logger.debug("⏳ Сервер не отвечает, возможно в процессе запуска")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка при авто-пинге: {e}")
            return False
    
    def get_status(self):
        """Возвращает статус системы"""
        return {
            "is_running": self.is_running,
            "ping_count": self.ping_count,
            "last_ping_time": self.last_ping_time.isoformat() if self.last_ping_time else None
        }

# Глобальная система авто-пинга
keep_alive_system = KeepAliveSystem()

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

# ==================== FASTAPI СЕРВЕР ====================
app = FastAPI(title="Telegram Bot Server", version="1.0.0")

# Глобальные переменные для управления потоками
bot_thread = None
uvicorn_server = None
application = None

def run_fastapi():
    """Запускает FastAPI сервер в отдельном потоке"""
    import uvicorn
    global uvicorn_server
    
    # Получаем порт из переменных окружения (для Render)
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"🌐 Запуск FastAPI сервера на {host}:{port}")
    
    config = uvicorn.Config(
        app, 
        host=host, 
        port=port,
        timeout_keep_alive=30,
        access_log=True,
        # Отключаем сигналы для работы в потоке
        log_config=None
    )
    
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()

async def run_bot():
    """Запускает Telegram бота в основном потоке"""
    global application
    
    # Инициализация базы данных
    init_db()
    
    # Проверка токена
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен не найден!")
        logger.error("ℹ️ Добавьте TELEGRAM_BOT_TOKEN в переменные окружения")
        return
    
    try:
        # Создание приложения бота
        application = Application.builder().token(TOKEN).build()
        
        # Добавление обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Логирование информации о запуске
        logger.info("=" * 50)
        logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
        logger.info("=" * 50)
        logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
        logger.info(f"🌐 Часовой пояс: Москва (UTC+3)")
        logger.info(f"⏰ Текущее время по Москве: {format_moscow_time()}")
        logger.info("=" * 50)
        logger.info("🚀 Бот запускается...")
        
        # Запуск системы авто-пинга
        keep_alive_system.start()
        
        # Запуск бота в режиме polling (БЕЗ stop_signals)
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
        # Бот работает вечно
        await asyncio.Event().wait()
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка в боте: {e}")
        raise
    finally:
        if application:
            await application.stop()
            await application.shutdown()

# FastAPI endpoints
@app.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "message": "Telegram Bot Server is running",
        "bot_status": "running" if application else "stopped",
        "time_moscow": format_moscow_time(),
        "keep_alive": keep_alive_system.get_status(),
        "docs": "/docs",
        "health": "/health",
        "ping": "/ping"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint для мониторинга"""
    bot_status = "running" if application else "stopped"
    
    return JSONResponse(
        content={
            "status": "healthy",
            "bot_running": bot_status,
            "keep_alive": keep_alive_system.get_status(),
            "service": "telegram-bot-server",
            "time_moscow": format_moscow_time(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "render_app": RENDER_APP_NAME
        },
        status_code=200
    )

@app.get("/ping")
async def ping():
    """Эндпоинт для ручного пинга"""
    keep_alive_system._perform_ping()
    return {
        "message": "Ping executed",
        "time": datetime.now(timezone.utc).isoformat()
    }

@app.get("/bot-status")
async def bot_status():
    """Проверка статуса бота"""
    bot_alive = application is not None
    
    return {
        "status": "running" if bot_alive else "stopped", 
        "message": "Бот активен" if bot_alive else "Бот не запущен",
        "keep_alive": keep_alive_system.get_status()
    }

# Основная функция запуска
async def main():
    """Основная функция для запуска всего приложения"""
    
    # Запускаем FastAPI сервер в отдельном потоке
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    
    logger.info("⏳ Ожидаем запуск FastAPI сервера...")
    time.sleep(3)  # Даем время серверу запуститься
    
    # Запускаем Telegram бота в основном потоке
    await run_bot()

if __name__ == "__main__":
    # Запускаем основное приложение
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Приложение остановлено пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        # Пытаемся перезапуститься через 30 секунд
        logger.info("🔄 Перезапуск через 30 секунд...")
        time.sleep(30)
        asyncio.run(main())
