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
from telegram.error import BadRequest, Forbidden
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
ADDING_KEY, BROADCASTING = range(2)

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

# --- 4. SIMPLE SINGLE KEY LOGIC ---
def get_active_key():
    """Sirf ek hi key layega jo DB mein hai"""
    doc = keys_collection.find_one({})
    return doc['key'] if doc else None

def set_new_key(api_key):
    """Purani key uda kar nayi set karega (Auto-Switch)"""
    keys_collection.delete_many({}) # Purani saari delete
    keys_collection.insert_one({"key": api_key}) # Nayi add
    return True

def delete_current_key():
    keys_collection.delete_many({})
    return True

# --- 5. AI GENERATION (WITH OWNER ALERT) ---
async def generate_content(prompt_text, bot_instance):
    api_key = get_active_key()
    
    if not api_key:
        return "❌ Error: No API Key set. Waiting for Owner."

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = await asyncio.wait_for(
            model.generate_content_async(prompt_text), timeout=10.0
        )
        return response.text.strip()

    except Exception as e:
        error_msg = str(e).lower()
        
        # --- OWNER NOTIFICATION LOGIC ---
        # Agar Quota khatam hua ya Rate Limit aayi
        if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
            print("⚠️ Limit Reached! Notifying Owner...")
            try:
                await bot_instance.send_message(
                    chat_id=OWNER_ID,
                    text=(
                        "🚨 **URGENT ALERT: API KEY EXPIRED** 🚨\n\n"
                        "Google API ki limit khatam ho gayi hai.\n"
                        "Users ko error aa raha hai.\n\n"
                        "👉 **Turant /start dabakar nayi Key Add karein.**"
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as notify_error:
                print(f"Failed to notify owner: {notify_error}")
            
            return "❌ Server Busy (Limit Reached). Owner notified."
        
        # Other Errors
        print(f"⚠️ API Error: {e}")
        return "❌ Error: Server Issue. Try again."

# --- Helper: Loading Bar ---
async def show_loading_bar(message, text="Generating"):
    frames = [
        f"<b>{text}...</b>\n▰▰▱▱▱▱▱▱▱▱ 20%",
        f"<b>{text}...</b>\n▰▰▰▰▰▰▱▱▱▱ 60%",
        f"<b>{text}...</b>\n▰▰▰▰▰▰▰▰▰▱ 90%"
    ]
    try:
        for frame in frames:
            await message.edit_text(frame, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.3) 
    except BadRequest: pass 

# --- 6. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Save User
    users_collection.update_one(
        {"_id": user.id},
        {"$set": {"first_name": user.first_name, "username": user.username}},
        upsert=True
    )

    # --- ADMIN PANEL (SIMPLIFIED) ---
    if str(user.id) == OWNER_ID:
        current_key = get_active_key()
        status = "🟢 Active" if current_key else "🔴 Missing"
        masked_key = f"...{current_key[-5:]}" if current_key else "None"
        
        user_count = users_collection.count_documents({})
        
        welcome_text = (
            f"👑 **Admin Control (Single Key)**\n\n"
            f"🔑 **Current Key:** `{masked_key}`\n"
            f"📊 **Status:** {status}\n"
            f"👥 **Users:** `{user_count}`\n\n"
            f"👇 **Actions:**"
        )
        
        kb = [
            [InlineKeyboardButton("🔄 Replace/Set Key", callback_data="admin_add")],
            [InlineKeyboardButton("🗑️ Delete Key", callback_data="admin_remove")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
        ]
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    # --- NORMAL USER ---
    txt = (
        f"👋 Hello {html.escape(user.first_name)}!\n\n"
        f"🔹 **Send any name** to generate stylish fonts.\n"
        f"🔹 Use `/bio <text>` to generate bio.\n"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def bio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = " ".join(context.args)
    if not user_text:
        await update.message.reply_text("⚠️ Usage: `/bio I love coding`")
        return

    msg = await update.message.reply_text("📝 <b>Writing...</b>", parse_mode=ParseMode.HTML)
    prompt = f"Write a short aesthetic bio for: '{user_text}'. Keep it under 3 lines. No intro."
    
    # Bot instance pass kar rahe hain notification ke liye
    result = await generate_content(prompt, context.bot)
    
    try:
        await msg.edit_text(f"<code>{html.escape(result)}</code>", parse_mode=ParseMode.HTML)
    except:
        await msg.edit_text(result)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    user_id = update.effective_user.id
    
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"current_name": user_name, "current_style": "random"}},
        upsert=True
    )

    keyboard = [
        [InlineKeyboardButton("🖤 Dark", callback_data="style_dark"),
         InlineKeyboardButton("🌸 Cute", callback_data="style_cute")],
        [InlineKeyboardButton("👾 Glitch", callback_data="style_glitch"),
         InlineKeyboardButton("🏳️ Minimal", callback_data="style_minimal")],
        [InlineKeyboardButton("🎲 Random", callback_data="style_random")]
    ]
    
    await update.message.reply_text(
        f"🎨 <b>Choose Vibe for:</b> <code>{html.escape(user_name)}</code>", 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("admin_"): return 

    user_data = users_collection.find_one({"_id": user_id})
    if not user_data or "current_name" not in user_data:
        await query.answer("❌ Session Expired", show_alert=True)
        return

    original_name = user_data["current_name"]
    
    style_map = {
        "style_dark": "Dark, Gothic, 🖤 symbols",
        "style_cute": "Cute, Kaomoji, 🌸 symbols",
        "style_glitch": "Glitch, Zalgo, 👾 symbols",
        "style_minimal": "Clean, Minimalist, 🏳️ symbols",
        "style_random": "Trendy aesthetic mixed style"
    }

    # Style persistence
    if data == "next":
        selected_style_key = user_data.get("current_style", "style_random")
        status_text = "⚡ <b>Regenerating...</b>"
    elif data == "back_menu":
        # Show menu again
        keyboard = [
            [InlineKeyboardButton("🖤 Dark", callback_data="style_dark"),
             InlineKeyboardButton("🌸 Cute", callback_data="style_cute")],
            [InlineKeyboardButton("👾 Glitch", callback_data="style_glitch"),
             InlineKeyboardButton("🏳️ Minimal", callback_data="style_minimal")],
            [InlineKeyboardButton("🎲 Random", callback_data="style_random")]
        ]
        await query.edit_message_text(f"🎨 <b>Choose Vibe:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return
    else:
        users_collection.update_one({"_id": user_id}, {"$set": {"current_style": data}})
        selected_style_key = data
        status_text = f"⏳ <b>Applying Vibe...</b>"

    style_prompt = style_map.get(selected_style_key, style_map["style_random"])

    await query.answer("Working...")
    try:
        await query.edit_message_text(status_text, parse_mode=ParseMode.HTML)
    except BadRequest: pass

    final_prompt = (
        f"Transform '{original_name}' into a {style_prompt} username. "
        f"Strictly ONE output. No explanation."
    )
    
    # Bot instance passed here
    result = await generate_content(final_prompt, context.bot)

    buttons = [
        [InlineKeyboardButton("🔄 Next Version", callback_data="next")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]
    ]
    
    try:
        final_text = f"<code>{html.escape(result)}</code>"
        await query.edit_message_text(final_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"<code>{html.escape(result)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# --- ADMIN ACTIONS ---

async def admin_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_add":
        await query.message.reply_text("📤 **Send the NEW API Key.**\n(Old key will be deleted automatically)")
        return ADDING_KEY
        
    elif data == "admin_remove":
        delete_current_key()
        await query.message.reply_text("🗑️ **Key Deleted.** Bot is now offline.")
        # Refresh Panel
        await start(update, context) 

    elif data == "admin_broadcast":
        await query.message.reply_text("📢 **Send Message to Broadcast.**")
        return BROADCASTING

async def save_key_handler(update, context):
    new_key = update.message.text.strip()
    set_new_key(new_key) # Old key deleted, new added
    await update.message.reply_text(f"✅ **New Key Set!**\nEnding in: `...{new_key[-5:]}`", parse_mode=ParseMode.MARKDOWN)
    await start(update, context)
    return ConversationHandler.END

async def broadcast_handler(update, context):
    msg = update.message.text
    status_msg = await update.message.reply_text("📢 **Sending...**")
    users = users_collection.find({})
    count = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user["_id"], text=msg)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await status_msg.edit_text(f"✅ Sent to {count} users.")
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Cancelled.")
    await start(update, context)
    return ConversationHandler.END

# --- MAIN ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_button_click, pattern="^admin_")],
        states={
            ADDING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_key_handler)],
            BROADCASTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_handler)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app_bot.add_handler(CommandHandler("bio", bio_command))
    app_bot.add_handler(conv_handler)
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_handler(CallbackQueryHandler(user_button_click, pattern="^(style_|next|back_menu)"))

    print("✅ Bot (Single Key Mode) Running...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
                            
