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
  - Runs an in-Discord Event Team application system: an "Apply Now"
    button that walks members through a short application (dropdowns +
    a popup form) and posts each submission into a private staff channel.
  - Designed to run 24/7 on a hosting service (e.g. Render).

CONFIG: fill in / verify the values below. The bot token should be set
as an environment variable (DISCORD_BOT_TOKEN) - never hardcode it.
"""

import os
import html
import requests
import discord
from discord import app_commands
from discord.ext import tasks

# =========================
# CONFIG
# =========================

KICK_SLUG = "klurge"

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# #live channel in Klurge's Korner (used for both Kick and YouTube live alerts)
DISCORD_LIVE_CHANNEL_ID = int(os.environ.get("DISCORD_LIVE_CHANNEL_ID", "1061373434631823404"))

# #videos channel (used for YouTube new-video-upload alerts)
DISCORD_VIDEOS_CHANNEL_ID = int(os.environ.get("DISCORD_VIDEOS_CHANNEL_ID", "1061373489447174295"))

# Role to ping for live alerts.
# TESTING: currently set to Server Head role. Swap to the real
# "Live Notification" role ID (1293949784767336532) once verified.
DISCORD_LIVE_ROLE_ID = int(os.environ.get("DISCORD_LIVE_ROLE_ID", "1293949784767336532"))

# Custom Kick emoji
KICK_EMOJI = "<:201195kick:1515955910021742662>"

# How often to check Kick live status (seconds)
CHECK_INTERVAL_SECONDS = 60

# --- YouTube settings ---

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "PASTE_YOUR_YOUTUBE_API_KEY_HERE")

YOUTUBE_CHANNEL_ID = "UCz-0rxzE7u2_GtaXH5I0Tfw"

# How often to check YouTube live status (seconds). 1200 = 20 minutes.
# search.list costs 100 units; 72 calls/day = 7,200 units.
YOUTUBE_LIVE_CHECK_INTERVAL_SECONDS = 1200

# How often to check for new video uploads (seconds). 900 = 15 minutes.
# playlistItems.list costs 1 unit; 96 calls/day = 96 units.
YOUTUBE_UPLOAD_CHECK_INTERVAL_SECONDS = 900

# Custom YouTube emoji
YOUTUBE_EMOJI = "<:562823youtube:1516115061545107649>"

# --- TEST MODE ---
# Set these to True to send a one-time test notification on startup,
# regardless of actual live/upload status. Set back to False after testing.
TEST_YOUTUBE_LIVE_PING = False
TEST_YOUTUBE_UPLOAD_PING = False

# --- ROTATING STATUS ---
# How often to rotate the bot's Discord status (seconds). 300 = 5 minutes.
STATUS_ROTATE_INTERVAL_SECONDS = 300

# Cycles through these when Klurge is NOT live.
# Format: (activity_type, text)
ROTATING_STATUSES = [
    ("watching",   "kick.com/klurge"),
    ("watching",   "youtube.com/@klurge"),
    ("playing",    "Valorant w/ Klurge"),
    ("listening",  "Klurge's Korner"),
    ("watching",   "Klurge cook in Valorant"),
    ("competing",  "Klurge's ranked games"),
    ("watching",   "Made by Ayush 💚"),
]

# --- EVENT TEAM APPLICATION SYSTEM ---
# REQUIRED before restarting, or the bot will fail to start:
#   GUILD_ID                    -> right-click your server icon -> Copy Server ID
#   APPLICATIONS_STAFF_CHANNEL_ID -> right-click your private staff channel -> Copy Channel ID
# (Enable Developer Mode first: User Settings -> Advanced -> Developer Mode)
GUILD_ID = int(os.environ.get("GUILD_ID", "PASTE_YOUR_SERVER_ID_HERE"))
APPLICATIONS_STAFF_CHANNEL_ID = int(os.environ.get("APPLICATIONS_STAFF_CHANNEL_ID", "PASTE_YOUR_STAFF_CHANNEL_ID_HERE"))

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
tree = app_commands.CommandTree(client)

# Tracks whether Klurge was live on the previous check
was_live = False

# Tracks the current index in ROTATING_STATUSES
status_index = 0

# Tracks video IDs that were YouTube livestreams — excluded from upload notifications
# since YouTube converts ended streams into VODs which would falsely trigger uploads
seen_livestream_ids = set()


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


def is_youtube_live():
    """
    Returns (is_live, video_id, title, thumbnail_url)
    Uses search.list with eventType=live (100 quota units per call).
    """
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "channelId": YOUTUBE_CHANNEL_ID,
            "eventType": "live",
            "type": "video",
            "key": YOUTUBE_API_KEY,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if items:
            item = items[0]
            video_id = item["id"]["videoId"]
            title = html.unescape(item["snippet"]["title"])
            thumbnail_url = item["snippet"]["thumbnails"]["high"]["url"]
            # Track this video ID so upload checker ignores it when stream ends
            seen_livestream_ids.add(video_id)
            return True, video_id, title, thumbnail_url

        return False, None, None, None

    except Exception as e:
        print(f"[ERROR] Failed to check YouTube live status: {e}")
        return False, None, None, None


async def send_live_notification(title, thumbnail_url, viewers):
    channel = client.get_channel(DISCORD_LIVE_CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Could not find channel {DISCORD_LIVE_CHANNEL_ID}")
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

    content = f"{KICK_EMOJI} <@&{DISCORD_LIVE_ROLE_ID}> Klurge is live on Kick! Join in"

    await channel.send(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    print("[INFO] Sent live notification to Discord.")


async def send_youtube_live_notification(video_id, title, thumbnail_url):
    channel = client.get_channel(DISCORD_LIVE_CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Could not find channel {DISCORD_LIVE_CHANNEL_ID}")
        return

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    embed = discord.Embed(
        title=title,
        url=video_url,
        color=0xFF0000,  # YouTube red
    )
    embed.set_author(name="Klurge is live on YouTube!")
    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Watch Stream", url=video_url, style=discord.ButtonStyle.link))

    content = f"{YOUTUBE_EMOJI} <@&{DISCORD_LIVE_ROLE_ID}> Klurge is live on YouTube! Join in"

    await channel.send(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    print("[INFO] Sent YouTube live notification to Discord.")


def get_latest_upload():
    """
    Returns (video_id, title, thumbnail_url) for the most recent UPLOADED
    video (not a live stream or upcoming premiere) on the channel's uploads
    playlist, or (None, None, None) if nothing new or on error.
    Uses playlistItems.list (1 unit) + videos.list (1 unit) per call.
    """
    try:
        # The uploads playlist ID is derived from the channel ID:
        # replace the leading "UC" with "UU"
        uploads_playlist_id = "UU" + YOUTUBE_CHANNEL_ID[2:]

        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "part": "snippet",
            "playlistId": uploads_playlist_id,
            "maxResults": 1,
            "key": YOUTUBE_API_KEY,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return None, None, None

        snippet = items[0]["snippet"]
        video_id = snippet["resourceId"]["videoId"]
        title = html.unescape(snippet["title"])
        thumbnail_url = snippet["thumbnails"]["high"]["url"]

        # --- Skip if this video was a known YouTube livestream ---
        # When a stream ends, YouTube converts it to a VOD with
        # liveBroadcastContent = "none", which would falsely trigger
        # an upload notification. We track all seen livestream IDs
        # and permanently exclude them.
        if video_id in seen_livestream_ids:
            print(f"[INFO] Skipping upload notification — this is a known livestream VOD: {title}")
            return None, None, None

        # --- Check if currently live or upcoming (skip if so) ---
        videos_url = "https://www.googleapis.com/youtube/v3/videos"
        videos_params = {
            "part": "snippet",
            "id": video_id,
            "key": YOUTUBE_API_KEY,
        }
        videos_resp = requests.get(videos_url, params=videos_params, timeout=15)
        videos_resp.raise_for_status()
        videos_data = videos_resp.json()

        video_items = videos_data.get("items", [])
        if video_items:
            live_broadcast_content = video_items[0]["snippet"].get("liveBroadcastContent", "none")
            if live_broadcast_content in ("live", "upcoming"):
                print(f"[INFO] Latest playlist item is a live/upcoming stream, skipping upload notification: {title}")
                # Also add to seen_livestream_ids as a safety net
                seen_livestream_ids.add(video_id)
                return None, None, None

        return video_id, title, thumbnail_url

    except Exception as e:
        print(f"[ERROR] Failed to check latest YouTube upload: {e}")
        return None, None, None


async def send_youtube_upload_notification(video_id, title, thumbnail_url):
    channel = client.get_channel(DISCORD_VIDEOS_CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Could not find channel {DISCORD_VIDEOS_CHANNEL_ID}")
        return

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    embed = discord.Embed(
        title=title,
        url=video_url,
        color=0xFF0000,  # YouTube red
    )
    embed.set_author(name="Klurge just posted a new video!")
    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Watch Video", url=video_url, style=discord.ButtonStyle.link))

    # TESTING: pings @everyone. Confirmed intentional per final design.
    content = f"{YOUTUBE_EMOJI} @everyone Klurge just posted a new video!"

    await channel.send(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )
    print("[INFO] Sent YouTube upload notification to Discord.")


async def set_rotating_status():
    """Sets the bot's presence to the next status in the rotation."""
    global status_index
    activity_type, text = ROTATING_STATUSES[status_index % len(ROTATING_STATUSES)]
    status_index += 1

    type_map = {
        "watching":   discord.ActivityType.watching,
        "listening":  discord.ActivityType.listening,
        "playing":    discord.ActivityType.playing,
        "competing":  discord.ActivityType.competing,
    }
    activity = discord.Activity(type=type_map.get(activity_type, discord.ActivityType.watching), name=text)
    await client.change_presence(status=discord.Status.online, activity=activity)


