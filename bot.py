import logging
import asyncio
import sys
import os
import traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from datetime import datetime, date
import psycopg2
import psycopg2.extras
import uuid

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения!")
    sys.exit(1)

ADMIN_IDS = []
admin_ids_str = os.environ.get('ADMIN_IDS')
if admin_ids_str:
    try:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(',') if id.strip()]
        logger.info(f"✅ ADMIN_IDS загружены: {ADMIN_IDS}")
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга ADMIN_IDS: {e}")
        sys.exit(1)
else:
    logger.error("❌ ADMIN_IDS не найден в переменных окружения!")
    sys.exit(1)

try:
    GROUP_CHAT_ID = int(os.environ.get('GROUP_CHAT_ID', '0'))
    if GROUP_CHAT_ID == 0:
        logger.error("❌ GROUP_CHAT_ID не найден в переменных окружения!")
        sys.exit(1)
    logger.info(f"✅ GROUP_CHAT_ID: {GROUP_CHAT_ID}")
except Exception as e:
    logger.error(f"❌ Ошибка парсинга GROUP_CHAT_ID: {e}")
    sys.exit(1)

CHANNEL_ID = os.environ.get('CHANNEL_ID')
if not CHANNEL_ID:
    logger.error("❌ CHANNEL_ID не найден в переменных окружения!")
    sys.exit(1)
logger.info(f"✅ CHANNEL_ID: {CHANNEL_ID}")

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    logger.info(f"✅ DATABASE_URL найден")
else:
    logger.error("❌ DATABASE_URL не найден в переменных окружения!")
    sys.exit(1)

logger.info("=" * 50)
logger.info("КОНФИГУРАЦИЯ ЗАВЕРШЕНА УСПЕШНО")
logger.info("=" * 50)

# ==================== ФУНКЦИИ РАБОТЫ С БД ====================
def get_db_connection():
    """Создает подключение к PostgreSQL на Bothost"""
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL не настроен!")
        return None
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return None

def init_db():
    """Инициализация таблиц в PostgreSQL"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("❌ Не удалось подключиться к БД для инициализации")
            return
        
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id BIGINT PRIMARY KEY,
                      username TEXT,
                      first_name TEXT,
                      registration_date TEXT,
                      ads_sent INTEGER DEFAULT 0,
                      ads_published INTEGER DEFAULT 0,
                      rating INTEGER DEFAULT 0,
                      is_blocked INTEGER DEFAULT 0,
                      is_admin INTEGER DEFAULT 0)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS daily_stats
                     (user_id BIGINT,
                      date TEXT,
                      ads_count INTEGER DEFAULT 0,
                      PRIMARY KEY (user_id, date))''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS stats
                     (date TEXT PRIMARY KEY, 
                      published_count INTEGER DEFAULT 0)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS bot_settings
                     (key TEXT PRIMARY KEY,
                      value TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS tickets
                     (ticket_id TEXT PRIMARY KEY,
                      user_id BIGINT,
                      username TEXT,
                      first_name TEXT,
                      message TEXT,
                      status TEXT DEFAULT 'open',
                      created_at TEXT,
                      closed_at TEXT,
                      closed_by BIGINT,
                      reply_message_id INTEGER)''')
        
        c.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", 
                  ('bot_enabled', '1'))
        c.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", 
                  ('maintenance_message', '🔧 Бот временно недоступен. Ведутся технические работы.'))
        
        default_welcome = """✋ Здравствуйте, {name}!

Это бот канала Купи/Продай Rostov

🌴 Сюда ты можешь кидать свои объявления и мы их в течении некоторого времени выложим!

🍀 Пример объявлений:
Продам:
О/п Кевин
Цена - 14кк
Связь - @psyxopaths

