import os
import logging
import sqlite3
import datetime
import requests
import time
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

# ===== КОНФИГУРАЦИЯ =====
class Config:
    # Токены (заполнить в Secrets Replit)
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))  # Ваш Telegram ID
    
    # Настройки GPT4Free (не требует API ключа)
    AI_PROVIDER = "you"  # you, deepai, theb - бесплатные провайдеры
    MAX_TOKENS = 300
    
    # Настройки бизнеса
    BUSINESS_NAME = "AI Bot Solutions"
    SERVICE_PRICE = "от 20 000 руб."
    DEVELOPMENT_TIME = "5-10 дней"
    SUPPORT_PHONE = "+79998887766"
    
    # Настройки базы данных
    DB_NAME = "leads.db"
    
    # Настройки сервера
    PORT = int(os.getenv('PORT', 8080))
    
    # Настройки напоминаний
    REMINDER_INTERVAL = 86400  # 24 часа в секундах
    
    @staticmethod
    def get_webhook_url():
        """Получение URL для вебхука в Replit"""
        try:
            owner = os.environ['REPL_OWNER']
            slug = os.environ['REPL_SLUG']
            return f"https://{slug}.{owner}.repl.co"
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
        self.conn = sqlite3.connect(Config.DB_NAME)
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
        c.execute("SELECT * FROM requests WHERE status = ?", (status,))
        return c.fetchall()
    
    def update_request_status(self, request_id: int, status: str):
        c = self.conn.cursor()
        c.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
        self.conn.commit()
    
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
        c.execute("SELECT * FROM questions WHERE answer IS " + ("NOT NULL" if answered else "NULL"))
        return c.fetchall()
    
    def answer_question(self, question_id: int, answer: str):
        c = self.conn.cursor()
        c.execute("UPDATE questions SET answer = ? WHERE id = ?", (answer, question_id))
        self.conn.commit()
    
    # Методы для базы знаний
    def add_to_knowledge_base(self, question: str, answer: str):
        c = self.conn.cursor()
        c.execute("INSERT INTO knowledge_base (question, answer) VALUES (?, ?)", (question, answer))
        self.conn.commit()
    
    def get_knowledge_base(self):
        c = self.conn.cursor()
        c.execute("SELECT question, answer FROM knowledge_base")
        return c.fetchall()
    
    # Методы для напоминаний
    def get_inactive_leads(self, days=2):
        c = self.conn.cursor()
        c.execute('''SELECT * FROM requests 
                    WHERE status = 'new' 
                    AND date(created_at) <= date('now', ?)''', (f'-{days} days',))
        return c.fetchall()

# Инициализация базы данных
db = Database()

