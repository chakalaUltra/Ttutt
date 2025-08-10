
import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
import uvicorn
import threading
import urllib.parse
import math

# ==== Config ====

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIRECT_URI = "https://ttutt-2.onrender.com/oauth/callback"

CONFIG_PATH = "server_configs.json"
BLACKLISTED_PATH = "blacklisted_servers.json"

# ==== Load/save JSON utils ====

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# Server-specific configs: {guild_id: {flag_channel_id, verified_role_id, log_channel_id, blacklisted_servers}}
server_configs = load_json(CONFIG_PATH, {})

# Global blacklisted servers structure (legacy support)
blacklisted_servers = load_json(BLACKLISTED_PATH, {})

def get_server_config(guild_id):
    guild_str = str(guild_id)
    if guild_str not in server_configs:
        server_configs[guild_str] = {
            "flag_channel_id": None,
            "verified_role_id": None,
            "unverified_role_id": None,
            "log_channel_id": None,
            "blacklisted_servers": {}
        }
        save_json(CONFIG_PATH, server_configs)
    return server_configs[guild_str]

# ==== Discord Bot setup ====

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix=";", intents=intents)

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

def is_bot_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == 1117540437016727612

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Set bot status to DND
    await bot.change_presence(status=discord.Status.dnd)

    # Add persistent views so buttons work after restart
    for guild in bot.guilds:
        bot.add_view(PersistentVerificationView(guild.id))
    print("Persistent views loaded.")

    try:
        await bot.tree.sync()
        print("Commands synced.")
    except Exception as e:
        print(f"Sync failed: {e}")
    verify_task.start()

@bot.event
async def on_guild_join(guild):
    """Auto-create channels when bot joins a server and notify owner"""
    try:
        # Create verification logs channel (admin-only)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Add permission for administrators
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
        
        log_channel = await guild.create_text_channel(
            "・VerificationUnit Logs",
            topic="Automated logs for verification system activities",
            reason="Auto-created by Security Bot",
            overwrites=overwrites
        )

        # Update server config
        config = get_server_config(guild.id)
        config["log_channel_id"] = log_channel.id
        save_json(CONFIG_PATH, server_configs)

        # Send setup message
        embed = discord.Embed(
            title="🤖 Security Bot Setup Complete!",
            description="Thank you for adding me to your server! I've automatically created the necessary channels.",
            color=0x00FF00
        )
        embed.add_field(
            name="📋 Next Steps",
            value=f"• Use `/flag-channel` to set where flagged users are reported\n• Use `/set-verified-role` to set the verification role\n• Use `/bl-servers` to add blacklisted servers\n• Use `/verify-panel` to create the verification panel",
            inline=False
        )
        embed.add_field(
            name="📁 Created Channels",
            value=f"🔒 {log_channel.mention} - Verification activity logs",
            inline=False
        )
        embed.set_footer(text="Use /help-security for more commands")

        await log_channel.send(embed=embed)

        # Notify bot owner
        await notify_bot_owner_server_join(guild)

    except Exception as e:
        print(f"Error creating channels for guild {guild.name}: {e}")

