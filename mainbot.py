# IMPORT
import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord import FFmpegPCMAudio, PCMVolumeTransformer
from discord.ext import commands
from collections import deque
import os
from dotenv import load_dotenv
import asyncio
import re
from datetime import datetime
import mysql.connector
import yt_dlp
from yt_dlp.utils import DownloadError
import validators

# LOAD ENV AND INTENTS
intents = discord.Intents.default()
intents.message_content = True
load_dotenv()

# CONFIG
operator = "("
dm = False
reminderCheckInterval = 20
song_queues = {}

# BOT SETUP
bot = commands.Bot(command_prefix=operator, intents=intents, help_command=None)

def get_queue(guild_id):
    if guild_id not in song_queues:
        song_queues[guild_id] = deque()
    return song_queues[guild_id]

# DATABASE SETUP
db = mysql.connector.connect(
    host=os.getenv("SQLHOST"),
    user=os.getenv("SQLUSER"),
    password=os.getenv("SQLPASS"),
    database=os.getenv("SQLDB")
)
cursor = db.cursor()

# COMMAND LIST
commandList = {
    "calendarset": "Make a repeating reminder: '{operator}calendarset {{UNIXTIMESTAMP}} {{INTERVAL}} {{MESSAGE}}'",
    "delmes": "Delete a reminder by replying to it.",
    "curunix": "Print current Unix timestamp.",
    "interval": "View interval formatting help.",
    "findtime": "Convert UTC datetime to Unix: '{operator}findtime YYYY-MM-DD HH:MM'",
    "help": "Show this help message.",
    "play": "Play a YouTube audio stream in a voice channel: '{operator}play <URL>'",
    "stop": "Stop playing music and leave the voice channel.",
    "skip": "Skip the currently playing song.",
    "code": "Generate a gift link for a code (GI/HSR): '{operator}gift <gi|hsr> <CODE>'",
    "gift": "Generate Hoyoverse gift links for GI or HSR: '{operator}gift <gi|hsr> <CODE1> [<CODE2> ...])'"
}

# HELP COMMAND
@bot.command()
async def help(ctx):
    help_message = "Available commands:\n"
    for command, desc in commandList.items():
        help_message += f"{command}: {desc.format(operator=operator)}\n"
    await ctx.send(help_message)

