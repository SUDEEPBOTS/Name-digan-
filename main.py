import os
import logging
from threading import Thread
from flask import Flask
import google.generativeai as genai
import pymongo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import html

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGO_URL = os.getenv("MONGO_URL")

# --- 2. DATABASE ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["NameStylerBot"]
    users_collection = db["users"]
    print("✅ MongoDB Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- 3. FLASK (UPTIME) ---
app = Flask('')
@app.route('/')
def home(): return "Alive"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. AI SETUP ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# --- 5. FUNCTIONS ---
def update_current_name(user_id, name):
    users_collection.update_one({"_id": user_id}, {"$set": {"current_name": name}}, upsert=True)

def get_user_current_name(user_id):
    u = users_collection.find_one({"_id": user_id})
    return u.get("current_name") if u else None

async def generate_aesthetic_name(name, previous_style=None):
    avoid_msg = ""
    if previous_style:
        avoid_msg = f"NOTE: User saw this style '{previous_style}', make it TOTALLY different now."
    
    prompt = (
        f"Design a highly aesthetic, trendy name for: '{name}'. "
        f"Use unique symbols, kaomoji, borders (e.g. ᯓ, 𓂃, 𓆩, 𓆪). "
        f"Return ONLY the styled text. No intro/outro. "
        f"{avoid_msg}"
    )
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return "⚠️ Server Busy. Try Again."

# --- 6. HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = html.escape(update.effective_user.first_name)
    txt = (
        f"👋 Hello <code>|• {name} ༄!</code>\n\n"
        f"<blockquote>Send me your name, and I will create a Modern Aesthetic Style for you! ✨</blockquote>"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.message.text
    update_current_name(user_id, user_name)

    # STEP 1: Pehle "Designing..." bhejo
    msg = await update.message.reply_text("✨ <b>Designing your name...</b>", parse_mode=ParseMode.HTML)
    
    # STEP 2: AI se style banwao
    style = await generate_aesthetic_name(user_name)

    # STEP 3: Usi message ko EDIT karo (Naya message nahi bhejega -> Clean Chat)
    buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next"),
                InlineKeyboardButton("Copy Name 📋", callback_data="copy")]]
    
    await msg.edit_text(
        f"`{style}`", 
        parse_mode=ParseMode.MARKDOWN_V2, 
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == "next":
        # Button dabate hi "Loading..." dikhao taaki user ko lage bot kaam kar raha hai
        await query.answer("Generating new style...") 
        await query.edit_message_text("🔄 <i>Creating new vibe...</i>", parse_mode=ParseMode.HTML)
        
        original_name = get_user_current_name(user_id)
        if not original_name:
            await query.edit_message_text("❌ Session expired. Send name again.")
            return

        # Naya style banao
        new_style = await generate_aesthetic_name(original_name)
        
        buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next"),
                    InlineKeyboardButton("Copy Name 📋", callback_data="copy")]]
        
        # Result show karo
        await query.edit_message_text(
            f"`{new_style}`", 
            parse_mode=ParseMode.MARKDOWN_V2, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data == "copy":
        # Copy Button ka sach: Telegram button se copy allow nahi karta via API
        # Isliye hum user ko sikha rahe hain ki text par tap kare
        await query.answer("⚠️ Button se copy nahi hota!\n👆 Upar Text par Tap karo, wo copy ho jayega.", show_alert=True)

# --- 7. RUN ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(button_click))
    app_bot.run_polling()

if __name__ == "__main__":
    main()
    