async def notify_bot_owner_server_join(guild):
    """Send notification to bot owner when joining a server"""
    try:
        BOT_OWNER_ID = 1117540437016727612
        owner = bot.get_user(BOT_OWNER_ID)
        
        if owner:
            embed = discord.Embed(
                title="🚀 Bot Added to New Server",
                color=0xFFFFFF  # White color
            )
            embed.add_field(
                name="📋 Server Info",
                value=f"**Server Name:** {guild.name}\n**Server Owner:** {guild.owner.mention if guild.owner else 'Unknown'}\n**Server ID:** {guild.id}\n**Added:** {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                inline=False
            )
            embed.set_footer(text=f"Total Servers: {len(bot.guilds)}")

            # Create leave server button
            leave_button = discord.ui.Button(
                label="Leave Server",
                style=discord.ButtonStyle.grey,
                emoji="🚪"
            )

            async def leave_callback(interaction: discord.Interaction):
                if interaction.user.id != BOT_OWNER_ID:
                    await interaction.response.send_message("❌ Only the bot owner can use this button.", ephemeral=True)
                    return
                
                try:
                    await guild.leave()
                    embed_updated = discord.Embed(
                        title="✅ Left Server Successfully",
                        description=f"Bot has left **{guild.name}** (`{guild.id}`)",
                        color=0x00FF00
                    )
                    await interaction.response.edit_message(embed=embed_updated, view=None)
                except Exception as e:
                    error_embed = discord.Embed(
                        title="❌ Error Leaving Server",
                        description=f"Failed to leave **{guild.name}**: {str(e)}",
                        color=0xFF0000
                    )
                    await interaction.response.edit_message(embed=error_embed, view=None)

            leave_button.callback = leave_callback
            view = discord.ui.View()
            view.add_item(leave_button)

            await owner.send(embed=embed, view=view)
            print(f"Notified bot owner about joining guild: {guild.name}")
        else:
            print(f"Could not find bot owner with ID {BOT_OWNER_ID}")
            
    except Exception as e:
        print(f"Error notifying bot owner: {e}")

async def log_action(guild_id, title, description, color=0x0099FF):
    """Helper function to log actions to the server's log channel"""
    config = get_server_config(guild_id)
    log_channel_id = config.get("log_channel_id")

    if log_channel_id:
        log_channel = bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="Security Bot Logs", icon_url=bot.user.avatar.url if bot.user.avatar else None)
            await log_channel.send(embed=embed)

# Verification queue to receive data from webserver
verification_queue = asyncio.Queue()

@tasks.loop(seconds=1)
async def verify_task():
    if not verification_queue.empty():
        print(f"Processing {verification_queue.qsize()} items in verification queue")
    while not verification_queue.empty():
        data = await verification_queue.get()
        print("Retrieved data from verification queue, processing...")
        await process_verification(data)

