"""
Pulse - Klurge's Korner Bot
-----------------------------
What this does:
  - Connects to Discord and stays ONLINE with a custom status.
  - Runs a background loop checking every CHECK_INTERVAL_SECONDS whether
    Klurge's Kick channel is live.
  - When it detects a transition from "offline" -> "live", it sends an
    embed message to #live, pinging the Live Notification role, with a
    "Watch Stream" button.
  - Designed to run 24/7 on a hosting service (e.g. Render).

CONFIG: fill in / verify the values below. The bot token should be set
as an environment variable (DISCORD_BOT_TOKEN) - never hardcode it.
"""

import os
import requests
import discord
from discord.ext import tasks

# =========================
# CONFIG
# =========================

KICK_SLUG = "klurge"

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# #live channel in Klurge's Korner
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "1061373434631823404"))

# "Live Notification" role
DISCORD_ROLE_ID = int(os.environ.get("DISCORD_ROLE_ID", "1293949784767336532"))

# Custom Kick emoji
KICK_EMOJI = "<:201195kick:1515955910021742662>"

# How often to check Kick live status (seconds)
CHECK_INTERVAL_SECONDS = 60

# =========================
# END CONFIG
# =========================

KICK_API_URL = f"https://kick.com/api/v1/channels/{KICK_SLUG}"

HEADERS_KICK = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# Tracks whether Klurge was live on the previous check
was_live = False


def is_channel_live():
    """
    Returns (is_live, title, thumbnail_url, viewer_count)
    """
    try:
        resp = requests.get(KICK_API_URL, headers=HEADERS_KICK, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        livestream = data.get("livestream")
        if livestream and livestream.get("is_live"):
            title = livestream.get("session_title", "Live on Kick!")
            viewers = livestream.get("viewer_count", 0)

            thumbnail_url = None
            thumbnail_data = livestream.get("thumbnail")
            if isinstance(thumbnail_data, dict):
                thumbnail_url = thumbnail_data.get("url")

            return True, title, thumbnail_url, viewers

        return False, None, None, None

    except Exception as e:
        print(f"[ERROR] Failed to check Kick status: {e}")
        return False, None, None, None


async def send_live_notification(title, thumbnail_url, viewers):
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Could not find channel {DISCORD_CHANNEL_ID}")
        return

    stream_url = f"https://kick.com/{KICK_SLUG}"

    embed = discord.Embed(
        title=title,
        url=stream_url,
        color=0x53FC18,
    )
    embed.set_author(name=f"{KICK_SLUG} is live on Kick!")
    if viewers is not None:
        embed.add_field(name="Viewers", value=str(viewers), inline=True)
    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Watch Stream", url=stream_url, style=discord.ButtonStyle.link))

    content = f"{KICK_EMOJI} <@&{DISCORD_ROLE_ID}> Klurge is live on Kick! Join in"

    await channel.send(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    print("[INFO] Sent live notification to Discord.")


@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def check_kick_status():
    global was_live

    live_now, title, thumbnail_url, viewers = is_channel_live()

    if live_now and not was_live:
        print(f"[INFO] Klurge just went live: {title}")
        await send_live_notification(title, thumbnail_url, viewers)
        # Update bot status to reflect live state
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(name=f"{title}", url=f"https://kick.com/{KICK_SLUG}"),
        )

    elif not live_now and was_live:
        print("[INFO] Klurge went offline.")
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="kick.com/klurge"),
        )

    was_live = live_now


@client.event
async def on_ready():
    print(f"[INFO] Logged in as {client.user} ({client.user.id})")
    # Set initial status
    await client.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name="kick.com/klurge"),
    )
    if not check_kick_status.is_running():
        check_kick_status.start()


client.run(DISCORD_BOT_TOKEN)