# ===== ТЕКСТЫ =====
GREETING = f"""
🤖 Привет! Я помощник {Config.BUSINESS_NAME}.
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

# ===== AI ФУНКЦИИ (GPT4Free) =====
async def generate_ai_response(user_input: str) -> str:
    """Генерация ответа через GPT4Free"""
    try:
        # Импортируем gpt4free уже внутри функции, чтобы избежать ошибок
        from gpt4free import you
        
        # Создаем промпт с контекстом
        system_prompt = f"""
        Ты профессиональный менеджер компании {Config.BUSINESS_NAME}, 
        специализирующейся на создании Telegram-ботов. Строго соблюдай правила:

        1. Отвечай ТОЛЬКО на вопросы о создании Telegram-ботов
        2. Будь лаконичен (1-3 предложения)
        3. При сложных вопросах предложи оставить заявку
        4. Не упоминай конкурентов
        5. Определяй язык вопроса и отвечай на том же языке

        Контекст бизнеса:
        - Стоимость: {Config.SERVICE_PRICE}
        - Сроки: {Config.DEVELOPMENT_TIME}
        - Контакты: {Config.SUPPORT_PHONE}
        """
        
        # Формируем полный запрос
        full_prompt = f"{system_prompt}\n\nВопрос пользователя: {user_input}"
        
        # Отправляем запрос через GPT4Free
        response = you.Completion.create(
            prompt=full_prompt,
            chat=[]  # Пустой чат для начала диалога
        )
        
        if response and hasattr(response, 'text'):
            return response.text.strip()[:Config.MAX_TOKENS]
        else:
            return "🤖 Извините, не удалось обработать ваш запрос. Попробуйте позже."
            
    except Exception as e:
        logger.error(f"GPT4Free error: {str(e)}")
        # Альтернативный способ через requests (если gpt4free не работает)
        try:
            return await generate_ai_response_fallback(user_input)
        except:
            return "🤖 В настоящее время сервис ИИ недоступен. Пожалуйста, попробуйте позже или оставьте заявку для связи с менеджером."

async def generate_ai_response_fallback(user_input: str) -> str:
    """Альтернативный способ генерации ответов через бесплатные API"""
    try:
        # Проверяем, относится ли вопрос к созданию ботов
        bot_keywords = ["бот", "telegram", "создани", "разраб", "автоматиз", "лид", "заявк"]
        user_input_lower = user_input.lower()
        
        if not any(keyword in user_input_lower for keyword in bot_keywords):
            return "Извините, я специализируюсь только на создании Telegram-ботов. Могу ли я помочь с этим?"
        
        # Используем бесплатный API как запасной вариант
        url = "https://api.deepai.org/api/text-generator"
        headers = {"api-key": "quickstart-QUdJIGlzIGNvbWluZy4uLi4K"}
        
        prompt = f"""
        Ты менеджер компании по созданию Telegram-ботов. 
        Отвечай кратко (1-2 предложения) только на вопросы о ботах.
        Вопрос: {user_input}
        Ответ:
        """
        
        response = requests.post(
            url, 
            data={"text": prompt},
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("output", "Не удалось сгенерировать ответ")[:Config.MAX_TOKENS]
        else:
            return "🤖 Извините, не удалось обработать ваш запрос. Попробуйте позже."
            
    except Exception as e:
        logger.error(f"Fallback AI error: {str(e)}")
        return "🤖 В настоящее время сервис ИИ недоступен. Пожалуйста, попробуйте позже или оставьте заявку для связи с менеджером."

# ===== ОСНОВНЫЕ ФУНКЦИИ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚀 Оставить заявку", callback_data='request_bot')],
        [InlineKeyboardButton("ℹ️ Услуги и цены", callback_data='info')],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data='ask_question')],
        [InlineKeyboardButton("👨‍💼 Связаться с менеджером", callback_data='contact_manager')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(GREETING, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'request_bot':
        context.user_data[user_id] = {'step': 0}
        await query.edit_message_text(text=REQUEST_FLOW[0])
    
    elif query.data == 'info':
        await query.edit_message_text(text=SERVICE_INFO, parse_mode='Markdown')
    
    elif query.data == 'ask_question':
        context.user_data[user_id] = {'mode': 'question'}
        await query.edit_message_text(text="✏️ Задайте ваш вопрос:")
    
    elif query.data == 'contact_manager':
        context.user_data[user_id] = {'mode': 'contact'}
        await query.edit_message_text(text="📱 Оставьте ваш контакт для связи (телефон или @username):")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "N/A"
    text = update.message.text
    
    # Проверяем состояние пользователя
    user_data = context.user_data.get(user_id, {})
    
    # Обработка заявки
    if 'step' in user_data:
        step = user_data['step']
        
        if step == 0:
            context.user_data[user_id] = {
                'step': 1,
                'business_type': text
            }
            await update.message.reply_text(REQUEST_FLOW[1])
            
        elif step == 1:
            context.user_data[user_id] = {
                'step': 2,
                'business_type': user_data['business_type'],
                'bot_tasks': text
            }
            await update.message.reply_text(REQUEST_FLOW[2])
            
        elif step == 2:
            # Сохраняем заявку
            request_data = {
                'user_id': user_id,
                'username': username,
                'contact': text,
                'business_type': user_data.get('business_type', ''),
                'bot_tasks': user_data.get('bot_tasks', '')
            }
            
            request_id = db.add_request(request_data)
            
            # Уведомляем администратора
            await notify_admin(
                context, 
                f"🚀 Новая заявка #{request_id}!\n"
                f"От: @{username}\n"
                f"Бизнес: {request_data['business_type']}\n"
                f"Задачи: {request_data['bot_tasks'][:100]}"
            )
            
            await update.message.reply_text(
                "✅ Заявка принята! Мы свяжемся с вами в ближайшее время."
            )
            del context.user_data[user_id]
    
    # Обработка вопроса
    elif user_data.get('mode') == 'question':
        question_id = db.add_question(user_id, username, text)
        await notify_admin(
            context, 
            f"❓ Новый вопрос #{question_id}!\n"
            f"От: @{username}\n"
            f"Вопрос: {text[:200]}"
        )
        await update.message.reply_text("✉️ Ваш вопрос передан менеджеру. Ответим в ближайшее время!")
        del context.user_data[user_id]
    
    # Обработка контакта для менеджера
    elif user_data.get('mode') == 'contact':
        await notify_admin(
            context, 
            f"👤 Пользователь хочет связаться с менеджером:\n"
            f"ID: {user_id}\n"
            f"Username: @{username}\n"
            f"Контакт: {text}"
        )
        await update.message.reply_text("✅ Ваши контакты переданы менеджеру. С вами свяжутся в ближайшее время!")
        del context.user_data[user_id]
    
    # Обычное сообщение - обработка AI
    else:
        response = await generate_ai_response(text)
        await update.message.reply_text(response)

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Отправка уведомления администратору"""
    if Config.ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=Config.ADMIN_USER_ID,
                text=message
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")

