import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import threading
import json
import os
import sys
from datetime import datetime

# --- LẤY CẤU HÌNH TỪ GITHUB SECRETS ---
try:
    SHOP_DOMAIN = os.environ["SHOP_DOMAIN"]
    USERNAME = os.environ["SHOP_USER"]
    PASSWORD = os.environ["SHOP_PASS"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    OWNER_ID = os.environ["OWNER_ID"]
except KeyError as e:
    print(f"❌ LỖI: Thiếu biến môi trường {e}. Hãy cài đặt trong GitHub Secrets!")
    sys.exit(1)

SCAN_INTERVAL = 60
DATA_FILE = "watchlist.json"

bot = telebot.TeleBot(BOT_TOKEN)

# --- DATABASE MANAGER ---
class DataManager:
    def __init__(self):
        self.watchlist = {}
        self.load_data()

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    self.watchlist = json.load(f)
            except:
                self.watchlist = {}
        else:
            self.watchlist = {}

    def save_data(self):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.watchlist, f, ensure_ascii=False, indent=2)

    def add_watch(self, product_id, name, price, threshold=0):
        self.watchlist[str(product_id)] = {
            "name": name,
            "threshold": int(threshold),
            "price": price,
            "last_alert": 0
        }
        self.save_data()

    def remove_watch(self, product_id):
        if str(product_id) in self.watchlist:
            del self.watchlist[str(product_id)]
            self.save_data()
            return True
        return False

db = DataManager()

# --- API SHOP ---
class ShopAPI:
    def get_all_data(self):
        url = f"{SHOP_DOMAIN}/api/ListResource.php?username={USERNAME}&password={PASSWORD}"
        try:
            return requests.get(url, timeout=20).json()
        except Exception as e:
            print(f"API Error: {e}")
            return None

    def get_product_by_id(self, p_id):
        data = self.get_all_data()
        if data and data.get('status') == 'success':
            for cat in data.get('categories', []):
                for item in cat.get('accounts', []):
                    if str(item['id']) == str(p_id):
                        return item
        return None

api = ShopAPI()

