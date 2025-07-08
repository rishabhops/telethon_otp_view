import os
import csv
import zipfile
import tempfile
import re
import asyncio
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Configuration
BOT_TOKEN = "8002580690:AAFrqfebWRA9Ias7qu9tAUxuZ3mU856IJiE"
API_ID = "10248430"
API_HASH = "42396a6ff14a569b9d59931643897d0d"
MAX_ACCOUNTS = 30  # Reduced for stability on mobile

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class SessionMonitor:
    def __init__(self):
        self.active_clients = {}
        self.temp_dirs = {}

    async def start_monitoring(self, session_path: str, phone: str, bot, chat_id: int):
        """Start monitoring a session for OTP messages"""
        try:
            client = TelegramClient(session_path, API_ID, API_HASH)
            await client.connect()
            
            if not await client.is_user_authorized():
                await bot.send_message(chat_id, f"❌ Session for {phone} is not authorized")
                await client.disconnect()
                return
                
            # Define the event handler
            async def event_handler(event):
                message_text = event.message.text or ""
                if self.is_otp_message(message_text):
                    sender = await event.get_sender()
                    sender_name = sender.first_name if sender else "Unknown"
                    await bot.send_message(
                        chat_id,
                        f"🔔 New OTP for {phone}:\n"
                        f"From: {sender_name} (ID: {event.message.sender_id})\n"
                        f"Message: {message_text}"
                    )
            
            # Register the event handler
            client.add_event_handler(event_handler, events.NewMessage(incoming=True))
            
            self.active_clients[phone] = client
            await bot.send_message(chat_id, f"✅ Monitoring started for {phone}")
            
            # Run the client in the background
            await client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"Error for {phone}: {str(e)}")
            await bot.send_message(chat_id, f"❌ Error for {phone}: {str(e)}")
            if phone in self.active_clients:
                del self.active_clients[phone]

    def is_otp_message(self, text: str) -> bool:
        """Check if message contains an OTP"""
        if not text:
            return False
            
        # Look for 4-8 digit codes
        if re.search(r'\b\d{4,8}\b', text):
            # Check for OTP-related keywords
            otp_keywords = ['otp', 'code', 'verification', 'password', 'one time', '2fa']
            return any(keyword in text.lower() for keyword in otp_keywords)
        return False

    async def stop_all(self):
        """Stop all active monitoring sessions"""
        for phone, client in list(self.active_clients.items()):
            try:
                await client.disconnect()
                del self.active_clients[phone]
            except Exception as e:
                logger.error(f"Error disconnecting {phone}: {str(e)}")
        
        # Clean up temporary files
        for temp_dir in self.temp_dirs.values():
            try:
                for root, dirs, files in os.walk(temp_dir, topdown=False):
                    for name in files:
                        os.remove(os.path.join(root, name))
                    for name in dirs:
                        os.rmdir(os.path.join(root, name))
                os.rmdir(temp_dir)
            except Exception as e:
                logger.error(f"Error cleaning temp dir: {str(e)}")
        self.temp_dirs = {}

# Global monitor instance
monitor = SessionMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📤 Send me a ZIP file containing:\n"
        "1. A folder named 'sessions' with .session files\n"
        "2. A 'phone.csv' file with phone numbers\n\n"
        "Format: 9142137449.session for each number\n\n"
        "I'll monitor these accounts for incoming OTP messages."
    )

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Stop any existing monitoring
    await monitor.stop_all()
    
    if not update.message.document:
        await update.message.reply_text("Please send a ZIP file")
        return

    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    monitor.temp_dirs[update.message.chat_id] = temp_dir
    zip_path = os.path.join(temp_dir, "sessions.zip")
    
    # Download ZIP
    try:
        file = await update.message.document.get_file()
        await file.download_to_drive(zip_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Download failed: {str(e)}")
        return

    # Extract ZIP
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        os.remove(zip_path)  # Remove zip after extraction
    except zipfile.BadZipFile:
        await update.message.reply_text("❌ Invalid ZIP file")
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Extraction error: {str(e)}")
        return

    # Verify structure
    sessions_dir = os.path.join(temp_dir, "sessions")
    csv_path = os.path.join(temp_dir, "phone.csv")
    
    if not os.path.isdir(sessions_dir):
        await update.message.reply_text("⚠️ 'sessions' folder not found or not a directory")
        return
    if not os.path.exists(csv_path):
        await update.message.reply_text("⚠️ 'phone.csv' file not found")
        return

    # Read phone numbers
    phone_numbers = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0].strip():
                    phone_numbers.append(row[0].strip())
    except Exception as e:
        await update.message.reply_text(f"📄 CSV Error: {str(e)}")
        return

    if not phone_numbers:
        await update.message.reply_text("❌ No phone numbers found in phone.csv")
        return

    # Limit number of accounts
    if len(phone_numbers) > MAX_ACCOUNTS:
        phone_numbers = phone_numbers[:MAX_ACCOUNTS]
        await update.message.reply_text(f"⚠️ Monitoring first {MAX_ACCOUNTS} accounts only")

    # Start monitoring each session
    success_count = 0
    for phone in phone_numbers:
        session_file = os.path.join(sessions_dir, f"{phone}.session")
        if not os.path.exists(session_file):
            await update.message.reply_text(f"⚠️ Session file missing for {phone}")
            continue
        
        asyncio.create_task(
            monitor.start_monitoring(
                session_file,
                phone,
                context.bot,
                update.message.chat_id
            )
        )
        success_count += 1
        # Add small delay between account startups
        await asyncio.sleep(1)

    await update.message.reply_text(f"🚀 Started monitoring {success_count} accounts")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await monitor.stop_all()
    await update.message.reply_text("🛑 Stopped all monitoring")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = len(monitor.active_clients)
    await update.message.reply_text(f"🔍 Currently monitoring {count} accounts")

def main():
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_zip))
    
    # Start the Bot
    application.run_polling()

if __name__ == "__main__":
    main()
