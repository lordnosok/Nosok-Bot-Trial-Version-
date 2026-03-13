import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View
import configparser
import os
import time
import random
import asyncio
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List

# -------------------- Configuration --------------------
# Load bot settings from config file
config = configparser.ConfigParser()
config.read('config/config.cfg')

TOKEN = config['DEFAULT']['TOKEN']
GUILD_ID = config['DEFAULT'].get('GUILD_ID')          # Optional: restrict commands to a test guild
BOT_NAME = config['DEFAULT']['BOT_NAME']
BOT_VERSION = config['DEFAULT']['BOT_VERSION']
OWNER_NAME = config['DEFAULT']['OWNER_NAME']
OWNER_ID = int(config['DEFAULT']['OWNER_ID'])
BANNER_FILENAME = config['DEFAULT']['BANNER_FILENAME']
LOGO_FILENAME = config['DEFAULT']['LOGO_FILENAME']

# Paths to resource files and database
RES_DIR = 'res'
BANNER_PATH = os.path.join(RES_DIR, BANNER_FILENAME)
LOGO_PATH = os.path.join(RES_DIR, LOGO_FILENAME)
DATA_DIR = 'data'
DB_PATH = os.path.join(DATA_DIR, 'warnings.db')

os.makedirs(DATA_DIR, exist_ok=True)

# Color constants for embeds
PRIMARY = 0x3498db      # Bright blue
SUCCESS = 0x2ecc71      # Green
WARNING = 0xf1c40f      # Yellow
ERROR = 0xe74c3c        # Red
INFO = 0x9b59b6         # Purple