# =========================
# EVENT TEAM APPLICATION SYSTEM
# =========================
# Flow: "Apply Now" button (persistent, survives restarts) -> 3 dropdowns
# (roles / age / activity) -> "Continue" opens a popup with the written
# questions -> on submit, posts a formatted card into the staff channel.
#
# Discord's popup forms ("modals") only support up to 5 text fields, which
# is why the multiple-choice questions live in dropdowns BEFORE the popup,
# rather than trying to cram all ~10 questions into one screen.

ROLE_OPTIONS = [
    ("Event Host / Customs Coordinator", "event_host"),
    ("Greeter / Activation Lead", "greeter"),
    ("Content Runner", "content_runner"),
    ("Not sure - open to anything", "not_sure"),
]

AGE_OPTIONS = ["Under 16", "16-17", "18+"]

ACTIVITY_OPTIONS = ["Daily", "A few times a week", "Occasionally", "I mostly lurk"]


class ApplicationModal(discord.ui.Modal, title="Event Team Application"):
    timezone = discord.ui.TextInput(
        label="Your timezone",
        placeholder="e.g. IST, EST, GMT+5:30",
        max_length=100,
    )
    availability = discord.ui.TextInput(
        label="Days/times you're generally free",
        placeholder="e.g. Weekday evenings IST, weekends anytime",
        max_length=200,
    )
    why_join = discord.ui.TextInput(
        label="Why do you want to join the event team?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    experience = discord.ui.TextInput(
        label="Relevant past experience (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )
    anything_else = discord.ui.TextInput(
        label="Anything else? (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, roles_selected, age_selected, activity_selected):
        super().__init__()
        self.roles_selected = roles_selected
        self.age_selected = age_selected
        self.activity_selected = activity_selected

    async def on_submit(self, interaction: discord.Interaction):
        staff_channel = client.get_channel(APPLICATIONS_STAFF_CHANNEL_ID)
        if staff_channel is None:
            print(f"[ERROR] Could not find applications staff channel {APPLICATIONS_STAFF_CHANNEL_ID}")
            await interaction.response.send_message(
                "Thanks for applying! (There's a setup issue on our end — please let an admin know.)",
                ephemeral=True,
            )
            return

        member = interaction.user
        joined_str = "Unknown"
        if isinstance(member, discord.Member) and member.joined_at:
            joined_str = discord.utils.format_dt(member.joined_at, style="R")

        embed = discord.Embed(
            title="New Event Team Application",
            color=0x9B59B6,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Applicant", value=f"{member.mention} ({member})", inline=False)
        embed.add_field(name="Member since", value=joined_str, inline=True)
        embed.add_field(name="Age range", value=self.age_selected or "Not answered", inline=True)
        embed.add_field(name="Activity level", value=self.activity_selected or "Not answered", inline=True)
        embed.add_field(name="Role(s) interested", value=self.roles_selected or "Not answered", inline=False)
        embed.add_field(name="Timezone", value=str(self.timezone), inline=True)
        embed.add_field(name="Availability", value=str(self.availability), inline=True)
        embed.add_field(name="Why they want to join", value=str(self.why_join), inline=False)
        if str(self.experience).strip():
            embed.add_field(name="Past experience", value=str(self.experience), inline=False)
        if str(self.anything_else).strip():
            embed.add_field(name="Anything else", value=str(self.anything_else), inline=False)
        embed.set_footer(text=f"User ID: {member.id}")

        await staff_channel.send(embed=embed, view=ReviewButtonView())
        print(f"[INFO] New event team application received from {member} ({member.id}).")

        await interaction.response.send_message(
            "Thanks for applying to the Klurge's Korner event team! We'll review it and follow up over DM soon. 💚",
            ephemeral=True,
        )


class ApplicationSelectView(discord.ui.View):
    """Shown right after clicking 'Apply Now'. Collects role/age/activity
    before opening the modal for the written questions. Not persistent —
    it's a short-lived step (10 min timeout), so it doesn't need to
    survive a bot restart."""

    def __init__(self):
        super().__init__(timeout=600)
        self.roles_selected = None
        self.age_selected = None
        self.activity_selected = None

    @discord.ui.select(
        placeholder="Which role(s) interest you? (pick 1-4)",
        min_values=1,
        max_values=4,
        options=[discord.SelectOption(label=label, value=value) for label, value in ROLE_OPTIONS],
        row=0,
    )
    async def select_roles(self, interaction: discord.Interaction, select: discord.ui.Select):
        labels = [label for label, value in ROLE_OPTIONS if value in select.values]
        self.roles_selected = ", ".join(labels)
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="Your age range",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label=age) for age in AGE_OPTIONS],
        row=1,
    )
    async def select_age(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.age_selected = select.values[0]
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="How active are you in the server currently?",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label=level) for level in ACTIVITY_OPTIONS],
        row=2,
    )
    async def select_activity(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.activity_selected = select.values[0]
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Continue →", style=discord.ButtonStyle.success, row=3)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (self.roles_selected and self.age_selected and self.activity_selected):
            await interaction.response.send_message(
                "Please answer all three questions above before continuing.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ApplicationModal(self.roles_selected, self.age_selected, self.activity_selected)
        )


class ApplyPanelView(discord.ui.View):
    """Persistent view — the 'Apply Now' button. Registered in on_ready()
    so it keeps working across bot restarts, on every message it was
    ever posted to."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.success, custom_id="pulse_apply_now_button", emoji="📋")
    async def apply_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Answer these three quick questions, then hit **Continue** to finish your application:",
            view=ApplicationSelectView(),
            ephemeral=True,
        )


class ReviewButtonView(discord.ui.View):
    """Persistent view — 'Mark Reviewed' button attached to each
    application card in the staff channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mark Reviewed", style=discord.ButtonStyle.secondary, custom_id="pulse_mark_reviewed_button", emoji="✅")
    async def mark_reviewed(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.colour = 0x2ECC71
        existing_footer = embed.footer.text if embed.footer else ""
        if "Reviewed by" not in existing_footer:
            embed.set_footer(text=f"{existing_footer} • Reviewed by {interaction.user}")
        button.label = "Reviewed ✓"
        button.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@tree.command(name="post_application_panel", description="Post the Event Team application panel in this channel.")
@app_commands.checks.has_permissions(manage_guild=True)
async def post_application_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Join the Klurge's Korner Event Team 💚",
        description=(
            "We're building a small volunteer team to help run Klurge's Korner — starting with "
            "weekly Valorant customs nights, plus keeping the server active day to day.\n\n"
            "This is a **volunteer, unpaid** role. Everyone starts with a short trial period before "
            "a permanent spot on the team. Takes about 2 minutes to apply.\n\n"
            "Click below to get started!"
        ),
        color=0x9B59B6,
    )
    await interaction.channel.send(embed=embed, view=ApplyPanelView())
    await interaction.response.send_message("Application panel posted!", ephemeral=True)


@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def check_kick_status():
    global was_live

    live_now, title, thumbnail_url, viewers = is_channel_live()

    if live_now and not was_live:
        print(f"[INFO] Klurge just went live: {title}")
        await send_live_notification(title, thumbnail_url, viewers)
        # Pause rotation and show streaming status
        if rotate_status.is_running():
            rotate_status.cancel()
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(name=title, url="https://twitch.tv/klurge11"),
        )

    elif not live_now and was_live:
        print("[INFO] Klurge went offline. Resuming status rotation.")
        if not rotate_status.is_running():
            rotate_status.start()

    was_live = live_now


