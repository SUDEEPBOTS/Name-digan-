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

# States
ADDING_KEY, REMOVING_KEY = range(2)

# --- 2. DATABASE ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["NameStylerBot"]
    users_collection = db["users"]
    keys_collection = db["api_keys"]
    print("✅ MongoDB Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- 3. FLASK (Keep Alive) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Alive!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. KEY LOGIC ---
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

# --- 5. AI GENERATION ---
current_key_index = 0

async def generate_aesthetic_name(name, previous_style=None):
    global current_key_index
    api_keys = get_all_keys()
    
    if not api_keys:
        return "❌ Error: No API Keys. Owner add keys /start"

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

    if previous_style:
        prompt += f" Don't give this style again: {previous_style}"

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
            print(f"⚠️ Key Error: {e}")
            continue

    return "❌ Server Busy. Try later."

# --- Helper: Loading Bar Animation ---
async def show_loading_bar(message):
    """
    Ye function message ko edit karke loading bar dikhayega.
    Rate limit se bachne ke liye hum sirf 2 frames dikhayenge.
    """
    frames = [
        "<b>Generating...</b>\n▰▰▱▱▱▱▱▱▱▱ 20%",
        "<b>Designing...</b>\n▰▰▰▰▰▰▱▱▱▱ 60%",
        "<b>Finishing...</b>\n▰▰▰▰▰▰▰▰▰▱ 90%"
    ]
    try:
        for frame in frames:
            await message.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5) # Thoda wait taaki animation dikhe
    except BadRequest:
        pass # Agar user ne delete kar diya ya edit fail hua to ignore karo

# --- 6. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) == OWNER_ID:
        count = keys_collection.count_documents({})
        kb = [
            [InlineKeyboardButton("➕ Add Key", callback_data="admin_add"),
             InlineKeyboardButton("🗑️ Remove", callback_data="admin_remove")],
            [InlineKeyboardButton("👁️ View Keys", callback_data="admin_view")]
        ]
        await update.message.reply_text(f"👑 **Admin Panel**\nKeys: `{count}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    txt = f"👋 Hello {html.escape(user.first_name)}!\n\nSend me your name to get a style."
    await update.message.reply_text(txt)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    user_id = update.effective_user.id
    
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"current_name": user_name}},
        upsert=True
    )

    # 1. Pehla message bhejo
    msg = await update.message.reply_text("⚡ <b>Starting...</b>", parse_mode=ParseMode.HTML)
    
    # 2. Loading Animation aur AI Generation ko ek saath (Parallel) chalao
    # Hum animation pehle chalayenge thoda sa
    await show_loading_bar(msg)

    # 3. AI se result mango
    style = await generate_aesthetic_name(user_name)

    # 4. Result Send (Click-to-Copy wala <code> tag)
    buttons = [[InlineKeyboardButton("🔄 Next Style", callback_data="next")]]
    
    try:
        # <code> tag use karne se click-to-copy banta hai
        final_text = f"<code>{html.escape(style)}</code>"
        await msg.edit_text(final_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: {e}")

async def user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data.startswith("admin_"): return

    if query.data == "next":
        await query.answer("Cooking new style... 🍳")
        
        # 1. Loading dikhao wapas usi message mein
        try:
             # Sirf ek simple loading dikhayenge taaki fast ho
            await query.edit_message_text("<b>Loading...</b>\n▰▰▰▰▰▱▱▱▱▱ 50%", parse_mode=ParseMode.HTML)
        except BadRequest: pass

        # 2. Data fetch
        data = users_collection.find_one({"_id": query.from_user.id})
        if not data or "current_name" not in data:
            await query.edit_message_text("❌ Name expired. Send name again.")
            return
        
        original_name = data["current_name"]

        # 3. Generate
        new_style = await generate_aesthetic_name(original_name)
        
        # 4. Result Update
        buttons = [[InlineKeyboardButton("🔄 Next Style", callback_data="next")]]
        
        try:
            final_text = f"<code>{html.escape(new_style)}</code>"
            await query.edit_message_text(final_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            # Fallback agar edit fail ho
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"<code>{html.escape(new_style)}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

# --- ADMIN HANDLERS ---
async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_add":
        await query.message.reply_text("Send API Key now.")
        return ADDING_KEY
    elif data == "admin_remove":
        await query.message.reply_text("Send Key to delete.")
        return REMOVING_KEY
    elif data == "admin_view":
        keys = get_all_keys()
        msg = "\n".join([f"`...{k[-5:]}`" for k in keys]) if keys else "No Keys."
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
    elif data == "admin_back":
        await start(update, context)

async def save_key(update, context):
    add_new_key(update.message.text.strip())
    await update.message.reply_text("✅ Key Added!")
    return ConversationHandler.END

async def del_key(update, context):
    remove_api_key(update.message.text.strip())
    await update.message.reply_text("🗑️ Key Deleted!")
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Cancelled")
    return ConversationHandler.END

# --- MAIN ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_button_click, pattern="^admin_")],
        states={
            ADDING_KEY: [MessageHandler(filters.TEXT, save_key)],
            REMOVING_KEY: [MessageHandler(filters.TEXT, del_key)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app_bot.add_handler(conv)
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(user_button_click, pattern="^next"))

    print("Bot Running with Animation...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()

