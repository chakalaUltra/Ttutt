# security4_part1.py
import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
import urllib.parse

# Config
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "https://ttutt-2.onrender.com/oauth/callback")

CONFIG_PATH = "server_configs.json"
BLACKLISTED_PATH = "blacklisted_servers.json"
PERSISTENT_VERIFICATION_VIEWS_PATH = "persistent_verification_views.json"
PERSISTENT_LEAVE_VIEWS_PATH = "persistent_leave_views.json"

# Bot owner
BOT_OWNER_ID = 1117540437016727612  # adjust if needed

# JSON helpers
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def ensure_persist_file(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4, ensure_ascii=False)

def load_json_safe(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def add_persist_view(path, guild_id):
    ensure_persist_file(path)
    ids = load_json_safe(path, [])
    gid = str(guild_id)
    if gid not in ids:
        ids.append(gid)
        save_json(path, ids)

def get_server_config(guild_id):
    cfg = load_json(CONFIG_PATH, {})
    gid = str(guild_id)
    if gid not in cfg:
        cfg[gid] = {
            "flag_channel_id": None,
            "verified_role_id": None,
            "unverified_role_id": None,
            "log_channel_id": None,
            "blacklisted_servers": {}
        }
        save_json(CONFIG_PATH, cfg)
    return cfg[gid]

# Discord bot setup
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=";", intents=intents)

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

def is_bot_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == BOT_OWNER_ID
    # security4_part2.py
import discord
import urllib.parse

# Note: This file relies on definitions from Part 1 (bot, BOT_OWNER_ID, etc.)
# No imports from other files are used here to keep it self-contained when concatenated.

class PermanentLeaveView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = int(guild_id)

    @discord.ui.button(label="🚪 Leave Server", style=discord.ButtonStyle.grey, custom_id="leave_server_button")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this button.", ephemeral=True)
            return

        guild = bot.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message("⚠️ Guild not found or bot not in this guild.", ephemeral=True)
            return

        try:
            await guild.leave()
            embed = discord.Embed(title="✅ Left Server",
                                description=f"Bot has left **{guild.name}** (`{guild.id}`)",
                                color=0x00FF00)
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            embed = discord.Embed(title="❌ Error Leaving Server",
                                description=f"Could not leave **{guild.name}**: {str(e)}",
                                color=0xFF0000)
            await interaction.response.edit_message(embed=embed, view=None)

class PersistentVerificationView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = int(guild_id)

    @discord.ui.button(label="🔐 Verify", style=discord.ButtonStyle.grey, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds",
            "prompt": "consent",
            "state": str(self.guild_id)
        }
        url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"

        embed = discord.Embed(title="🔗 Verification Link",
                              description=f"[🔐 Click here to complete verification]({url})\n\nThis will check your Discord servers and verify your account.",
                              color=0x00D4FF)
        embed.set_footer(text="🔒 Secure OAuth2 Verification")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await bot.change_presence(status=discord.Status.dnd)
    except Exception:
        pass

    ensure_persist_file(PERSISTENT_VERIFICATION_VIEWS_PATH)
    ensure_persist_file(PERSISTENT_LEAVE_VIEWS_PATH)

    # Load existing views
    try:
        ids = load_json_safe(PERSISTENT_VERIFICATION_VIEWS_PATH, [])
        for gid in ids:
            bot.add_view(PersistentVerificationView(int(gid)))
        leave_ids = load_json_safe(PERSISTENT_LEAVE_VIEWS_PATH, [])
        for gid in leave_ids:
            bot.add_view(PermanentLeaveView(int(gid)))
        print("Persistent views loaded.")
    except Exception as e:
        print(f"Error loading persistent views: {e}")

    try:
        await bot.tree.sync()
        print("Commands synced.")
    except Exception as e:
        print(f"Sync failed: {e}")

@bot.event
async def on_guild_join(guild):
    try:
        add_persist_view(PERSISTENT_VERIFICATION_VIEWS_PATH, guild.id)
        add_persist_view(PERSISTENT_LEAVE_VIEWS_PATH, guild.id)
        await notify_bot_owner_server_join(guild)
        print(f"Joined guild {guild.name} and registered for permanent views.")
    except Exception as e:
        print(f"Error during on_guild_join for {guild.name}: {e}")