# ===== АДМИН-ПАНЕЛЬ =====
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Панель администратора"""
    if update.message.from_user.id != Config.ADMIN_USER_ID:
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 Новые заявки", callback_data='list_requests')],
        [InlineKeyboardButton("❓ Новые вопросы", callback_data='list_questions')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔐 Панель управления:", reply_markup=reply_markup)

async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'list_requests':
        requests = db.get_requests()
        if not requests:
            await query.message.reply_text("🟢 Новых заявок нет")
            return
        
        for req in requests:
            text = (f"📋 Заявка #{req[0]}\n"
                    f"👤 Пользователь: @{req[2]}\n"
                    f"📱 Контакт: {req[3]}\n"
                    f"🏢 Бизнес: {req[4]}")
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f'accept_req_{req[0]}'),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_req_{req[0]}')
                ]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == 'list_questions':
        questions = db.get_questions(answered=False)
        if not questions:
            await query.message.reply_text("🟢 Новых вопросов нет")
            return
        
        for q in questions:
            text = (f"❓ Вопрос #{q[0]}\n"
                    f"👤 От: @{q[2]}\n"
                    f"📝 Текст: {q[3][:100]}")
            
            keyboard = [
                [InlineKeyboardButton("💬 Ответить", callback_data=f'answer_{q[0]}')]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data.startswith('accept_req_'):
        request_id = int(query.data.split('_')[2])
        db.update_request_status(request_id, "accepted")
        await query.message.reply_text(f"✅ Заявка #{request_id} принята")
    
    elif query.data.startswith('reject_req_'):
        request_id = int(query.data.split('_')[2])
        db.update_request_status(request_id, "rejected")
        await query.message.reply_text(f"❌ Заявка #{request_id} отклонена")
    
    elif query.data.startswith('answer_'):
        question_id = int(query.data.split('_')[1])
        context.user_data['answering_question'] = question_id
        await query.message.reply_text(f"✏️ Введите ответ на вопрос #{question_id}:")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений администратора"""
    if update.message.from_user.id != Config.ADMIN_USER_ID:
        return
    
    text = update.message.text
    
    # Ответ на вопрос
    if 'answering_question' in context.user_data:
        question_id = context.user_data['answering_question']
        db.answer_question(question_id, text)
        
        # Получаем данные вопроса
        question_data = next((q for q in db.get_questions() if q[0] == question_id), None)
        if question_data:
            # Отправляем ответ пользователю
            try:
                await context.bot.send_message(
                    chat_id=question_data[1],
                    text=f"ℹ️ Ответ на ваш вопрос:\n\n{text}"
                )
            except:
                await update.message.reply_text("❌ Не удалось отправить ответ пользователю")
            
            # Добавляем в базу знаний
            db.add_to_knowledge_base(question_data[3], text)
        
        await update.message.reply_text(f"✅ Ответ на вопрос #{question_id} сохранён!")
        del context.user_data['answering_question']

# ===== НАПОМИНАНИЯ =====
async def send_reminders(context: CallbackContext):
    """Отправка напоминаний неактивным лидам"""
    try:
        inactive_leads = db.get_inactive_leads()
        for lead in inactive_leads:
            try:
                await context.bot.send_message(
                    chat_id=lead[1],
                    text="👋 Напоминаем о вашей заявке на создание бота! "
                         "Хотите уточнить детали или добавить информацию?"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки напоминания: {e}")
    except Exception as e:
        logger.error(f"Ошибка в задаче напоминаний: {e}")

# ===== ЗАПУСК СЕРВЕРА =====
def main():
    # Создание приложения
    application = Application.builder().token(Config.TELEGRAM_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(admin_button_handler))
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))
    
    # Напоминания
    if Config.ADMIN_USER_ID:
        application.job_queue.run_repeating(
            send_reminders, 
            interval=Config.REMINDER_INTERVAL, 
            first=10
        )
    
    # Настройка вебхука
    try:
        url = Config.get_webhook_url()
        application.run_webhook(
            listen="0.0.0.0",
            port=Config.