# CALENDAR SET
@bot.command()
async def calendarset(ctx, timestamp: int, interval: str, *, msg: str):
    try:
        unixValue = parseDuration(interval)
        curTime = int(datetime.now().timestamp())

        if timestamp < curTime - unixValue + 1:
            await ctx.send("❌ Timestamp is too far in the past.", delete_after=5)
            return
        if unixValue < reminderCheckInterval:
            await ctx.send(f"❌ Interval must be at least {reminderCheckInterval} seconds.")
            return

        reminder_msg = await ctx.send(f"{msg}: <t:{timestamp}:F> `(every {interval})`")
        if dm:
            try:
                await ctx.author.send(f"Reminder set for <t:{timestamp}:F> repeating every {interval}: {msg}")
            except:
                await ctx.send("❌ Could not send you a DM.", delete_after=2)

        cursor.execute("""
            INSERT INTO reminder_table 
            (reminder_id, reminder_channel, reminder_user, reminder_nexttime, reminder_interval, reminder_content, reminder_intervalvar)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (reminder_msg.id, reminder_msg.channel.id, ctx.author.id, timestamp, unixValue, msg, interval))
        db.commit()

    except Exception as e:
        print("calendarset error:", e)
        await ctx.send("❌ Error setting reminder.")

# DELETE MESSAGE
@bot.command()
async def delmes(ctx):
    try:
        if not ctx.message.reference:
            await ctx.send("❌ Reply to the reminder you want to delete.", delete_after=2)
            return

        ref_id = ctx.message.reference.message_id
        cursor.execute("SELECT reminder_user FROM reminder_table WHERE reminder_id = %s", (ref_id,))
        result = cursor.fetchone()

        if result is None or result[0] != ctx.author.id:
            await ctx.send("❌ You can only delete your own reminders.", delete_after=2)
            return

        msg = await ctx.channel.fetch_message(ref_id)
        await msg.delete()
        await ctx.message.delete()

        cursor.execute("DELETE FROM reminder_table WHERE reminder_id = %s", (ref_id,))
        db.commit()

    except Exception as e:
        print("delmes error:", e)
        await ctx.send("❌ Error deleting reminder.", delete_after=2)

# CURUNIX
@bot.command()
async def curunix(ctx):
    await ctx.send(f"Current Unix timestamp: {int(datetime.now().timestamp())}")

# FINDTIME
@bot.command()
async def findtime(ctx, *, content):
    try:
        dt = datetime.strptime(content.strip(), "%Y-%m-%d %H:%M")
        ts = int(dt.timestamp())
        await ctx.send(f"The Unix timestamp for `{content}` is: {ts} (<t:{ts}:F>)")
    except ValueError:
        await ctx.send("❌ Use format: YYYY-MM-DD HH:MM")

# INTERVAL HELP
@bot.command()
async def interval(ctx):
    msg = (
        f"Use: `{operator}calendarset {{UNIX}} {{INTERVAL}} {{MESSAGE}}`\n"
        f"Interval format: combinations of w, d, h, m, s (e.g., 1w2d3h)."
    )
    await ctx.send(msg)

# YOUTUBE MUSIC PLAYER
@bot.command()
async def play(ctx, *, query):

    if not ctx.author.voice:
        await ctx.send("❌ You must be in a voice channel.")
        return
    query = query.strip().replace("<", "").replace(">", "")

    isURL = validators.url(query) and ("youtube.com" in query or "youtu.be" in query)
    searchQuery = query if isURL else f"ytsearch:{query}"

    channel = ctx.author.voice.channel
    if not ctx.voice_client:
        await channel.connect()


    ydl_opts = {
        'options': '-vn',
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        'format': 'bestaudio', 'quiet': True, 'cookiefile': 'cookies.txt'}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(searchQuery, download=False)
            if 'entries' in info:  # Handle search result
                info = info['entries'][0]
            audio_url = info.get('url')

        audio_url = info.get('url')
        title = info.get('title', 'Unknown Title')

        if not audio_url:
            await ctx.send("❌ Could not retrieve audio stream.")
            return
        
        queue  = get_queue(ctx.guild.id)
        entry = {'url': audio_url, 'title': title}

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            queue.append(entry)
            await ctx.send(f"🎶 Added to queue: {title}")
        else:
            source = ffmpeg_audio = FFmpegPCMAudio(audio_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
            audio_src = PCMVolumeTransformer(source, volume=0.2)
            ctx.voice_client.play(audio_src, after=lambda e: play_next(ctx, ctx.voice_client))
            await ctx.send(f"🎶 Now playing: {title}")

    except DownloadError as e:
        await ctx.send("❌ Could not download or play the video. It may be restricted.")
        print(f"[yt_dlp] DownloadError: {e}")
    except Exception as e:
        await ctx.send("❌ An error occurred while processing the request.")
        print(f"[play error] {e}")

# STOP PLAYING MUSIC
@bot.command()
async def stop(ctx):
    voice_client = ctx.voice_client
    if not voice_client:
        await ctx.send("❌ I'm not connected to any voice channel.", delete_after=5)
        return

    if ctx.author.voice is None or ctx.author.voice.channel != voice_client.channel:
        await ctx.send("❌ You must be in the same voice channel to stop me.", delete_after=5)
        return

    await voice_client.disconnect()
    await ctx.send("🛑 Stopped and left the voice channel.")

# SKIP SONG
@bot.command()
async def skip(ctx):
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_connected():
        await ctx.send("❌ I'm not connected to any voice channel.", delete_after=5)
        return

    if ctx.author.voice is None or ctx.author.voice.channel != voice_client.channel:
        await ctx.send("❌ You must be in the same voice channel to skip the song.", delete_after=5)
        return

    if not voice_client.is_playing() and not voice_client.is_paused():
        await ctx.send("❌ No song is currently playing.", delete_after=5)
        return
    
    await ctx.send("⏭️ Skipping the current song.")

    voice_client.stop()
    play_next(ctx, voice_client)

#QUEUE MANAGEMENT
def play_next(ctx, voice_client):
    queue = get_queue(ctx.guild.id)

    if queue:
        next_entry = queue.popleft()
        source = FFmpegPCMAudio(next_entry['url'], before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
        audio = PCMVolumeTransformer(source, volume=0.2)
        voice_client.play(audio, after=lambda e: play_next(ctx, voice_client))
        coro = ctx.send(f"🎶 Now playing: {next_entry['title']}")
        bot.loop.create_task(coro)
    else:
        coro = voice_client.disconnect()
        bot.loop.create_task(coro)

@bot.command()
async def gift(ctx):
    args = ctx.message.content.split()
    if len(args) < 3:
        await ctx.send("Usage: (gift <gi|hsr|zzz> <CODE1> [<CODE2> ...]")
        return

    game = args[1].lower()
    codes = args[2:]

    if game not in ("gi", "hsr", "zzz"):
        await ctx.send("Valid games are: gi, hsr, zzz.")
        return

    links = []
    links.append(f"Gift links for {game.upper()}:")
    for code in codes:
        code = code.strip().upper()
        if game == "gi":
            url = f"<https://genshin.hoyoverse.com/en/gift?code={code}>"
            links.append(f"{code}: {url}")
        elif game == "hsr":
            url = f"<https://hsr.hoyoverse.com/gift?code={code}>"
            links.append(f"{code}: {url}")
        elif game == "zzz":
            url = f"<https://zenless.hoyoverse.com/redemption?code={code}>"
            links.append(f"{code}: {url}")

    await ctx.send("\n".join(links))

@app_commands.user_install()
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True)
@bot.tree.command(name="gift", description="Generate Hoyoverse gift links for GI or HSR")
@app_commands.describe(
    game="Game to generate links for (gi, hsr, zzz)",
    codes="Space-separated list of gift codes"
)
async def gift_slash(interaction: discord.Interaction, game: str, codes: str):
    game = game.lower()
    if game not in ("gi", "hsr", "zzz"):
        await interaction.response.send_message("Game must be 'gi', 'hsr', or 'zzz'.", ephemeral=True)
        return

    code_list = codes.upper().split()
    links = [f"Gift links for {game.upper()}:"]
    for code in code_list:
        if game == "gi":
            url = f"<https://genshin.hoyoverse.com/en/gift?code={code}>"
        elif game == "hsr":
            url = f"<https://hsr.hoyoverse.com/gift?code={code}>"
        elif game == "zzz":
            url = f"<https://zenless.hoyoverse.com/redemption?code={code}>"

        links.append(f"{code}: {url}")

    await interaction.response.send_message("\n".join(links))

#CUTEBABY
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.strip() == "CUTEBABY":
        await message.channel.send("https://cdn.discordapp.com/attachments/1337529806606176307/1349904126800166993/flutterpat.gif?ex=68403def&is=683eec6f&hm=2552d294ac24c6ec4ce8fb6f4793e79f8da0be42af396b2f330b5e773d1df78b&")
        await message.channel.send("https://tenor.com/view/flutterpage-flutterpage-reverse-1999-flutterpage-bonk-gif-7260436718441088941")
        await message.channel.send("https://tenor.com/view/flutterpage-reverse-1999-re1999-good-morning-gif-1593186061945744120")
        await message.channel.send("https://cdn.discordapp.com/attachments/312670434921283584/1379595561413509150/iu.png?ex=6840cffd&is=683f7e7d&hm=f2a6ad2f859cfce8398ab7fcc3d7dedbf7f7efdb5749fb55d441af72407403c3&")
        return
    
    if message.content.strip() == "cementeater":
        await message.channel.send("https://tenor.com/view/reverse1999-anime-r1999-37-game-gif-3582570055491321386")
        await message.channel.send("https://tenor.com/view/reverse1999-anime-r1999-37-game-gif-3582570055491321386")
        await message.channel.send("https://tenor.com/view/reverse1999-anime-r1999-37-game-gif-3582570055491321386")
        await message.channel.send("https://tenor.com/view/reverse1999-anime-r1999-37-game-gif-3582570055491321386")
        return
    
    if message.content.strip() == "IIYEII":
        await message.channel.send("https://cdn.discordapp.com/attachments/312670434921283584/1393567490247884890/sharkass.gif?ex=6873a45c&is=687252dc&hm=e14ad68f7d95b8e4cf03b0102125fa224d9d06ac48f778f5fb4442615242cade&")
        return
    
    if message.content.strip() == "beengobongo":
        await message.channel.send("https://tenor.com/view/ump9-ump-girls-frontline-gfl-gif-20084820")
        return

    if message.content == "deez nuts":
        await message.channel.send("ha gotteem")
        return

    await bot.process_commands(message) 



# REMINDER CHECK LOOP
@tasks.loop(seconds=reminderCheckInterval)
async def check_reminders():
    now = int(datetime.now().timestamp())
    cursor.execute("""
        SELECT reminder_id, reminder_channel, reminder_user, reminder_nexttime,
        reminder_interval, reminder_content, reminder_intervalvar
        FROM reminder_table
        WHERE reminder_nexttime <= %s
    """, (now,))
    due = cursor.fetchall()

    if due:
        print(f"Checking reminders at {now}. Due reminders: {len(due)}")

    for messageID, channelID, authorID, reminderTime, intervalTime, messageContent, intervalVar in due:
        try:
            channel = bot.get_channel(channelID)
            messageToEdit = await channel.fetch_message(messageID)
            newTime = reminderTime + intervalTime

            await messageToEdit.edit(content=f"{messageContent}: <t:{newTime}:F> `(every {intervalVar})`")

            cursor.execute(
                "UPDATE reminder_table SET reminder_nexttime = %s WHERE reminder_id = %s",
                (newTime, messageID)
            )
            db.commit()

        except discord.NotFound:
            cursor.execute("DELETE FROM reminder_table WHERE reminder_id = %s", (messageID,))
            db.commit()
            print("Deleted missing reminder", messageID)
        except Exception as e:
            print(f"Reminder update error for {messageID}: {e}")


# PARSE DURATION
def parseDuration(preDuration):
    pattern = r"(\d+)([wdhms])"
    matches = re.findall(pattern, preDuration)

    unitMults = {'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}
    return sum(int(val) * unitMults[unit] for val, unit in matches)

# BOT READY EVENT
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    check_reminders.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print("Failed to sync commands:", e)


# RUN
bot.run(os.getenv("CALENDARBOT_KEY"))
