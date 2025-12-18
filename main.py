import os
import logging
import asyncio
import traceback  # Error detail dekhne ke liye add kiya
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
OWNER_ID = os.getenv("OWNER_ID") 

# States for Conversation
ADDING_KEY, REMOVING_KEY = range(2)

# --- 2. DATABASE CONNECTION ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["NameStylerBot"]
    users_collection = db["users"]
    keys_collection = db["api_keys"]
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

async def generate_aesthetic_name(name, previous_style=None):
    global current_key_index
    
    api_keys = get_all_keys()
    
    if not api_keys:
        return "❌ Error: No API Keys Found in DB."

    prompt = (
        f"Design a highly aesthetic, trendy name for: '{name}'. "
        f"Use unique symbols, kaomoji, borders (e.g. ᯓ, 𓂃, 𓆩, 𓆪). "
        f"Return ONLY the styled text. No intro/outro."
    )
    if previous_style:
        prompt += f" NOTE: Do NOT generate this style: '{previous_style}'."

    # Rotation Logic
    for _ in range(len(api_keys)):
        current_key_index = (current_key_index + 1) % len(api_keys)
        key_to_use = api_keys[current_key_index]
        
        try:
            # Configure specifically for this request
            genai.configure(api_key=key_to_use)
            model = genai.GenerativeModel('gemini-2.5-flash') # Model update kiya (fast version)

            response = await asyncio.wait_for(
                model.generate_content_async(prompt), timeout=10.0
            )
            return response.text.strip()

        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ Key Error ({key_to_use[-5:]}): {error_msg}") # Console log for debugging
            
            if "429" in error_msg or "400" in error_msg or "exhausted" in error_msg:
                continue 
            else:
                # Agar koi aur error hai toh traceback print karo
                traceback.print_exc()
                return f"❌ Error: {error_msg}"

    return "❌ All Keys Exhausted."

# --- 6. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id == OWNER_ID:
        total_keys = keys_collection.count_documents({})
        welcome_text = (
            f"👑 **Welcome Boss!**\n\n"
            f"🔑 **Total Keys:** `{total_keys}`\n"
            f"Select action:"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Add Key", callback_data="admin_add"),
             InlineKeyboardButton("🗑️ Remove Key", callback_data="admin_remove")],
            [InlineKeyboardButton("👁️ View Keys", callback_data="admin_view")]
        ]
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        return

    # User ko DB mein save karo
    users_collection.update_one(
        {"_id": user.id}, 
        {"$set": {"first_name": user.first_name}}, 
        upsert=True
    )

    name = html.escape(user.first_name)
    txt = f"👋 Hello <b>{name}</b>!\nSend me a name to style it."
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# --- ADMIN FUNCTIONS ---
async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_view":
        keys = get_all_keys()
        if not keys:
            await query.edit_message_text("📂 **No Keys Found.**")
            return
        msg = "🔑 **Active Keys:**\n\n"
        for i, key in enumerate(keys):
            msg += f"{i+1}. `...{key[-5:]}`\n"
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "admin_add":
        await query.message.reply_text("📤 Send new Gemini API Key.")
        return ADDING_KEY

    elif data == "admin_remove":
        await query.message.reply_text("🗑️ Send API Key to delete.")
        return REMOVING_KEY

    elif data == "admin_back":
        await start(update, context) # Wapas start function call karo

async def save_new_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_text = update.message.text.strip()
    if add_new_key(key_text):
        await update.message.reply_text(f"✅ Added: `...{key_text[-5:]}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("⚠️ Exists already.")
    return ConversationHandler.END

async def delete_old_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_text = update.message.text.strip()
    if remove_api_key(key_text):
        await update.message.reply_text("🗑️ Deleted.")
    else:
        await update.message.reply_text("❌ Not found.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# --- USER FUNCTIONS ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    user_id = update.effective_user.id

    # 1. IMPORTANT: Name ko DB mein save karo taaki 'Next' button kaam kare
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"current_name": user_name}},
        upsert=True
    )

    msg = await update.message.reply_text("🎨 <b>Designing...</b>", parse_mode=ParseMode.HTML)
    
    # 2. Generate Style
    style = await generate_aesthetic_name(user_name)

    if style.startswith("❌"):
        await msg.edit_text(style)
        return

    buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next")]]
    
    # 3. Safe sending (MarkdownV2 kabhi kabhi crash karta hai agar symbols galat ho)
    try:
        await msg.edit_text(f"`{style}`", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        # Agar Markdown error aaye toh normal text bhej do
        await msg.edit_text(f"{style}", reply_markup=InlineKeyboardMarkup(buttons))

async def user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Check karo ye admin button toh nahi hai
    if query.data.startswith("admin_"):
        return # Ise ConversationHandler handle karega

    if query.data == "next":
        try:
            await query.answer("Generating...")
        except: pass

        # DB se purana naam nikalo
        user_data = users_collection.find_one({"_id": query.from_user.id})
        original_name = user_data.get("current_name") if user_data else None

        if not original_name:
            await query.edit_message_text("❌ Session Expired. Send name again.")
            return

        # Naya style generate karo (purana style avoid karke)
        current_text = query.message.text
        new_style = await generate_aesthetic_name(original_name, previous_style=current_text)
        
        buttons = [[InlineKeyboardButton("Next Style 🔄", callback_data="next")]]
        
        try:
            await query.edit_message_text(f"`{new_style}`", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(buttons))
        except:
            await query.edit_message_text(f"{new_style}", reply_markup=InlineKeyboardMarkup(buttons))

# --- 7. RUN ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Admin Conversation
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
    app_bot.add_handler(CallbackQueryHandler(user_button_click, pattern="^next"))

    print("Bot Started...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
        
