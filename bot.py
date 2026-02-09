import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    MessageHandler, 
    filters,
    ConversationHandler
)

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DB_NAME = 'breaks.db'

# Константы
SLOT_DURATION = 15  # минут
MAX_PEOPLE_PER_SLOT = 3
TOTAL_SLOTS_PER_DAY = 96  # 24ч * 4 слота

# NTP сервер для точного времени


# Состояния для ConversationHandler
WAITING_FOR_NAME = 1

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ФУНКЦИИ ВРЕМЕНИ ====================
def get_current_time():
    """Возвращает текущее время в формате HH:MM"""
    now = datetime.now()
    return now.strftime('%H:%M'), now

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
    
    # Таблица таймслотов
    c.execute('''CREATE TABLE IF NOT EXISTS time_slots
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  slot_time TEXT UNIQUE,
                  date DATE,
                  max_people INTEGER DEFAULT 3)''')
    
    # Таблица бронирований
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  slot_id INTEGER,
                  booked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  status TEXT DEFAULT 'active',
                  FOREIGN KEY (user_id) REFERENCES users(user_id),
                  FOREIGN KEY (slot_id) REFERENCES time_slots(id))''')
    
    # Создаем слоты на сегодня если их нет
    today = datetime.now().strftime('%Y-%m-%d')
    generate_slots_for_date(today, conn)
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def generate_slots_for_date(date, conn=None):
    """Генерирует 96 слотов на указанную дату"""
    close_conn = False
    if not conn:
        conn = sqlite3.connect(DB_NAME)
        close_conn = True
    
    c = conn.cursor()
    
    for hour in range(24):  # 0-23
        for minute in [0, 15, 30, 45]:
            start_time = f"{hour:02d}:{minute:02d}"
            end_hour = hour if minute < 45 else (hour + 1) % 24
            end_minute = (minute + 15) % 60
            end_time = f"{end_hour:02d}:{end_minute:02d}"
            
            slot_time = f"{start_time}-{end_time}"
            
            # Вставляем слот если его нет
            c.execute('''INSERT OR IGNORE INTO time_slots 
                         (slot_time, date, max_people) 
                         VALUES (?, ?, ?)''',
                      (slot_time, date, MAX_PEOPLE_PER_SLOT))
    
    if close_conn:
        conn.commit()
        conn.close()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_db_connection():
    """Возвращает соединение с БД"""
    return sqlite3.connect(DB_NAME)

def can_register_new_user():
    """Проверяет, можно ли зарегистрировать нового пользователя (лимит 50)"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    conn.close()
    return total_users < 50

def get_or_create_user(telegram_id, username, full_name=None):
    """Получает или создает пользователя"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Ищем пользователя
    c.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
    user = c.fetchone()
    
    # Если пользователь уже существует - возвращаем его
    if user:
        user_id = user[0]
        # Обновляем имя если оно изменилось
        if full_name and user[3] != full_name:
            c.execute('UPDATE users SET full_name = ? WHERE user_id = ?',
                      (full_name, user_id))
            conn.commit()
        conn.close()
        return user_id
    
    # Если это новый пользователь, проверяем лимит
    if full_name:
        c.execute('SELECT COUNT(*) FROM users')
        total_users = c.fetchone()[0]
        
        if total_users >= 50:
            conn.close()
            return None  # Лимит достигнут
        
        # Создаем нового пользователя
        c.execute('''INSERT INTO users (telegram_id, username, full_name)
                     VALUES (?, ?, ?)''',
                  (telegram_id, username, full_name))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return user_id
    
    conn.close()
    return None

def get_user_fio(telegram_id):
    """Получает ФИО пользователя"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT full_name FROM users WHERE telegram_id = ?', (telegram_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def get_next_2_hours_slots():
    """Возвращает слоты на ближайшие 2 часа - с использованием точного времени"""
    current_time_str, now = get_current_time()
    current_date = now.strftime('%Y-%m-%d')
    
    # Вычисляем время через 2 часа
    two_hours_later = now + timedelta(hours=2)
    end_time = two_hours_later.strftime('%H:%M')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Получаем слоты на ближайшие 2 часа
    query = '''
    SELECT 
        ts.id,
        ts.slot_time,
        ts.max_people,
        COUNT(b.id) as booked_count,
        GROUP_CONCAT(u.full_name, ', ') as people_names
    FROM time_slots ts
    LEFT JOIN bookings b ON ts.id = b.slot_id AND b.status = 'active'
    LEFT JOIN users u ON b.user_id = u.user_id
    WHERE ts.date = ?
      AND SUBSTR(ts.slot_time, 1, 5) >= ?
    GROUP BY ts.id, ts.slot_time, ts.max_people
    ORDER BY ts.slot_time
    LIMIT 8
    '''
    
    c.execute(query, (current_date, current_time_str))
    slots = c.fetchall()
    conn.close()
    
    return slots, current_time_str, end_time

def get_all_today_bookings():
    """Возвращает все бронирования на сегодня"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    query = '''
    SELECT 
        ts.slot_time,
        ts.max_people,
        COUNT(b.id) as booked_count,
        GROUP_CONCAT(u.full_name, ', ') as people_names
    FROM time_slots ts
    LEFT JOIN bookings b ON ts.id = b.slot_id AND b.status = 'active'
    LEFT JOIN users u ON b.user_id = u.user_id
    WHERE ts.date = ?
    GROUP BY ts.id, ts.slot_time, ts.max_people
    ORDER BY ts.slot_time
    '''
    
    c.execute(query, (today,))
    slots = c.fetchall()
    conn.close()
    
    return slots

def book_slot(user_id, slot_id):
    """Бронирует слот для пользователя"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Проверяем, не занят ли уже слот
    c.execute('SELECT COUNT(*) FROM bookings WHERE slot_id = ? AND status = "active"', (slot_id,))
    booked_count = c.fetchone()[0]
    
    c.execute('SELECT max_people FROM time_slots WHERE id = ?', (slot_id,))
    max_people = c.fetchone()[0]
    
    if booked_count >= max_people:
        conn.close()
        return False, "Слот полностью занят"
    
    # Проверяем, не забронировал ли уже пользователь этот слот
    c.execute('SELECT * FROM bookings WHERE user_id = ? AND slot_id = ? AND status = "active"', 
              (user_id, slot_id))
    if c.fetchone():
        conn.close()
        return False, "Вы уже записаны на этот слот"
    
    # Создаем бронирование
    c.execute('INSERT INTO bookings (user_id, slot_id) VALUES (?, ?)', (user_id, slot_id))
    conn.commit()
    
    # Получаем информацию о слоте
    c.execute('SELECT slot_time FROM time_slots WHERE id = ?', (slot_id,))
    slot_time = c.fetchone()[0]
    
    # Получаем других участников
    c.execute('''SELECT u.full_name FROM bookings b
                 JOIN users u ON b.user_id = u.user_id
                 WHERE b.slot_id = ? AND b.status = "active" AND b.user_id != ?''',
              (slot_id, user_id))
    other_users = [row[0] for row in c.fetchall()]
    
    conn.close()
    return True, (slot_time, other_users)

def cancel_booking(user_id, slot_id):
    """Отменяет бронирование"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''UPDATE bookings SET status = "cancelled" 
                 WHERE user_id = ? AND slot_id = ? AND status = "active"''',
              (user_id, slot_id))
    success = c.rowcount > 0
    
    if success:
        c.execute('SELECT slot_time FROM time_slots WHERE id = ?', (slot_id,))
        slot_time = c.fetchone()[0]
        conn.commit()
        conn.close()
        return True, slot_time
    
    conn.close()
    return False, None