async def notify_bot_owner_server_join(guild):
    owner = bot.get_user(BOT_OWNER_ID)
    if owner:
        embed = discord.Embed(title="🚀 Bot Added to New Server", color=0xFFFFFF)
        embed.add_field(name="Server Name", value=guild.name, inline=False)
        embed.add_field(name="Server ID", value=str(guild.id), inline=False)
        embed.set_footer(text=f"Total Servers: {len(bot.guilds)}")
        view = PermanentLeaveView(guild.id)
        bot.add_view(view)
        try:
            await owner.send(embed=embed, view=view)
        except Exception:
            pass
    else:
        print("Bot owner not found.")
        # security4_part3.py
import asyncio
import urllib.parse
import discord
from discord.ext import tasks
from discord import app_commands

# Assumes definitions from Part 1 (bot, get_server_config, load_json_safe, save_json, log_action, etc.)

verification_queue = asyncio.Queue()

@tasks.loop(seconds=1)
async def verify_task():
    if not verification_queue.empty():
        print(f"Processing {verification_queue.qsize()} items in verification queue")
    while not verification_queue.empty():
        data = await verification_queue.get()
        await process_verification(data)

async def process_verification(data):
    user_id = data["user_id"]
    guild_ids = data["guild_ids"]
    username = data["username"]
    target_guild_id = data.get("target_guild_id")

    # Audit data
    user_data = load_json_safe("user_verification_data.json", {})
    guild_str = str(target_guild_id)
    if guild_str not in user_data:
        user_data[guild_str] = {}
    user_data[guild_str][str(user_id)] = {
        "username": username,
        "guild_ids": guild_ids,
        "timestamp": discord.utils.utcnow().isoformat()
    }
    save_json("user_verification_data.json", user_data)

    guild = bot.get_guild(target_guild_id)
    if not guild:
        print("Bot not in target guild!")
        return
    member = guild.get_member(user_id)
    if not member:
        print(f"User {user_id} not in guild.")
        return

    config = get_server_config(guild.id)
    log_channel = bot.get_channel(config.get("log_channel_id")) if config.get("log_channel_id") else None

    blacklist = config.get("blacklisted_servers", {})
    flagged = any(sid in guild_ids for sid in blacklist.keys())

    if flagged:
        if log_channel:
            embed = discord.Embed(
                title="🚨 Security Alert - User Flagged",
                description=f"User {member.mention} flagged due to blacklist membership.",
                color=0xFF4444
            )
            await log_channel.send(embed=embed)
        try:
            await member.send(discord.Embed(title="❌ Verification Failed",
                                            description="You are in a blacklisted server.",
                                            color=0xFF4444))
        except Exception:
            pass
        return

    # Success: grant verified role, remove unverified if present
    verified_role = guild.get_role(config.get("verified_role_id"))
    unverified_role = guild.get_role(config.get("unverified_role_id"))
    if verified_role:
        try:
            await member.add_roles(verified_role)
        except Exception:
            pass
    if unverified_role and unverified_role in member.roles:
        try:
            await member.remove_roles(unverified_role)
        except Exception:
            pass
    try:
        await member.send(discord.Embed(
            title="✅ Verification Successful!",
            description=f"You've been verified in {guild.name}.",
            color=0x00FF00
        ))
    except Exception:
        pass

# Commands
@bot.tree.command(name="verify-panel", description="🛡️ Set the verification panel with permanent button")
@app_commands.check(is_admin)
async def verify_panel(interaction: discord.Interaction):
    await interaction.response.send_message("Panel sent", ephemeral=True)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "prompt": "consent",
        "state": str(interaction.guild.id)
    }
    verification_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"

    embed = discord.Embed(title="🛡️ Verification Required",
                          description=f"Click the button below to verify, or use this direct link: {verification_url}",
                          color=0x00D4FF)
    view = PersistentVerificationView(interaction.guild.id)
    await interaction.channel.send(embed=embed, view=view)
    await log_action(interaction.guild.id, "📋 Verification Panel Created",
                     f"Admin {interaction.user.mention} created a verification panel in {interaction.channel.mention}",
                     0x00D4FF)

@bot.tree.command(name="flag-channel", description="🚩 Set the channel where flagged users are reported")
@app_commands.check(is_admin)
async def flag_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = str(interaction.guild.id)
    config = load_json(CONFIG_PATH, {})
    entry = config.setdefault(gid, {"flag_channel_id": None, "verified_role_id": None, "unverified_role_id": None, "log_channel_id": None, "blacklisted_servers": {}})
    entry["flag_channel_id"] = channel.id
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(title="🚩 Flag Channel Updated", description=f"Flag channel set to {channel.mention}", color=0x00FF00)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await log_action(interaction.guild.id, "🚩 Flag Channel Set",
                     f"Admin {interaction.user.mention} set the flag channel to {channel.mention}", 0x00FF00)

