import os
import logging
import asyncio
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Get token from environment variable or set it here
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Get from @BotFather
CHANNEL_ID = ""  # Leave empty, bot will ask for it or you can set it here

# User state (no database needed)
user_states = {}

# Keep-alive function to prevent Render from sleeping
async def keep_alive(application):
    """Sends a dummy message every 10 minutes to keep bot active"""
    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
            logger.info("Keep-alive ping - bot is active")
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user_id = update.effective_user.id
    user_states[user_id] = {'step': 'awaiting_channel'}
    
    await update.message.reply_text(
        "👋 Welcome to YouTube Playlist Downloader Bot!\n\n"
        "I can download entire YouTube playlists and upload them to your channel.\n\n"
        "📝 First, send me your channel ID or forward a message from the channel.\n"
        "Format: @channelname or -100123456789"
    )

# Set channel
async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channel ID input"""
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        user_states[user_id] = {}
    
    # Check if it's a forwarded message from a channel
    if update.message.forward_from_chat:
        channel_id = update.message.forward_from_chat.id
        user_states[user_id]['channel_id'] = channel_id
        user_states[user_id]['step'] = 'awaiting_playlist'
        
        await update.message.reply_text(
            f"✅ Channel set: {channel_id}\n\n"
            "Now send me a YouTube playlist link!"
        )
    else:
        # Manual channel ID input
        channel_id = update.message.text.strip()
        
        # Validate channel ID format
        if channel_id.startswith('@') or channel_id.startswith('-100'):
            user_states[user_id]['channel_id'] = channel_id
            user_states[user_id]['step'] = 'awaiting_playlist'
            
            await update.message.reply_text(
                f"✅ Channel set: {channel_id}\n\n"
                "Now send me a YouTube playlist link!"
            )
        else:
            await update.message.reply_text(
                "❌ Invalid channel ID format.\n"
                "Please use: @channelname or -100123456789\n"
                "Or forward a message from the channel."
            )

# Download and upload playlist
async def process_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download playlist and upload to channel"""
    user_id = update.effective_user.id
    playlist_url = update.message.text.strip()
    
    # Check if user has set channel
    if user_id not in user_states or 'channel_id' not in user_states[user_id]:
        await update.message.reply_text(
            "⚠️ Please set a channel first!\n"
            "Use /start to begin."
        )
        return
    
    channel_id = user_states[user_id]['channel_id']
    
    # Validate YouTube URL
    if 'youtube.com' not in playlist_url and 'youtu.be' not in playlist_url:
        await update.message.reply_text(
            "❌ Invalid YouTube URL. Please send a valid YouTube playlist link."
        )
        return
    
    await update.message.reply_text("🔍 Fetching playlist info...")
    
    try:
        # Get playlist info
        ydl_opts_info = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)
            
            if 'entries' not in playlist_info:
                await update.message.reply_text("❌ Could not find videos in playlist.")
                return
            
            total_videos = len(playlist_info['entries'])
            playlist_title = playlist_info.get('title', 'Unknown Playlist')
        
        await update.message.reply_text(
            f"📋 Found playlist: {playlist_title}\n"
            f"📹 Total videos: {total_videos}\n\n"
            f"⏳ Starting download and upload process...\n"
            f"This may take a while depending on playlist size."
        )
        
        # Download directory
        download_dir = f"/tmp/downloads_{user_id}"
        os.makedirs(download_dir, exist_ok=True)
        
        # Download options - HIGH QUALITY
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(download_dir, '%(playlist_index)s - %(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'merge_output_format': 'mp4',
        }
        
        # Download playlist
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([playlist_url])
        
        # Get downloaded files
        video_files = sorted([f for f in os.listdir(download_dir) if f.endswith('.mp4')])
        
        if not video_files:
            await update.message.reply_text("❌ No videos were downloaded.")
            shutil.rmtree(download_dir, ignore_errors=True)
            return
        
        # Upload videos to channel
        for idx, video_file in enumerate(video_files, 1):
            try:
                video_path = os.path.join(download_dir, video_file)
                file_size = os.path.getsize(video_path)
                
                # Telegram file size limit is 2GB
                if file_size > 2 * 1024 * 1024 * 1024:
                    await update.message.reply_text(
                        f"⚠️ Skipping {video_file} (too large: {file_size / (1024*1024):.1f}MB)"
                    )
                    continue
                
                await update.message.reply_text(
                    f"📤 Uploading {idx}/{len(video_files)}: {video_file[:50]}..."
                )
                
                # Upload to channel
                with open(video_path, 'rb') as video:
                    await context.bot.send_video(
                        chat_id=channel_id,
                        video=video,
                        caption=f"#{idx} - {video_file.replace('.mp4', '')}",
                        supports_streaming=True,
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=300
                    )
                
                # Delete file after upload to save space
                os.remove(video_path)
                
            except Exception as e:
                logger.error(f"Error uploading {video_file}: {e}")
                await update.message.reply_text(
                    f"❌ Error uploading {video_file}: {str(e)[:100]}"
                )
        
        # Cleanup
        shutil.rmtree(download_dir, ignore_errors=True)
        
        await update.message.reply_text(
            f"✅ Completed! Uploaded {len(video_files)} videos to channel.\n\n"
            "Send another playlist link or /start to set a new channel."
        )
        
    except Exception as e:
        logger.error(f"Error processing playlist: {e}")
        await update.message.reply_text(
            f"❌ Error: {str(e)[:200]}\n\n"
            "Please check the playlist link and try again."
        )
        # Cleanup on error
        if os.path.exists(f"/tmp/downloads_{user_id}"):
            shutil.rmtree(f"/tmp/downloads_{user_id}", ignore_errors=True)

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    user_id = update.effective_user.id
    
    # If user hasn't started, prompt them
    if user_id not in user_states:
        await update.message.reply_text(
            "Please use /start first to set up the bot!"
        )
        return
    
    step = user_states[user_id].get('step', '')
    
    if step == 'awaiting_channel':
        await set_channel(update, context)
    elif step == 'awaiting_playlist':
        await process_playlist(update, context)
    else:
        await update.message.reply_text(
            "Send me a YouTube playlist link or use /start to reset."
        )

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message"""
    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "1. Send /start\n"
        "2. Send your channel ID or forward a message from the channel\n"
        "3. Send a YouTube playlist link\n"
        "4. Wait for videos to download and upload\n\n"
        "⚠️ Make sure the bot is an admin in your channel!\n\n"
        "Commands:\n"
        "/start - Start/reset the bot\n"
        "/help - Show this message",
        parse_mode='Markdown'
    )

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Exception while handling an update: {context.error}")

# Main function
def main():
    """Start the bot"""
    # Check if token is set
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Please set your TELEGRAM_BOT_TOKEN in the script!")
        return
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start keep-alive task
    application.job_queue.run_repeating(
        lambda context: asyncio.create_task(keep_alive(application)),
        interval=600,
        first=10
    )
    
    # Start bot
    print("🤖 Bot is starting...")
    print("✅ Keep-alive feature enabled - bot will stay active on Render")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