def get_user_bookings(telegram_id):
    """Получает все активные бронирования пользователя"""
    user_id = get_or_create_user(telegram_id, None)
    if not user_id:
        return []
    
    conn = get_db_connection()
    c = conn.cursor()
    
    query = '''
    SELECT 
        b.id as booking_id,
        ts.slot_time,
        ts.max_people,
        (SELECT COUNT(*) FROM bookings b2 
         WHERE b2.slot_id = ts.id AND b2.status = "active") as booked_count,
        (SELECT GROUP_CONCAT(u2.full_name, ', ') FROM bookings b2
         JOIN users u2 ON b2.user_id = u2.user_id
         WHERE b2.slot_id = ts.id AND b2.status = "active" AND b2.user_id != ?) as other_users
    FROM bookings b
    JOIN time_slots ts ON b.slot_id = ts.id
    WHERE b.user_id = ? AND b.status = "active"
    ORDER BY ts.slot_time
    '''
    
    c.execute(query, (user_id, user_id))
    bookings = c.fetchall()
    conn.close()
    
    return bookings

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard():
    """Основная клавиатура"""
    keyboard = [
        [KeyboardButton("📅 ЗАПИСАТЬСЯ"), KeyboardButton("👤 МОИ ЗАПИСИ")],
        [KeyboardButton("🏢 ВСЕ БРОНИРОВАНИЯ"), KeyboardButton("📊 СТАТИСТИКА")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_slots_keyboard(slots):
    """Клавиатура с доступными слотами"""
    keyboard = []
    row = []
    
    for slot in slots:
        slot_id, slot_time, max_people, booked_count, people_names = slot
        free_slots = max_people - booked_count
        
        if booked_count >= max_people:
            icon = "🔴"
            text = f"{slot_time} {icon}"
            callback_data = f"full_{slot_id}"
        elif free_slots == 1:
            icon = "🟡"
            text = f"{slot_time} {icon}"
            callback_data = f"book_{slot_id}"
        else:
            icon = "🟢"
            text = f"{slot_time} {icon}"
            callback_data = f"book_{slot_id}"
        
        row.append(InlineKeyboardButton(text, callback_data=callback_data))
        
        if len(row) == 2:  # 2 кнопки в ряд
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # Кнопки навигации
    keyboard.append([
        InlineKeyboardButton("🔄 ОБНОВИТЬ", callback_data="refresh"),
        InlineKeyboardButton("👀 ВСЕ СЛОТЫ", callback_data="all_slots")
    ])
    
    return InlineKeyboardMarkup(keyboard)

# ==================== КОМАНДЫ БОТА ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    
    # Проверяем, зарегистрирован ли пользователь
    user_fio = get_user_fio(user.id)
    
    if user_fio:
        # Пользователь уже зарегистрирован
        await update.message.reply_text(
            f"👋 С возвращением, **{user_fio}**!\n\n"
            "🤖 Ваше ФИО сохранено в системе.\n"
            "При записи на перерыв оно будет отображаться в таймслотах.\n\n"
            "👇 Выберите действие:",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    else:
        # Проверяем, можно ли зарегистрировать нового пользователя
        if not can_register_new_user():
            await update.message.reply_text(
                "❌ **Достигнут лимит пользователей!**\n\n"
                "В системе уже зарегистрировано максимальное количество пользователей (50).\n"
                "Новая регистрация временно недоступна.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        
        # Просим зарегистрироваться
        await update.message.reply_text(
            "🤖 Привет! Я бот для записи на перерывы.\n\n"
            "📝 Для отображения вашего имени в списках\n"
            "введите ваше ФИО:\n\n"
            "**Формат:** Фамилия Имя Отчество\n"
            "**Пример:** Иванов Иван Иванович\n\n"
            "👇 Введите ниже:",
            parse_mode='Markdown'
        )
        return WAITING_FOR_NAME

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрация ФИО пользователя"""
    user = update.effective_user
    full_name = update.message.text.strip()
    
    # Простая валидация
    if len(full_name) < 3:
        await update.message.reply_text(
            "❌ Слишком короткое имя. Введите ФИО полностью.\n"
            "Пример: **Иванов Иван Иванович**",
            parse_mode='Markdown'
        )
        return WAITING_FOR_NAME
    
    # Сохраняем пользователя
    user_id = get_or_create_user(user.id, user.username, full_name)
    
    if user_id:
        await update.message.reply_text(
            f"✅ Готово!\n\n"
            f"👤 Ваше имя для отображения:\n"
            f"**{full_name}**\n\n"
            f"Теперь при записи на перерыв ваше имя будет видно в списке.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Ошибка регистрации. Попробуйте еще раз.",
            reply_markup=get_main_keyboard()
        )
    
    return ConversationHandler.END

async def show_book_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню записи с ТОЧНЫМ временем"""
    user = update.effective_user
    
    # Проверяем регистрацию
    if not get_user_fio(user.id):
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь!\n"
            "Используйте команду /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем слоты и точное время
    slots, current_time, two_hours_later = get_next_2_hours_slots()
    
    if not slots:
        await update.message.reply_text(
            f"⏰ На ближайшие 2 часа ({current_time} → {two_hours_later}) нет доступных слотов.\n"
            "Попробуйте позже или посмотрите все слоты.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Формируем сообщение с ТОЧНЫМ временем
    message = (
        f"⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
        f"🕐 **Точное время:** {current_time}\n"
        f"📅 **Показываем слоты:** {current_time} → {two_hours_later} (2 часа)\n\n"
        f"**Легенда:**\n"
        f"🟢 - свободно\n"
        f"🟡 - 1 место свободно\n"
        f"🔴 - занят\n\n"
        f"👇 Нажмите на слот для записи:"
    )
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_slots_keyboard(slots)
    )

async def show_all_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все бронирования на сегодня"""
    slots = get_all_today_bookings()
    
    if not slots:
        await update.message.reply_text(
            "📭 На сегодня нет бронирований.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем точное время для заголовка
    current_time, _ = get_current_time()
    
    message = f"🏢 **ВСЕ БРОНИРОВАНИЯ: СЕГОДНЯ**\n"
    message += f"🕐 **Текущее время:** {current_time}\n\n"
    
    for slot_time, max_people, booked_count, people_names in slots:
        if booked_count == 0:
            icon = "🟢"
            info = "свободно"
        elif booked_count == max_people:
            icon = "🔴"
            # Берем только фамилии и инициалы для компактности
            names = people_names.split(', ')
            short_names = []
            for name in names:
                parts = name.split()
                if len(parts) >= 2:
                    short_names.append(f"{parts[0]} {parts[1][0]}.")
                else:
                    short_names.append(name)
            info = ', '.join(short_names)
        else:
            icon = "🟡"
            names = people_names.split(', ') if people_names else []
            short_names = []
            for name in names:
                parts = name.split()
                if len(parts) >= 2:
                    short_names.append(f"{parts[0]} {parts[1][0]}.")
                else:
                    short_names.append(name)
            info = ', '.join(short_names) if short_names else "свободно"
        
        message += f"{icon} **{slot_time}** - {info}\n"
    
    message += f"\n📊 **ИТОГО:** {len([s for s in slots if s[2] > 0])} слотов занято"
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

async def show_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает мои бронирования"""
    user = update.effective_user
    
    # Проверяем регистрацию
    user_fio = get_user_fio(user.id)
    if not user_fio:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь!\n"
            "Используйте команду /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    bookings = get_user_bookings(user.id)
    
    if not bookings:
        await update.message.reply_text(
            "📭 У вас нет активных записей.\n\n"
            "👇 Хотите записаться?",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем точное время
    current_time, _ = get_current_time()
    
    message = f"📋 **ВАШИ АКТИВНЫЕ ЗАПИСИ**\n"
    message += f"🕐 **Текущее время:** {current_time}\n\n"
    
    for i, (booking_id, slot_time, max_people, booked_count, other_users) in enumerate(bookings, 1):
        if booked_count >= max_people:
            icon = "🔴"
        elif booked_count == max_people - 1:
            icon = "🟡"
        else:
            icon = "🟢"
        
        message += f"{i}. {icon} **{slot_time}**\n"
        
        if other_users:
            message += f"   👥 С вами: {other_users}\n"
        else:
            message += f"   👤 Пока вы один\n"
        
        # Добавляем кнопку отмены
        context.user_data[f"cancel_{i}"] = booking_id
    
    message += f"\n📊 **Всего:** {len(bookings)} записей"
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику - УПРОЩЕННЫЙ ВАРИАНТ"""
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Общая статистика
    c.execute('SELECT COUNT(DISTINCT user_id) FROM bookings WHERE status = "active"')
    active_users = c.fetchone()[0] or 0
    
    c.execute('SELECT COUNT(*) FROM time_slots WHERE date = ?', (today,))
    total_slots = c.fetchone()[0] or TOTAL_SLOTS_PER_DAY
    
    c.execute('''SELECT COUNT(DISTINCT ts.id) FROM bookings b
                 JOIN time_slots ts ON b.slot_id = ts.id
                 WHERE b.status = "active" AND ts.date = ?''', (today,))
    booked_slots = c.fetchone()[0] or 0
    
    conn.close()
    
    # Получаем точное время
    current_time, _ = get_current_time()
    
    # Упрощенная статистика
    message = (
        f"📊 **СТАТИСТИКА НА СЕГОДНЯ**\n"
        f"🕐 **Текущее время:** {current_time}\n\n"
        f"👥 **Участников:** {active_users} человек\n"
        f"📅 **Всего слотов:** {total_slots}\n"
        f"✅ **Занято слотов:** {booked_slots}\n"
        f"🎯 **Свободно:** {total_slots - booked_slots} слотов"
    )
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок с ТОЧНЫМ временем"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    # Проверяем регистрацию
    user_fio = get_user_fio(user.id)
    if not user_fio:
        await query.edit_message_text(
            "❌ Сначала зарегистрируйтесь!\n"
            "Используйте команду /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    user_id = get_or_create_user(user.id, user.username, user_fio)
    
    if data.startswith("book_"):
        # Бронирование слота
        slot_id = int(data.split("_")[1])
        
        success, result = book_slot(user_id, slot_id)
        
        if success:
            slot_time, other_users = result
            
            # Получаем точное время для сообщения
            current_time, _ = get_current_time()
            
            if other_users:
                users_text = ", ".join(other_users)
                message = (
                    f"✅ **ВЫ ЗАПИСАЛИСЬ!**\n"
                    f"🕐 **Время записи:** {current_time}\n\n"
                    f"🎯 **Слот:** {slot_time}\n"
                    f"👤 **Ваше имя:** {user_fio}\n"
                    f"👥 **Вместе с вами:** {users_text}\n\n"
                    f"📋 Ваши записи: /my"
                )
            else:
                message = (
                    f"✅ **ВЫ ЗАПИСАЛИСЬ!**\n"
                    f"🕐 **Время записи:** {current_time}\n\n"
                    f"🎯 **Слот:** {slot_time}\n"
                    f"👤 **Ваше имя:** {user_fio}\n"
                    f"👥 **Пока вы единственный в этом слоте**\n\n"
                    f"📋 Ваши записи: /my"
                )
            
            await query.edit_message_text(
                message,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ {result}\n\n"
                f"Попробуйте другой слот.",
                reply_markup=query.message.reply_markup
            )
    
    elif data.startswith("full_"):
        # Слот занят
        await query.answer("❌ Этот слот полностью занят!", show_alert=True)
    
    elif data == "refresh":
        # Обновить список слотов с ТОЧНЫМ временем
        slots, current_time, two_hours_later = get_next_2_hours_slots()
        
        if slots:
            message = (
                f"⏰ **ВЫБОР ВРЕМЕНИ**\n\n"
                f"🕐 **Точное время:** {current_time}\n"
                f"📅 **Показываем слоты:** {current_time} → {two_hours_later} (2 часа)\n\n"
                f"👇 Нажмите на слот для записи:"
            )
            
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=get_slots_keyboard(slots)
            )
        else:
            await query.edit_message_text(
                f"⏰ На ближайшие 2 часа ({current_time} → {two_hours_later}) нет доступных слотов.",
                reply_markup=get_main_keyboard()
            )
    
    elif data == "all_slots":
        # Показать все бронирования
        await show_all_bookings_for_button(query)

async def show_all_bookings_for_button(query):
    """Показывает все бронирования для inline-кнопки"""
    slots = get_all_today_bookings()
    
    if not slots:
        await query.edit_message_text(
            "📭 На сегодня нет бронирований.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Получаем точное время
    current_time, _ = get_current_time()
    
    message = f"🏢 **ВСЕ БРОНИРОВАНИЯ: СЕГОДНЯ**\n"
    message += f"🕐 **Текущее время:** {current_time}\n\n"
    
    for slot_time, max_people, booked_count, people_names in slots:
        if booked_count == 0:
            icon = "🟢"
            info = "свободно"
        elif booked_count == max_people:
            icon = "🔴"
            names = people_names.split(', ') if people_names else []
            short_names = []
            for name in names[:3]:  # Показываем только первых 3
                parts = name.split()
                if len(parts) >= 2:
                    short_names.append(f"{parts[0]} {parts[1][0]}.")
            info = ', '.join(short_names)
            if booked_count > 3:
                info += f" (+{booked_count - 3})"
        else:
            icon = "🟡"
            names = people_names.split(', ') if people_names else []
            short_names = []
            for name in names:
                parts = name.split()
                if len(parts) >= 2:
                    short_names.append(f"{parts[0]} {parts[1][0]}.")
            info = ', '.join(short_names) if short_names else "свободно"
        
        message += f"{icon} **{slot_time}** - {info}\n"
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    
    if text == "📅 ЗАПИСАТЬСЯ":
        await show_book_menu(update, context)
    elif text == "👤 МОИ ЗАПИСИ":
        await show_my_bookings(update, context)
    elif text == "🏢 ВСЕ БРОНИРОВАНИЯ":
        await show_all_bookings(update, context)
    elif text == "📊 СТАТИСТИКА":
        await show_stats(update, context)
    else:
        await update.message.reply_text(
            "Используйте кнопки ниже 👇",
            reply_markup=get_main_keyboard()
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога"""
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

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
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # ConversationHandler для регистрации
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_FOR_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Регистрируем обработчики
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Логирование запуска
    logger.info("=" * 50)
    logger.info("🤖 БОТ ДЛЯ ЗАПИСИ НА ПЕРЕРЫВЫ")
    logger.info("=" * 50)
    logger.info(f"✅ Токен: {'Найден' if TOKEN else 'НЕ НАЙДЕН!'}")
    logger.info(f"⏰ Слоты: {SLOT_DURATION} минут, {MAX_PEOPLE_PER_SLOT} чел/слот")
    logger.info(f"📅 Слотов в день: {TOTAL_SLOTS_PER_DAY}")
    logger.info("=" * 50)
    logger.info("🚀 Бот запускается...")
    
    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()
