import os
import logging
import asyncio 
from threading import Thread
from flask import Flask
import google.generativeai as genai
import pymongo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    CallbackQueryHandler, ContextTypes, ConversationHandler
)
import html

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = os.getenv("OWNER_ID") # Apna Telegram ID daalna zaroori hai!

# States for Conversation (Add/Remove Key)
ADDING_KEY, REMOVING_KEY = range(2)

# --- 2. DATABASE CONNECTION ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["NameStylerBot"]
    users_collection = db["users"]
    keys_collection = db["api_keys"] # Naya folder API Keys ke liye
    print("✅ MongoDB Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- 3. FLASK SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. KEY MANAGEMENT LOGIC ---
def get_all_keys():
    """DB se saari keys list lata hai"""
    docs = keys_collection.find({})
    return [doc['key'] for doc in docs]

def add_new_key(api_key):
    if not keys_collection.find_one({"key": api_key}):
        keys_collection.insert_one({"key": api_key})
        return True
    return False

def remove_api_key(api_key):
    result = keys_collection.delete_one({"key": api_key})
    return result.deleted_count > 0

# --- 5. AI GENERATION LOGIC (ROTATION) ---
current_key_index = 0

async def generate_aesthetic_name(name, app_instance, previous_style=None):
    global current_key_index
    
    # DB se Taaza Keys lo
    api_keys = get_all_keys()
    
    if not api_keys:
        return "❌ No API Keys Found! Owner please add keys."

    prompt = (
        f"Design a highly aesthetic, trendy name for: '{name}'. "
        f"Use unique symbols, kaomoji, borders (e.g. ᯓ, 𓂃, 𓆩, 𓆪). "
        f"Return ONLY the styled text. No intro/outro."
    )
    if previous_style:
        prompt += f" NOTE: Do NOT generate this style: '{previous_style}'."

    # Rotation Logic
    for _ in range(len(api_keys)):
        # Key select karo
        current_key_index = (current_key_index + 1) % len(api_keys)
        key_to_use = api_keys[current_key_index]
        
        genai.configure(api_key=key_to_use)
        model = genai.GenerativeModel('gemini-2.5-flash')

        try:
            response = await asyncio.wait_for(
                model.generate_content_async(prompt), timeout=10.0
            )
            return response.text.strip()

        except Exception as e:
            error_msg = str(e)
            # Agar Rate Limit ya Quota ka issue hai to next key try karega
            if "429" in error_msg or "400" in error_msg or "exhausted" in error_msg:
                print(f"⚠️ Key ending in ...{key_to_use[-5:]} Exhausted. Switching...")
                continue # Loop next key par jayega
            else:
                return f"❌ Error: {error_msg}"

    return "❌ All Keys Exhausted. Owner needs to add more."

# --- 6. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    # --- ADMIN PANEL (SIRF OWNER KE LIYE) ---
    if user_id == OWNER_ID:
        total_keys = keys_collection.count_documents({})
        
        welcome_text = (
            f"👑 **Welcome Boss!**\n\n"
            f"🤖 **Bot Status:** Active\n"
            f"🔑 **Total API Keys:** `{total_keys}`\n\n"
            f"Select an action below:"
        )
        
        keyboard = [
            [InlineKeyboardButton("➕ Add API Key", callback_data="admin_add"),
             InlineKeyboardButton("🗑️ Remove Key", callback_data="admin_remove")],
            [InlineKeyboardButton("👁️ View All Keys", callback_data="admin_view")]
        ]
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        return

    # --- NORMAL USER WELCOME ---
    try:
        if not users_collection.find_one({"_id": user.id}):
            users_collection.insert_one({"_id": user.id, "first_name": user.first_name})
    except: pass

    name = html.escape(user.first_name)
    txt = (
        f"👋 Hello <code>|• {name} ༄!</code>\n\n"
        f"<blockquote>Send me your name, and I will create a Modern Aesthetic Style! ✨</blockquote>"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# --- ADMIN FUNCTIONS ---

async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_view":
        keys = get_all_keys()
        if not keys:
            await query.edit_message_text("📂 **No Keys Found.** Add some first!")
            return
        
        msg = "🔑 **Active API Keys:**\n\n"
        for i, key in enumerate(keys):
            # Key ko hide karke dikhayenge security ke liye (Last 4 digits only)
            masked_key = f"xxxx...{key[-5:]}"
            msg += f"{i+1}. `{masked_key}`\n"
        
        # Wapas Main Menu jane ka button
        kb = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_back")]]
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "admin_add":
        await query.message.reply_text("📤 **Send me the new Gemini API Key now.**\nType /cancel to stop.")
        return ADDING_KEY

    elif data == "admin_remove":
        await query.message.reply_text("🗑️ **Send me the API Key you want to delete.**\nType /cancel to stop.")
        return REMOVING_KEY

    elif data == "admin_back":
        # Wapas Start wala panel dikhao
        total_keys = keys_collection.count_documents({})
        welcome_text = (
            f"👑 **Admin Panel**\n"
            f"🔑 **Total API Keys:** `{total_keys}`"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Add API Key", callback_data="admin_add"),
             InlineKeyboardButton("🗑️ Remove Key", callback_data="admin_remove")],
            [InlineKeyboardButton("👁️ View All Keys", callback_data="admin_view")]
        ]
        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def save_new_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_text = update.message.text.strip()
    
    if add_new_key(key_text):
        await update.message.reply_text(f"✅ **Success:** Key ending in `...{key_text[-5:]}` added!")
    else:
        await update.message.reply_text("⚠️ This key already exists in database.")
    
    return ConversationHandler.END

async def delete_old_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_text = update.message.text.strip()
    
    if remove_api_key(key_text):
        await update.message.reply_text(f"🗑️ **Deleted:** Key ending in `...{key_text[-5:]}` removed.")
    else:
        await update.message.reply_text("❌ Key not found in database.")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation Cancelled.")
    return ConversationHandler.END

# --- USER FUNCTIONS (SAME AS BEFORE) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    msg = await update.message.reply_text("⚡ <b>Connecting to AI...</b>", parse_mode=ParseMode.HTML)
    await asyncio.sleep(0.5)
    await msg.edit_text("🎨 <b>Designing your masterpiece...</b>", parse_mode=ParseMode.HTML)

    style = await generate_aesthetic_name(user_name, context.application)

    if "Error" in style or "Timeout" in style:
        await msg.edit_text(f"⚠️ {style}")
        return

    buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next")]]
    await msg.edit_text(f"`{style}`", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(buttons))

async def user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "next":
        try:
            await query.edit_message_text("▰▱▱▱▱▱▱▱▱▱ 10%")
            await asyncio.sleep(0.2)
            await query.edit_message_text("▰▰▰▰▰▰▰▰▱▱ 80%")
        except: pass

        original_name = db.users.find_one({"_id": query.from_user.id}).get("current_name")
        if not original_name:
            await query.edit_message_text("❌ Expired.")
            return

        new_style = await generate_aesthetic_name(original_name, context.application)
        buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next")]]
        await query.edit_message_text(f"`{new_style}`", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(buttons))


# --- 7. RUN ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation Handler for Adding/Removing Keys
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_button_click, pattern="^admin_")],
        states={
            ADDING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_new_key)],
            REMOVING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_old_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app_bot.add_handler(conv_handler)
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(user_button_click, pattern="^next")) # Sirf user wale buttons

    app_bot.run_polling()

if __name__ == "__main__":
    main()
        
