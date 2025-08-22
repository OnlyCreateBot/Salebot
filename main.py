import os
import logging
import sqlite3
import datetime
import requests
import time
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    CallbackContext
)
from telegram.constants import ParseMode
from flask import Flask, request

# ===== КОНФИГУРАЦИЯ =====
class Config:
    # Токены (заполнить в Secrets Replit)
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
    MANAGER_USER_IDS = [int(uid) for uid in os.getenv('MANAGER_USER_IDS', '').split(',') if uid]

    # Настройки бизнеса
    BUSINESS_NAME = "Create AI Bot"
    SERVICE_PRICE = "от 100 000 тг."
    DEVELOPMENT_TIME = "7-14 дней"
    SUPPORT_PHONE = "@arufak"

    # Настройки базы данных
    DB_NAME = "leads.db"

    # Настройки сервера
    PORT = int(os.getenv('PORT', 8080))
    REMINDER_INTERVAL = 86400  # 24 часа в секундах

    @staticmethod
    def get_webhook_url():
        """Получение URL для вебхука в Replit"""
        try:
            repl_owner = os.environ.get('REPL_OWNER', 'unknown')
            repl_slug = os.environ.get('REPL_SLUG', 'unknown')
            return f"https://{repl_slug}.{repl_owner}.repl.co"
        except:
            return "https://your-domain.com"

