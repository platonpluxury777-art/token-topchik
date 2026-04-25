import os
import sys
import json
import time
import threading
import sqlite3
from io import BytesIO
import requests
import telebot
from telebot import types

# Используем OpenCV вместо pyzbar
try:
    import cv2
    import numpy as np
except ImportError:
    os.system(f'{sys.executable} -m pip install opencv-python-headless numpy')
    import cv2
    import numpy as np

from PIL import Image

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv('BOT_TOKEN', "8747779948:AAGXTBuDOmhM_X7dPBMRGUFCiL5Qj2_1wv0")
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN', "569144:AAs82ABvMXw8uTlYYfIrZOMWZA5C7bYhfdr")
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS', "105635005").split(',')]
DATA_DIR = os.getenv('DATA_DIR', '/app/data')

os.makedirs(DATA_DIR, exist_ok=True)
bot = telebot.TeleBot(BOT_TOKEN)

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = os.path.join(DATA_DIR, 'shop.db')

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT, 
            balance REAL DEFAULT 0,
            max_account TEXT,
            registered_date TEXT
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            price REAL,
            token_type TEXT,
            stock INTEGER DEFAULT 0,
            tokens TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending',
            crypto_payment_id TEXT,
            delivered_token TEXT,
            created_date TEXT
        );
        CREATE TABLE IF NOT EXISTS login_sessions (
            user_id INTEGER,
            qr_data TEXT,
            status TEXT,
            created_time REAL
        );
    ''')
    conn.commit()
    conn.close()

# ==================== QR КОД (OpenCV вместо pyzbar) ====================
def decode_qr(image_path):
    """Декодирование QR кода через OpenCV"""
    try:
        # Читаем изображение
        img = cv2.imread(image_path)
        
        # Создаем детектор QR
        detector = cv2.QRCodeDetector()
        
        # Декодируем
        data, bbox, _ = detector.detectAndDecode(img)
        
        if data:
            return {"success": True, "qr_data": data}
        else:
            return {"success": False, "message": "QR код не найден"}
    except Exception as e:
        return {"success": False, "message": str(e)}

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

# ==================== АВТОВЫДАЧА ТОВАРА ====================
def get_available_token(product_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT tokens, stock FROM products WHERE id = ? AND stock > 0", (product_id,))
    product = cursor.fetchone()
    
    if not product:
        conn.close()
        return None
    
    try:
        tokens = json.loads(product['tokens']) if product['tokens'] else []
        if tokens and len(tokens) > 0:
            token = tokens.pop(0)
            cursor.execute(
                "UPDATE products SET tokens = ?, stock = ? WHERE id = ?",
                (json.dumps(tokens), len(tokens), product_id)
            )
            conn.commit()
            conn.close()
            return token
    except:
        pass
    
    conn.close()
    return None

def deliver_product_auto(user_id, product_id, order_id):
    conn = get_db()
    cursor = conn.cursor()
    
    token = get_available_token(product_id)
    
    if token:
        cursor.execute(
            "UPDATE orders SET status = 'completed', delivered_token = ? WHERE id = ?",
            (token, order_id)
        )
        conn.commit()
        
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        product_name = product['name'] if product else "Товар"
        
        bot.send_message(
            user_id,
            f"🎉 *Заказ #{order_id} выполнен!*\n\n"
            f"📦 Товар: {product_name}\n"
            f"🔑 Ваш токен: `{token}`\n\n"
            f"⚠️ Сохраните токен!",
            parse_mode="Markdown"
        )
        
        for admin_id in ADMIN_IDS:
            bot.send_message(admin_id, f"✅ Автовыдача заказа #{order_id}\nПользователь: {user_id}\nТокен: {token}")
        
        conn.close()
        return True
    else:
        cursor.execute("UPDATE orders SET status = 'paid_no_stock' WHERE id = ?", (order_id,))
        conn.commit()
        
        bot.send_message(user_id, f"⚠️ Заказ #{order_id} оплачен! Админ выдаст токен вручную.")
        
        for admin_id in ADMIN_IDS:
            bot.send_message(admin_id, f"🚨 Нет токенов! Заказ #{order_id}, пользователь: {user_id}")
        
        conn.close()
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

# ==================== ОБРАБОТЧИКИ ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, registered_date) VALUES (?, ?, datetime('now'))", 
                   (user_id, username))
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, 
                    "👋 *Добро пожаловать в Max Shop!*\n\n"
                    "🎉 Магазин токенов для Max\n"
                    "💳 Оплата: CryptoBot\n"
                    "⚡ Автовыдача токенов",
                    parse_mode="Markdown",
                    reply_markup=main_menu(user_id))

@bot.message_handler(commands=['add_product'])
def add_product_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        args = message.text.replace('/add_product ', '').split('|')
        name = args[0].strip()
        description = args[1].strip()
        price = float(args[2].strip())
        token_type = args[3].strip()
        tokens_list = [t.strip() for t in args[4].strip().split(',')]
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (name, description, price, token_type, stock, tokens) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, price, token_type, len(tokens_list), json.dumps(tokens_list))
        )
        conn.commit()
        conn.close()
        
        bot.reply_to(message, f"✅ Товар '{name}' добавлен!\n📦 Токенов: {len(tokens_list)} шт.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")

@bot.message_handler(content_types=['photo'])
def handle_qr_photo(message):
    user_id = message.from_user.id
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM login_sessions WHERE user_id = ? AND status = 'waiting_qr'", (user_id,))
    session = cursor.fetchone()
    
    if not session:
        bot.reply_to(message, "❌ Сначала нажмите '📱 Вход по QR'")
        conn.close()
        return
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    temp_path = os.path.join(DATA_DIR, f"qr_{user_id}.jpg")
    with open(temp_path, 'wb') as f:
        f.write(downloaded_file)
    
    result = decode_qr(temp_path)
    
    if result["success"]:
        cursor.execute("UPDATE users SET max_account = ? WHERE user_id = ?", (result["qr_data"], user_id))
        cursor.execute("UPDATE login_sessions SET status = 'completed' WHERE user_id = ?", (user_id,))
        conn.commit()
        bot.reply_to(message, "✅ Вход выполнен успешно!", reply_markup=main_menu(user_id))
    else:
        bot.reply_to(message, f"❌ Ошибка: {result['message']}")
    
    if os.path.exists(temp_path):
        os.remove(temp_path)
    
    conn.close()

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    
    if message.text == "🛍 Магазин":
        shop(message)
    elif message.text == "💰 Баланс":
        balance(message)
    elif message.text == "📱 Вход по QR":
        qr_login(message)
    elif message.text == "🔧 Админ" and user_id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🎛 *Админ-панель*", parse_mode="Markdown", reply_markup=admin_menu())
    elif message.text == "ℹ️ Инфо":
        bot.send_message(message.chat.id, "ℹ️ *Max Shop*\n\nТокены для Max\nОплата: CryptoBot\nАвтовыдача", 
                        parse_mode="Markdown", reply_markup=main_menu(user_id))

def shop(message):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, price, stock FROM products WHERE stock > 0")
    products = cursor.fetchall()
    
    if not products:
        bot.send_message(message.chat.id, "😔 Нет доступных товаров", reply_markup=main_menu(message.from_user.id))
        conn.close()
        return
    
    for product in products:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"Купить за {product['price']} USDT", callback_data=f"buy_{product['id']}"))
        
        bot.send_message(
            message.chat.id,
            f"📦 *{product['name']}*\n📝 {product['description']}\n💵 Цена: {product['price']} USDT\n📦 В наличии: {product['stock']} шт.",
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    conn.close()

def balance(message):
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, max_account FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    
    if user:
        status = "✅ Подключен" if user['max_account'] else "❌ Не подключен"
        bot.send_message(message.chat.id, f"💰 Баланс: {user['balance']:.2f} USDT\n🔐 Max: {status}", 
                        reply_markup=main_menu(user_id))
    
    conn.close()

def qr_login(message):
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO login_sessions (user_id, status, created_time) VALUES (?, 'waiting_qr', ?)", 
                   (user_id, time.time()))
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, "📱 Отправьте скриншот QR кода из Max", reply_markup=main_menu(user_id))

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data.startswith("buy_"):
        product_id = int(call.data.split("_")[1])
        process_purchase(call, product_id)
    elif call.data == "admin_add_product":
        bot.send_message(call.message.chat.id, "📦 Формат:\n/add_product Название | Описание | Цена | Тип | Токен1,Токен2")
    elif call.data == "admin_products":
        show_admin_products(call)
    elif call.data == "admin_stats":
        show_admin_stats(call)
    elif call.data.startswith("check_"):
        invoice_id = call.data.split("_")[1]
        check_payment(call, invoice_id)
    
    bot.answer_callback_query(call.id)

def process_purchase(call, product_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, stock FROM products WHERE id = ? AND stock > 0", (product_id,))
    product = cursor.fetchone()
    
    if not product:
        bot.answer_callback_query(call.id, "❌ Товар недоступен")
        conn.close()
        return
    
    invoice = crypto_api.create_invoice(amount=product['price'], currency="USDT", description=f"Покупка: {product['name']}")
    
    if invoice.get("ok"):
        invoice_id = invoice["result"]["invoice_id"]
        
        cursor.execute(
            "INSERT INTO orders (user_id, product_id, amount, status, crypto_payment_id, created_date) VALUES (?, ?, ?, 'pending', ?, datetime('now'))",
            (call.from_user.id, product_id, product['price'], invoice_id)
        )
        conn.commit()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 Оплатить", url=invoice["result"]["pay_url"]))
        markup.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_{invoice_id}"))
        
        bot.send_message(call.message.chat.id,
                        f"🛒 *Заказ создан*\n\n📦 {product['name']}\n💵 {product['price']} USDT\n🔑 ID: `{invoice_id}`",
                        parse_mode="Markdown", reply_markup=markup)
    
    conn.close()

def check_payment(call, invoice_id):
    payment = crypto_api.check_payment(invoice_id)
    
    if payment.get("ok") and payment["result"]["status"] == "paid":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, product_id FROM orders WHERE crypto_payment_id = ? AND status = 'pending'", (invoice_id,))
        order = cursor.fetchone()
        
        if order:
            success = deliver_product_auto(order['user_id'], order['product_id'], order['id'])
            if success:
                bot.answer_callback_query(call.id, "✅ Токен выдан!")
            else:
                bot.answer_callback_query(call.id, "⚠️ Ожидайте админа")
        
        conn.close()
    else:
        bot.answer_callback_query(call.id, "⏳ Оплата не получена")

def show_admin_products(call):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, stock FROM products")
    products = cursor.fetchall()
    
    if not products:
        bot.send_message(call.message.chat.id, "📋 Нет товаров")
    else:
        text = "📋 *Товары:*\n\n"
        for p in products:
            text += f"#{p['id']} {p['name']}\n💰 {p['price']} USDT | 📦 {p['stock']} шт.\n\n"
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
    
    conn.close()

def show_admin_stats(call):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    users = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) as cnt, SUM(amount) as total FROM orders WHERE status = 'completed'")
    orders = cursor.fetchone()
    
    bot.send_message(call.message.chat.id,
                    f"📊 *Статистика*\n\n👥 Пользователей: {users}\n✅ Продаж: {orders['cnt']}\n💰 Выручка: {orders['total'] or 0:.2f} USDT",
                    parse_mode="Markdown")
    conn.close()

# ==================== ФОНОВАЯ ПРОВЕРКА ====================
def payment_checker():
    while True:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT id, crypto_payment_id, user_id, product_id FROM orders WHERE status = 'pending'")
            orders = cursor.fetchall()
            
            for order in orders:
                payment = crypto_api.check_payment(order['crypto_payment_id'])
                if payment.get("ok") and payment["result"]["status"] == "paid":
                    deliver_product_auto(order['user_id'], order['product_id'], order['id'])
            
            conn.close()
        except Exception as e:
            print(f"Payment check error: {e}")
        
        time.sleep(30)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    init_db()
    print("🤖 Max Shop Bot запущен!")
    
    threading.Thread(target=payment_checker, daemon=True).start()
    bot.infinity_polling()
