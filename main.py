import os
import logging
from threading import Thread
from flask import Flask
import google.generativeai as genai
import pymongo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# --- 1. CONFIGURATION (ENV VARIABLES) ---
# Render ki settings me ye teeno zaroor dalna
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGO_URL = os.getenv("MONGO_URL")

# --- 2. DATABASE CONNECTION (MONGODB) ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["NameStylerBot"]
    users_collection = db["users"]
    print("✅ Connected to MongoDB successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {e}")

# --- 3. FLASK SERVER (FOR 24/7 UPTIME) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running! 🚀"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 4. GEMINI AI SETUP ---
genai.configure(api_key=GEMINI_API_KEY)

# Creativity settings: High temperature = Unique designs every time
generation_config = {
    "temperature": 1.1,
    "top_p": 0.95,
    "max_output_tokens": 100,
}

# Note: Agar 2.5 Flash available ho to 'gemini-2.5-flash' likh dena
model = genai.GenerativeModel('gemini-1.5-flash', generation_config=generation_config)

# --- 5. HELPER FUNCTIONS ---

def add_user(user_id, first_name):
    """User ko database me add karta hai (New User)"""
    if not users_collection.find_one({"_id": user_id}):
        users_collection.insert_one({
            "_id": user_id, 
            "first_name": first_name,
            "total_generations": 0
        })

def update_current_name(user_id, name_text):
    """User ka current name save karta hai"""
    users_collection.update_one(
        {"_id": user_id}, 
        {"$set": {"current_name": name_text}}, 
        upsert=True
    )

def get_user_current_name(user_id):
    """Database se saved name nikalta hai"""
    user = users_collection.find_one({"_id": user_id})
    return user.get("current_name") if user else None

async def generate_aesthetic_name(name: str, previous_style: str = None) -> str:
    """Gemini se name style karwata hai + Anti-Repeat Logic"""
    
    avoid_instruction = ""
    if previous_style:
        # Ye line AI ko purana style copy karne se rokegi
        avoid_instruction = f"IMPORTANT: The user rejected this style: '{previous_style}'. Do NOT make it similar. Create something COMPLETELY different."

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
        return "Error generating style. Try again."

# --- 6. BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.first_name)
    
    msg = (
        f"👋 **Hello {user.first_name}!**\n\n"
        "Send me your name (e.g., Sudeep), and I will transform it into a **Modern Aesthetic Style**! ✨\n\n"
        "I use AI to create unique designs every time. Try me!"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check users"""
    count = users_collection.count_documents({})
    await update.message.reply_text(f"📊 **Total Users:** {count}", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.message.text
    
    # Database me name save kiya
    update_current_name(user_id, user_name)
    
    await update.message.reply_text("✨ *Designing your name...*", parse_mode=ParseMode.MARKDOWN)
    
    styled_name = await generate_aesthetic_name(user_name)
    
    keyboard = [
        [InlineKeyboardButton("Next Style 🔄", callback_data="next"),
         InlineKeyboardButton("Copy Name 📋", callback_data="copy")]
    ]
    
    await update.message.reply_text(
        f"`{styled_name}`", 
        parse_mode=ParseMode.MARKDOWN_V2, 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer() # Loading animation stop
    
    if query.data == "next":
        # 1. Database se original naam nikala
        original_name = get_user_current_name(user_id)
        
        if not original_name:
            await query.edit_message_text("❌ Session expired. Please send the name again.")
            return

        # 2. Screen par jo abhi style hai, use grab kiya (taaki repeat na ho)
        # Message text me styled name backticks ke andar hoga, hum raw text le rahe hain
        current_style = query.message.text 

        # 3. New Style Generate kiya (Purana style avoid karke)
        new_style = await generate_aesthetic_name(original_name, previous_style=current_style)
        
        keyboard = [[InlineKeyboardButton("Next Style 🔄", callback_data="next"),
                     InlineKeyboardButton("Copy Name 📋", callback_data="copy")]]
        
        try:
            # Message edit kiya naye style ke sath
            await query.edit_message_text(
                f"`{new_style}`", 
                parse_mode=ParseMode.MARKDOWN_V2, 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass # Agar text exact same hua to Telegram error deta hai, ignore it.

    elif query.data == "copy":
        await query.answer("👆 Tap on the name above to copy it!", show_alert=True)

# --- 7. MAIN EXECUTION ---
def main():
    keep_alive() # Flask Server Start
    
    # Bot Setup
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers Add
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
  