@tasks.loop(seconds=STATUS_ROTATE_INTERVAL_SECONDS)
async def rotate_status():
    # Only rotate when not live (Kick live check handles live status separately)
    if not was_live and not was_youtube_live:
        await set_rotating_status()


# Tracks whether Klurge was live on YouTube on the previous check
was_youtube_live = False

# Tracks the most recently seen YouTube video ID (to detect new uploads)
last_seen_video_id = None


@tasks.loop(seconds=YOUTUBE_LIVE_CHECK_INTERVAL_SECONDS)
async def check_youtube_live_status():
    global was_youtube_live

    live_now, video_id, title, thumbnail_url = is_youtube_live()

    if live_now and not was_youtube_live:
        print(f"[INFO] Klurge just went live on YouTube: {title}")
        await send_youtube_live_notification(video_id, title, thumbnail_url)
        # Pause rotation and show streaming status
        if rotate_status.is_running():
            rotate_status.cancel()
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(name=title, url="https://twitch.tv/klurge11"),
        )

    elif not live_now and was_youtube_live:
        print("[INFO] Klurge's YouTube stream ended. Resuming status rotation.")
        if not was_live and not rotate_status.is_running():
            rotate_status.start()

    was_youtube_live = live_now


@tasks.loop(seconds=YOUTUBE_UPLOAD_CHECK_INTERVAL_SECONDS)
async def check_youtube_uploads():
    global last_seen_video_id

    video_id, title, thumbnail_url = get_latest_upload()

    if video_id is None:
        return

    if last_seen_video_id is None:
        # Safety fallback - shouldn't normally happen since on_ready sets this
        last_seen_video_id = video_id
        print(f"[INFO] Latest video recorded (fallback): {title}")
        return

    if video_id != last_seen_video_id:
        print(f"[INFO] New YouTube video detected: {title}")
        await send_youtube_upload_notification(video_id, title, thumbnail_url)
        last_seen_video_id = video_id


