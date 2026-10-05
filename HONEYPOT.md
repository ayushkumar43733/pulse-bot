# Pulse honeypot and recovery appeals

Every non-exempt member message in the configured honeypot triggers a fixed
7-day timeout. Pulse deletes the triggering message and DMs an Appeal timeout
button. Discord automatically expires the timeout even while Pulse is offline.
No bans or softbans are performed.

After account recovery, the user submits an explanation through the DM button.
Staff receive Approve & remove timeout and Decline appeal buttons in the private
log channel. Approval removes the timeout immediately; declining preserves its
expiry. Pulse DMs the decision. Submitting an appeal never lifts a timeout itself.

## Configuration

Set HONEYPOT_CHANNEL_ID and HONEYPOT_LOG_CHANNEL_ID to separate text channel IDs
in GUILD_ID. Set HONEYPOT_DB_PATH to a writable file on persistent host storage
(default: honeypot.sqlite3 in the working directory). Keep the database across
restarts and redeployments; existing buttons and decisions depend on it. Run one
Pulse process against the database. Back it up as moderation data.

Remove HONEYPOT_ACTION, HONEYPOT_TIMEOUT_MINUTES and HONEYPOT_DELETE_SECONDS;
these obsolete settings are ignored. Duration is fixed at seven days.

Give Pulse Moderate Members and a role above ordinary members. In the honeypot,
grant View Channel, Send Messages, Embed Links and Manage Messages. In the private
staff channel, grant View Channel, Send Messages and Embed Links.

Initially hide the new honeypot channel. Run /post_honeypot_panel with Manage
Server permission, replacing any old warning panel. Then allow members to view
and send messages. Disable thread creation: only the exact channel is monitored.
Test with a consenting non-staff account, including DM submission, a bot restart,
and staff approval. No Message Content intent is required.

Unset HONEYPOT_CHANNEL_ID or set it to 0 to disable new triggers after restarting.
Existing appeals remain reviewable while Pulse runs.

## Safeguards and limits

- Only the case owner can submit, once per case. Only staff with Moderate Members
  and a higher role (or the server owner) can review. Staff should verify recovery:
  compromised accounts can also press the appeal button. Never request secrets.
- Old appeals cannot remove expired, removed or subsequently changed timeouts.
  Those cases require manual staff review. A pre-existing longer timeout is
  preserved without creating a new honeypot appeal.
- If DMs are blocked, Pulse reports it to staff. The user must contact staff through
  another route; Discord DM restrictions cannot be bypassed.
- Bots, webhooks, the owner and members with Administrator, Manage Server,
  Ban Members or Moderate Members are exempt. HONEYPOT_EXEMPT_ROLE_IDS optionally
  lists additional exempt role IDs separated by commas.
- Duplicate triggers are suppressed for ten seconds after processing. Spam outside
  the honeypot is not detected or cleaned up. A trigger does not prove hacking.
- A visible warning reduces accidental triggers but cannot eliminate them.

Run offline tests: python -m unittest discover -s tests -v
