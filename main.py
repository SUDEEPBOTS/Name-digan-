import os
import logging
import asyncio
import traceback
from threading import Thread
from flask import Flask
import google.generativeai as genai
import pymongo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
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

# --- 3. FLASK SERVER (Keep Alive) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. KEY MANAGEMENT ---
def get_all_keys():
    """Returns list of all API keys"""
    docs = keys_collection.find({})
    return [doc['key'] for doc in docs]

def add_new_key(api_key):
    if not keys_collection.find_one({"key": api_key}):
        keys_collection.insert_one({"key": api_key})
        return True
    return False

def remove_key_by_index(index):
    """Deletes key based on its position (1, 2, 3...)"""
    all_keys = get_all_keys()
    if 0 <= index < len(all_keys):
        key_to_remove = all_keys[index]
        keys_collection.delete_one({"key": key_to_remove})
        return True, key_to_remove
    return False, None

# --- 5. AI GENERATION LOGIC ---
current_key_index = 0

async def generate_aesthetic_name(name, previous_style=None):
    global current_key_index
    api_keys = get_all_keys()
    
    if not api_keys:
        return "❌ Error: No API Keys found! Admin please add keys."

    prompt = (
        f"You are an expert aesthetic font designer. "
        f"Transform '{name}' into a unique, trendy, and stylish version. "
        f"Use cool unicode symbols, kaomoji, and borders. "
        f"Strict Rule: Return ONLY the styled text. No explanation."
    )
    
    if previous_style:
        prompt += f" Note: Do NOT generate this style again: {previous_style}"

    # Try different keys if one fails
    for _ in range(len(api_keys)):
        current_key_index = (current_key_index + 1) % len(api_keys)
        key_to_use = api_keys[current_key_index]
        
        try:
            genai.configure(api_key=key_to_use)
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = await asyncio.wait_for(
                model.generate_content_async(prompt), timeout=8.0
            )
            return response.text.strip()
        except Exception as e:
            print(f"⚠️ Key Error ({key_to_use[-5:]}): {e}")
            continue

    return "❌ Server Busy (All Keys Failed/Quota Exceeded)."

# --- Helper: Loading Bar ---
async def show_loading_bar(message):
    frames = [
        "<b>Generating...</b>\n▰▰▱▱▱▱▱▱▱▱ 20%",
        "<b>Designing...</b>\n▰▰▰▰▰▰▱▱▱▱ 60%",
        "<b>Finishing...</b>\n▰▰▰▰▰▰▰▰▰▱ 90%"
    ]
    try:
        for frame in frames:
            await message.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.4) 
    except BadRequest: pass 

# --- 6. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # --- ADMIN PANEL ---
    if str(user.id) == OWNER_ID:
        count = keys_collection.count_documents({})
        kb = [
            [InlineKeyboardButton("➕ Add Key", callback_data="admin_add"),
             InlineKeyboardButton("🗑️ Remove Key", callback_data="admin_remove")],
            [InlineKeyboardButton("👁️ View Keys", callback_data="admin_view")]
        ]
        await update.message.reply_text(
            f"👑 **Admin Panel**\n"
            f"🔑 Active Keys: `{count}`\n"
            f"Status: {'🟢 Online' if count > 0 else '🔴 No Keys'}",
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # --- NORMAL USER ---
    txt = f"👋 Hello {html.escape(user.first_name)}!\n\nSend me your name to get a style."
    await update.message.reply_text(txt)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    user_id = update.effective_user.id
    
    # Save name for 'Next' button
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"current_name": user_name}},
        upsert=True
    )

    msg = await update.message.reply_text("⚡ <b>Starting...</b>", parse_mode=ParseMode.HTML)
    await show_loading_bar(msg)

    style = await generate_aesthetic_name(user_name)

    buttons = [[InlineKeyboardButton("🔄 Next Style", callback_data="next")]]
    
    try:
        # <code> tag makes it clickable/copyable
        final_text = f"<code>{html.escape(style)}</code>"
        await msg.edit_text(final_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: {e}")

async def user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data.startswith("admin_"): return # Handled by ConversationHandler

    if query.data == "next":
        await query.answer("Cooking new style... 🍳")
        
        try:
            await query.edit_message_text("<b>Loading...</b>\n▰▰▰▰▰▱▱▱▱▱ 50%", parse_mode=ParseMode.HTML)
        except BadRequest: pass

        data = users_collection.find_one({"_id": query.from_user.id})
        if not data or "current_name" not in data:
            await query.edit_message_text("❌ Name expired. Send name again.")
            return
        
        original_name = data["current_name"]
        new_style = await generate_aesthetic_name(original_name)
        
        buttons = [[InlineKeyboardButton("🔄 Next Style", callback_data="next")]]
        
        try:
            final_text = f"<code>{html.escape(new_style)}</code>"
            await query.edit_message_text(final_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            # Fallback for edit fail
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"<code>{html.escape(new_style)}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

# --- ADMIN CONVERSATION ---

async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_add":
        await query.message.reply_text("📤 **Send the new API Key.**\n(Type /cancel to stop)")
        return ADDING_KEY
        
    elif data == "admin_remove":
        # Show keys with numbers
        keys = get_all_keys()
        if not keys:
            await query.message.reply_text("❌ No keys to delete.")
            return ConversationHandler.END
            
        msg = "🗑️ **Reply with the NUMBER to delete:**\n\n"
        for i, key in enumerate(keys):
            msg += f"{i+1}. `...{key[-5:]}`\n"
        
        await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return REMOVING_KEY

    elif data == "admin_view":
        keys = get_all_keys()
        msg = "🔑 **Active Keys:**\n\n"
        if keys:
            for i, key in enumerate(keys):
                msg += f"{i+1}. `...{key[-5:]}`\n"
        else:
            msg += "❌ No Keys Found."
            
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        
    elif data == "admin_back":
        await start(update, context)
        return ConversationHandler.END

async def save_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_text = update.message.text.strip()
    if add_new_key(key_text):
        await update.message.reply_text(f"✅ **Key Added!**\nEnding in: `...{key_text[-5:]}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("⚠️ Key already exists.")
    
    # Show main menu again
    await start(update, context)
    return ConversationHandler.END

async def delete_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("⚠️ Please send a NUMBER (e.g., 1). Try again or /cancel.")
        return REMOVING_KEY # Keep asking
        
    index = int(text) - 1 # Convert to 0-based index
    success, removed_key = remove_key_by_index(index)
    
    if success:
        await update.message.reply_text(f"🗑️ **Deleted Key:** `...{removed_key[-5:]}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Invalid Number. Key not found.")
        
    await start(update, context)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation Cancelled.")
    await start(update, context)
    return ConversationHandler.END

# --- MAIN RUNNER ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation Handler Setup
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_button_click, pattern="^admin_")],
        states={
            ADDING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_key_handler)],
            REMOVING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_key_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app_bot.add_handler(conv_handler)
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(user_button_click, pattern="^next"))

    print("✅ Bot is Running...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
    
