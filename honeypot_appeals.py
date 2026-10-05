"""Persistent timeout cases and DM appeals reviewed by server moderators."""

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import discord

log = logging.getLogger(__name__)


def member_embed(title, description, member=None, color=0xF1C40F):
    """Consistent moderation cards without enabling mention notifications."""
    embed = discord.Embed(title=title, description=description, color=color,
                          timestamp=discord.utils.utcnow())
    embed.set_footer(text="Pulse • Server security")
    if member is not None:
        embed.set_author(name=f"{member.display_name} (@{member.name})"[:256],
                         icon_url=str(member.display_avatar.url))
        embed.set_thumbnail(url=str(member.display_avatar.url))
        embed.add_field(name="Member", value=f"<@{member.id}>", inline=True)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
    return embed


class CaseStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                expires TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                review_message INTEGER, review_channel INTEGER)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, guild_id, user_id, expires):
        with self.connect() as db:
            return db.execute("INSERT INTO cases (guild_id,user_id,expires) VALUES (?,?,?)",
                              (guild_id, user_id, expires.isoformat())).lastrowid

    def get(self, case_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()

    def update(self, case_id, status, message=None, channel=None):
        with self.connect() as db:
            db.execute("UPDATE cases SET status=?, review_message=COALESCE(?,review_message), "
                       "review_channel=COALESCE(?,review_channel) WHERE id=?",
                       (status, message, channel, case_id))


def case_id_from(message):
    try:
        return int(message.embeds[0].footer.text.removeprefix("Pulse case: "))
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


class Appeals:
    def __init__(self, guard, path):
        self.guard = guard
        self.store = CaseStore(path)
        self.lock = asyncio.Lock()
        self.registered = False

    def register_views(self):
        if not self.registered:
            self.guard.client.add_view(AppealView(self))
            self.guard.client.add_view(ReviewView(self))
            self.registered = True

    async def notify(self, member, expires):
        case_id = self.store.create(member.guild.id, member.id, expires)
        embed = discord.Embed(
            title="Pulse: 7-day safety timeout",
            description=(f"You posted in the honeypot channel in **{member.guild.name}**. "
                         "Pulse applied a 7-day timeout to stop possible automated spam.\n\n"
                         f"It expires automatically {discord.utils.format_dt(expires, 'F')} "
                         f"({discord.utils.format_dt(expires, 'R')}).\n\n"
                         "After recovering and securing your account, use **Appeal timeout** below. "
                         "Staff must approve removal; submitting an appeal does not remove the timeout. "
                         "Never share passwords, tokens, or recovery codes."), color=0xF1C40F)
        embed.set_footer(text=f"Pulse case: {case_id}")
        try:
            await member.send(embed=embed, view=AppealView(self),
                              allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await self.guard.report(member.guild,
                f"User {member.id} timed out, but the appeal DM could not be delivered. "
                "Staff must help them appeal through another contact route.", member=member, title="Appeal DM unavailable")

    async def active_member(self, case):
        guild = self.guard.client.get_guild(case["guild_id"])
        if guild is None:
            raise ValueError("Pulse cannot access that server. Please contact staff.")
        member = await guild.fetch_member(case["user_id"])
        expires = datetime.fromisoformat(case["expires"])
        current = member.timed_out_until
        if current is None or current <= discord.utils.utcnow():
            raise ValueError("This timeout has already expired or been removed.")
        if abs((current - expires).total_seconds()) > 1:
            raise ValueError("The timeout was changed after this case. Staff must review it manually.")
        return member

    async def submit(self, interaction, case_id, explanation):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            case = self.store.get(case_id)
            if case is None or case["user_id"] != interaction.user.id or case["status"] != "open":
                await interaction.followup.send("This appeal is unavailable or has already been submitted.", ephemeral=True)
                return
            try:
                member = await self.active_member(case)
                channel = member.guild.get_channel(self.guard.log_channel_id)
                if channel is None:
                    raise ValueError("The staff channel is unavailable. Please contact staff or try again later.")
                embed = member_embed("Recovery appeal • Awaiting review",
                    discord.utils.escape_markdown(explanation), member)
                expires = datetime.fromisoformat(case["expires"])
                embed.add_field(name="Timeout expires", value=f"{discord.utils.format_dt(expires, chr(70))}\n{discord.utils.format_dt(expires, chr(82))}", inline=False)
                embed.add_field(name="Review", value="Verify account recovery before approving. Approval removes this timeout.", inline=False)
                embed.set_footer(text=f"Pulse case: {case_id}")
                message = await channel.send(embed=embed, view=ReviewView(self),
                                             allowed_mentions=discord.AllowedMentions.none())
                self.store.update(case_id, "pending", message.id, channel.id)
            except (ValueError, discord.HTTPException) as exc:
                text = str(exc) if isinstance(exc, ValueError) else "Could not submit the appeal. Please try again or contact staff."
                await interaction.followup.send(text, ephemeral=True)
                return
        await interaction.followup.send("Appeal sent to staff. Your timeout remains until approved or it expires.", ephemeral=True)

    async def review(self, interaction, approve):
        if (interaction.guild_id != self.guard.guild_id
                or not isinstance(interaction.user, discord.Member)
                or not interaction.user.guild_permissions.moderate_members):
            await interaction.response.send_message("You need Moderate Members in this server to review appeals.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            case_id = case_id_from(interaction.message)
            case = self.store.get(case_id)
            if (case is None or case["status"] != "pending"
                    or case["guild_id"] != interaction.guild_id
                    or case["review_message"] != interaction.message.id
                    or case["review_channel"] != interaction.channel_id):
                await interaction.followup.send("This appeal is unavailable or already reviewed.", ephemeral=True)
                return
            try:
                member = await self.active_member(case)
                if (interaction.user.id != member.guild.owner_id
                        and interaction.user.top_role <= member.top_role):
                    raise ValueError("Your role must be above this member to review their appeal.")
                if approve:
                    await member.timeout(None, reason=f"Pulse recovery appeal {case_id} approved by {interaction.user.id}")
                status = "approved" if approve else "denied"
                self.store.update(case_id, status)
            except (ValueError, discord.HTTPException) as exc:
                text = str(exc) if isinstance(exc, ValueError) else "Unable to review this timeout. Check membership, permissions, and role hierarchy."
                await interaction.followup.send(text, ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            embed.title = "Recovery appeal • " + ("Approved" if approve else "Declined")
            embed.colour = 0x2ECC71 if approve else 0xE74C3C
            embed.add_field(name="Reviewed by", value=f"<@{interaction.user.id}> (`{interaction.user.id}`)", inline=False)
            try:
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                log.exception("Could not update appeal card %s", case_id)
            notice = "No rejection DM sent; the timeout remains in place."
            if approve:
                try:
                    await member.send("Your recovery appeal was approved. Your timeout has been removed.",
                                      allowed_mentions=discord.AllowedMentions.none())
                    notice = "Timeout removed and approval DM sent."
                except discord.HTTPException:
                    notice = "Timeout removed, but the approval DM could not be delivered."
            await self.guard.report(member.guild,
                f"**Case #{case_id}** • Reviewed by <@{interaction.user.id}>\n{notice}",
                member=member, title="Appeal approved" if approve else "Appeal declined",
                color=0x2ECC71 if approve else 0xE74C3C)
        await interaction.followup.send(f"Appeal {status}. {notice}", ephemeral=True)


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        log.error("Appeal interaction failed", exc_info=(type(error), error, error.__traceback__))
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Could not complete this action. Please contact staff or try again later.", ephemeral=True)


class AppealView(SafeView):
    def __init__(self, appeals):
        super().__init__(timeout=None)
        self.appeals = appeals

    @discord.ui.button(label="Appeal timeout", style=discord.ButtonStyle.primary, custom_id="pulse_timeout_appeal")
    async def appeal(self, interaction, button):
        case_id = case_id_from(interaction.message)
        case = self.appeals.store.get(case_id)
        if (interaction.guild_id is not None or case is None or case["user_id"] != interaction.user.id
                or case["status"] != "open"):
            await interaction.response.send_message("This appeal is unavailable or already submitted.", ephemeral=True)
            return
        if datetime.fromisoformat(case["expires"]) <= discord.utils.utcnow():
            await interaction.response.send_message("This timeout has expired. No appeal is needed.", ephemeral=True)
            return
        await interaction.response.send_modal(AppealModal(self.appeals, case_id))


class AppealModal(discord.ui.Modal, title="Request timeout removal"):
    explanation = discord.ui.TextInput(label="How did you recover and secure your account?",
        style=discord.TextStyle.paragraph, min_length=20, max_length=1500,
        placeholder="Explain what happened. Do not include passwords, tokens, or recovery codes.")

    def __init__(self, appeals, case_id):
        super().__init__()
        self.appeals, self.case_id = appeals, case_id

    async def on_submit(self, interaction):
        await self.appeals.submit(interaction, self.case_id, str(self.explanation))

    async def on_error(self, interaction, error):
        await SafeView.on_error(self, interaction, error, None)


class ReviewView(SafeView):
    def __init__(self, appeals):
        super().__init__(timeout=None)
        self.appeals = appeals

    @discord.ui.button(label="Approve & remove timeout", style=discord.ButtonStyle.success, custom_id="pulse_timeout_approve")
    async def approve(self, interaction, button):
        await self.appeals.review(interaction, True)

    @discord.ui.button(label="Decline appeal", style=discord.ButtonStyle.danger, custom_id="pulse_timeout_decline")
    async def decline(self, interaction, button):
        await self.appeals.review(interaction, False)