@client.event
async def on_ready():
    global was_live, was_youtube_live, last_seen_video_id

    print(f"[INFO] Logged in as {client.user} ({client.user.id})")

    # --- Register persistent application-system views ---
    # This makes the "Apply Now" and "Mark Reviewed" buttons keep working
    # on old messages even after the bot restarts.
    client.add_view(ApplyPanelView())
    client.add_view(ReviewButtonView())

    # --- Sync slash commands to the server (guild sync = near-instant) ---
    try:
        synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"[INFO] Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"[ERROR] Failed to sync slash commands: {e}")

    # --- Initialize current state WITHOUT sending notifications ---
    # This prevents duplicate "just went live" pings if the bot restarts
    # while a stream is already ongoing.

    kick_live_now, kick_title, _, _ = is_channel_live()
    was_live = kick_live_now
    if kick_live_now:
        print(f"[INFO] Startup check: Klurge is already live on Kick ({kick_title}). Will not re-notify.")

    yt_live_now, yt_video_id, yt_title, _ = is_youtube_live()
    was_youtube_live = yt_live_now
    if yt_live_now:
        seen_livestream_ids.add(yt_video_id)
        print(f"[INFO] Startup check: Klurge is already live on YouTube ({yt_title}). Will not re-notify.")

    # Record current latest video so we don't treat it as "new" on first upload check
    initial_video_id, initial_title, _ = get_latest_upload()
    if initial_video_id:
        last_seen_video_id = initial_video_id
        print(f"[INFO] Startup check: latest YouTube video recorded ({initial_title}).")

    # Set initial status — YouTube takes priority if live on both
    if yt_live_now:
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(name=yt_title, url="https://twitch.tv/klurge11"),
        )
    elif kick_live_now:
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Streaming(name=kick_title, url="https://twitch.tv/klurge11"),
        )
    else:
        await set_rotating_status()

    if not check_kick_status.is_running():
        check_kick_status.start()
    if not check_youtube_live_status.is_running():
        check_youtube_live_status.start()
    if not check_youtube_uploads.is_running():
        check_youtube_uploads.start()
    # Start rotation only if not currently live on either platform
    if not kick_live_now and not yt_live_now and not rotate_status.is_running():
        rotate_status.start()

    # --- TEST MODE: send one-time sample notifications ---
    if TEST_YOUTUBE_LIVE_PING:
        print("[INFO] Sending one-time TEST YouTube live ping...")
        await send_youtube_live_notification(
            video_id="dQw4w9WgXcQ",
            title="TEST MESSAGE - ignore if this is a real stream title",
            thumbnail_url=None,
        )
        print("[INFO] TEST YouTube live ping sent. Set TEST_YOUTUBE_LIVE_PING = False before normal use.")

    if TEST_YOUTUBE_UPLOAD_PING:
        print("[INFO] Sending one-time TEST YouTube upload ping...")
        await send_youtube_upload_notification(
            video_id="dQw4w9WgXcQ",
            title="TEST MESSAGE - ignore if this is a real video title",
            thumbnail_url=None,
        )
        print("[INFO] TEST YouTube upload ping sent. Set TEST_YOUTUBE_UPLOAD_PING = False before normal use.")


client.run(DISCORD_BOT_TOKEN)
