import os
import logging
import asyncio # Animation ke liye zaroori hai
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

# --- 3. FLASK SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Alive"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. AI SETUP ---
genai.configure(api_key=GEMINI_API_KEY)
# Note: 1.5 Flash hi use karein, 2.5 abhi 500 Error de raha hai.
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
        avoid_msg = f"User rejected this style: '{previous_style}'. Create a COMPLETELY different vibe."
    
    prompt = (
        f"You are an expert modern aesthetic font designer for Gen-Z. "
        f"Transform the name '{name}' into a highly aesthetic, trendy, and stylish version. "
        f"Use unique unicode symbols, kaomoji, and decorative borders. "
        f"Style Examples (Vibe): ᯓ𓂃❛ 𝐒 𝛖 𝐝 ֟፝ᥱ 𝛆 𝛒 </𝟑 𝁘ໍ𝀛𓂃🍷 or 𓆩🖤𓆪 or ✦ ִ ֶ ָ 𓆝 𓆟 𓆞 "
        f"Strict Rules: \n"
        f"1. No old/clunky symbols.\n"
        f"2. Return ONLY the styled text.\n"
        f"3. {avoid_instruction}"
    )
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return "⚠️ Server Busy. Try Again."

# --- 6. HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Database me add karo
    if not users_collection.find_one({"_id": user.id}):
        users_collection.insert_one({"_id": user.id, "first_name": user.first_name})

    name = html.escape(user.first_name)
    txt = (
        f"👋 Hello <code>|• {name} ༄!</code>\n\n"
        f"<blockquote>Send me your name (e.g., Sudeep), and I will transform it into a Modern Aesthetic Style! ✨</blockquote>\n\n"
        f"<i>I use AI to create unique designs every time. Try me!</i>"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.message.text
    update_current_name(user_id, user_name)

    # 1. Pehle "Designing..." bhejo (User ko pata chale process shuru hua)
    msg = await update.message.reply_text("⚡ <b>Connecting to AI...</b>", parse_mode=ParseMode.HTML)
    
    # 2. Loading Animation (Chhota sa effect)
    await asyncio.sleep(0.5)
    await msg.edit_text("🎨 <b>Designing your masterpiece...</b>", parse_mode=ParseMode.HTML)

    # 3. AI Generate karega
    style = await generate_aesthetic_name(user_name)

    # 4. Result Edit karke dikhao (Naya message nahi banega)
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
        # --- LOADING BAR ANIMATION START ---
        # User ko lagega bot kuch heavy process kar raha hai
        try:
            await query.edit_message_text("▰▱▱▱▱▱▱▱▱▱ 10%")
            await asyncio.sleep(0.3) # Thoda rukna zaroori hai
            await query.edit_message_text("▰▰▰▰▱▱▱▱▱▱ 40%")
            await asyncio.sleep(0.3)
            await query.edit_message_text("▰▰▰▰▰▰▰▰▱▱ 80%")
        except Exception:
            pass # Agar user ne jaldi daba diya to error ignore karo
        # --- ANIMATION END ---

        original_name = get_user_current_name(user_id)
        if not original_name:
            await query.edit_message_text("❌ Session expired. Send name again.")
            return

        # Naya style generate karo
        new_style = await generate_aesthetic_name(original_name)
        
        buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next"),
                    InlineKeyboardButton("Copy Name 📋", callback_data="copy")]]
        
        # Final Result Show karo
        await query.edit_message_text(
            f"`{new_style}`", 
            parse_mode=ParseMode.MARKDOWN_V2, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data == "copy":
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
    