💥 Обязательно указывайте свой username, а то с вами не смогут связаться."""
        
        c.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", 
                  ('welcome_message', default_welcome))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

# Инициализируем БД при старте
init_db()

# ==================== ФУНКЦИИ РАБОТЫ С БД ====================
def get_user_stats(user_id: int):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        reg_date = datetime.now().isoformat()
        
        c.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (user_id, username, first_name, registration_date, ads_sent, ads_published, rating, is_blocked, is_admin) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      (user_id, None, None, reg_date, 0, 0, 0, 0, 1 if user_id in ADMIN_IDS else 0))
            conn.commit()
        
        c.execute("SELECT username, first_name, registration_date, ads_sent, ads_published, rating, is_blocked, is_admin FROM users WHERE user_id = %s",
                  (user_id,))
        result = c.fetchone()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка get_user_stats: {e}")
        return None

def find_user_by_username_or_id(search_term):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        
        try:
            user_id = int(search_term)
            c.execute("SELECT user_id, username, first_name, registration_date, ads_sent, ads_published, rating, is_blocked, is_admin FROM users WHERE user_id = %s", (user_id,))
            result = c.fetchone()
            if result:
                conn.close()
                return result
        except:
            pass
        
        username = search_term.replace('@', '').lower()
        c.execute("SELECT user_id, username, first_name, registration_date, ads_sent, ads_published, rating, is_blocked, is_admin FROM users WHERE LOWER(username) = %s", (username,))
        result = c.fetchone()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка поиска пользователя: {e}")
        return None

def update_user_ads(user_id: int, username: str, first_name: str):
    try:
        today = date.today().isoformat()
        conn = get_db_connection()
        if not conn:
            return
        
        c = conn.cursor()
        
        c.execute("UPDATE users SET username = %s, first_name = %s WHERE user_id = %s",
                  (username, first_name, user_id))
        
        c.execute("INSERT INTO daily_stats (user_id, date, ads_count) VALUES (%s, %s, 1) "
                  "ON CONFLICT (user_id, date) DO UPDATE SET ads_count = daily_stats.ads_count + 1",
                  (user_id, today))
        
        c.execute("UPDATE users SET ads_sent = ads_sent + 1 WHERE user_id = %s", (user_id,))
        c.execute("UPDATE users SET rating = ads_sent + ads_published WHERE user_id = %s", (user_id,))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка update_user_ads: {e}")

def increment_published(user_id: int):
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        c = conn.cursor()
        c.execute("UPDATE users SET ads_published = ads_published + 1 WHERE user_id = %s", (user_id,))
        c.execute("UPDATE users SET rating = ads_sent + ads_published WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка increment_published: {e}")

def get_daily_stats():
    try:
        today = date.today().isoformat()
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("INSERT INTO stats (date, published_count) VALUES (%s, 0) ON CONFLICT (date) DO NOTHING", (today,))
        c.execute("SELECT published_count FROM stats WHERE date = %s", (today,))
        result = c.fetchone()[0]
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка get_daily_stats: {e}")
        return 0

def get_total_ads_sent():
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(ads_sent), 0) FROM users")
        result = c.fetchone()[0]
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка get_total_ads_sent: {e}")
        return 0

def get_total_ads_published():
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(ads_published), 0) FROM users")
        result = c.fetchone()[0]
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Ошибка get_total_ads_published: {e}")
        return 0

def block_user(user_id: int):
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        c = conn.cursor()
        c.execute("UPDATE users SET is_blocked = 1 WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка block_user: {e}")

def unblock_user(user_id: int):
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        c = conn.cursor()
        c.execute("UPDATE users SET is_blocked = 0 WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка unblock_user: {e}")

def get_total_users():
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Ошибка get_total_users: {e}")
        return 0

def get_active_users_today():
    try:
        today = date.today().isoformat()
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_id) FROM daily_stats WHERE date = %s", (today,))
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Ошибка get_active_users_today: {e}")
        return 0

def get_blocked_users_count():
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Ошибка get_blocked_users_count: {e}")
        return 0

def get_all_users(limit=100, offset=0):
    try:
        conn = get_db_connection()
        if not conn:
            return []
        
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, registration_date, ads_sent, ads_published, rating, is_blocked, is_admin FROM users ORDER BY rating DESC LIMIT %s OFFSET %s", (limit, offset))
        users = c.fetchall()
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Ошибка get_all_users: {e}")
        return []

def get_top_users(limit=10):
    try:
        conn = get_db_connection()
        if not conn:
            return []
        
        c = conn.cursor()
        c.execute("SELECT username, first_name, rating, ads_sent, ads_published FROM users WHERE is_blocked = 0 ORDER BY rating DESC LIMIT %s", (limit,))
        top = c.fetchall()
        conn.close()
        return top
    except Exception as e:
        logger.error(f"Ошибка get_top_users: {e}")
        return []

def get_bot_setting(key):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        c.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка get_bot_setting: {e}")
        return None

def set_bot_setting(key, value):
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        c = conn.cursor()
        c.execute("UPDATE bot_settings SET value = %s WHERE key = %s", (value, key))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка set_bot_setting: {e}")

def is_bot_enabled():
    return get_bot_setting('bot_enabled') == '1'

def get_welcome_message():
    return get_bot_setting('welcome_message')

def set_welcome_message(message):
    set_bot_setting('welcome_message', message)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def create_ticket(user_id, username, first_name, message):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        ticket_id = str(uuid.uuid4())[:8]
        created_at = datetime.now().isoformat()
        
        c.execute("INSERT INTO tickets (ticket_id, user_id, username, first_name, message, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (ticket_id, user_id, username, first_name, message, 'open', created_at))
        
        conn.commit()
        conn.close()
        return ticket_id
    except Exception as e:
        logger.error(f"Ошибка create_ticket: {e}")
        return None

def get_ticket(ticket_id):
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,))
        ticket = c.fetchone()
        conn.close()
        return ticket
    except Exception as e:
        logger.error(f"Ошибка get_ticket: {e}")
        return None

def close_ticket(ticket_id, closed_by):
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        c = conn.cursor()
        closed_at = datetime.now().isoformat()
        c.execute("UPDATE tickets SET status = 'closed', closed_at = %s, closed_by = %s WHERE ticket_id = %s",
                  (closed_at, closed_by, ticket_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка close_ticket: {e}")
        return False

def get_open_tickets_count():
    try:
        conn = get_db_connection()
        if not conn:
            return 0
        
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Ошибка get_open_tickets_count: {e}")
        return 0

def get_user_tickets(user_id):
    try:
        conn = get_db_connection()
        if not conn:
            return []
        
        c = conn.cursor()
        c.execute("SELECT ticket_id, message, status, created_at FROM tickets WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        tickets = c.fetchall()
        conn.close()
        return tickets
    except Exception as e:
        logger.error(f"Ошибка get_user_tickets: {e}")
        return []

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("📋 Отправить объявление"), KeyboardButton("👤 Мой профиль ⭐")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("❓ Помощь / Тикет")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_group_keyboard(user_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✅ Выложить в канал", callback_data=f"publish_{user_id}"),
            InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{user_id}")
        ],
        [
            InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block_{user_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ticket_keyboard(ticket_id: str, user_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ответить", callback_data=f"ticket_reply_{ticket_id}"),
            InlineKeyboardButton("✅ Закрыть", callback_data=f"ticket_close_{ticket_id}")
        ],
        [
            InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ticket_block_{user_id}_{ticket_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    open_tickets = get_open_tickets_count()
    tickets_button = f"🎫 Тикеты ({open_tickets})" if open_tickets > 0 else "🎫 Тикеты"
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Поиск пользователя", callback_data="admin_search")],
        [InlineKeyboardButton("🏆 Топ пользователей", callback_data="admin_top")],
        [InlineKeyboardButton(tickets_button, callback_data="admin_tickets")],
        [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton("🔙 Выход", callback_data="admin_exit")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_user_action_keyboard(user_id: int, is_blocked: bool):
    keyboard = []
    if is_blocked:
        keyboard.append([InlineKeyboardButton("✅ Разблокировать", callback_data=f"admin_unblock_{user_id}")])
    else:
        keyboard.append([InlineKeyboardButton("❌ Заблокировать", callback_data=f"admin_block_{user_id}")])
    keyboard.append([InlineKeyboardButton("📊 Статистика", callback_data=f"user_stats_{user_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_search")])
    return InlineKeyboardMarkup(keyboard)

def get_bot_settings_keyboard():
    enabled = is_bot_enabled()
    status_text = "🟢 Включен" if enabled else "🔴 Выключен"
    status_button = f"{status_text} (нажмите чтобы изменить)"
    
    keyboard = [
        [InlineKeyboardButton(status_button, callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("✏️ Изменить приветствие", callback_data="admin_edit_welcome")],
        [InlineKeyboardButton("✏️ Сообщение о тех.работах", callback_data="admin_edit_maintenance")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_users_navigation_keyboard(page, total_pages):
    keyboard = []
    nav_buttons = []
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"users_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"users_page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Назад в админку", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)

def get_broadcast_confirm_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✅ Отправить", callback_data="broadcast_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    logger.info(f"Пользователь {user.id} (@{user.username}) запустил бота")
    
    get_user_stats(user.id)
    
    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text(
            f"👋 Бот добавлен в группу!\n\n"
            f"ID этой группы: `{chat.id}`"
        )
    else:
        welcome_template = get_welcome_message()
        welcome_text = welcome_template.replace("{name}", user.first_name)
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_keyboard()
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    await update.message.reply_text(
        "🔑 **Админ-панель**\n\nВыберите действие:",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    await update.message.reply_text(
        f"📌 Информация о чате:\n"
        f"Chat ID: `{chat.id}`\n"
        f"Chat type: {chat.type}\n"
        f"Chat title: {chat.title if chat.title else 'Нет названия'}"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    cleared = False
    for mode in ['broadcast_mode', 'edit_maintenance', 'edit_welcome', 'awaiting_ad', 'ticket_mode', 'reply_to_ticket', 'search_mode']:
        if context.user_data.get(mode):
            context.user_data.pop(mode, None)
            cleared = True
    
    if cleared:
        await update.message.reply_text(
            "✅ Действие отменено",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Нет активных действий для отмены",
            reply_markup=get_main_keyboard()
        )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    if not is_admin(user.id):
        await query.edit_message_text("❌ У вас нет прав администратора.")
        return
    
    if data == "admin_stats":
        total_users = get_total_users()
        active_today = get_active_users_today()
        blocked_users = get_blocked_users_count()
        published_today = get_daily_stats()
        open_tickets = get_open_tickets_count()
        total_sent = get_total_ads_sent()
        total_published = get_total_ads_published()
        
        stats_text = (
            f"📊 **СТАТИСТИКА БОТА**\n\n"
            f"👥 **Пользователей:** {total_users}\n"
            f"📊 **Активных сегодня:** {active_today}\n"
            f"🚫 **Заблокировано:** {blocked_users}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📤 **Отправлено всего:** {total_sent}\n"
            f"📥 **Опубликовано всего:** {total_published}\n"
            f"✅ **За сегодня:** {published_today}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎫 **Открытых тикетов:** {open_tickets}\n"
            f"📅 **Дата:** {date.today().strftime('%d.%m.%Y')}"
        )
        
        await query.edit_message_text(
            stats_text,
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )
    
    elif data == "admin_users":
        await show_users_page(query, context, 0)
    
    elif data == "admin_search":
        context.user_data['search_mode'] = True
        await query.edit_message_text(
            "🔍 **ПОИСК ПОЛЬЗОВАТЕЛЯ**\n\n"
            "Введите ID пользователя или @юзернейм:\n\n"
            "📌 **Примеры:**\n"
            "• `123456789` — поиск по ID\n"
            "• `@username` — поиск по юзернейму\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Для отмены отправьте /cancel",
            parse_mode='Markdown'
        )
    
    elif data == "admin_top":
        await show_top_users(query)
    
    elif data == "admin_tickets":
        await show_tickets_page(query, context, 0)
    
    elif data == "admin_broadcast":
        context.user_data['broadcast_mode'] = True
        await query.edit_message_text(
            "📨 **РЕЖИМ РАССЫЛКИ**\n\n"
            "Отправьте сообщение для рассылки всем пользователям.\n\n"
            "⚠️ **Внимание!** Сообщение увидят ВСЕ пользователи бота.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Для отмены отправьте /cancel",
            parse_mode='Markdown'
        )
    
    elif data == "admin_settings":
        await show_bot_settings(query)
    
    elif data == "admin_toggle_bot":
        current = is_bot_enabled()
        new_value = '0' if current else '1'
        set_bot_setting('bot_enabled', new_value)
        status = "включен 🟢" if new_value == '1' else "выключен 🔴"
        await query.answer(f"Бот {status}", show_alert=True)
        await show_bot_settings(query)
    
    elif data == "admin_edit_welcome":
        context.user_data['edit_welcome'] = True
        current = get_welcome_message()
        await query.edit_message_text(
            f"✏️ **ТЕКУЩЕЕ ПРИВЕТСТВИЕ:**\n\n{current}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Отправьте новое приветствие\n"
            f"Используйте {{name}} для подстановки имени\n\n"
            f"Для отмены отправьте /cancel",
            parse_mode='Markdown'
        )
    
    elif data == "admin_edit_maintenance":
        context.user_data['edit_maintenance'] = True
        await query.edit_message_text(
            "✏️ **СООБЩЕНИЕ О ТЕХ.РАБОТАХ**\n\n"
            "Введите новое сообщение:\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Для отмены отправьте /cancel",
            parse_mode='Markdown'
        )
    
    elif data.startswith("admin_block_"):
        user_id = int(data.split("_")[2])
        block_user(user_id)
        await query.answer("Пользователь заблокирован", show_alert=True)
        await show_user_info(query, user_id)
    
    elif data.startswith("admin_unblock_"):
        user_id = int(data.split("_")[2])
        unblock_user(user_id)
        await query.answer("Пользователь разблокирован", show_alert=True)
        await show_user_info(query, user_id)
    
    elif data.startswith("user_stats_"):
        user_id = int(data.split("_")[2])
        await show_user_info(query, user_id)
    
    elif data == "admin_back":
        await query.edit_message_text(
            "🔑 **АДМИН-ПАНЕЛЬ**\n\nВыберите действие:",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )
    
    elif data == "admin_exit":
        await query.edit_message_text(
            "🔙 Вы вышли из админ-панели",
            reply_markup=None
        )
    
    elif data.startswith("users_page_"):
        page = int(data.split("_")[2])
        await show_users_page(query, context, page)
    
    elif data == "broadcast_confirm":
        if 'broadcast_message' in context.user_data:
            await start_broadcast(update, context)
        else:
            await query.edit_message_text("❌ Сообщение не найдено")
    
    elif data == "broadcast_cancel":
        context.user_data.pop('broadcast_mode', None)
        context.user_data.pop('broadcast_message', None)
        await query.edit_message_text(
            "❌ Рассылка отменена",
            reply_markup=get_admin_keyboard()
        )

async def show_bot_settings(query):
    enabled = is_bot_enabled()
    maintenance_msg = get_bot_setting('maintenance_message')
    welcome_msg = get_welcome_message()
    
    settings_text = (
        f"⚙️ **НАСТРОЙКИ БОТА**\n\n"
        f"🔹 **Статус:** {'🟢 Включен' if enabled else '🔴 Выключен'}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 **Приветствие:**\n`{welcome_msg[:100]}...`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📢 **Тех.работы:**\n`{maintenance_msg}`"
    )
    
    await query.edit_message_text(
        settings_text,
        parse_mode='Markdown',
        reply_markup=get_bot_settings_keyboard()
    )

async def show_user_info(query, user_id):
    user_data = find_user_by_username_or_id(str(user_id))
    
    if not user_data:
        await query.edit_message_text("❌ Пользователь не найден")
        return
    
    user_id, username, first_name, reg_date, ads_sent, ads_published, rating, is_blocked, is_admin_user = user_data
    
    today = date.today().isoformat()
    conn = get_db_connection()
    if conn:
        c = conn.cursor()
        c.execute("SELECT ads_count FROM daily_stats WHERE user_id = %s AND date = %s", (user_id, today))
        daily = c.fetchone()
        conn.close()
        daily_count = daily[0] if daily else 0
    else:
        daily_count = 0
    
    status = "❌ Заблокирован" if is_blocked else "✅ Активен"
    admin = "👑 Админ" if is_admin_user else "👤 Пользователь"
    username_str = f"@{username}" if username else "нет юзернейма"
    
    info_text = (
        f"👤 **{first_name or 'Без имени'}**\n\n"
        f"┣ 🆔 ID: `{user_id}`\n"
        f"┣ 👥 {username_str}\n"
        f"┣ 📅 {reg_date.split('T')[0]}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 **Отправлено:** {ads_sent}\n"
        f"📥 **Опубликовано:** {ads_published}\n"
        f"📈 **Сегодня:** {daily_count}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ **Рейтинг:** {rating}\n"
        f"🔒 **Статус:** {status}\n"
        f"👑 **Роль:** {admin}"
    )
    
    await query.edit_message_text(
        info_text,
        parse_mode='Markdown',
        reply_markup=get_user_action_keyboard(user_id, is_blocked)
    )

async def show_users_page(query, context, page):
    users_per_page = 5
    users = get_all_users(users_per_page, page * users_per_page)
    total_users = get_total_users()
    total_pages = (total_users + users_per_page - 1) // users_per_page
    
    text = f"👥 **СПИСОК ПОЛЬЗОВАТЕЛЕЙ**\nСтраница {page+1}/{total_pages}\n\n"
    
    for user in users:
        user_id, username, first_name, reg_date, ads_sent, ads_published, rating, is_blocked, is_admin_user = user
        status = "❌" if is_blocked else "✅"
        admin = "👑" if is_admin_user else ""
        name = first_name or "без имени"
        username_str = f"@{username}" if username else "нет"
        
        text += f"{status}{admin} **{name}**\n"
        text += f"┣ ID: `{user_id}` | {username_str}\n"
        text += f"┣ ⭐ {rating} | 📤{ads_sent} 📥{ads_published}\n"
        text += f"┗ 📅 {reg_date.split('T')[0]}\n\n"
    
    if not users:
        text += "Пользователей не найдено"
    
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_users_navigation_keyboard(page, total_pages)
    )

async def show_top_users(query):
    top_users = get_top_users(10)
    
    if not top_users:
        await query.edit_message_text(
            "🏆 **ТОП ПОЛЬЗОВАТЕЛЕЙ**\n\nПока нет данных",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )
        return
    
    text = "🏆 **ТОП ПОЛЬЗОВАТЕЛЕЙ**\n\n"
    
    for i, user in enumerate(top_users, 1):
        username, first_name, rating, sent, published = user
        name = first_name or "без имени"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} **{name}**\n"
        text += f"┣ ⭐ {rating} очков\n"
        text += f"┗ 📤{sent} | 📥{published}\n\n"
    
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

async def show_tickets_page(query, context, page):
    try:
        conn = get_db_connection()
        if not conn:
            await query.edit_message_text("❌ Ошибка подключения к БД")
            return
        
        c = conn.cursor()
        tickets_per_page = 5
        offset = page * tickets_per_page
        c.execute("SELECT ticket_id, user_id, username, first_name, message, created_at FROM tickets WHERE status = 'open' ORDER BY created_at DESC LIMIT %s OFFSET %s", 
                  (tickets_per_page, offset))
        tickets = c.fetchall()
        
        c.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
        total_tickets = c.fetchone()[0]
        conn.close()
        
        total_pages = (total_tickets + tickets_per_page - 1) // tickets_per_page
        
        if not tickets:
            await query.edit_message_text(
                "✅ Нет открытых тикетов",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
            return
        
        text = f"🎫 **ОТКРЫТЫЕ ТИКЕТЫ**\nСтраница {page+1}/{total_pages}\n\n"
        
        for ticket in tickets:
            ticket_id, user_id, username, first_name, message, created_at = ticket
            short_msg = message[:30] + "..." if len(message) > 30 else message
            created = datetime.fromisoformat(created_at).strftime('%d.%m %H:%M')
            username_str = f"@{username}" if username else "нет"
            
            text += f"**#{ticket_id}**\n"
            text += f"┣ от {username_str}\n"
            text += f"┣ {short_msg}\n"
            text += f"┗ {created} | ID: {user_id}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="admin_back")
            ]])
        )
    except Exception as e:
        logger.error(f"Ошибка show_tickets_page: {e}")
        await query.edit_message_text("❌ Ошибка загрузки тикетов")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if context.user_data.get('broadcast_mode'):
        await handle_broadcast_message(update, context)
        return
    
    if context.user_data.get('edit_maintenance'):
        await handle_edit_maintenance(update, context)
        return
    
    if context.user_data.get('edit_welcome'):
        await handle_edit_welcome(update, context)
        return
    
    if context.user_data.get('reply_to_ticket'):
        await handle_ticket_reply(update, context)
        return
    
    if context.user_data.get('search_mode'):
        await handle_user_search(update, context)
        return
    
    if update.message.text:
        logger.info(f"Сообщение от {user.id}: {update.message.text[:30]}")
    
    if not is_bot_enabled() and not is_admin(user.id):
        maintenance_msg = get_bot_setting('maintenance_message')
        await update.message.reply_text(f"🔧 {maintenance_msg}")
        return
    
    menu_buttons = ["📋 Отправить объявление", "👤 Мой профиль ⭐", "📊 Статистика", "❓ Помощь / Тикет"]
    
    if update.message.text and update.message.text in menu_buttons:
        await handle_menu_buttons(update, context)
    elif context.user_data.get("awaiting_ad"):
        await forward_to_moderation_group(update, context)
    elif context.user_data.get("ticket_mode"):
        await handle_ticket_creation(update, context)
    else:
        logger.info(f"Сообщение игнорируется")

async def handle_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    search_term = update.message.text.strip()
    
    if not is_admin(user.id):
        context.user_data.pop('search_mode', None)
        return
    
    user_data = find_user_by_username_or_id(search_term)
    
    if not user_data:
        await update.message.reply_text(
            "❌ **Пользователь не найден**\n\n"
            "Попробуйте другой ID или @юзернейм.",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )
        context.user_data.pop('search_mode', None)
        return
    
    user_id, username, first_name, reg_date, ads_sent, ads_published, rating, is_blocked, is_admin_user = user_data
    
    today = date.today().isoformat()
    conn = get_db_connection()
    if conn:
        c = conn.cursor()
        c.execute("SELECT ads_count FROM daily_stats WHERE user_id = %s AND date = %s", (user_id, today))
        daily = c.fetchone()
        conn.close()
        daily_count = daily[0] if daily else 0
    else:
        daily_count = 0
    
    status = "❌ Заблокирован" if is_blocked else "✅ Активен"
    admin = "👑 Админ" if is_admin_user else "👤 Пользователь"
    username_str = f"@{username}" if username else "нет юзернейма"
    
    info_text = (
        f"👤 **{first_name or 'Без имени'}**\n\n"
        f"┣ 🆔 ID: `{user_id}`\n"
        f"┣ 👥 {username_str}\n"
        f"┣ 📅 {reg_date.split('T')[0]}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 **Отправлено:** {ads_sent}\n"
        f"📥 **Опубликовано:** {ads_published}\n"
        f"📈 **Сегодня:** {daily_count}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⭐ **Рейтинг:** {rating}\n"
        f"🔒 **Статус:** {status}\n"
        f"👑 **Роль:** {admin}"
    )
    
    await update.message.reply_text(
        info_text,
        parse_mode='Markdown',
        reply_markup=get_user_action_keyboard(user_id, is_blocked)
    )
    
    context.user_data.pop('search_mode', None)

async def handle_edit_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not is_admin(user.id):
        context.user_data.pop('edit_welcome', None)
        return
    
    new_welcome = update.message.text
    set_welcome_message(new_welcome)
    context.user_data.pop('edit_welcome', None)
    
    await update.message.reply_text(
        "✅ **Приветствие обновлено!**",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

async def handle_ticket_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message_text = update.message.text
    
    if not message_text:
        await update.message.reply_text(
            "❌ **Ошибка**\n\nОтправьте текстовое сообщение.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        context.user_data.pop('ticket_mode', None)
        return
    
    ticket_id = create_ticket(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        message=message_text
    )
    
    if ticket_id:
        ticket_info = (
            f"🎫 **НОВЫЙ ТИКЕТ** #{ticket_id}\n\n"
            f"👤 @{user.username or 'нет'} (ID: {user.id})\n"
            f"📝 {message_text}\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=ticket_info,
            parse_mode='Markdown',
            reply_markup=get_ticket_keyboard(ticket_id, user.id)
        )
        
        await update.message.reply_text(
            f"✅ **Тикет #{ticket_id} создан!**\n\n"
            f"Администратор ответит вам в ближайшее время.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ **Ошибка**\n\nНе удалось создать тикет.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
    
    context.user_data.pop('ticket_mode', None)

async def handle_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reply_text = update.message.text
    ticket_id = context.user_data.get('reply_to_ticket')
    
    if not ticket_id:
        context.user_data.pop('reply_to_ticket', None)
        return
    
    ticket = get_ticket(ticket_id)
    if not ticket:
        await update.message.reply_text("❌ Тикет не найден")
        context.user_data.pop('reply_to_ticket', None)
        return
    
    user_id = ticket[1]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✏️ **Ответ на тикет #{ticket_id}**\n\n{reply_text}\n\n— Администратор",
            parse_mode='Markdown'
        )
        await update.message.reply_text(f"✅ **Ответ отправлен** пользователю")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await update.message.reply_text("❌ **Ошибка**\n\nНе удалось отправить ответ")
    
    context.user_data.pop('reply_to_ticket', None)

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not is_admin(user.id):
        context.user_data.pop('broadcast_mode', None)
        return
    
    context.user_data['broadcast_message'] = update.message
    
    await update.message.reply_text(
        "📨 **ПОДТВЕРЖДЕНИЕ РАССЫЛКИ**\n\n"
        "Вы уверены, что хотите отправить это сообщение ВСЕМ пользователям?",
        parse_mode='Markdown',
        reply_markup=get_broadcast_confirm_keyboard()
    )

async def handle_edit_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not is_admin(user.id):
        context.user_data.pop('edit_maintenance', None)
        return
    
    new_message = update.message.text
    set_bot_setting('maintenance_message', new_message)
    context.user_data.pop('edit_maintenance', None)
    
    await update.message.reply_text(
        "✅ **Сообщение обновлено!**",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if 'broadcast_message' not in context.user_data:
        await query.edit_message_text("❌ Сообщение не найдено")
        return
    
    broadcast_msg = context.user_data['broadcast_message']
    users = get_all_users(limit=1000)
    
    await query.edit_message_text(f"📨 Рассылка {len(users)} пользователям...")
    
    success = 0
    failed = 0
    
    for user in users:
        user_id = user[0]
        try:
            if broadcast_msg.text:
                await context.bot.send_message(chat_id=user_id, text=broadcast_msg.text)
            elif broadcast_msg.photo:
                await context.bot.send_photo(chat_id=user_id, photo=broadcast_msg.photo[-1].file_id, caption=broadcast_msg.caption)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    context.user_data.pop('broadcast_mode', None)
    context.user_data.pop('broadcast_message', None)
    
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"📊 **ОТЧЕТ О РАССЫЛКЕ**\n\n✅ Успешно: {success}\n❌ Ошибок: {failed}",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

async def forward_to_moderation_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    try:
        if not GROUP_CHAT_ID:
            await update.message.reply_text("❌ Ошибка конфигурации")
            return
        
        forwarded = await update.message.forward(chat_id=GROUP_CHAT_ID)
        
        info_text = (
            f"📋 **НОВОЕ ОБЪЯВЛЕНИЕ**\n\n"
            f"👤 @{user.username or 'нет'} (ID: {user.id})\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=info_text,
            parse_mode='Markdown',
            reply_markup=get_group_keyboard(user.id)
        )
        
        update_user_ads(user.id, user.username, user.first_name)
        
        await update.message.reply_text(
            "✅ **Объявление отправлено на модерацию!**",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка отправки")
    finally:
        context.user_data["awaiting_ad"] = False

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user

    if text == "📋 Отправить объявление":
        stats = get_user_stats(user.id)
        if stats and stats[6] == 1:
            await update.message.reply_text(
                "❌ **Вы заблокированы**\n\nОбратитесь в поддержку.",
                parse_mode='Markdown'
            )
            return
        
        await update.message.reply_text(
            "📝 **ОТПРАВКА ОБЪЯВЛЕНИЯ**\n\n"
            "Отправьте ваше объявление:\n"
            "• Текст\n"
            "• Фото\n"
            "• Видео\n"
            "• Стикеры (Premium сохраняются!)",
            parse_mode='Markdown'
        )
        context.user_data["awaiting_ad"] = True

    elif text == "👤 Мой профиль ⭐":
        stats = get_user_stats(user.id)
        if not stats:
            await update.message.reply_text("❌ Ошибка загрузки")
            return
        
        username, first_name, reg_date, ads_sent, ads_published, rating, is_blocked, is_admin_user = stats
        
        today = date.today().isoformat()
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("SELECT ads_count FROM daily_stats WHERE user_id = %s AND date = %s", (user.id, today))
            daily = c.fetchone()
            conn.close()
            daily_count = daily[0] if daily else 0
        else:
            daily_count = 0
        
        if rating < 5:
            level = "🌱 Новичок"
            progress = "▰" * rating + "▱" * (5 - rating)
        elif rating < 15:
            level = "🌿 Активный"
            progress = "▰" * (rating - 5) + "▱" * (15 - rating)
        elif rating < 30:
            level = "⭐ Опытный"
            progress = "▰" * (rating - 15) + "▱" * (30 - rating)
        elif rating < 50:
            level = "💫 Ветеран"
            progress = "▰" * (rating - 30) + "▱" * (50 - rating)
        else:
            level = "👑 Легенда"
            progress = "▰" * 20
        
        status = "❌ Заблокирован" if is_blocked else "✅ Активен"
        
        profile_text = (
            f"     👤 **МОЙ ПРОФИЛЬ**     \n\n"
            f"✨ **Имя:** {first_name}\n"
            f"👥 **Ник:** @{username or 'нет'}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📅 **На сайте:** {reg_date.split('T')[0]}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 **СТАТИСТИКА**\n\n"
            f"📤 **Отправлено:** {ads_sent}\n"
            f"📥 **Опубликовано:** {ads_published}\n"
            f"📈 **Сегодня:** {daily_count}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏆 **РЕЙТИНГ**\n\n"
            f"⭐ **Всего баллов:** {rating}\n"
            f"📊 **Уровень:** {level}\n"
            f"📈 **Прогресс:** {progress}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔒 **Статус:** {status}"
        )
        
        if is_admin(user.id):
            profile_text += "\n\n🔑 **/admin**"
        
        await update.message.reply_text(profile_text, parse_mode='Markdown')

    elif text == "📊 Статистика":
        total_users = get_total_users()
        active_today = get_active_users_today()
        published_today = get_daily_stats()
        blocked_users = get_blocked_users_count()
        total_sent = get_total_ads_sent()
        total_published = get_total_ads_published()
        
        stats_text = (
            f"     📊 **СТАТИСТИКА БОТА**     \n\n"
            f"👥 **Пользователей в боте:** {total_users}\n"
            f"📊 **Активных сегодня:** {active_today}\n"
            f"🚫 **Заблокировано:** {blocked_users}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📤 **Всего отправлено:** {total_sent}\n"
            f"📥 **Всего опубликовано:** {total_published}\n"
            f"✅ **Опубликовано сегодня:** {published_today}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 **Дата:** {date.today().strftime('%d.%m.%Y')}"
        )
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    elif text == "❓ Помощь / Тикет":
        await update.message.reply_text(
            "📝 **СОЗДАНИЕ ТИКЕТА**\n\n"
            "Опишите вашу проблему одним сообщением.\n\n"
            "📌 **Примеры:**\n"
            "• Не отправляется объявление\n"
            "• Вопрос по модерации\n"
            "• Предложение сотрудничества\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Напишите ваше сообщение:",
            parse_mode='Markdown'
        )
        context.user_data['ticket_mode'] = True

async def group_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = int(data.split("_")[1])

    if data.startswith("publish_"):
        try:
            original_id = query.message.message_id - 1
            await context.bot.forward_message(
                chat_id=CHANNEL_ID,
                from_chat_id=GROUP_CHAT_ID,
                message_id=original_id
            )
            increment_daily_published()
            increment_published(user_id)
            
            await query.edit_message_text(
                f"✅ **ОПУБЛИКОВАНО**\n\nID: `{user_id}`\n✨ Премиум-эффекты сохранены",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await query.edit_message_text("❌ Ошибка публикации")

    elif data.startswith("delete_"):
        await query.edit_message_text(
            f"❌ **УДАЛЕНО**\n\nID: `{user_id}`",
            parse_mode='Markdown'
        )

    elif data.startswith("block_"):
        block_user(user_id)
        await query.edit_message_text(
            f"🚫 **ЗАБЛОКИРОВАН**\n\nID: `{user_id}`",
            parse_mode='Markdown'
        )

async def run_bot():
    logger.info("=" * 50)
    logger.info("ЗАПУСК БОТА")
    logger.info("=" * 50)
    
    app_builder = Application.builder().token(BOT_TOKEN)
    app_builder.connect_timeout(30.0)
    app_builder.read_timeout(30.0)
    app_builder.write_timeout(30.0)
    app_builder.pool_timeout(30.0)
    
    application = app_builder.build()
    
    try:
        me = await application.bot.get_me()
        logger.info(f"✅ Бот @{me.username} подключен")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("getid", get_chat_id))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|users_page_|user_stats_|broadcast_)"))
    application.add_handler(CallbackQueryHandler(group_action_handler, pattern="^(publish_|delete_|block_)"))
    application.add_handler(MessageHandler(filters.ALL, message_handler))

    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удален")
    except:
        pass
    
    logger.info("🔄 Запускаю бота в режиме POLLING...")
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=True,
        timeout=30,
        poll_interval=1.0,
        allowed_updates=Update.ALL_TYPES
    )
    
    logger.info("✅ Бот успешно запущен в режиме POLLING!")
    logger.info("📁 База данных: PostgreSQL на Bothost")
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Останавливаем бота...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

def main():
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    main()