# --- MONITOR THREAD ---
def monitor_thread():
    print(">>> 🕵️ MONITOR SERVICE STARTED...")
    while True:
        try:
            if not db.watchlist:
                time.sleep(SCAN_INTERVAL)
                continue

            full_data = api.get_all_data()
            if not full_data or full_data.get('status') != 'success':
                time.sleep(SCAN_INTERVAL)
                continue

            stock_map = {}
            for cat in full_data.get('categories', []):
                for item in cat.get('accounts', []):
                    stock_map[str(item['id'])] = int(item['amount'])

            current_time = time.time()
            for p_id, config in db.watchlist.items():
                current_stock = stock_map.get(p_id, 0)
                threshold = config['threshold']
                
                is_alert = False
                if threshold == 0:
                     if current_stock > 0: is_alert = True
                else:
                     if current_stock >= threshold: is_alert = True

                # Cooldown 10 phút (600s)
                if is_alert and (current_time - config['last_alert'] > 600):
                    msg = (
                        f"🚨 <b>HÀNG VỀ: {config['name']}</b>\n"
                        f"🆔 ID: <code>{p_id}</code>\n"
                        f"📦 Tồn kho: <b>{current_stock}</b> (Yêu cầu: >{threshold})\n"
                        f"💰 Giá: {config['price']}đ"
                    )
                    try:
                        bot.send_message(OWNER_ID, msg, parse_mode='HTML')
                        db.watchlist[p_id]['last_alert'] = current_time
                        db.save_data()
                    except Exception as e:
                        print(f"Lỗi gửi tin: {e}")

            time.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"Lỗi Monitor Loop: {e}")
            time.sleep(10)

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start', 'menu'])
def main_menu(message):
    # Check quyền chủ nhân
    if str(message.chat.id) != str(OWNER_ID):
        return # Im lặng với người lạ

    data = api.get_all_data()
    if not data:
        bot.reply_to(message, "❌ Lỗi kết nối Shop.")
        return

    markup = InlineKeyboardMarkup()
    for cat in data.get('categories', []):
        markup.add(InlineKeyboardButton(f"📂 {cat['name']}", callback_data=f"cat_{cat['id']}"))

    markup.add(InlineKeyboardButton(f"📋 Watchlist ({len(db.watchlist)})", callback_data="view_watch"))
    bot.send_message(message.chat.id, "CHỌN DANH MỤC:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    # Logic giống hệt phiên bản cũ, chỉ copy lại phần xử lý nút bấm
    if call.data.startswith("cat_"):
        cat_id = call.data.split("_")[1]
        data = api.get_all_data()
        markup = InlineKeyboardMarkup()
        found = False
        
        for cat in data.get('categories', []):
            if str(cat['id']) == str(cat_id):
                found = True
                for item in cat.get('accounts', []):
                    icon = "🔴" if int(item['amount']) == 0 else "🟢"
                    btn_text = f"{icon} {item['name'][:25]}... | {item['amount']}"
                    markup.add(InlineKeyboardButton(btn_text, callback_data=f"prod_{item['id']}"))
                break
        
        markup.add(InlineKeyboardButton("🔙 Quay lại", callback_data="back_home"))
        if found:
            bot.edit_message_text("Chọn sản phẩm:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("prod_"):
        p_id = call.data.split("_")[1]
        item = api.get_product_by_id(p_id)
        if item:
            msg = f"📦 <b>{item['name']}</b>\n🆔 ID: <code>{item['id']}</code>\n💰 Giá: {item['price']}đ\n📊 Tồn: {item['amount']}"
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔔 THIẾT LẬP THEO DÕI", callback_data=f"setup_{p_id}"))
            markup.add(InlineKeyboardButton("🔙 Quay lại", callback_data=f"back_home"))
            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=markup)

    elif call.data.startswith("setup_"):
        p_id = call.data.split("_")[1]
        markup = InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(
            InlineKeyboardButton("Có là báo (>0)", callback_data=f"setthresh_{p_id}_0"),
            InlineKeyboardButton("> 10", callback_data=f"setthresh_{p_id}_10")
        )
        markup.add(
            InlineKeyboardButton("> 50", callback_data=f"setthresh_{p_id}_50"),
            InlineKeyboardButton("> 100", callback_data=f"setthresh_{p_id}_100")
        )
        markup.add(InlineKeyboardButton("✏️ Nhập số...", callback_data=f"setthresh_{p_id}_custom"))
        markup.add(InlineKeyboardButton("🔙 Hủy", callback_data="back_home"))
        bot.edit_message_text(f"📡 Báo động cho ID {p_id} khi:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("setthresh_"):
        _, p_id, val = call.data.split("_")
        if val == "custom":
            msg = bot.send_message(call.message.chat.id, "⌨️ Nhập số lượng tối thiểu:")
            bot.register_next_step_handler(msg, process_custom_threshold, p_id)
            return
        threshold = int(val)
        item = api.get_product_by_id(p_id)
        if item:
            db.add_watch(p_id, item['name'], item['price'], threshold)
            bot.answer_callback_query(call.id, "✅ Đã lưu!")
            main_menu(call.message)

    elif call.data == "view_watch":
        if not db.watchlist:
            bot.answer_callback_query(call.id, "Trống!")
            return
        markup = InlineKeyboardMarkup()
        for pid, conf in db.watchlist.items():
            btn_text = f"{conf['name'][:20]}... (> {conf['threshold']})"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"edit_{pid}"))
        markup.add(InlineKeyboardButton("🔙 Menu", callback_data="back_home"))
        bot.edit_message_text("📋 Danh sách đang theo dõi:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("edit_"):
        p_id = call.data.split("_")[1]
        if p_id in db.watchlist:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🗑 XÓA", callback_data=f"untrack_{p_id}"))
            markup.add(InlineKeyboardButton("✏️ Sửa ngưỡng", callback_data=f"setup_{p_id}"))
            markup.add(InlineKeyboardButton("🔙 Quay lại", callback_data="view_watch"))
            bot.edit_message_text(f"🔧 Cấu hình: {db.watchlist[p_id]['name']}", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("untrack_"):
        p_id = call.data.split("_")[1]
        db.remove_watch(p_id)
        bot.answer_callback_query(call.id, "Đã xóa!")
        call.data = "view_watch"
        callback_query(call)

    elif call.data == "back_home":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        main_menu(call.message)

def process_custom_threshold(message, p_id):
    try:
        val = int(message.text)
        item = api.get_product_by_id(p_id)
        if item:
            db.add_watch(p_id, item['name'], item['price'], val)
            bot.reply_to(message, f"✅ Đã lưu! Báo khi > {val}")
    except:
        bot.reply_to(message, "❌ Lỗi: Phải là số.")

# --- MAIN RUN ---
if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f: json.dump({}, f)

    t = threading.Thread(target=monitor_thread)
    t.daemon = True
    t.start()
    
    print(f">>> 🤖 BOT STARTED ON GITHUB ACTIONS (Owner: {OWNER_ID})")
    bot.infinity_polling()