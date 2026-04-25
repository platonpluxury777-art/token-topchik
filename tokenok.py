import telebot
from telebot import types
import sqlite3
import requests
import json
import time
import threading
import os
from io import BytesIO
from PIL import Image
from pyzbar.pyzbar import decode

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8747779948:AAGXTBuDOmhM_X7dPBMRGUFCiL5Qj2_1wv0"
CRYPTO_BOT_TOKEN = "569144:AAs82ABvMXw8uTlYYfIrZOMWZA5C7bYhfd"
ADMIN_IDS = [105635005]  # ID админов

bot = telebot.TeleBot(BOT_TOKEN)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect('shop.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, 
                      username TEXT, 
                      balance REAL DEFAULT 0,
                      max_account TEXT,
                      registered_date TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS products
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT,
                      description TEXT,
                      price REAL,
                      token_type TEXT,
                      stock INTEGER,
                      tokens TEXT)''')  # JSON строка с токенами
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS orders
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      product_id INTEGER,
                      amount REAL,
                      status TEXT,
                      crypto_payment_id TEXT,
                      delivered_token TEXT,
                      created_date TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS login_sessions
                     (user_id INTEGER,
                      qr_data TEXT,
                      status TEXT,
                      created_time REAL)''')
    
    conn.commit()
    return conn

db = init_db()

# ==================== CRYPTO BOT API ====================
class CryptoBotAPI:
    def __init__(self, token):
        self.token = token
        self.api_url = "https://pay.crypt.bot/api"
    
    def create_invoice(self, amount, currency="USDT", description=""):
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }
        data = {
            "asset": currency,
            "amount": str(amount),
            "description": description
        }
        response = requests.post(f"{self.api_url}/createInvoice", headers=headers, json=data)
        return response.json()
    
    def check_payment(self, invoice_id):
        headers = {"Crypto-Pay-API-Token": self.token}
        response = requests.get(f"{self.api_url}/getInvoice", headers=headers, params={"invoice_id": invoice_id})
        return response.json()

crypto_api = CryptoBotAPI(CRYPTO_BOT_TOKEN)

# ==================== АВТОМАТИЧЕСКАЯ ВЫДАЧА ТОВАРА ====================
def get_available_token(product_id):
    """Получает доступный токен из запасов товара"""
    cursor = db.cursor()
    cursor.execute("SELECT tokens, stock FROM products WHERE id = ? AND stock > 0", (product_id,))
    product = cursor.fetchone()
    
    if not product:
        return None
    
    tokens_json, stock = product
    
    try:
        tokens = json.loads(tokens_json) if tokens_json else []
        if tokens and len(tokens) > 0:
            token = tokens.pop(0)  # Берем первый токен
            
            # Обновляем список токенов и уменьшаем stock
            cursor.execute(
                "UPDATE products SET tokens = ?, stock = ? WHERE id = ?",
                (json.dumps(tokens), len(tokens), product_id)
            )
            db.commit()
            return token
    except:
        pass
    
    return None

def deliver_product_auto(user_id, product_id, order_id):
    """Автоматическая выдача товара после оплаты"""
    cursor = db.cursor()
    
    # Пытаемся получить токен автоматически
    token = get_available_token(product_id)
    
    if token:
        # Сохраняем выданный токен в заказе
        cursor.execute(
            "UPDATE orders SET status = 'completed', delivered_token = ? WHERE id = ?",
            (token, order_id)
        )
        db.commit()
        
        # Получаем информацию о товаре
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        product_name = product[0] if product else "Товар"
        
        # Отправляем токен пользователю
        bot.send_message(
            user_id,
            f"🎉 *Заказ #{order_id} выполнен!*\n\n"
            f"📦 Товар: {product_name}\n"
            f"🔑 Ваш токен: `{token}`\n\n"
            f"⚠️ Сохраните токен в надежном месте!\n"
            f"Спасибо за покупку! 🛍",
            parse_mode="Markdown"
        )
        
        # Уведомляем админов
        for admin_id in ADMIN_IDS:
            bot.send_message(
                admin_id,
                f"✅ Автовыдача заказа #{order_id}\n"
                f"Пользователь: {user_id}\n"
                f"Товар: {product_name}\n"
                f"Токен: {token}"
            )
        
        return True
    else:
        # Если токенов нет - уведомляем о необходимости ручной выдачи
        cursor.execute(
            "UPDATE orders SET status = 'paid_no_stock' WHERE id = ?",
            (order_id,)
        )
        db.commit()
        
        bot.send_message(
            user_id,
            f"⚠️ *Заказ #{order_id} оплачен!*\n\n"
            f"Администратор выдаст вам токен в ближайшее время.\n"
            f"Приносим извинения за задержку.",
            parse_mode="Markdown"
        )
        
        # Уведомляем админов о нехватке токенов
        for admin_id in ADMIN_IDS:
            bot.send_message(
                admin_id,
                f"🚨 ТРЕВОГА! Закончились токены!\n"
                f"Заказ #{order_id} оплачен, но нет доступных токенов.\n"
                f"Пользователь: {user_id}\n"
                f"Товар ID: {product_id}"
            )
        
        return False