# ===== НАСТРОЙКА ЛОГГИРОВАНИЯ =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== БАЗА ДАННЫХ =====
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(Config.DB_NAME, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        c = self.conn.cursor()

        # Таблица заявок
        c.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            contact TEXT NOT NULL,
            business_type TEXT,
            bot_tasks TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Таблица вопросов
        c.execute('''CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            question TEXT NOT NULL,
            answer TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Таблица знаний
        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Таблица менеджеров
        c.execute('''CREATE TABLE IF NOT EXISTS managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            is_active BOOLEAN DEFAULT TRUE
        )''')

        self.conn.commit()

    # Методы для заявок
    def add_request(self, user_data: dict):
        c = self.conn.cursor()
        c.execute('''INSERT INTO requests 
                    (user_id, username, contact, business_type, bot_tasks)
                    VALUES (?, ?, ?, ?, ?)''',
                  (user_data['user_id'], 
                   user_data.get('username', ''),
                   user_data['contact'],
                   user_data.get('business_type', ''),
                   user_data.get('bot_tasks', '')))
        self.conn.commit()
        return c.lastrowid

    def get_requests(self, status='new'):
        c = self.conn.cursor()
        c.execute("SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC", (status,))
        return c.fetchall()

    def update_request_status(self, request_id: int, status: str):
        c = self.conn.cursor()
        c.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
        self.conn.commit()

    def get_request_by_id(self, request_id: int):
        c = self.conn.cursor()
        c.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
        return c.fetchone()

    # Методы для вопросов
    def add_question(self, user_id: int, username: str, question: str):
        c = self.conn.cursor()
        c.execute('''INSERT INTO questions 
                    (user_id, username, question)
                    VALUES (?, ?, ?)''',
                  (user_id, username, question))
        self.conn.commit()
        return c.lastrowid

    def get_questions(self, answered=False):
        c = self.conn.cursor()
        if answered:
            c.execute("SELECT * FROM questions WHERE answer IS NOT NULL ORDER BY created_at DESC")
        else:
            c.execute("SELECT * FROM questions WHERE answer IS NULL ORDER BY created_at DESC")
        return c.fetchall()

    def answer_question(self, question_id: int, answer: str):
        c = self.conn.cursor()
        c.execute("UPDATE questions SET answer = ?, status = 'answered' WHERE id = ?", (answer, question_id))
        self.conn.commit()

    def get_question_by_id(self, question_id: int):
        c = self.conn.cursor()
        c.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
        return c.fetchone()

    # Методы для базы знаний
    def add_to_knowledge_base(self, question: str, answer: str):
        c = self.conn.cursor()
        c.execute("INSERT INTO knowledge_base (question, answer) VALUES (?, ?)", (question, answer))
        self.conn.commit()

    def get_knowledge_base(self):
        c = self.conn.cursor()
        c.execute("SELECT question, answer FROM knowledge_base ORDER BY created_at DESC")
        return c.fetchall()

    # Методы для напоминаний
    def get_inactive_leads(self, days=2):
        c = self.conn.cursor()
        c.execute('''SELECT * FROM requests 
                    WHERE status = 'new' 
                    AND date(created_at) <= date('now', ?)''', (f'-{days} days',))
        return c.fetchall()

    # Методы для статистики
    def get_stats(self):
        c = self.conn.cursor()
        stats = {}

        # Общее количество заявок
        c.execute("SELECT COUNT(*) FROM requests")
        stats['total_requests'] = c.fetchone()[0]

        # Новые заявки
        c.execute("SELECT COUNT(*) FROM requests WHERE status = 'new'")
        stats['new_requests'] = c.fetchone()[0]

        # Принятые заявки
        c.execute("SELECT COUNT(*) FROM requests WHERE status = 'accepted'")
        stats['accepted_requests'] = c.fetchone()[0]

        # Общее количество вопросов
        c.execute("SELECT COUNT(*) FROM questions")
        stats['total_questions'] = c.fetchone()[0]

        # Неотвеченные вопросы
        c.execute("SELECT COUNT(*) FROM questions WHERE answer IS NULL")
        stats['unanswered_questions'] = c.fetchone()[0]

        # Количество менеджеров
        c.execute("SELECT COUNT(*) FROM managers WHERE is_active = TRUE")
        stats['active_managers'] = c.fetchone()[0]

        return stats

    # Методы для менеджеров
    def add_manager(self, user_id: int, username: str):
        c = self.conn.cursor()
        try:
            c.execute("INSERT INTO managers (user_id, username) VALUES (?, ?)", (user_id, username))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_active_managers(self):
        c = self.conn.cursor()
        c.execute("SELECT user_id FROM managers WHERE is_active = TRUE")
        return [row[0] for row in c.fetchall()]

    def remove_manager(self, user_id: int):
        c = self.conn.cursor()
        c.execute("DELETE FROM managers WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def is_manager(self, user_id: int):
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM managers WHERE user_id = ? AND is_active = TRUE", (user_id,))
        return c.fetchone()[0] > 0

# Инициализация базы данных
db = Database()

# ===== ТЕКСТЫ =====
GREETING = f"""
🤖 Привет! Я AI-помощник {Config.BUSINESS_NAME}.
Помогаю автоматизировать бизнес с помощью Telegram-ботов.

Выберите действие:
"""

SERVICE_INFO = f"""
⚡️ *Наши услуги:*
- Создание AI-ботов для Telegram
- Интеграция с платежными системами
- Боты для сбора лидов и продаж
- Техническая поддержка 24/7

⏱ *Срок разработки:* {Config.DEVELOPMENT_TIME}
💵 *Стоимость:* {Config.SERVICE_PRICE}

📞 Контакты: {Config.SUPPORT_PHONE}
"""

REQUEST_FLOW = [
    "📝 Для какого бизнеса нужен бот?",
    "🔧 Какие задачи должен решать бот?",
    "📱 Оставьте контакт для связи (телефон или @username):"
]

# ===== ФУНКЦИИ КНОПОК =====
def get_menu_buttons():
    """Возвращает стандартные кнопки меню"""
    return [
        [InlineKeyboardButton("🏠 Вернуться в меню", callback_data='back_to_menu')],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data='ask_ai_question')]
    ]

def get_main_menu_keyboard(user_id=None):
    """Возвращает клавиатуру главного меню"""
    keyboard = [
        [InlineKeyboardButton("🚀 Оставить заявку", callback_data='request_bot')],
        [InlineKeyboardButton("ℹ️ Услуги и цены", callback_data='info')],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data='ask_ai_question')],
        [InlineKeyboardButton("👨‍💼 Связаться с менеджером", callback_data='contact_manager')]
    ]

    # Добавляем кнопку админ панели если это админ или менеджер
    if user_id and (user_id == Config.ADMIN_USER_ID or db.is_manager(user_id)):
        keyboard.append([InlineKeyboardButton("🔐 Админ панель", callback_data='admin_panel')])

    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Возвращает клавиатуру админ панели"""
    keyboard = [
        [InlineKeyboardButton("📝 Новые заявки", callback_data='admin_requests')],
        [InlineKeyboardButton("❓ Новые вопросы", callback_data='admin_questions')],
        [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
        [InlineKeyboardButton("📚 База знаний", callback_data='admin_knowledge')],
        [InlineKeyboardButton("➕ Добавить менеджера", callback_data='admin_add_manager')],
        [InlineKeyboardButton("➖ Удалить менеджера", callback_data='admin_remove_manager')],
        [InlineKeyboardButton("👥 Список менеджеров", callback_data='admin_list_managers')],
        [InlineKeyboardButton("🏠 Главное меню", callback_data='back_to_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== ФУНКЦИИ ПРОВЕРКИ ПРАВ =====
def is_admin_or_manager(user_id):
    """Проверяет, является ли пользователь админом или менеджером"""
    return user_id == Config.ADMIN_USER_ID or db.is_manager(user_id)

# ===== AI ФУНКЦИИ =====
async def generate_ai_response(user_input: str) -> str:
    """Генерация ответа через AI"""
    try:
        # Сначала проверяем базу знаний
        knowledge = db.get_knowledge_base()
        user_input_lower = user_input.lower()

        for question, answer in knowledge:
            if any(word in user_input_lower for word in question.lower().split() if len(word) > 3):
                return f"💡 {answer}\n\nЕсли нужна дополнительная информация, обращайтесь к менеджеру!"

        # Ключевые слова для распознавания тем
        bot_keywords = ["бот", "telegram", "создани", "разраб", "автоматиз", "лид", "заявк", "интеграц"]
        price_keywords = ["цена", "стоимость", "сколько", "прайс", "тариф", "оплата", "стоит", "деньги"]
        time_keywords = ["срок", "время", "когда", "быстро", "долго"]
        function_keywords = ["функции", "возможности", "что умеет", "может", "делать","ты кто"]
        payment_keywords = ["оплата", "платеж", "деньги", "карта", "перевод"]

        # Проверяем на что похож вопрос
        if any(keyword in user_input_lower for keyword in price_keywords):
            return "💵 *Стоимость наших услуг:*\n\n• Простой бот - от 80 000 тенге\n• Бот с AI - от 125 000 тг.\n• Интеграция с CRM - от 175 000 тг.\n• Комплексное решение - от 250 000 тг.\n\nТочная цена зависит от функционала. Оставьте заявку для расчета!"

        elif any(keyword in user_input_lower for keyword in time_keywords):
            return "⏱ *Сроки разработки:*\n\n• Простой бот - 3-5 дней\n• Средней сложности - 7-14 дней\n• Сложный проект - 10-20 дней\n\nМы работаем быстро и качественно!"

        elif any(keyword in user_input_lower for keyword in function_keywords):
            return "🔧 *Возможности наших ботов:*\n\n• Прием и обработка заявок\n• AI-консультант клиентов\n• Интеграция с CRM системами\n• Прием платежей\n• Рассылка уведомлений\n• Аналитика и отчеты\n• Многоязычность\n• Работа 24/7"

        elif any(keyword in user_input_lower for keyword in payment_keywords):
            return "💳 *Варианты оплаты:*\n\n• Банковский перевод\n• Оплата по карте\n• Электронные кошельки\n• Рассрочка для крупных проектов\n\n50% предоплата, 50% после сдачи проекта."

        elif any(keyword in user_input_lower for keyword in bot_keywords):
            return f"🤖 *О наших Telegram-ботах:*\n\nМы создаем современные боты с AI для автоматизации бизнеса:\n\n• Генерация лидов\n• Консультации клиентов\n• Автоматизация продаж\n• Техподдержка 24/7\n\n{SERVICE_INFO}"

        elif "контакт" in user_input_lower or "связаться" in user_input_lower:
            return f"📞 *Наши контакты:*\n\nТелефон: {Config.SUPPORT_PHONE}\nПишите в любое время!\n\nИли оставьте заявку через бота - мы сами с вами свяжемся в течение часа."

        elif "привет" in user_input_lower or "здравствуй" in user_input_lower:
            return f"👋 Привет! Я AI-помощник {Config.BUSINESS_NAME}!\n\nПомогаю создавать крутые Telegram-боты для бизнеса. Что вас интересует?"

        elif "спасибо" in user_input_lower or "благодар" in user_input_lower:
            return "😊 Пожалуйста! Всегда рад помочь!\n\nЕсли есть еще вопросы - обращайтесь!"

        else:
            # Вежливый перевод на создание бота
            return f"🤖 *Понял вас!*\n\nМы можем обсудить создание Telegram-бота для вашего бизнеса, который будет решать множество задач.\n\n{SERVICE_INFO}\n\nЧто вас интересует больше всего? Или, возможно, вы хотите оставить заявку на консультацию?"

    except Exception as e:
        logger.error(f"AI response error: {str(e)}")
        return "🤖 Извините, произошла техническая ошибка. Попробуйте переформулировать вопрос или свяжитесь с менеджером напрямую."

# ===== ФУНКЦИИ УВЕДОМЛЕНИЙ =====
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Отправка уведомления администратору"""
    if Config.ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=Config.ADMIN_USER_ID,
                text=message
            )
            logger.info(f"Уведомление админу отправлено")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

async def notify_managers(context: ContextTypes.DEFAULT_TYPE, message: str, question_id: int = None):
    """Отправка уведомления всем активным менеджерам"""
    manager_ids = db.get_active_managers()
    if not manager_ids:
        logger.warning("Нет активных менеджеров для уведомления.")
        return

    for manager_id in manager_ids:
        try:
            keyboard = []
            if question_id:
                keyboard.append([InlineKeyboardButton("💬 Ответить на вопрос", callback_data=f'answer_question_from_manager_{question_id}')])
            keyboard.append([InlineKeyboardButton("🔐 Админ панель", callback_data='admin_panel')])
            
            await context.bot.send_message(
                chat_id=manager_id,
                text=f"📢 *Новое уведомление!*\n\n{message}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            logger.info(f"Уведомление менеджеру {manager_id} отправлено")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления менеджеру {manager_id}: {e}")

async def send_answer_to_user(context: ContextTypes.DEFAULT_TYPE, question_id: int, answer: str):
    """Отправка ответа пользователю, который задал вопрос"""
    question_data = db.get_question_by_id(question_id)
    if not question_data:
        logger.error(f"Не удалось найти вопрос с ID {question_id}")
        return

    user_id = question_data[1]
    menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"💬 *Ответ на ваш вопрос:*\n\n{answer}",
            reply_markup=menu_buttons
        )
        logger.info(f"Ответ на вопрос #{question_id} отправлен пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа пользователю {user_id}: {e}")

# ===== ОСНОВНЫЕ ФУНКЦИИ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.message.from_user.id
    context.user_data.clear()

    # Добавляем пользователя как менеджера, если он есть в списке Config
    if user_id in Config.MANAGER_USER_IDS:
        db.add_manager(user_id, update.message.from_user.username)

    reply_markup = get_main_menu_keyboard(user_id)
    await update.message.reply_text(GREETING, reply_markup=reply_markup)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик всех callback-запросов"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        # Кнопка "Вернуться в меню"
        if query.data == 'back_to_menu':
            context.user_data.clear()
            reply_markup = get_main_menu_keyboard(user_id)
            await query.edit_message_text(text=GREETING, reply_markup=reply_markup)
            return

        # Кнопка "Задать вопрос AI"
        elif query.data == 'ask_ai_question':
            context.user_data['mode'] = 'ai_question'
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await query.edit_message_text(
                text="🤖 Задайте любой вопрос о создании ботов для бизнеса:",
                reply_markup=menu_buttons
            )
            return

        # Обычные пользовательские кнопки
        elif query.data == 'request_bot':
            context.user_data['step'] = 0
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await query.edit_message_text(text=REQUEST_FLOW[0], reply_markup=menu_buttons)

        elif query.data == 'info':
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await query.edit_message_text(text=SERVICE_INFO, reply_markup=menu_buttons, parse_mode=ParseMode.MARKDOWN)

        elif query.data == 'contact_manager':
            context.user_data['mode'] = 'contact'
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await query.edit_message_text(
                text="📱 Оставьте ваш контакт для связи (телефон или @username):",
                reply_markup=menu_buttons
            )

        # Админ панель
        elif query.data == 'admin_panel':
            if is_admin_or_manager(user_id):
                reply_markup = get_admin_keyboard()
                await query.edit_message_text("🔐 *Панель управления:*", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            else:
                menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
                await query.edit_message_text("❌ У вас нет прав для этого действия", reply_markup=menu_buttons)

        # Обработка ответов от менеджеров
        elif query.data.startswith('answer_question_from_manager_'):
            if is_admin_or_manager(user_id):
                question_id = int(query.data.split('_')[-1])
                context.user_data['answering_question_as_manager'] = question_id
                reply_markup = InlineKeyboardMarkup(get_menu_buttons())
                await query.edit_message_text(
                    text=f"💬 Введите ваш ответ на вопрос #{question_id}:",
                    reply_markup=reply_markup
                )
            else:
                menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
                await query.edit_message_text("❌ У вас нет прав для этого действия", reply_markup=menu_buttons)

        # Админские кнопки
        elif query.data.startswith('admin_') and is_admin_or_manager(user_id):
            await handle_admin_callbacks(query, context)
        
        # Кнопки принятия/отклонения заявок
        elif query.data.startswith(('accept_req_', 'reject_req_')) and is_admin_or_manager(user_id):
            await handle_request_actions(query, context)
            
        else:
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await query.edit_message_text("❌ У вас нет прав для этого действия", reply_markup=menu_buttons)

    except Exception as e:
        logger.error(f"Ошибка в обработчике callback: {e}")
        menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
        try:
            await query.edit_message_text("❌ Произошла ошибка. Попробуйте позже.", reply_markup=menu_buttons)
        except:
            pass

async def handle_request_actions(query, context):
    """Обработка действий с заявками"""
    user_id = query.from_user.id
    
    if query.data.startswith('accept_req_'):
        request_id = int(query.data.split('_')[2])
        db.update_request_status(request_id, "accepted")
        
        # Уведомляем пользователя о принятии заявки
        request_data = db.get_request_by_id(request_id)
        if request_data:
            try:
                await context.bot.send_message(
                    chat_id=request_data[1],
                    text=f"✅ *Ваша заявка принята!*\n\nНаш менеджер свяжется с вами в ближайшее время.\n\nЗаявка #{request_id}",
                    reply_markup=InlineKeyboardMarkup(get_menu_buttons()),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя о принятии заявки: {e}")
        
        reply_markup = get_admin_keyboard()
        await query.edit_message_text(f"✅ Заявка #{request_id} принята", reply_markup=reply_markup)

    elif query.data.startswith('reject_req_'):
        request_id = int(query.data.split('_')[2])
        db.update_request_status(request_id, "rejected")
        
        # Уведомляем пользователя об отклонении заявки
        request_data = db.get_request_by_id(request_id)
        if request_data:
            try:
                await context.bot.send_message(
                    chat_id=request_data[1],
                    text=f"❌ *К сожалению, ваша заявка не подошла.*\n\nВы можете оставить новую заявку с другими требованиями.\n\nЗаявка #{request_id}",
                    reply_markup=InlineKeyboardMarkup(get_menu_buttons()),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя об отклонении заявки: {e}")
        
        reply_markup = get_admin_keyboard()
        await query.edit_message_text(f"❌ Заявка #{request_id} отклонена", reply_markup=reply_markup)

async def handle_admin_callbacks(query, context):
    """Обработка админских callback-запросов"""
    user_id = query.from_user.id

    if query.data == 'admin_requests':
        requests = db.get_requests()
        if not requests:
            reply_markup = get_admin_keyboard()
            await query.edit_message_text("🟢 Новых заявок нет", reply_markup=reply_markup)
            return

        await query.edit_message_text("📋 Загружаю заявки...", reply_markup=None)
        
        for req in requests:
            text = f"📋 Заявка #{req[0]}\n👤 Пользователь: @{req[2] or 'N/A'}\n📱 Контакт: {req[3]}\n🏢 Бизнес: {req[4]}\n🔧 Задачи: {req[5]}\n📅 Создана: {req[7]}"

            keyboard = [
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f'accept_req_{req[0]}'),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_req_{req[0]}')
                ],
                [InlineKeyboardButton("🔐 Админ панель", callback_data='admin_panel')]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'admin_questions':
        questions = db.get_questions(answered=False)
        if not questions:
            reply_markup = get_admin_keyboard()
            await query.edit_message_text("🟢 Новых вопросов нет", reply_markup=reply_markup)
            return

        await query.edit_message_text("❓ Загружаю вопросы...", reply_markup=None)
        
        for q in questions:
            text = f"❓ Вопрос #{q[0]}\n👤 От: @{q[2] or 'N/A'}\n📝 Текст: {q[3]}\n📅 Создан: {q[5]}"

            keyboard = [
                [InlineKeyboardButton("💬 Ответить", callback_data=f'answer_{q[0]}')],
                [InlineKeyboardButton("🔐 Админ панель", callback_data='admin_panel')]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'admin_stats':
        stats = db.get_stats()
        text = f"📊 *Статистика бота:*\n\n📝 Всего заявок: {stats['total_requests']}\n🆕 Новых: {stats['new_requests']}\n✅ Принятых: {stats['accepted_requests']}\n\n❓ Всего вопросов: {stats['total_questions']}\n⏳ Неотвеченных: {stats['unanswered_questions']}\n👨‍💼 Активных менеджеров: {stats['active_managers']}"

        reply_markup = get_admin_keyboard()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'admin_knowledge':
        knowledge = db.get_knowledge_base()
        if not knowledge:
            reply_markup = get_admin_keyboard()
            await query.edit_message_text("📚 База знаний пуста", reply_markup=reply_markup)
            return

        text = "📚 *База знаний:*\n\n"
        for i, (question, answer) in enumerate(knowledge[-5:], 1):
            text += f"{i}. *Q:* {question[:50]}...\n   *A:* {answer[:50]}...\n\n"

        reply_markup = get_admin_keyboard()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'admin_add_manager':
        context.user_data['mode'] = 'add_manager'
        reply_markup = get_admin_keyboard()
        await query.edit_message_text("➕ Введите ID пользователя для добавления в менеджеры:", reply_markup=reply_markup)

    elif query.data == 'admin_remove_manager':
        context.user_data['mode'] = 'remove_manager'
        reply_markup = get_admin_keyboard()
        await query.edit_message_text("➖ Введите ID пользователя для удаления из менеджеров:", reply_markup=reply_markup)

    elif query.data == 'admin_list_managers':
        active_managers = db.get_active_managers()
        if not active_managers:
            reply_markup = get_admin_keyboard()
            await query.edit_message_text("👥 Список менеджеров пуст.", reply_markup=reply_markup)
            return

        text = "👥 *Активные менеджеры:*\n\n"
        for i, manager_id in enumerate(active_managers, 1):
            text += f"{i}. ID: {manager_id}\n"

        reply_markup = get_admin_keyboard()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    elif query.data.startswith('answer_'):
        question_id = int(query.data.split('_')[1])
        context.user_data['answering_question'] = question_id
        reply_markup = get_admin_keyboard()
        await query.edit_message_text(f"✏️ Введите ответ на вопрос #{question_id}:", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений"""
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "N/A"
    text = update.message.text

    # Проверяем, это менеджер и он отвечает на вопрос
    if 'answering_question_as_manager' in context.user_data:
        if is_admin_or_manager(user_id):
            question_id = context.user_data['answering_question_as_manager']
            answer = text
            db.answer_question(question_id, answer)

            # Получаем текст вопроса и добавляем в базу знаний
            question_data = db.get_question_by_id(question_id)
            if question_data:
                db.add_to_knowledge_base(question_data[3], answer)

            # Отправляем ответ пользователю
            await send_answer_to_user(context, question_id, answer)

            reply_markup = InlineKeyboardMarkup(get_menu_buttons())
            await update.message.reply_text("✅ Ваш ответ отправлен!", reply_markup=reply_markup)
            del context.user_data['answering_question_as_manager']
            return

    # Проверяем, это админ и он отвечает на вопрос
    if user_id == Config.ADMIN_USER_ID and 'answering_question' in context.user_data:
        question_id = context.user_data['answering_question']
        answer = text
        db.answer_question(question_id, answer)

        # Получаем текст вопроса и добавляем в базу знаний
        question_data = db.get_question_by_id(question_id)
        if question_data:
            db.add_to_knowledge_base(question_data[3], answer)

        # Уведомляем пользователя об ответе
        await send_answer_to_user(context, question_id, answer)

        reply_markup = get_admin_keyboard()
        await update.message.reply_text(f"✅ Ответ на вопрос #{question_id} сохранён и отправлен!", reply_markup=reply_markup)
        del context.user_data['answering_question']
        return

    # Обработка добавления/удаления менеджера
    if 'mode' in context.user_data:
        if context.user_data['mode'] == 'add_manager' and user_id == Config.ADMIN_USER_ID:
            try:
                manager_user_id = int(text)
                if db.add_manager(manager_user_id, "N/A"):
                    await update.message.reply_text(f"✅ Пользователь с ID {manager_user_id} добавлен в менеджеры.")
                else:
                    await update.message.reply_text(f"❌ Пользователь с ID {manager_user_id} уже является менеджером.")
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID. Пожалуйста, введите число.")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка при добавлении менеджера: {e}")
            finally:
                del context.user_data['mode']
                reply_markup = get_admin_keyboard()
                await update.message.reply_text("🔐 Админ панель:", reply_markup=reply_markup)
                return

        elif context.user_data['mode'] == 'remove_manager' and user_id == Config.ADMIN_USER_ID:
            try:
                manager_user_id = int(text)
                db.remove_manager(manager_user_id)
                await update.message.reply_text(f"✅ Пользователь с ID {manager_user_id} удалён из менеджеров.")
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID. Пожалуйста, введите число.")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка при удалении менеджера: {e}")
            finally:
                del context.user_data['mode']
                reply_markup = get_admin_keyboard()
                await update.message.reply_text("🔐 Админ панель:", reply_markup=reply_markup)
                return

    # Обработка заявки
    if 'step' in context.user_data:
        step = context.user_data['step']

        if step == 0:
            context.user_data['step'] = 1
            context.user_data['business_type'] = text
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await update.message.reply_text(REQUEST_FLOW[1], reply_markup=menu_buttons)

        elif step == 1:
            context.user_data['step'] = 2
            context.user_data['bot_tasks'] = text
            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await update.message.reply_text(REQUEST_FLOW[2], reply_markup=menu_buttons)

        elif step == 2:
            request_data = {
                'user_id': user_id,
                'username': username,
                'contact': text,
                'business_type': context.user_data.get('business_type', ''),
                'bot_tasks': context.user_data.get('bot_tasks', '')
            }

            request_id = db.add_request(request_data)
            logger.info(f"Новая заявка #{request_id} от пользователя {user_id}")

            # Уведомляем администратора и менеджеров
            message = f"🚀 Новая заявка #{request_id}!\n\n👤 От: @{username}\n🏢 Бизнес: {request_data['business_type']}\n🔧 Задачи: {request_data['bot_tasks'][:100]}...\n📱 Контакт: {text}"
            await notify_admin(context, message)
            await notify_managers(context, message)

            menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
            await update.message.reply_text(
                "✅ *Заявка принята!*\n\nМы свяжемся с вами в ближайшее время.\n\nСредний срок ответа: 1-2 часа в рабочее время.",
                reply_markup=menu_buttons,
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data.clear()

    # Обработка AI вопроса
    elif context.user_data.get('mode') == 'ai_question':
        question_id = db.add_question(user_id, username, text)
        logger.info(f"Новый вопрос #{question_id} от пользователя {user_id}")

        # Уведомляем админа и менеджеров о новом вопросе
        message = f"❓ Новый вопрос #{question_id}!\n\n👤 От: @{username}\n📝 Вопрос: {text}"
        await notify_admin(context, message)
        await notify_managers(context, message, question_id)

        # Генерируем AI ответ
        response = await generate_ai_response(text)
        menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
        await update.message.reply_text(response, reply_markup=menu_buttons, parse_mode=ParseMode.MARKDOWN)
        context.user_data.clear()

    # Обработка контакта для менеджера
    elif context.user_data.get('mode') == 'contact':
        message = f"👤 Запрос на связь с менеджером:\n\nID: {user_id}\nUsername: @{username}\nКонтакт: {text}"
        await notify_admin(context, message)
        await notify_managers(context, message)
        
        menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
        await update.message.reply_text(
            "✅ *Ваши контакты переданы менеджеру.*\n\nС вами свяжутся в ближайшее время!",
            reply_markup=menu_buttons,
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.clear()

    # Обычное сообщение - обработка AI
    else:
        question_id = db.add_question(user_id, username, text)
        logger.info(f"Новый вопрос #{question_id} от пользователя {user_id}")

        # Уведомляем админа и менеджеров о новом вопросе
        message = f"❓ Новый вопрос #{question_id}!\n\n👤 От: @{username}\n📝 Вопрос: {text}"
        await notify_admin(context, message)
        await notify_managers(context, message, question_id)

        response = await generate_ai_response(text)
        menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
        await update.message.reply_text(response, reply_markup=menu_buttons, parse_mode=ParseMode.MARKDOWN)

# ===== АДМИН-ПАНЕЛЬ =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Панель администратора"""
    user_id = update.message.from_user.id
    if not is_admin_or_manager(user_id):
        menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
        await update.message.reply_text("❌ У вас нет прав доступа", reply_markup=menu_buttons)
        return

    reply_markup = get_admin_keyboard()
    await update.message.reply_text("🔐 *Панель управления:*", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# ===== НАПОМИНАНИЯ =====
async def send_reminders(context: CallbackContext):
    """Отправка напоминаний неактивным лидам"""
    try:
        inactive_leads = db.get_inactive_leads()
        for lead in inactive_leads:
            try:
                menu_buttons = InlineKeyboardMarkup(get_menu_buttons())
                await context.bot.send_message(
                    chat_id=lead[1],
                    text="👋 *Напоминаем о вашей заявке на создание бота!*\n\nХотите уточнить детали или добавить информацию?",
                    reply_markup=menu_buttons,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Ошибка отправки напоминания: {e}")
    except Exception as e:
        logger.error(f"Ошибка в задаче напоминаний: {e}")

# ===== ЗАПУСК СЕРВЕРА =====
async def main():
    if not Config.TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не установлен! Добавьте его в Secrets.")
        return

    if not Config.ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID не установлен. Функции админ-панели будут недоступны.")

    if not Config.MANAGER_USER_IDS:
        logger.warning("MANAGER_USER_IDS не установлены. Уведомления менеджерам не будут отправляться.")

    logger.info("Запуск бота...")

    # Создание приложения
    application = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))

    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(handle_callbacks))

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Напоминания
    if application.job_queue:
        try:
            application.job_queue.run_repeating(
                send_reminders,
                interval=Config.REMINDER_INTERVAL,
                first=10
            )
            logger.info("Напоминания настроены")
        except Exception as e:
            logger.warning(f"Не удалось настроить напоминания: {e}")

    # Запуск бота
    try:
        webhook_url = Config.get_webhook_url()
        logger.info(f"Запуск бота с вебхуком: {webhook_url}")
        
        # Устанавливаем вебхук
        await application.bot.set_webhook(url=f"{webhook_url}/webhook")
        
        # Создаем Flask приложение для обработки вебхуков
        app = Flask(__name__)
        
        @app.route('/webhook', methods=['POST'])
        def webhook():
            json_data = request.get_json()
            update = Update.de_json(json_data, application.bot)
            asyncio.create_task(application.process_update(update))
            return 'OK'
        
        @app.route('/', methods=['GET'])
        def health_check():
            return f'Bot is running! Webhook URL: {webhook_url}/webhook'
        
        # Запускаем Flask
        app.run(host='0.0.0.0', port=Config.PORT)
        
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        # Fallback к polling если вебхук не работает
        logger.info("Переключение на polling режим...")
        await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())