@bot.tree.command(name="set-verified-role", description="✅ Set the verified and unverified roles")
@app_commands.check(is_admin)
@app_commands.describe(verified_role="Role to assign when user passes verification", unverified_role="Role to remove when user passes verification")
async def set_verified_role(interaction: discord.Interaction, verified_role: discord.Role, unverified_role: discord.Role):
    gid = str(interaction.guild.id)
    config = load_json(CONFIG_PATH, {})
    entry = config.setdefault(gid, {"flag_channel_id": None, "verified_role_id": None, "unverified_role_id": None, "log_channel_id": None, "blacklisted_servers": {}})
    entry["verified_role_id"] = verified_role.id
    entry["unverified_role_id"] = unverified_role.id
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(title="✅ Verification Roles Updated",
                        description=f"Verified: {verified_role.mention}, Unverified: {unverified_role.mention}",
                        color=0x00FF00)
    await interaction.response.send_message# security4_part3.py
import asyncio
import urllib.parse
import discord
from discord.ext import tasks
from discord import app_commands

# Assumes definitions from Part 1 (bot, get_server_config, load_json_safe, save_json, log_action, etc.)

verification_queue = asyncio.Queue()

@tasks.loop(seconds=1)
async def verify_task():
    if not verification_queue.empty():
        print(f"Processing {verification_queue.qsize()} items in verification queue")
    while not verification_queue.empty():
        data = await verification_queue.get()
        await process_verification(data)

async def process_verification(data):
    user_id = data["user_id"]
    guild_ids = data["guild_ids"]
    username = data["username"]
    target_guild_id = data.get("target_guild_id")

    # Audit data
    user_data = load_json_safe("user_verification_data.json", {})
    guild_str = str(target_guild_id)
    if guild_str not in user_data:
        user_data[guild_str] = {}
    user_data[guild_str][str(user_id)] = {
        "username": username,
        "guild_ids": guild_ids,
        "timestamp": discord.utils.utcnow().isoformat()
    }
    save_json("user_verification_data.json", user_data)

    guild = bot.get_guild(target_guild_id)
    if not guild:
        print("Bot not in target guild!")
        return
    member = guild.get_member(user_id)
    if not member:
        print(f"User {user_id} not in guild.")
        return

    config = get_server_config(guild.id)
    log_channel = bot.get_channel(config.get("log_channel_id")) if config.get("log_channel_id") else None

    blacklist = config.get("blacklisted_servers", {})
    flagged = any(sid in guild_ids for sid in blacklist.keys())

    if flagged:
        if log_channel:
            embed = discord.Embed(
                title="🚨 Security Alert - User Flagged",
                description=f"User {member.mention} flagged due to blacklist membership.",
                color=0xFF4444
            )
            await log_channel.send(embed=embed)
        try:
            await member.send(discord.Embed(title="❌ Verification Failed",
                                            description="You are in a blacklisted server.",
                                            color=0xFF4444))
        except Exception:
            pass
        return

    # Success: grant verified role, remove unverified if present
    verified_role = guild.get_role(config.get("verified_role_id"))
    unverified_role = guild.get_role(config.get("unverified_role_id"))
    if verified_role:
        try:
            await member.add_roles(verified_role)
        except Exception:
            pass
    if unverified_role and unverified_role in member.roles:
        try:
            await member.remove_roles(unverified_role)
        except Exception:
            pass
    try:
        await member.send(discord.Embed(
            title="✅ Verification Successful!",
            description=f"You've been verified in {guild.name}.",
            color=0x00FF00
        ))
    except Exception:
        pass

# Commands
@bot.tree.command(name="verify-panel", description="🛡️ Set the verification panel with permanent button")
@app_commands.check(is_admin)
async def verify_panel(interaction: discord.Interaction):
    await interaction.response.send_message("Panel sent", ephemeral=True)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "prompt": "consent",
        "state": str(interaction.guild.id)
    }
    verification_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"

    embed = discord.Embed(title="🛡️ Verification Required",
                          description=f"Click the button below to verify, or use this direct link: {verification_url}",
                          color=0x00D4FF)
    view = PersistentVerificationView(interaction.guild.id)
    await interaction.channel.send(embed=embed, view=view)
    await log_action(interaction.guild.id, "📋 Verification Panel Created",
                     f"Admin {interaction.user.mention} created a verification panel in {interaction.channel.mention}",
                     0x00D4FF)