# ==================== КЛАВИАТУРЫ ====================
def main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🛍 Магазин", "💰 Баланс")
    markup.add("📱 Вход по QR", "ℹ️ Инфо")
    if user_id in ADMIN_IDS:
        markup.add("🔧 Админ")
    return markup

def admin_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📦 Добавить товар", callback_data="admin_add_product"),
        types.InlineKeyboardButton("📋 Товары", callback_data="admin_products"),
        types.InlineKeyboardButton("💰 Выдать баланс", callback_data="admin_give_balance"),
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
    )
    return markup

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    cursor = db.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, registered_date) VALUES (?, ?, datetime('now'))", (user_id, username))
    db.commit()
    
    bot.send_message(
        message.chat.id,
        f"👋 *Добро пожаловать в Max Shop!*\n\n"
        f"🎉 Магазин токенов для мессенджера Max\n"
        f"💳 Оплата через CryptoBot\n"
        f"⚡ Мгновенная автовыдача",
        parse_mode="Markdown",
        reply_markup=main_menu(user_id)
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🎛 *Админ-панель*", parse_mode="Markdown", reply_markup=admin_menu())

@bot.message_handler(commands=['add_product'])
def add_product_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        # Формат: /add_product Название | Описание | Цена | Тип | Токен1,Токен2,Токен3
        args = message.text.replace('/add_product ', '').split('|')
        name = args[0].strip()
        description = args[1].strip()
        price = float(args[2].strip())
        token_type = args[3].strip()
        tokens_list = [t.strip() for t in args[4].strip().split(',')]
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO products (name, description, price, token_type, stock, tokens) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, price, token_type, len(tokens_list), json.dumps(tokens_list))
        )
        db.commit()
        
        bot.reply_to(message, f"✅ Товар '{name}' добавлен!\n📦 Токенов: {len(tokens_list)} шт.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}\nФормат: /add_product Название | Описание | Цена | Тип | Токен1,Токен2,Токен3")

@bot.message_handler(commands=['give_balance'])
def give_balance_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        args = message.text.replace('/give_balance ', '').split()
        target_user_id = int(args[0])
        amount = float(args[1])
        
        cursor = db.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_user_id))
        db.commit()
        
        bot.reply_to(message, f"✅ Баланс {target_user_id} пополнен на {amount} USDT")
        bot.send_message(target_user_id, f"💰 Ваш баланс пополнен на {amount} USDT!")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")

