"""Opt-in honeypot moderation; no message-content intent required."""

import asyncio
import logging
import os
from datetime import timedelta

import discord
from discord import app_commands
from honeypot_appeals import Appeals, member_embed

log = logging.getLogger(__name__)


class Honeypot:
    def __init__(self, client, guild_id):
        self.client = client
        self.guild_id = guild_id
        self.channel_id = int(os.getenv("HONEYPOT_CHANNEL_ID", "0"))
        self.log_channel_id = int(os.getenv("HONEYPOT_LOG_CHANNEL_ID", "0"))
        self.exempt_roles = {int(value.strip()) for value in
                             os.getenv("HONEYPOT_EXEMPT_ROLE_IDS", "").split(",") if value.strip()}
        if self.channel_id and (not self.log_channel_id or self.channel_id == self.log_channel_id):
            raise ValueError("Set HONEYPOT_LOG_CHANNEL_ID to a separate staff channel")
        self.inflight = set()
        self.appeals = Appeals(self, os.getenv("HONEYPOT_DB_PATH", "honeypot.sqlite3"))

    async def report(self, guild, text, *, member=None, title="Moderation alert", color=0xE67E22, expires=None, channel_id=None):
        log.warning("Honeypot guild=%s: %s", guild.id, text)
        channel = guild.get_channel(self.log_channel_id)
        if channel is not None:
            try:
                embed = member_embed(title, text, member, color)
                if channel_id is not None:
                    embed.add_field(name="Channel", value=f"<#{channel_id}>")
                if expires is not None:
                    embed.add_field(name="Timeout expires", value=f"{discord.utils.format_dt(expires, 'F')}\n{discord.utils.format_dt(expires, 'R')}", inline=False)
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.exception("Unable to send honeypot staff log")

    async def handle(self, message):
        if (not self.channel_id or message.guild is None
                or message.guild.id != self.guild_id
                or message.channel.id != self.channel_id
                or message.author.bot or message.webhook_id is not None
                or not isinstance(message.author, discord.Member)):
            return
        member, guild = message.author, message.guild
        perms = member.guild_permissions
        if (member.id == guild.owner_id or perms.administrator or perms.manage_guild
                or perms.ban_members or perms.moderate_members
                or any(role.id in self.exempt_roles for role in member.roles)):
            return
        key = (guild.id, member.id)
        if key in self.inflight:
            return
        self.inflight.add(key)
        reason = f"Pulse honeypot: message {message.id} in channel {message.channel.id}"
        try:
            me = guild.me
            if me is None or member.top_role >= me.top_role:
                await self.report(guild, f"Cannot moderate user {member.id}: check Pulse's role hierarchy. Message {message.id}.", member=member)
                return
            if not me.guild_permissions.moderate_members:
                await self.report(guild, f"Cannot timeout user {member.id}: Pulse needs Moderate Members.", member=member)
                return
            until = discord.utils.utcnow() + timedelta(days=7)
            applied = member.timed_out_until is None or member.timed_out_until < until
            if applied:
                await member.timeout(until, reason=reason)
            outcome = f"Applied a 7-day timeout to user {member.id}." if applied else f"Preserved existing longer timeout for user {member.id}."
            if applied:
                try:
                    await self.appeals.notify(member, until)
                except Exception:
                    log.exception("Could not save or deliver timeout appeal")
                    await self.report(guild, f"Timeout applied to user {member.id}, but appeal setup failed. Staff assistance required.", member=member)
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                outcome += " Trigger message could not be deleted; check Manage Messages."
            await self.report(guild, f"{outcome}\nTrigger message: `{message.id}`", member=member,
                              title="7-day timeout applied" if applied else "Existing timeout preserved",
                              expires=until if applied else member.timed_out_until, channel_id=message.channel.id)
        except discord.HTTPException as exc:
            await self.report(guild, f"Moderation failed for user {member.id}: {type(exc).__name__} (code {exc.code}). Message {message.id}.", member=member)
        finally:
            # Cover already-queued duplicate message events without growing permanent state.
            asyncio.get_running_loop().call_later(10, self.inflight.discard, key)

    async def post_panel(self, interaction):
        if (not self.channel_id or interaction.guild_id != self.guild_id
                or interaction.channel_id != self.channel_id):
            await interaction.response.send_message("Run this in the configured HONEYPOT_CHANNEL_ID channel.", ephemeral=True)
            return
        me = interaction.guild.me
        permissions = interaction.channel.permissions_for(me)
        required = ["view_channel", "send_messages", "embed_links", "manage_messages"]
        required.append("moderate_members")
        missing = [name for name in required if not getattr(permissions, name)]
        staff = interaction.guild.get_channel(self.log_channel_id)
        if staff is None or not all(getattr(staff.permissions_for(me), p) for p in ("view_channel", "send_messages", "embed_links")):
            missing.append("access to staff log channel")
        if missing:
            await interaction.response.send_message("Missing permissions: " + ", ".join(missing), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="DO NOT SEND MESSAGES IN THIS CHANNEL",
            description=("This channel catches automated spam from compromised accounts. "
                         "Sending any message here, including an image or link, results in **a 7-day timeout**. "
                         "Pulse will DM you an appeal button for use after account recovery. Staff can approve early removal; otherwise the timeout expires automatically. Use the other channels to chat."),
            color=0xF1C40F)
        embed.set_author(name=interaction.guild.name)
        if interaction.guild.icon:
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url)
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_image(url="https://raw.githubusercontent.com/ayushkumar43733/pulse-bot/main/klurge_banner.webp")
        embed.set_footer(text="Pulse • Server security")
        await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send("Warning panel posted. Keep it visible to members.", ephemeral=True)


def install_honeypot(client, tree, guild_id):
    honeypot = Honeypot(client, guild_id)

    @client.event
    async def on_message(message):
        await honeypot.handle(message)

    @tree.command(name="post_honeypot_panel", description="Post the warning in the configured honeypot channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post_honeypot_panel(interaction: discord.Interaction):
        await honeypot.post_panel(interaction)

    @post_honeypot_panel.error
    async def panel_error(interaction, error):
        text = ("You need Manage Server to post this panel." if isinstance(error, app_commands.CheckFailure)
                else "Could not post the panel. Check Pulse's channel permissions and logs.")
        log.error("Honeypot panel error: %s", error)
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    return honeypot