# -------------------- Database Setup --------------------
# SQLite database for warnings and guild settings
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS warnings
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              guild_id INTEGER,
              user_id INTEGER,
              moderator_id INTEGER,
              reason TEXT,
              timestamp INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS guild_settings
             (guild_id INTEGER PRIMARY KEY,
              log_channel INTEGER,
              mute_role INTEGER)''')
conn.commit()

# -------------------- Bot Setup --------------------
intents = discord.Intents.default()
intents.message_content = True   # Needed to read message content for AFK detection
intents.members = True            # Required for member-related events and commands
intents.voice_states = True       # Required for voice channel features

class ProBot(commands.Bot):
    """
    Custom bot class extending commands.Bot.
    Holds additional attributes: start_time, afk_users, temp_vc_channels.
    """
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.start_time = time.time()
        self.afk_users = {}          # user_id: (reason, timestamp)
        self.temp_vc_channels = {}   # channel_id: (owner_id, created_at)

    async def setup_hook(self):
        """Sync slash commands to a specific guild or globally."""
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Commands synced to guild {GUILD_ID}")
        else:
            await self.tree.sync()
            print("Commands synced globally")

    async def on_ready(self):
        """Event triggered when the bot is ready and connected."""
        print(f'{self.user} has connected to Discord!')
        print(f'Bot is in {len(self.guilds)} guilds')
        # Start background task to clean up empty temporary voice channels
        self.loop.create_task(self.cleanup_temp_vc())

    async def cleanup_temp_vc(self):
        """
        Background task that periodically checks and deletes empty temporary voice channels.
        Runs every 60 seconds.
        """
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(60)
            for channel_id, (owner_id, created_at) in list(self.temp_vc_channels.items()):
                channel = self.get_channel(channel_id)
                if channel and len(channel.members) == 0:
                    try:
                        await channel.delete(reason="Temporary voice channel expired")
                        del self.temp_vc_channels[channel_id]
                    except Exception:
                        pass

bot = ProBot()

# -------------------- Helper Functions --------------------
def get_uptime():
    """Return a formatted string of the bot's uptime."""
    uptime = time.time() - bot.start_time
    d, r = divmod(uptime, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{int(d)}d {int(h)}h {int(m)}m {int(s)}s"

async def get_logo():
    """Return a discord.File for the logo if it exists, otherwise None."""
    return discord.File(LOGO_PATH, filename="logo.png") if os.path.exists(LOGO_PATH) else None

async def get_banner():
    """Return a discord.File for the banner if it exists, otherwise None."""
    return discord.File(BANNER_PATH, filename="banner.png") if os.path.exists(BANNER_PATH) else None

def create_embed(title, description=None, color=PRIMARY, thumbnail=True, timestamp=True):
    """
    Create a standardized embed with the bot's footer, optional thumbnail (logo), and timestamp.
    """
    embed = discord.Embed(title=title, description=description, color=color)
    if timestamp:
        embed.timestamp = datetime.utcnow()
    embed.set_footer(text=f"{BOT_NAME} v{BOT_VERSION}", icon_url="attachment://logo.png" if os.path.exists(LOGO_PATH) else None)
    if thumbnail and os.path.exists(LOGO_PATH):
        embed.set_thumbnail(url="attachment://logo.png")
    return embed

async def safe_reply(interaction: discord.Interaction, embed: discord.Embed, file=None, view=None, ephemeral=False):
    """
    Safely send a response to an interaction.
    Handles cases where the interaction has already been responded to by using followup.
    """
    if interaction is None:
        print("safe_reply called with None interaction")
        return

    try:
        # Try to send as a normal response
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=ephemeral)
            return
    except (discord.errors.InteractionResponded, AttributeError) as e:
        # Interaction already responded or invalid
        print(f"safe_reply: interaction already responded or invalid: {e}")

    # Fallback to followup
    try:
        await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=ephemeral)
    except Exception as e:
        print(f"safe_reply: followup failed: {e}")

def hierarchy_check(interaction: discord.Interaction, target: discord.Member) -> bool:
    """
    Check if the command invoker has a higher role than the target member.
    Used for moderation commands to prevent privilege escalation.
    """
    if interaction.user == interaction.guild.owner:
        return True
    if target == interaction.guild.owner:
        return False
    return interaction.user.top_role > target.top_role

def log_to_channel(guild_id, embed):
    """
    Send a log embed to the configured log channel for the guild.
    Does nothing if no log channel is set.
    """
    try:
        c.execute("SELECT log_channel FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()
        if row and row[0]:
            channel = bot.get_channel(row[0])
            if channel:
                asyncio.create_task(channel.send(embed=embed))
    except Exception as e:
        print(f"Logging error: {e}")

# -------------------- Pagination View --------------------
class PaginatorView(View):
    """
    A view with previous/next buttons to paginate through a list of embeds.
    Also includes a close button to delete the message.
    """
    def __init__(self, embeds: List[discord.Embed], timeout=60):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.current = 0
        self.total = len(embeds)
        self.update_buttons()

    def update_buttons(self):
        """Disable previous button on first page, next button on last page."""
        self.children[0].disabled = self.current == 0
        self.children[1].disabled = self.current == self.total - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        """Go to the previous embed page."""
        self.current -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        """Go to the next embed page."""
        self.current += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="✖", style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: Button):
        """Delete the message."""
        await interaction.message.delete()

# -------------------- Confirmation View --------------------
class ConfirmView(View):
    """
    A simple confirmation view with Confirm and Cancel buttons.
    Used for dangerous actions like kick/ban.
    """
    def __init__(self, interaction: discord.Interaction, target, action, reason):
        super().__init__(timeout=30)
        self.interaction = interaction
        self.target = target
        self.action = action
        self.reason = reason
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        """User confirmed the action."""
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        """User cancelled the action."""
        self.value = False
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self):
        """If no response within timeout, treat as cancel."""
        self.value = False
        self.stop()

# -------------------- Slash Commands --------------------

# ---------- General Commands ----------
@bot.tree.command(name="info", description="Show detailed bot information")
async def info(interaction: discord.Interaction):
    """Display bot stats, version, owner, uptime, and optionally a banner image."""
    await interaction.response.defer()
    embed = create_embed(
        title=f"✨ {BOT_NAME}",
        description=(
            f"**Version:** `{BOT_VERSION}`\n"
            f"**Developer:** {OWNER_NAME} (<@{OWNER_ID}>)\n"
            f"**Uptime:** {get_uptime()}\n"
            f"**Servers:** `{len(bot.guilds)}`\n"
            f"**Users:** `{sum(g.member_count for g in bot.guilds)}`\n"
            f"**Commands:** `{len(bot.tree.get_commands())}`\n\n"
            f"Use `/help` to explore commands."
        ),
        thumbnail=False
    )
    banner = await get_banner()
    logo = await get_logo()
    files = []
    if banner:
        embed.set_image(url="attachment://banner.png")
        files.append(banner)
    if logo:
        embed.set_thumbnail(url="attachment://logo.png")
        files.append(logo)
    if files:
        await interaction.followup.send(embed=embed, files=files)
    else:
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Display interactive help menu")
async def help_command(interaction: discord.Interaction):
    """Show a paginated help menu with all commands categorized."""
    embeds = []
    # Page 1: General
    e1 = create_embed("📋 General Commands", "Basic commands for everyone.")
    e1.add_field(name="`/info`", value="Bot information", inline=True)
    e1.add_field(name="`/ping`", value="Bot latency", inline=True)
    e1.add_field(name="`/avatar [user]`", value="User avatar", inline=True)
    e1.add_field(name="`/serverinfo`", value="Server details", inline=True)
    e1.add_field(name="`/userinfo [user]`", value="User details", inline=True)
    e1.add_field(name="`/invite`", value="Invite bot", inline=True)
    e1.add_field(name="`/help`", value="This menu", inline=True)
    embeds.append(e1)

    # Page 2: Fun
    e2 = create_embed("🎉 Fun Commands", "Entertainment commands.")
    e2.add_field(name="`/roll [sides]`", value="Roll a dice", inline=True)
    e2.add_field(name="`/coinflip`", value="Flip a coin", inline=True)
    e2.add_field(name="`/joke`", value="Random joke", inline=True)
    e2.add_field(name="`/8ball <question>`", value="Magic 8-ball", inline=True)
    e2.add_field(name="`/rps <choice>`", value="Rock-Paper-Scissors", inline=True)
    embeds.append(e2)

    # Page 3: Utility
    e3 = create_embed("🛠️ Utility Commands", "Useful tools.")
    e3.add_field(name="`/poll <question> [options]`", value="Create a poll", inline=True)
    e3.add_field(name="`/timer <seconds> [reminder]`", value="Set a timer", inline=True)
    e3.add_field(name="`/afk [reason]`", value="Set AFK status", inline=True)
    e3.add_field(name="`/calc <expression>`", value="Calculate math", inline=True)
    e3.add_field(name="`/servericon`", value="Get server icon", inline=True)
    e3.add_field(name="`/banner`", value="Get server banner", inline=True)
    e3.add_field(name="`/tempvc`", value="Create temporary VC", inline=True)
    e3.add_field(name="`/voiceinfo`", value="Current VC info", inline=True)
    embeds.append(e3)

    # Page 4: Moderation
    e4 = create_embed("🛡️ Moderation Commands", "Requires appropriate permissions.")
    e4.add_field(name="`/kick <member> [reason]`", value="Kick member", inline=True)
    e4.add_field(name="`/ban <member> [reason]`", value="Ban member", inline=True)
    e4.add_field(name="`/clear <amount>`", value="Clear messages", inline=True)
    e4.add_field(name="`/timeout <member> <minutes> [reason]`", value="Timeout member", inline=True)
    e4.add_field(name="`/warn <member> <reason>`", value="Warn member", inline=True)
    e4.add_field(name="`/warnings [member]`", value="List warnings", inline=True)
    e4.add_field(name="`/clearwarns <member>`", value="Clear warnings", inline=True)
    e4.add_field(name="`/mute <member> [reason]`", value="Mute member (role)", inline=True)
    e4.add_field(name="`/unmute <member>`", value="Unmute member", inline=True)
    e4.add_field(name="`/deafen <member>`", value="Deafen in VC", inline=True)
    e4.add_field(name="`/undeafen <member>`", value="Undeafen in VC", inline=True)
    e4.add_field(name="`/move <member> <channel>`", value="Move member to VC", inline=True)
    e4.add_field(name="`/vcmoveall <from_channel> <to_channel>`", value="Move all members", inline=True)
    e4.add_field(name="`/voicekick <member>`", value="Disconnect from VC", inline=True)
    e4.add_field(name="`/lockdown [channel]`", value="Lock a channel", inline=True)
    e4.add_field(name="`/slowmode <seconds> [channel]`", value="Set slowmode", inline=True)
    e4.add_field(name="`/addrole <member> <role>`", value="Add role", inline=True)
    e4.add_field(name="`/removerole <member> <role>`", value="Remove role", inline=True)
    e4.add_field(name="`/setlogs <channel>`", value="Set log channel", inline=True)
    embeds.append(e4)

    # Page 5: Voice
    e5 = create_embed("🔊 Voice Commands", "Voice channel management.")
    e5.add_field(name="`/tempvc`", value="Create temporary VC", inline=True)
    e5.add_field(name="`/voiceinfo`", value="Info about current VC", inline=True)
    e5.add_field(name="`/vcmove <member> <channel>`", value="Move member", inline=True)
    e5.add_field(name="`/vcmoveall <from> <to>`", value="Move all", inline=True)
    e5.add_field(name="`/voicekick <member>`", value="Disconnect", inline=True)
    embeds.append(e5)

    logo = await get_logo()
    view = PaginatorView(embeds)
    await safe_reply(interaction, embeds[0], file=logo, view=view)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    """Respond with the bot's WebSocket and API latency."""
    latency = round(bot.latency * 1000)
    embed = create_embed("🏓 Pong!", f"**Latency:** `{latency}ms`\n**WebSocket:** `{latency}ms`")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="avatar", description="Get a user's avatar")
@app_commands.describe(user="The user (default yourself)")
async def avatar(interaction: discord.Interaction, user: discord.User = None):
    """Display the avatar of a user in full size."""
    user = user or interaction.user
    embed = create_embed(f"{user.name}'s Avatar")
    embed.set_image(url=user.display_avatar.url)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="serverinfo", description="Detailed server information")
async def serverinfo(interaction: discord.Interaction):
    """Show multiple pages of information about the current server."""
    g = interaction.guild
    embeds = []

    # Embed 1: General
    e1 = create_embed(f"📊 {g.name} - General", thumbnail=False)
    e1.set_thumbnail(url=g.icon.url if g.icon else None)
    e1.add_field(name="🆔 ID", value=g.id, inline=True)
    e1.add_field(name="👑 Owner", value=g.owner.mention, inline=True)
    e1.add_field(name="📅 Created", value=discord.utils.format_dt(g.created_at, style='R'), inline=True)
    e1.add_field(name="🌍 Region", value=str(g.preferred_locale), inline=True)
    e1.add_field(name="👥 Members", value=g.member_count, inline=True)
    e1.add_field(name="🤖 Bots", value=sum(1 for m in g.members if m.bot), inline=True)
    e1.add_field(name="💬 Text Channels", value=len(g.text_channels), inline=True)
    e1.add_field(name="🔊 Voice Channels", value=len(g.voice_channels), inline=True)
    e1.add_field(name="🎭 Roles", value=len(g.roles), inline=True)
    e1.add_field(name="😀 Emojis", value=len(g.emojis), inline=True)
    e1.add_field(name="✨ Boost Level", value=g.premium_tier, inline=True)
    e1.add_field(name="🚀 Boosts", value=g.premium_subscription_count, inline=True)
    embeds.append(e1)

    # Embed 2: Channels
    e2 = create_embed(f"📁 Channels", thumbnail=False)
    categories = {}
    for channel in g.channels:
        if channel.category:
            categories.setdefault(channel.category.name, []).append(channel.mention)
        else:
            categories.setdefault("No Category", []).append(channel.mention)
    for cat, chs in categories.items():
        e2.add_field(name=cat, value=", ".join(chs[:5]) + ("..." if len(chs) > 5 else ""), inline=False)
    embeds.append(e2)

    # Embed 3: Roles (top 15)
    e3 = create_embed(f"🎭 Roles ({len(g.roles)})", thumbnail=False)
    roles = sorted(g.roles, key=lambda r: r.position, reverse=True)[:15]
    e3.description = " ".join([r.mention for r in roles if r.name != "@everyone"])
    embeds.append(e3)

    # Embed 4: Emojis
    e4 = create_embed(f"😀 Emojis ({len(g.emojis)})", thumbnail=False)
    if g.emojis:
        e4.description = " ".join([str(e) for e in g.emojis[:30]])
    else:
        e4.description = "No emojis."
    embeds.append(e4)

    logo = await get_logo()
    view = PaginatorView(embeds)
    await safe_reply(interaction, embeds[0], file=logo, view=view)

@bot.tree.command(name="userinfo", description="Detailed user information")
@app_commands.describe(user="The user (default yourself)")
async def userinfo(interaction: discord.Interaction, user: discord.User = None):
    """Show detailed information about a user, including roles, permissions, and voice status."""
    user = user or interaction.user
    member = interaction.guild.get_member(user.id) if interaction.guild else None
    embed = create_embed(f"{user.name}#{user.discriminator}")
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="🆔 ID", value=user.id, inline=True)
    embed.add_field(name="📆 Account Created", value=discord.utils.format_dt(user.created_at, style='R'), inline=True)
    if member:
        embed.add_field(name="📥 Joined Server", value=discord.utils.format_dt(member.joined_at, style='R'), inline=True)
        embed.add_field(name="📛 Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="🎤 Voice Channel", value=member.voice.channel.mention if member.voice else "None", inline=True)
        embed.add_field(name="📶 Status", value=str(member.status).title(), inline=True)
        embed.add_field(name="🎮 Activity", value=member.activity.name if member.activity else "None", inline=True)
        # Key permissions
        perms = []
        if member.guild_permissions.administrator:
            perms.append("Administrator")
        if member.guild_permissions.manage_guild:
            perms.append("Manage Server")
        if member.guild_permissions.manage_messages:
            perms.append("Manage Messages")
        if member.guild_permissions.kick_members:
            perms.append("Kick Members")
        if member.guild_permissions.ban_members:
            perms.append("Ban Members")
        embed.add_field(name="🔑 Key Permissions", value=", ".join(perms) if perms else "None", inline=False)
        # Roles
        roles = [r.mention for r in member.roles[1:]][:15]
        embed.add_field(name=f"🎭 Roles [{len(member.roles)-1}]", value=" ".join(roles) if roles else "None", inline=False)
    else:
        embed.add_field(name="📌 Note", value="User not in this server", inline=False)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="invite", description="Bot invite link")
async def invite(interaction: discord.Interaction):
    """Generate an OAuth2 invite link with the necessary permissions."""
    perms = discord.Permissions(
        kick_members=True, ban_members=True, moderate_members=True,
        manage_messages=True, read_messages=True, send_messages=True,
        embed_links=True, attach_files=True, read_message_history=True,
        use_external_emojis=True, add_reactions=True, move_members=True,
        mute_members=True, deafen_members=True
    )
    url = discord.utils.oauth_url(bot.user.id, permissions=perms)
    embed = create_embed("🔗 Invite Me", f"[Click to add {BOT_NAME} to your server]({url})")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

# ---------- Fun Commands ----------
@bot.tree.command(name="roll", description="Roll a dice")
@app_commands.describe(sides="Number of sides (default 6)")
async def roll(interaction: discord.Interaction, sides: int = 6):
    """Roll a dice with a given number of sides."""
    result = random.randint(1, sides)
    embed = create_embed("🎲 Dice Roll", f"You rolled **{result}** (1-{sides})")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    """Flip a coin and return Heads or Tails."""
    result = random.choice(["Heads", "Tails"])
    embed = create_embed("🪙 Coin Flip", f"Result: **{result}**")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="joke", description="Random joke")
async def joke(interaction: discord.Interaction):
    """Return a random joke from a predefined list."""
    jokes = [
        "Why don't scientists trust atoms? Because they make up everything!",
        "What do you call a fake noodle? An impasta!",
        "Why did the scarecrow win an award? Because he was outstanding in his field!",
        "How does a penguin build its house? Igloos it together!",
        "Why don't skeletons fight each other? They don't have the guts.",
        "What do you call a fish with no eyes? A fsh.",
        "Why can't you give Elsa a balloon? Because she will let it go.",
        "What's orange and sounds like a parrot? A carrot.",
        "How do you make holy water? You boil the hell out of it.",
        "Why did the coffee file a police report? It got mugged."
    ]
    embed = create_embed("😂 Random Joke", random.choice(jokes))
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="8ball", description="Ask the magic 8-ball")
@app_commands.describe(question="Your question")
async def eightball(interaction: discord.Interaction, question: str):
    """Answer a yes/no question with a random 8-ball response."""
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes definitely.",
        "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
        "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.",
        "Very doubtful."
    ]
    embed = create_embed("🎱 Magic 8-Ball", f"**Q:** {question}\n**A:** {random.choice(responses)}")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="rps", description="Rock-Paper-Scissors")
@app_commands.describe(choice="rock / paper / scissors")
async def rps(interaction: discord.Interaction, choice: str):
    """Play Rock-Paper-Scissors against the bot."""
    choices = ["rock", "paper", "scissors"]
    if choice.lower() not in choices:
        embed = create_embed("❌ Invalid", "Use `rock`, `paper`, or `scissors`.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    bot_choice = random.choice(choices)
    if choice.lower() == bot_choice:
        result = "It's a tie!"
    elif (choice.lower() == "rock" and bot_choice == "scissors") or \
         (choice.lower() == "paper" and bot_choice == "rock") or \
         (choice.lower() == "scissors" and bot_choice == "paper"):
        result = "You win!"
    else:
        result = "I win!"
    embed = create_embed("✂️ Rock-Paper-Scissors",
                         f"**You:** {choice.capitalize()}\n**Bot:** {bot_choice.capitalize()}\n\n**{result}**")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

# ---------- Utility Commands ----------
@bot.tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question", options="Comma-separated options (max 9)")
async def poll(interaction: discord.Interaction, question: str, options: str = "Yes,No"):
    """Create a poll with up to 9 options; users vote by reacting with number emojis."""
    opts = [o.strip() for o in options.split(",")][:9]
    if len(opts) < 2:
        embed = create_embed("❌ Error", "Provide at least 2 options.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"][:len(opts)]
    desc = f"**{question}**\n\n"
    for i, opt in enumerate(opts):
        desc += f"{emojis[i]} {opt}\n"
    embed = create_embed("📊 Poll", desc)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)
    msg = await interaction.original_response()
    for e in emojis:
        await msg.add_reaction(e)

@bot.tree.command(name="timer", description="Set a timer")
@app_commands.describe(seconds="Time in seconds", reminder="Optional reminder")
async def timer(interaction: discord.Interaction, seconds: int, reminder: str = ""):
    """Start a timer; after the specified seconds, the bot will mention the user."""
    if seconds <= 0 or seconds > 86400:
        embed = create_embed("❌ Invalid", "Choose between 1 and 86400 seconds.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    embed = create_embed("⏰ Timer Set", f"Timer for **{seconds}**s started.\nI'll ping you when done.")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)
    await asyncio.sleep(seconds)
    await interaction.followup.send(f"{interaction.user.mention} Timer finished! {reminder}")

@bot.tree.command(name="afk", description="Set an AFK status")
@app_commands.describe(reason="Reason (default: AFK)")
async def afk(interaction: discord.Interaction, reason: str = "AFK"):
    """Mark yourself as AFK. When mentioned, others will be notified. Removed when you speak."""
    bot.afk_users[interaction.user.id] = (reason, time.time())
    embed = create_embed("🟢 AFK Set", f"You are now AFK: **{reason}**")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.event
async def on_message(message):
    """Handle AFK detection and removal."""
    if message.author.bot:
        return
    # If someone mentions an AFK user, notify the sender
    for user in message.mentions:
        if user.id in bot.afk_users:
            r, t = bot.afk_users[user.id]
            dur = int(time.time() - t)
            await message.channel.send(f"{message.author.mention} {user.display_name} is AFK: **{r}** (for {dur}s)", delete_after=10)
    # Remove AFK when user speaks
    if message.author.id in bot.afk_users:
        del bot.afk_users[message.author.id]
        await message.channel.send(f"Welcome back {message.author.mention}! I removed your AFK.", delete_after=5)
    await bot.process_commands(message)

@bot.tree.command(name="calc", description="Calculate math expression")
@app_commands.describe(expression="e.g. 2+2*5")
async def calc(interaction: discord.Interaction, expression: str):
    """Safely evaluate a mathematical expression using math module functions."""
    try:
        allowed = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        allowed.update({"abs": abs, "round": round})
        res = eval(expression, {"__builtins__": {}}, allowed)
        embed = create_embed("🧮 Calculator", f"**Expr:** `{expression}`\n**Result:** `{res}`")
    except Exception:
        embed = create_embed("❌ Error", "Invalid expression.", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="servericon", description="Get server icon")
async def servericon(interaction: discord.Interaction):
    """Display the server's icon in full size."""
    if not interaction.guild.icon:
        embed = create_embed("No Icon", "This server has no icon.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    embed = create_embed(f"{interaction.guild.name}'s Icon")
    embed.set_image(url=interaction.guild.icon.url)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="banner", description="Get server banner")
async def serverbanner(interaction: discord.Interaction):
    """Display the server's banner if available."""
    if not interaction.guild.banner:
        embed = create_embed("No Banner", "This server has no banner.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    embed = create_embed(f"{interaction.guild.name}'s Banner")
    embed.set_image(url=interaction.guild.banner.url)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="tempvc", description="Create a temporary voice channel")
@app_commands.describe(name="Channel name", limit="User limit (0 = unlimited)")
async def tempvc(interaction: discord.Interaction, name: str = "Temp VC", limit: int = 0):
    """Create a temporary voice channel that will be deleted when empty."""
    if not interaction.user.voice:
        embed = create_embed("❌ Not in VC", "You must be in a voice channel to use this.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    category = interaction.user.voice.channel.category
    try:
        vc = await interaction.guild.create_voice_channel(
            name=name,
            category=category,
            user_limit=limit,
            reason=f"Temporary VC created by {interaction.user}"
        )
        bot.temp_vc_channels[vc.id] = (interaction.user.id, time.time())
        await interaction.user.move_to(vc)
        embed = create_embed("✅ Temporary VC Created", f"{vc.mention} has been created.\nIt will be deleted when empty.", color=SUCCESS)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed to create VC: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="voiceinfo", description="Show info about current voice channel")
async def voiceinfo(interaction: discord.Interaction):
    """Display details about the voice channel you are currently in."""
    if not interaction.user.voice:
        embed = create_embed("❌ Not in VC", "You are not in a voice channel.", color=ERROR)
        logo = await get_logo()
        return await safe_reply(interaction, embed, file=logo)
    vc = interaction.user.voice.channel
    members = vc.members
    member_list = "\n".join([f"{m.mention} { '(Muted)' if m.voice.mute else ''}{ '(Deafened)' if m.voice.deaf else ''}" for m in members[:10]])
    if len(members) > 10:
        member_list += f"\n... and {len(members)-10} more"
    embed = create_embed(f"🔊 {vc.name}")
    embed.add_field(name="ID", value=vc.id, inline=True)
    embed.add_field(name="Bitrate", value=f"{vc.bitrate//1000} kbps", inline=True)
    embed.add_field(name="User Limit", value=vc.user_limit or "Unlimited", inline=True)
    embed.add_field(name="Members", value=str(len(members)), inline=True)
    embed.add_field(name="Region", value=str(vc.rtc_region or "Auto"), inline=True)
    embed.add_field(name="Category", value=vc.category.name if vc.category else "None", inline=True)
    embed.add_field(name="Member List", value=member_list or "None", inline=False)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

# ---------- Moderation Commands ----------
@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = None):
    """Kick a member from the server after confirmation."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Kick", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    # Confirmation
    view = ConfirmView(interaction, member, "kick", reason)
    embed_confirm = create_embed("⚠️ Confirm Kick", f"Are you sure you want to kick {member.mention}?\nReason: {reason or 'None'}", color=WARNING)
    await safe_reply(interaction, embed_confirm, view=view)
    await view.wait()
    if view.value:
        try:
            await member.kick(reason=reason)
            embed = create_embed("✅ Member Kicked", f"{member.mention} has been kicked.\nReason: {reason or 'None'}", color=SUCCESS)
            # Log
            log_embed = create_embed("👢 Kick", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}\n**Reason:** {reason or 'None'}", color=WARNING)
            log_to_channel(interaction.guild_id, log_embed)
        except Exception as e:
            embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
        logo = await get_logo()
        await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = None):
    """Ban a member from the server after confirmation."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Ban", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    view = ConfirmView(interaction, member, "ban", reason)
    embed_confirm = create_embed("⚠️ Confirm Ban", f"Are you sure you want to ban {member.mention}?\nReason: {reason or 'None'}", color=WARNING)
    await safe_reply(interaction, embed_confirm, view=view)
    await view.wait()
    if view.value:
        try:
            await member.ban(reason=reason)
            embed = create_embed("✅ Member Banned", f"{member.mention} has been banned.\nReason: {reason or 'None'}", color=SUCCESS)
            log_embed = create_embed("🔨 Ban", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}\n**Reason:** {reason or 'None'}", color=ERROR)
            log_to_channel(interaction.guild_id, log_embed)
        except Exception as e:
            embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
        logo = await get_logo()
        await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="clear", description="Clear messages")
@app_commands.describe(amount="Number of messages (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    """Delete a specified number of messages from the current channel."""
    if amount < 1 or amount > 100:
        embed = create_embed("❌ Invalid", "Choose 1-100.", color=ERROR)
        return await safe_reply(interaction, embed)
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        embed = create_embed("✅ Cleared", f"Deleted **{len(deleted)}** messages.", color=SUCCESS)
        log_embed = create_embed("🧹 Clear", f"**Channel:** {interaction.channel.mention}\n**Amount:** {len(deleted)}\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="timeout", description="Timeout a member")
@app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = None):
    """Timeout (mute in chat) a member for a specified duration."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Timeout", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)
        embed = create_embed("✅ Timed Out", f"{member.mention} timed out for {minutes}min.\nReason: {reason or 'None'}", color=SUCCESS)
        log_embed = create_embed("⏳ Timeout", f"**User:** {member} ({member.id})\n**Duration:** {minutes}min\n**Mod:** {interaction.user}\n**Reason:** {reason or 'None'}", color=WARNING)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    """Issue a warning to a member. Warning is stored in the database and DM sent."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Warn", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    # Add to database
    c.execute("INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
              (interaction.guild_id, member.id, interaction.user.id, reason, int(time.time())))
    conn.commit()
    try:
        await member.send(f"⚠️ You have been warned in **{interaction.guild.name}** by {interaction.user.mention}\nReason: {reason}")
        embed = create_embed("✅ Warning Issued", f"{member.mention} warned.\nReason: {reason}", color=SUCCESS)
    except:
        embed = create_embed("⚠️ Warning Issued", f"{member.mention} warned (DM failed).\nReason: {reason}", color=SUCCESS)
    log_embed = create_embed("⚠️ Warning", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}\n**Reason:** {reason}", color=WARNING)
    log_to_channel(interaction.guild_id, log_embed)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="warnings", description="List warnings for a member")
@app_commands.describe(member="Member (default yourself)")
@app_commands.checks.has_permissions(manage_messages=True)
async def warnings(interaction: discord.Interaction, member: discord.Member = None):
    """Display all warnings for a given member."""
    member = member or interaction.user
    c.execute("SELECT id, moderator_id, reason, timestamp FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC",
              (interaction.guild_id, member.id))
    rows = c.fetchall()
    if not rows:
        embed = create_embed("📋 Warnings", f"{member.mention} has no warnings.", color=INFO)
    else:
        desc = ""
        for i, (wid, mod_id, reason, ts) in enumerate(rows[:10], 1):
            mod = bot.get_user(mod_id) or f"<@{mod_id}>"
            desc += f"**{i}.** <t:{ts}:R> by {mod}: {reason}\n"
        embed = create_embed(f"📋 Warnings for {member.display_name}", desc[:4000], color=INFO)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="clearwarns", description="Clear all warnings for a member")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarns(interaction: discord.Interaction, member: discord.Member):
    """Delete all warning records for a member."""
    c.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id))
    conn.commit()
    embed = create_embed("✅ Warnings Cleared", f"All warnings for {member.mention} have been cleared.", color=SUCCESS)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="mute", description="Mute a member (assign mute role)")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.checks.has_permissions(mute_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, reason: str = None):
    """Assign the mute role to a member, preventing them from speaking in text channels."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Mute", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    # Fetch or create mute role
    mute_role_id = None
    c.execute("SELECT mute_role FROM guild_settings WHERE guild_id = ?", (interaction.guild_id,))
    row = c.fetchone()
    if row and row[0]:
        mute_role = interaction.guild.get_role(row[0])
    else:
        # Create mute role
        mute_role = await interaction.guild.create_role(name="Muted", reason="Auto-created mute role")
        # Overwrite permissions in all channels
        for channel in interaction.guild.channels:
            await channel.set_permissions(mute_role, send_messages=False, speak=False)
        c.execute("INSERT OR REPLACE INTO guild_settings (guild_id, mute_role) VALUES (?, ?)",
                  (interaction.guild_id, mute_role.id))
        conn.commit()
    try:
        await member.add_roles(mute_role, reason=reason)
        embed = create_embed("✅ Member Muted", f"{member.mention} has been muted.\nReason: {reason or 'None'}", color=SUCCESS)
        log_embed = create_embed("🔇 Mute", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}\n**Reason:** {reason or 'None'}", color=WARNING)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="unmute", description="Unmute a member")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(mute_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    """Remove the mute role from a member."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Unmute", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    c.execute("SELECT mute_role FROM guild_settings WHERE guild_id = ?", (interaction.guild_id,))
    row = c.fetchone()
    if not row or not row[0]:
        embed = create_embed("❌ No Mute Role", "Mute role not set up.", color=ERROR)
        return await safe_reply(interaction, embed)
    mute_role = interaction.guild.get_role(row[0])
    if not mute_role:
        embed = create_embed("❌ No Mute Role", "Mute role not found.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.remove_roles(mute_role)
        embed = create_embed("✅ Member Unmuted", f"{member.mention} has been unmuted.", color=SUCCESS)
        log_embed = create_embed("🔊 Unmute", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="deafen", description="Deafen a member in voice")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(deafen_members=True)
async def deafen(interaction: discord.Interaction, member: discord.Member):
    """Deafen a member in voice chat (they won't hear anything)."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Deafen", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    if not member.voice:
        embed = create_embed("❌ Not in VC", "User is not in a voice channel.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.edit(deafen=True)
        embed = create_embed("✅ Member Deafened", f"{member.mention} has been deafened.", color=SUCCESS)
        log_embed = create_embed("🔇 Deafen", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}", color=WARNING)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="undeafen", description="Undeafen a member in voice")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(deafen_members=True)
async def undeafen(interaction: discord.Interaction, member: discord.Member):
    """Remove deafen from a member in voice chat."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Undeafen", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.edit(deafen=False)
        embed = create_embed("✅ Member Undeafened", f"{member.mention} has been undeafened.", color=SUCCESS)
        log_embed = create_embed("🔊 Undeafen", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="move", description="Move a member to another voice channel")
@app_commands.describe(member="Member", channel="Target voice channel")
@app_commands.checks.has_permissions(move_members=True)
async def move(interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
    """Move a specific member from their current voice channel to another."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Move", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    if not member.voice:
        embed = create_embed("❌ Not in VC", "User is not in a voice channel.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.move_to(channel)
        embed = create_embed("✅ Member Moved", f"{member.mention} moved to {channel.mention}.", color=SUCCESS)
        log_embed = create_embed("🚚 Move", f"**User:** {member} ({member.id})\n**To:** {channel.name}\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="vcmoveall", description="Move all members from one VC to another")
@app_commands.describe(from_channel="Source channel", to_channel="Target channel")
@app_commands.checks.has_permissions(move_members=True)
async def vcmoveall(interaction: discord.Interaction, from_channel: discord.VoiceChannel, to_channel: discord.VoiceChannel):
    """Move every member from one voice channel to another."""
    members = from_channel.members
    if not members:
        embed = create_embed("❌ No Members", "Source channel is empty.", color=ERROR)
        return await safe_reply(interaction, embed)
    count = 0
    for m in members:
        try:
            await m.move_to(to_channel)
            count += 1
        except:
            pass
    embed = create_embed("✅ Members Moved", f"Moved **{count}** members from {from_channel.mention} to {to_channel.mention}.", color=SUCCESS)
    log_embed = create_embed("🚚 Mass Move", f"**From:** {from_channel.name}\n**To:** {to_channel.name}\n**Count:** {count}\n**Mod:** {interaction.user}", color=INFO)
    log_to_channel(interaction.guild_id, log_embed)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="voicekick", description="Disconnect a member from voice")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(move_members=True)
async def voicekick(interaction: discord.Interaction, member: discord.Member):
    """Disconnect a member from voice chat (move them to None)."""
    if not hierarchy_check(interaction, member):
        embed = create_embed("❌ Cannot Disconnect", "Role hierarchy prevents this.", color=ERROR)
        return await safe_reply(interaction, embed)
    if not member.voice:
        embed = create_embed("❌ Not in VC", "User is not in a voice channel.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.move_to(None)
        embed = create_embed("✅ Member Disconnected", f"{member.mention} has been disconnected from voice.", color=SUCCESS)
        log_embed = create_embed("🔇 Voice Kick", f"**User:** {member} ({member.id})\n**Mod:** {interaction.user}", color=WARNING)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="lockdown", description="Lock a channel (disable send messages for @everyone)")
@app_commands.describe(channel="Channel to lock (default current)")
@app_commands.checks.has_permissions(manage_channels=True)
async def lockdown(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """Prevent @everyone from sending messages in a text channel."""
    channel = channel or interaction.channel
    try:
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        embed = create_embed("🔒 Channel Locked", f"{channel.mention} has been locked.", color=SUCCESS)
        log_embed = create_embed("🔒 Lockdown", f"**Channel:** {channel.mention}\n**Mod:** {interaction.user}", color=WARNING)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="slowmode", description="Set slowmode in a channel")
@app_commands.describe(seconds="Seconds between messages (0 to disable)", channel="Channel (default current)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    """Set the slowmode delay for a text channel."""
    channel = channel or interaction.channel
    try:
        await channel.edit(slowmode_delay=seconds)
        if seconds > 0:
            embed = create_embed("🐢 Slowmode Set", f"Slowmode in {channel.mention} set to **{seconds}s**.", color=SUCCESS)
        else:
            embed = create_embed("🐢 Slowmode Disabled", f"Slowmode in {channel.mention} disabled.", color=SUCCESS)
        log_embed = create_embed("🐢 Slowmode", f"**Channel:** {channel.mention}\n**Seconds:** {seconds}\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="addrole", description="Add a role to a member")
@app_commands.describe(member="Member", role="Role to add")
@app_commands.checks.has_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    """Assign a role to a member."""
    if role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        embed = create_embed("❌ Cannot Add Role", "Role is higher or equal to your top role.", color=ERROR)
        return await safe_reply(interaction, embed)
    if role in member.roles:
        embed = create_embed("❌ Already Has Role", f"{member.mention} already has {role.mention}.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.add_roles(role)
        embed = create_embed("✅ Role Added", f"Added {role.mention} to {member.mention}.", color=SUCCESS)
        log_embed = create_embed("➕ Add Role", f"**User:** {member} ({member.id})\n**Role:** {role.name}\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(member="Member", role="Role to remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    """Remove a role from a member."""
    if role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        embed = create_embed("❌ Cannot Remove Role", "Role is higher or equal to your top role.", color=ERROR)
        return await safe_reply(interaction, embed)
    if role not in member.roles:
        embed = create_embed("❌ Doesn't Have Role", f"{member.mention} does not have {role.mention}.", color=ERROR)
        return await safe_reply(interaction, embed)
    try:
        await member.remove_roles(role)
        embed = create_embed("✅ Role Removed", f"Removed {role.mention} from {member.mention}.", color=SUCCESS)
        log_embed = create_embed("➖ Remove Role", f"**User:** {member} ({member.id})\n**Role:** {role.name}\n**Mod:** {interaction.user}", color=INFO)
        log_to_channel(interaction.guild_id, log_embed)
    except Exception as e:
        embed = create_embed("❌ Error", f"Failed: {e}", color=ERROR)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

@bot.tree.command(name="setlogs", description="Set the log channel for moderation actions")
@app_commands.describe(channel="Channel to send logs")
@app_commands.checks.has_permissions(administrator=True)
async def setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    """Configure a text channel to receive moderation logs."""
    c.execute("INSERT OR REPLACE INTO guild_settings (guild_id, log_channel) VALUES (?, ?)",
              (interaction.guild_id, channel.id))
    conn.commit()
    embed = create_embed("✅ Log Channel Set", f"Logs will be sent to {channel.mention}.", color=SUCCESS)
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo)

# -------------------- Error Handler --------------------
@bot.tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for slash commands."""
    if interaction is None:
        print(f"Error without interaction: {error}")
        return
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(error.missing_permissions)
        embed = create_embed("❌ Permission Denied", f"You need: **{missing}**", color=ERROR)
    elif isinstance(error, app_commands.CommandOnCooldown):
        embed = create_embed("⏳ Cooldown", f"Try again in {error.retry_after:.1f}s.", color=ERROR)
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        embed = create_embed("❌ Bot Missing Permissions", f"I need: **{missing}**", color=ERROR)
    else:
        embed = create_embed("❌ Error", "Something went wrong.", color=ERROR)
        print(f"Unhandled: {error}")
    logo = await get_logo()
    await safe_reply(interaction, embed, file=logo, ephemeral=True)

# -------------------- Run --------------------
if __name__ == "__main__":
    bot.run(TOKEN)