@bot.message_handler(commands=['add_tokens'])
def add_tokens_command(message):
    """Добавление токенов к существующему товару: /add_tokens ID_товара Токен1,Токен2"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        args = message.text.replace('/add_tokens ', '').split()
        product_id = int(args[0])
        new_tokens = [t.strip() for t in args[1].split(',')]
        
        cursor = db.cursor()
        cursor.execute("SELECT tokens FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        
        if not product:
            bot.reply_to(message, "❌ Товар не найден")
            return
        
        current_tokens = json.loads(product[0]) if product[0] else []
        current_tokens.extend(new_tokens)
        
        cursor.execute(
            "UPDATE products SET tokens = ?, stock = ? WHERE id = ?",
            (json.dumps(current_tokens), len(current_tokens), product_id)
        )
        db.commit()
        
        bot.reply_to(message, f"✅ Добавлено {len(new_tokens)} токенов к товару #{product_id}\n📦 Всего: {len(current_tokens)} шт.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")

# ==================== ОБРАБОТЧИКИ ТЕКСТА ====================
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    
    if message.text == "🛍 Магазин":
        show_shop(message)
    elif message.text == "💰 Баланс":
        show_balance(message)
    elif message.text == "📱 Вход по QR":
        start_qr_login(message)
    elif message.text == "🔧 Админ":
        bot.send_message(message.chat.id, "🎛 *Админ-панель*", parse_mode="Markdown", reply_markup=admin_menu())
    elif message.text == "ℹ️ Инфо":
        show_info(message)

def show_shop(message):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, description, price, stock FROM products WHERE stock > 0")
    products = cursor.fetchall()
    
    if not products:
        bot.send_message(message.chat.id, "😔 Сейчас нет доступных товаров", reply_markup=main_menu(message.from_user.id))
        return
    
    for product in products:
        product_id, name, description, price, stock = product
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"Купить за {price} USDT", callback_data=f"buy_{product_id}"))
        
        bot.send_message(
            message.chat.id,
            f"📦 *{name}*\n📝 {description}\n💵 Цена: {price} USDT\n📦 В наличии: {stock} шт.",
            parse_mode="Markdown",
            reply_markup=markup
        )

def show_balance(message):
    user_id = message.from_user.id
    cursor = db.cursor()
    cursor.execute("SELECT balance, max_account FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    if user_data:
        balance, max_account = user_data
        status = "✅ Подключен" if max_account else "❌ Не подключен"
        bot.send_message(message.chat.id, f"💰 Баланс: {balance:.2f} USDT\n🔐 Max: {status}", reply_markup=main_menu(user_id))

def start_qr_login(message):
    user_id = message.from_user.id
    cursor = db.cursor()
    cursor.execute("INSERT OR REPLACE INTO login_sessions (user_id, status, created_time) VALUES (?, 'waiting_qr', ?)", (user_id, time.time()))
    db.commit()
    
    bot.send_message(message.chat.id, "📱 Отправьте скриншот QR кода из Max\n(Настройки → Устройства → QR код)", reply_markup=main_menu(user_id))

def show_info(message):
    bot.send_message(
        message.chat.id,
        "ℹ️ *Max Shop*\n\n🛍 Токены для Max\n💳 Оплата: CryptoBot\n⚡ Автовыдача\n📞 Поддержка: @support",
        parse_mode="Markdown",
        reply_markup=main_menu(message.from_user.id)
    )

# ==================== ОБРАБОТЧИК QR ====================
@bot.message_handler(content_types=['photo'])
def handle_qr_photo(message):
    user_id = message.from_user.id
    
    cursor = db.cursor()
    cursor.execute("SELECT status FROM login_sessions WHERE user_id = ? AND status = 'waiting_qr'", (user_id,))
    session = cursor.fetchone()
    
    if not session:
        bot.reply_to(message, "❌ Сначала нажмите '📱 Вход по QR'")
        return
    
    # Скачиваем и обрабатываем QR
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    temp_path = f"qr_{user_id}.jpg"
    with open(temp_path, 'wb') as f:
        f.write(downloaded_file)
    
    try:
        image = Image.open(temp_path)
        decoded_objects = decode(image)
        
        if decoded_objects:
            qr_data = decoded_objects[0].data.decode('utf-8')
            
            cursor.execute("UPDATE users SET max_account = ? WHERE user_id = ?", (qr_data, user_id))
            cursor.execute("UPDATE login_sessions SET status = 'completed' WHERE user_id = ?", (user_id,))
            db.commit()
            
            bot.reply_to(message, "✅ Вход выполнен успешно!\n🔐 Max аккаунт подключен", reply_markup=main_menu(user_id))
        else:
            bot.reply_to(message, "❌ QR код не найден\nОтправьте четкое фото")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ==================== CALLBACK ОБРАБОТЧИКИ ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    
    if call.data.startswith("buy_"):
        product_id = int(call.data.split("_")[1])
        process_purchase(call, product_id)
    
    elif call.data == "admin_add_product":
        bot.send_message(call.message.chat.id, "📦 Формат:\n/add_product Название | Описание | Цена | Тип | Токен1,Токен2,Токен3")
    
    elif call.data == "admin_products":
        show_admin_products(call)
    
    elif call.data == "admin_give_balance":
        bot.send_message(call.message.chat.id, "💰 Формат:\n/give_balance user_id сумма")
    
    elif call.data == "admin_stats":
        show_admin_stats(call)
    
    elif call.data.startswith("check_"):
        invoice_id = call.data.split("_")[1]
        check_payment_status(call, invoice_id)
    
    bot.answer_callback_query(call.id)

def process_purchase(call, product_id):
    cursor = db.cursor()
    cursor.execute("SELECT name, price, stock FROM products WHERE id = ? AND stock > 0", (product_id,))
    product = cursor.fetchone()
    
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар недоступен")
        return
    
    name, price, stock = product
    
    # Создаем инвойс CryptoBot
    invoice = crypto_api.create_invoice(amount=price, currency="USDT", description=f"Покупка: {name}")
    
    if invoice.get("ok"):
        payment_url = invoice["result"]["pay_url"]
        invoice_id = invoice["result"]["invoice_id"]
        
        cursor.execute(
            "INSERT INTO orders (user_id, product_id, amount, status, crypto_payment_id, created_date) VALUES (?, ?, ?, 'pending', ?, datetime('now'))",
            (call.from_user.id, product_id, price, invoice_id)
        )
        db.commit()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 Оплатить", url=payment_url))
        markup.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_{invoice_id}"))
        
        bot.send_message(
            call.message.chat.id,
            f"🛒 *Заказ создан*\n\n📦 {name}\n💵 {price} USDT\n🔑 ID: `{invoice_id}`\n\nНажмите 'Оплатить' 👇",
            parse_mode="Markdown",
            reply_markup=markup
        )
    else:
        bot.send_message(call.message.chat.id, "❌ Ошибка создания платежа")

def check_payment_status(call, invoice_id):
    payment = crypto_api.check_payment(invoice_id)
    
    if payment.get("ok") and payment["result"]["status"] == "paid":
        cursor = db.cursor()
        cursor.execute("SELECT id, user_id, product_id FROM orders WHERE crypto_payment_id = ? AND status = 'pending'", (invoice_id,))
        order = cursor.fetchone()
        
        if order:
            order_id, user_id, product_id = order
            success = deliver_product_auto(user_id, product_id, order_id)
            
            if success:
                bot.answer_callback_query(call.id, "✅ Токен выдан!")
                bot.send_message(call.message.chat.id, "🎉 Оплата получена! Токен отправлен выше 👆")
            else:
                bot.answer_callback_query(call.id, "⚠️ Ожидайте выдачи админом")
        else:
            bot.answer_callback_query(call.id, "✅ Уже обработан")
    else:
        bot.answer_callback_query(call.id, "⏳ Оплата еще не получена")

def show_admin_products(call):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, price, stock FROM products")
    products = cursor.fetchall()
    
    if not products:
        bot.send_message(call.message.chat.id, "📋 Нет товаров")
        return
    
    text = "📋 *Список товаров:*\n\n"
    for product in products:
        product_id, name, price, stock = product
        text += f"#{product_id} {name}\n💰 {price} USDT | 📦 {stock} шт.\n\n"
    
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

def show_admin_stats(call):
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM orders WHERE status = 'completed'")
    orders = cursor.fetchone()
    
    cursor.execute("SELECT SUM(stock) FROM products")
    total_stock = cursor.fetchone()[0] or 0
    
    bot.send_message(
        call.message.chat.id,
        f"📊 *Статистика*\n\n👥 Пользователей: {total_users}\n✅ Продаж: {orders[0]}\n💰 Выручка: {orders[1] or 0:.2f} USDT\n📦 Токенов: {total_stock}",
        parse_mode="Markdown"
    )

# ==================== ФОНОВАЯ ПРОВЕРКА ПЛАТЕЖЕЙ ====================
def check_payments_job():
    while True:
        try:
            cursor = db.cursor()
            cursor.execute("SELECT id, crypto_payment_id, user_id, product_id FROM orders WHERE status = 'pending'")
            pending_orders = cursor.fetchall()
            
            for order in pending_orders:
                order_id, payment_id, user_id, product_id = order
                
                payment_status = crypto_api.check_payment(payment_id)
                
                if payment_status.get("ok") and payment_status["result"]["status"] == "paid":
                    deliver_product_auto(user_id, product_id, order_id)
                    
        except Exception as e:
            print(f"Error checking payments: {e}")
        
        time.sleep(30)  # Проверка каждые 30 секунд

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("🤖 Max Shop Bot запущен!")
    
    # Фоновая проверка платежей
    payment_thread = threading.Thread(target=check_payments_job, daemon=True)
    payment_thread.start()
    
    # Запуск бота
    bot.infinity_polling()