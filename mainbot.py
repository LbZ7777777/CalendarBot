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
import datetime
import calendar
import mysql.connector
import yt_dlp
from yt_dlp.utils import DownloadError
import validators
import traceback

# LOAD ENV AND INTENTS
intents = discord.Intents.default()
intents.message_content = True
load_dotenv()

# CONFIG
operator = "("
dm = False
reminderCheckInterval = 20
song_queues = {}

# BOT SETUP ###################################################################################################################
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

##
### ASSIST COMMANDS ###################################################################################################################
##

# DICTS
commandList = {
    "calendarset": "Make a repeating reminder: '{operator}calendarset {{UNIXTIMESTAMP}} {{INTERVAL}} {{MESSAGE}}'",
    "calendarbatch": "Create a batch reminder for a game: '{operator}calendarbatch <gi>'",
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

gameList = {
    "gi": "Genshin Impact",
    "hsr": "Honkai: Star Rail",
    "zzz": "Zenless Zone Zero",
    "wuwa": "Wuthering Waves",
    "gfl": "Girls Frontline 2"
}

# HELP COMMAND
@bot.command()
async def help(ctx):
    help_message = "Available commands:\n"
    for command, desc in commandList.items():
        help_message += f"{command}: {desc.format(operator=operator)}\n"
    await ctx.send(help_message)

##
### CALENDAR COMMANDS ###################################################################################################################
##

# CALENDAR SET
@bot.command()
async def calendarset(ctx, timestamp: int, interval: str, *, msg: str):
    try:
        unixValue = parseDuration(interval, "unix")
        curTime = int(datetime.datetime.now().timestamp())

        if timestamp < curTime - unixValue + 1:
            await ctx.send("❌ Timestamp is too far in the past.", delete_after=5)
            return
        if unixValue < reminderCheckInterval:
            await ctx.send(f"❌ Interval must be at least {reminderCheckInterval} seconds.")
            return

        reminder_msg = await ctx.send(f"{msg}: <t:{timestamp}:F> <t:{timestamp}:R> `(every {interval})`")
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
    await ctx.send(f"Current Unix timestamp: {int(datetime.datetime.now().timestamp())}")

# FINDTIME
@bot.command()
async def findtime(ctx, *, content):
    try:
        ts = int(datetime.datetime.strptime(content, "%Y-%m-%d %H:%M").timestamp())
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

# CALENDAR BATCH 
@bot.command()
async def calendarbatch(ctx, game: str):
    try:

        if game not in gameList:
            await ctx.send("❌ Valid games are: gi", delete_after=5)
            return
        
        curServID = ctx.guild.id
        cursor.execute("SELECT * FROM new_table WHERE batchlinked_server = %s and batchlinked_game = %s", (curServID, game))
        result = cursor.fetchone()

        if result is not None:
            try:
                channel = bot.get_channel(result[1])
                message = await channel.fetch_message(result[0])
                if message:
                    await ctx.send(f"❌ Batch for {game} already exists in this server. Please delete existing batch first.", delete_after=5)
                    return
            except discord.NotFound:
                # Message not found, delete from database and CONTINUE execution
                cursor.execute("DELETE FROM new_table WHERE batchlinked_id = %s", (result[0],))
                db.commit()
                #await ctx.send(f"⚠️ Batch message not found. Deleted from database, proceeding to create new batch.")

        cursor.execute(""" 
        SELECT batch_nexttime, batch_interval, batch_content, batch_intervalvar, batch_type
        FROM batch_table
        WHERE batch_game = %s
        """, (game,))
        batch_data = cursor.fetchall()
        if not batch_data:
            await ctx.send(f"❌ No existing batch found for {game}. Please create one first.", delete_after=5)
            return
        batch_data.sort(key=lambda x: x[0])  # Sort by next time
        content = f"{gameList.get(game, game)} batch reminder: \n"

        for batch in batch_data:
            if batch[6] == 0:  # If batch_type is 0, it's a normal reminder
                content += f"{batch[4]}: <t:{batch[2]}:F> <t:{batch[2]}:R> `(every {batch[5]})`\n"
            if batch[6] == 1:  # If batch_type is 1, it's a date reminder
                curSuffix = daySuffix(batch[5])
                content += f"{batch[4]}: <t:{batch[2]}:F> <t:{batch[2]}:R> `(every {batch[5]}{curSuffix})`\n"
        batch_msg = await ctx.send(content)

        print(batch_msg.id)
        cursor.execute("""
            INSERT INTO new_table
            (batchlinked_id, batchlinked_channel, batchlinked_server, batchlinked_user, batchlinked_game)
            VALUES (%s, %s, %s, %s, %s)
            """, (batch_msg.id, batch_msg.channel.id, ctx.guild.id, ctx.author.id, game))
            
        db.commit()
        await ctx.send(f"✅ Batch for {game} created successfully in this server.", delete_after=5)

    except Exception as e:
        print(f"calendarbatch error: {e}")
        await ctx.send("❌ Error checking existing batch.", delete_after=5)
        return

##
### YOUTUBE MUSIC PLAYER SETUP ###################################################################################################################
##

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

# QUEUE MANAGEMENT
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


# GIFT LINK
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

## 
### SLASH COMMANDS ###################################################################################################################
##

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

# TEXT RESPONSES
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

##
### PARSING ###################################################################################################################
##

# EDIT BATCH MESSAGES
async def editBatchMessage(batch_game, new_content):
    try:    
        cursor.execute("""
            SELECT batchlinked_id, batchlinked_channel, batchlinked_server
            FROM new_table
            WHERE batchlinked_game = %s
        """, (batch_game,))
        messages = cursor.fetchall()
        for messageID, channelID, serverID in messages:
            channel = bot.get_channel(channelID)
            messageToEdit = await channel.fetch_message(messageID)
            
            await messageToEdit.edit(content=new_content)
        print("Edited batch messages for game:", batch_game, "in", len(messages), "servers.")

    except discord.NotFound:
        cursor.execute("DELETE FROM new_table WHERE batchlinked_id = %s", (messageID,))
        db.commit()
    except Exception as e:
        print(f"Error editing batch message for {batch_game}: {e}")

# REMINDER CHECK LOOP
@tasks.loop(seconds=reminderCheckInterval)
async def check_reminders():
    now = int(datetime.datetime.now().timestamp())
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

            await messageToEdit.edit(content=f"{messageContent}: <t:{newTime}:F> <t:{newTime}:R> `(every {intervalVar})`")

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

# BATCH CHECK LOOP
@tasks.loop(seconds=reminderCheckInterval)
async def check_batch():
    dueCheck = []
    due = []
    now = int(datetime.datetime.now().timestamp())
    cursor.execute("""
        SELECT batch_game, batch_nexttime
        FROM batch_table
    """)	
    dueCheck = cursor.fetchall()
    if not dueCheck:
        return
    for row in dueCheck:
        if row[1] <= now:
            due.append(row)
    due_set = set(row[0] for row in due)
    #print(f"Due batch reminders at {now}: {len(due_set)}")

    for game in due_set:
        try:
            cursor.execute("""
                SELECT batch_id, batch_game, batch_nexttime, batch_interval, batch_content, batch_intervalvar, batch_type
                FROM batch_table 
                WHERE batch_game = %s
            """, (game,))
            batch_data = cursor.fetchall()

            if batch_data:
                # Process the batch data
                print(f"Processing batch for {game} at {now}.")
                for batch in batch_data:
                        if batch[2] <= now:
                            if batch[6] == 0:  # If batch_type is 0, it's a normal reminder
                                new_time = batch[2] + batch[3]

                                # Update the next time for the batch
                                cursor.execute(
                                    "UPDATE batch_table SET batch_nexttime = %s WHERE batch_id = %s",
                                    (new_time, batch[0])
                                )
                                db.commit()
                            if batch[6] == 1:  # If batch_type is 1, it's a date reminder
                                new_time = parseDateUpdate(batch[5], batch[2])

                                # Update the next time for the batch
                                cursor.execute(
                                    "UPDATE batch_table SET batch_nexttime = %s WHERE batch_id = %s",
                                    (new_time, batch[0])
                                )
                                db.commit()

                cursor.execute("""
                SELECT batch_id, batch_game, batch_nexttime, batch_interval, batch_content, batch_intervalvar, batch_type
                FROM batch_table 
                WHERE batch_game = %s
                """, (game,))
                batch_data = cursor.fetchall()

                batch_data.sort(key=lambda x: x[2])
                content = f"{gameList.get(game, game)} batch reminders: \n"
                for batch in batch_data:
                    if batch[6] == 0:  # If batch_type is 0, it's a normal reminder
                        content += f"{batch[4]}: <t:{batch[2]}:F> <t:{batch[2]}:R> `(every {batch[5]})`\n"
                    if batch[6] == 1:  # If batch_type is 1, it's a date reminder
                        curSuffix = daySuffix(batch[5])
                        content += f"{batch[4]}: <t:{batch[2]}:F> <t:{batch[2]}:R> `(every {batch[5]}{curSuffix})`\n"

                await editBatchMessage(game, content)
                
        except Exception as e:
            print(f"Batch processing error for {game}: {e}")
            traceback.print_exc()

# PARSE DURATION
def parseDuration(preDuration, type):
    if type == "unix":
        pattern = r"(\d+)([wdhms])"
        matches = re.findall(pattern, preDuration)

        unitMults = {'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}
        return sum(int(val) * unitMults[unit] for val, unit in matches)
    
def parseDateUpdate(dateInfo, currentTime):
    dateInfo = int(dateInfo)
    currentDT = datetime.datetime.fromtimestamp(currentTime)

    year = currentDT.year
    month = currentDT.month
    day = currentDT.day

    if day < dateInfo:
        try:
            targetDate = datetime.datetime(year, month, dateInfo, currentDT.hour, currentDT.minute, currentDT.second)
        except ValueError:
            # fallback: if invalid day, go to last day of this month
            if month == 12:
                nextMonthFirst = datetime.datetime(year + 1, 1, 1, currentDT.hour, currentDT.minute, currentDT.second)
            else:
                nextMonthFirst = datetime.datetime(year, month + 1, 1, currentDT.hour, currentDT.minute, currentDT.second)
            lastDay = nextMonthFirst - datetime.timedelta(days=1)
            targetDate = datetime.datetime(year, month, lastDay.day, currentDT.hour, currentDT.minute, currentDT.second)
    else:
        # move to next month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        try:
            targetDate = datetime.datetime(year, month, dateInfo, currentDT.hour, currentDT.minute, currentDT.second)
        except ValueError:
            # fallback: last day of next month
            if month == 12:
                nextMonthFirst = datetime.datetime(year + 1, 1, 1, currentDT.hour, currentDT.minute, currentDT.second)
            else:
                nextMonthFirst = datetime.datetime(year, month + 1, 1, currentDT.hour, currentDT.minute, currentDT.second)
            lastDay = nextMonthFirst - datetime.timedelta(days=1)
            targetDate = datetime.datetime(year, month, lastDay.day, currentDT.hour, currentDT.minute, currentDT.second)

    deltaSeconds = (targetDate - currentDT).total_seconds()
    return int(currentTime + deltaSeconds)



def daySuffix(day):
    day = int(day)
    if 10 <= day % 100 <= 13:
        return 'th'
    else:
        return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')

# BOT READY EVENT ###################################################################################################################
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    check_reminders.start()
    check_batch.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print("Failed to sync commands:", e)

# RUN  
bot.run(os.getenv("CALENDARBOT_KEY"))