@bot.tree.command(name="flag-channel", description="🚩 Set the channel where flagged users are reported")
@app_commands.check(is_admin)
async def flag_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = str(interaction.guild.id)
    config = load_json(CONFIG_PATH, {})
    entry = config.setdefault(gid, {"flag_channel_id": None, "verified_role_id": None, "unverified_role_id": None, "log_channel_id": None, "blacklisted_servers": {}})
    entry["flag_channel_id"] = channel.id
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(title="🚩 Flag Channel Updated", description=f"Flag channel set to {channel.mention}", color=0x00FF00)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await log_action(interaction.guild.id, "🚩 Flag Channel Set",
                     f"Admin {interaction.user.mention} set the flag channel to {channel.mention}", 0x00FF00)

@bot.tree.command(name="set-verified-role", description="✅ Set the verified and unverified roles")
@app_commands.check(is_admin)
@app_commands.describe(verified_role="Role to assign when user passes verification", unverified_role="Role to remove when user passes verification")
async def set_verified_role(interaction: discord.Interaction, verified_role: discord.Role, unverified_role: discord.Role):
    gid = str(interaction.guild.id)
    config = load_json(CONFIG_PATH, {})
    entry = config.setdefault(gid, {"flag_channel_id": None, "verified_role_id": None, "unverified_role_id": None, "log_channel_id": None, "blacklisted_servers": {}})
    entry["verified_role_id"] = verified_role.id
    entry["unverified_role_id"] = unverified_role.id
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(title="✅ Verification Roles Updated",
                        description=f"Verified: {verified_role.mention}, Unverified: {unverified_role.mention}",
                        color=0x00FF00)
    await interaction.response.send_message
    # security4_part4.py
# No imports here. All imports are in Part 1.

# FastAPI app (shared across modules)
app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"=== Incoming request ===")
    print(f"Method: {request.method}")
    print(f"URL: {request.url}")
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return HTMLResponse("<h2>🛡️ OAuth2 Verification Server</h2><p>Click Verify in Discord to start.</p>")

@app.get("/oauth/callback")
async def oauth_callback(code: str = None, error: str = None, state: str = None):
    print("=== OAuth callback triggered ===")
    if error:
        return HTMLResponse(f"<h3>❌ OAuth error: {error}</h3>")
    if not code:
        return HTMLResponse("<h3>❌ No code provided.</h3>")

    token_url = "https://discord.com/api/oauth2/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds"
    }
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if token_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get token: {token_resp.text}</h3>")
        token_json = token_resp.json()
        access_token = token_json.get("access_token")

        user_resp = await client.get("https://discord.com/api/users/@me",
                                     headers={"Authorization": f"Bearer {access_token}"})
        if user_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get user info: {user_resp.text}</h3>")
        user_json = user_resp.json()

        guilds_resp = await client.get("https://discord.com/api/users/@me/guilds",
                                       headers={"Authorization": f"Bearer {access_token}"})
        if guilds_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get guilds: {guilds_resp.text}</h3>")
        guilds_json = guilds_resp.json()

    user_id = int(user_json["id"])
    username = user_json["username"]
    discriminator = user_json["discriminator"]
    user_guild_ids = [str(g["id"]) for g in guilds_json]
    target_guild_id = int(state) if state else None

    verification_data = {
        "user_id": user_id,
        "username": username,
        "discriminator": discriminator,
        "guild_ids": user_guild_ids,
        "target_guild_id": target_guild_id
    }
    print(f"Adding user {username} ({user_id}) to verification queue for guild {target_guild_id}")
    await verification_queue.put(verification_data)

    return HTMLResponse("<h3>✅ Verification complete! You may close this window and return to Discord.</h3>")

# Bring the verification queue and its processor from Part 3
# (Ensure verify_task and process_verification exist because Part 3 defines them)
# If they’re not present for some reason, fallback to a no-op
try:
    _ = verify_task
except NameError:
    async def no_op(*args, **kwargs):
        pass
    verify_task = no_op

def start_uvicorn():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    # Start the webserver in a separate thread
    thread = threading.Thread(target=start_uvicorn, daemon=True)
    thread.start()

    # Run the Discord bot
    bot.run(BOT_TOKEN)