async def process_verification(data):
    """
    data dict keys:
    user_id (int), username (str), discriminator (str), guild_ids (list of str), target_guild_id (int)
    """
    user_id = data["user_id"]
    guild_ids = data["guild_ids"]
    username = data["username"]
    target_guild_id = data.get("target_guild_id")

    print(f"Processing verification for user {username} ({user_id})")
    print(f"User is in {len(guild_ids)} servers: {guild_ids}")

    # Store user verification data
    user_data = load_json("user_verification_data.json", {})
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

    print(f"Found member: {member.display_name}")

    config = get_server_config(guild.id)
    server_blacklisted = config.get("blacklisted_servers", {})

    print(f"Checking against blacklisted server IDs: {list(server_blacklisted.keys())}")
    print(f"User's guild IDs: {guild_ids}")

    flagged_servers = [
        server_blacklisted[sid]
        for sid in server_blacklisted
        if sid in guild_ids
    ]

    print(f"Blacklisted servers found: {flagged_servers}")

    flag_channel = bot.get_channel(config.get("flag_channel_id")) if config.get("flag_channel_id") else None

    if flagged_servers:
        print(f"User {username} is in blacklisted servers: {flagged_servers}")
        if flag_channel:
            embed = discord.Embed(
                title="🚨 Security Alert - User Flagged",
                description=f"**User:** {member.mention}\n**Status:** ⚠️ Flagged during verification\n**Reason:** Member of blacklisted servers",
                color=0xFF4444  # Bright red
            )
            embed.add_field(
                name="🔒 Blacklisted Servers",
                value=f"```\n{chr(10).join(flagged_servers)}```",
                inline=False
            )
            embed.add_field(
                name="👤 User Info",
                value=f"**Username:** {username}\n**ID:** {user_id}\n**Mention:** {member.mention}",
                inline=True
            )
            embed.add_field(
                name="📊 Server Count",
                value=f"**Total Servers:** {len(guild_ids)}\n**Flagged:** {len(flagged_servers)}",
                inline=True
            )
            embed.set_footer(text="Security Verification System", icon_url=bot.user.avatar.url if bot.user.avatar else None)
            embed.timestamp = discord.utils.utcnow()
            await flag_channel.send(embed=embed)
            print("Flag notification sent to channel")
        else:
            print("No flag channel configured!")
        try:
            embed = discord.Embed(
                title="❌ Verification Failed",
                description="❌ Sorry, it seems like you could not verify. For further questions please contact our Staff Members!",
                color=0xFF4444
            )
            await member.send(embed=embed)
            print("DM sent to user (flagged)")
        except Exception as e:
            print(f"Could not send DM to user: {e}")
    else:
        print(f"User {username} passed verification")
        
        # Get verified and unverified roles
        verified_role = guild.get_role(config.get("verified_role_id"))
        unverified_role = guild.get_role(config.get("unverified_role_id"))
        
        # Add verified role
        if verified_role:
            try:
                await member.add_roles(verified_role)
                print(f"Added verified role {verified_role.name} to user")
            except discord.Forbidden:
                print(f"Missing permissions to add verified role {verified_role.name}")
            except Exception as e:
                print(f"Error adding verified role: {e}")
        else:
            print("No verified role configured!")
        
        # Remove unverified role if configured
        if unverified_role and unverified_role in member.roles:
            try:
                await member.remove_roles(unverified_role)
                print(f"Removed unverified role {unverified_role.name} from user")
            except discord.Forbidden:
                print(f"Missing permissions to remove unverified role {unverified_role.name}")
            except Exception as e:
                print(f"Error removing unverified role: {e}")
        
        try:
            embed = discord.Embed(
                title="✅ Verification Successful!",
                description=f"✅ You've been verified in {guild.name}! You may continue on.",
                color=0x00FF00
            )
            await member.send(embed=embed)
            print("DM sent to user (verified)")
        except Exception as e:
            print(f"Could not send DM to user: {e}")

# ---- Slash commands ----

# Persistent verification view that recreates itself
class PersistentVerificationView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)  # No timeout = permanent
        self.guild_id = guild_id

    @discord.ui.button(label="🔐 Verify", style=discord.ButtonStyle.grey, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Respond immediately to avoid timeout
        await interaction.response.defer(ephemeral=True)
        
        # Build OAuth2 URL with proper URL-encoding
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds",
            "prompt": "consent",
            "state": str(self.guild_id)
        }
        url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
        
        embed = discord.Embed(
            title="🔗 Verification Link",
            description=f"**[🔐 Click here to complete verification]({url})**\n\n.",
            color=0xFFFFFF
        )
        embed.set_footer(text="🔒 Secure OAuth2 Verification")
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="verify-panel", description="🛡️ Set the verification panel with permanent button")
@app_commands.check(is_admin)
async def verify_panel(interaction: discord.Interaction):
    # Send hidden confirmation message first
    await interaction.response.send_message("Panel sent", ephemeral=True)
    
    # Build OAuth2 URL for embed link as backup
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "prompt": "consent",
        "state": str(interaction.guild.id)
    }
    verification_url = f"https://discord.com/api/oauth2/authorize?{urllib.parse.urlencode(params)}"
    
    # Create the verification panel embed with both button and direct link
    embed = discord.Embed(
        title="🛡️ Verification Required",
        description=f"Click the button below to verify, or use this **[direct link]({verification_url})**\n\n",
        color=0xFFFFFF  # Bright cyan
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1330685351412498480/1403393124692398162/Screenshot_20250808-1753442.png?ex=68976332&is=689611b2&hm=575bee2a6ed0d2e351e93ebe0ac451230d671f8e4bb3dde98c20dc37ca3ee7b7&")
    embed.set_footer(text="🔒 Secure OAuth2 Verification", icon_url=bot.user.avatar.url if bot.user.avatar else None)

    # Create persistent view
    view = PersistentVerificationView(interaction.guild.id)
    
    # Send the panel with permanent button
    await interaction.channel.send(embed=embed, view=view)

    # Log this action
    await log_action(
        interaction.guild.id,
        "📋 Verification Panel Created",
        f"Admin {interaction.user.mention} created a verification panel in {interaction.channel.mention}",
        0x00D4FF
    )

@bot.tree.command(name="flag-channel", description="🚩 Set the channel where flagged users are reported")
@app_commands.check(is_admin)
@app_commands.describe(channel="Channel to send flagged user notifications")
async def flag_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    config = get_server_config(interaction.guild.id)
    config["flag_channel_id"] = channel.id
    save_json(CONFIG_PATH, server_configs)

    embed = discord.Embed(
        title="🚩 Flag Channel Updated",
        description=f"Flag channel has been set to {channel.mention}",
        color=0x00FF00
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Log this action
    await log_action(
        interaction.guild.id,
        "🚩 Flag Channel Set",
        f"Admin {interaction.user.mention} set the flag channel to {channel.mention}",
        0x00FF00
    )

@bot.tree.command(name="set-verified-role", description="✅ Set the verified and unverified roles (both required)")
@app_commands.check(is_admin)
@app_commands.describe(
    verified_role="Role to assign when user passes verification",
    unverified_role="Role to remove when user passes verification"
)
async def set_verified_role(interaction: discord.Interaction, verified_role: discord.Role, unverified_role: discord.Role):
    config = get_server_config(interaction.guild.id)
    config["verified_role_id"] = verified_role.id
    config["unverified_role_id"] = unverified_role.id
    
    save_json(CONFIG_PATH, server_configs)

    embed = discord.Embed(
        title="✅ Verification Roles Updated",
        description=f"**Verified role:** {verified_role.mention}\n**Unverified role:** {unverified_role.mention} (will be removed upon successful verification)",
        color=0x00FF00
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Log this action
    log_description = f"Admin {interaction.user.mention} set the verified role to {verified_role.mention} and unverified role to {unverified_role.mention}"
    
    await log_action(
        interaction.guild.id,
        "✅ Verification Roles Set",
        log_description,
        0x00FF00
    )

@bot.tree.command(name="bl-servers", description="🔒 Add blacklisted server by ID and name")
@app_commands.check(is_admin)
@app_commands.describe(server_id="ID of the server to blacklist", server_name="Name of the server (for display)")
async def bl_servers(interaction: discord.Interaction, server_id: str, server_name: str):
    config = get_server_config(interaction.guild.id)
    config["blacklisted_servers"][server_id] = server_name
    save_json(CONFIG_PATH, server_configs)

    embed = discord.Embed(
        title="🔒 Server Blacklisted",
        description=f"Added blacklisted server: **{server_name}** (`{server_id}`)",
        color=0xFF4444
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Log this action
    await log_action(
        interaction.guild.id,
        "🔒 Server Blacklisted",
        f"Admin {interaction.user.mention} added **{server_name}** (`{server_id}`) to the blacklist",
        0xFF4444
    )

class RemoveBLServers(discord.ui.Select):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        config = get_server_config(guild_id)
        server_blacklisted = config.get("blacklisted_servers", {})

        options = [
            discord.SelectOption(label=name, description=f"ID: {sid}", value=sid)
            for sid, name in server_blacklisted.items()
        ]
        super().__init__(placeholder="🗑️ Select a server to remove", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        sid = self.values[0]
        config = get_server_config(self.guild_id)
        name = config["blacklisted_servers"].pop(sid, None)
        save_json(CONFIG_PATH, server_configs)

        if name:
            embed = discord.Embed(
                title="🗑️ Server Removed from Blacklist",
                description=f"Removed blacklisted server: **{name}** (`{sid}`)",
                color=0x00FF00
            )
            await interaction.response.edit_message(embed=embed, view=None)

            # Log this action
            await log_action(
                self.guild_id,
                "🗑️ Server Removed from Blacklist",
                f"Admin {interaction.user.mention} removed **{name}** (`{sid}`) from the blacklist",
                0x00FF00
            )
        else:
            embed = discord.Embed(
                title="❌ Error",
                description="Server not found in blacklist.",
                color=0xFF4444
            )
            await interaction.response.edit_message(embed=embed, view=None)

class RemoveBLView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__()
        self.add_item(RemoveBLServers(guild_id))

@bot.tree.command(name="bl-remove", description="🗑️ Remove a blacklisted server")
@app_commands.check(is_admin)
async def bl_remove(interaction: discord.Interaction):
    config = get_server_config(interaction.guild.id)
    server_blacklisted = config.get("blacklisted_servers", {})

    if not server_blacklisted:
        embed = discord.Embed(
            title="📝 No Blacklisted Servers",
            description="No blacklisted servers to remove.",
            color=0xFFAA00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    view = RemoveBLView(interaction.guild.id)
    embed = discord.Embed(
        title="🗑️ Remove Blacklisted Server",
        description="Select a server to remove from the blacklist:",
        color=0x0099FF
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="global-annc", description="📢 Send a global announcement to all servers (Bot Owner Only)")
@app_commands.check(is_bot_owner)
@app_commands.describe(message="The announcement message to send to all servers")
async def global_announcement(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    
    success_count = 0
    failed_count = 0
    failed_servers = []
    
    embed = discord.Embed(
        title="📢 Global Announcement",
        description=message,
        color=0x00D4FF,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Global Security Bot Announcement", icon_url=bot.user.avatar.url if bot.user.avatar else None)
    
    for guild in bot.guilds:
        try:
            # Try to find an announcement channel first
            announcement_channel = None
            
            # Look for common announcement channel names
            for channel in guild.text_channels:
                if any(name in channel.name.lower() for name in ['announcement', 'announcements', 'news', 'updates', 'general']):
                    # Check if bot has permission to send messages
                    if channel.permissions_for(guild.me).send_messages:
                        announcement_channel = channel
                        break
            
            # If no announcement channel found, try to use the first available channel
            if not announcement_channel:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        announcement_channel = channel
                        break
            
            if announcement_channel:
                await announcement_channel.send(embed=embed)
                success_count += 1
                print(f"✅ Sent announcement to {guild.name} in #{announcement_channel.name}")
            else:
                failed_count += 1
                failed_servers.append(f"{guild.name} (No accessible channels)")
                print(f"❌ Failed to send announcement to {guild.name} - No accessible channels")
                
        except Exception as e:
            failed_count += 1
            failed_servers.append(f"{guild.name} ({str(e)})")
            print(f"❌ Failed to send announcement to {guild.name}: {e}")
    
    # Send summary to bot owner
    result_embed = discord.Embed(
        title="📊 Global Announcement Results",
        color=0x00FF00 if failed_count == 0 else 0xFFAA00
    )
    result_embed.add_field(
        name="📈 Statistics",
        value=f"**Total Servers:** {len(bot.guilds)}\n**Successful:** {success_count}\n**Failed:** {failed_count}",
        inline=False
    )
    
    if failed_servers:
        # Limit the failed servers list to avoid Discord's embed limits
        failed_list = "\n".join(failed_servers[:10])
        if len(failed_servers) > 10:
            failed_list += f"\n... and {len(failed_servers) - 10} more"
        
        result_embed.add_field(
            name="❌ Failed Servers",
            value=f"```\n{failed_list}\n```",
            inline=False
        )
    
    result_embed.set_footer(text="Global Announcement Complete")
    result_embed.timestamp = discord.utils.utcnow()
    
    await interaction.followup.send(embed=result_embed, ephemeral=True)

@bot.tree.command(name="help-security", description="❓ Show all security bot commands")
async def help_security(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Security Bot Commands",
        description="Complete list of available security commands",
        color=0x0099FF
    )

    if is_bot_owner(interaction):
        embed.add_field(
            name="👑 Bot Owner Commands",
            value="• `/global-annc` - Send global announcement to all servers",
            inline=False
        )

    if is_admin(interaction):
        embed.add_field(
            name="🔧 Admin Commands",
            value="• `/verify-panel` - Create verification panel\n• `/flag-channel` - Set flagged users channel\n• `/set-verified-role` - Set verified & unverified roles\n• `/bl-servers` - Add blacklisted server\n• `/bl-remove` - Remove blacklisted server\n• `/help-security` - Show this help",
            inline=False
        )
    else:
        embed.add_field(
            name="👤 User Commands",
            value="• `/help-security` - Show this help\n• Use the verification panel to get verified",
            inline=False
        )

    embed.add_field(
        name="🔄 How Verification Works",
        value="1. User clicks verify button\n2. OAuth2 checks their servers\n3. Bot compares with blacklist\n4. User gets role or flagged",
        inline=False
    )

    embed.set_footer(text="Security Bot Help System")
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.CheckFailure):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You do not have permission to use this command.",
            color=0xFF4444
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred: {error}",
            color=0xFF4444
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ==== FastAPI webserver ====

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"=== Incoming request ===")
    print(f"Method: {request.method}")
    print(f"URL: {request.url}")
    print(f"Path: {request.url.path}")
    print(f"Query params: {dict(request.query_params)}")
    print(f"Headers: {dict(request.headers)}")
    response = await call_next(request)
    print(f"Response status: {response.status_code}")
    return response

@app.get("/")
async def root():
    return HTMLResponse("<h2>🛡️ OAuth2 Verification Server</h2><p>Click Verify in Discord to start.</p>")

@app.get("/oauth/callback")
async def oauth_callback(code: str = None, error: str = None, state: str = None):
    print("=== OAuth callback triggered ===")
    print(f"Code received: {code is not None}")
    print(f"Error received: {error}")
    print(f"State (guild_id): {state}")

    if error:
        print(f"OAuth error occurred: {error}")
        return HTMLResponse(f"<h3>❌ OAuth error: {error}</h3>")
    if not code:
        print("No authorization code provided")
        return HTMLResponse("<h3>❌ No code provided.</h3>")

    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds"
    }

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(token_url, data=data, headers=headers)
        if token_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get token: {token_resp.text}</h3>")
        token_json = token_resp.json()
        access_token = token_json.get("access_token")

        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get user info: {user_resp.text}</h3>")
        user_json = user_resp.json()

        guilds_resp = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if guilds_resp.status_code != 200:
            return HTMLResponse(f"<h3>❌ Failed to get guilds: {guilds_resp.text}</h3>")
        guilds_json = guilds_resp.json()

    user_id = int(user_json["id"])
    username = user_json["username"]
    discriminator = user_json["discriminator"]
    user_guild_ids = [str(g["id"]) for g in guilds_json]
    target_guild_id = int(state) if state else None

    # Put data into bot's verification queue
    verification_data = {
        "user_id": user_id,
        "username": username,
        "discriminator": discriminator,
        "guild_ids": user_guild_ids,
        "target_guild_id": target_guild_id
    }
    print(f"Adding user {username} ({user_id}) to verification queue for guild {target_guild_id}")
    print(f"User guild IDs: {user_guild_ids}")
    await verification_queue.put(verification_data)

    return HTMLResponse("<h3>✅ Verification complete! You may close this window and return to Discord.</h3>")

# ==== Running bot + webserver in one script ====

def start_uvicorn():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    # Run FastAPI webserver in a separate thread
    thread = threading.Thread(target=start_uvicorn, daemon=True)
    thread.start()
    # Run Discord bot in main thread (blocking)
    bot.run(BOT_TOKEN)
