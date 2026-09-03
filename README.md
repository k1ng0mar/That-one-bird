# That One Bird 🐦

Discord mod bot for any server. Moderation plus everyday utilities. Runs on discord.py with a local SQLite file.

Every command works as `/slash` and `?prefix`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Fill in TOKEN in `.env` then run. `bot.db` is created on first run. Delete it to reset everything.

On Render and similar hosts the bot binds a small keep alive web server to `$PORT` by itself. Nothing to set up there.

## What it does

* Moderation: warn, unwarn, clearwarns, warns, history, modlogs, mute and unmute with a DM when the mute ends, kick, ban, tempban with auto unban, jail and unjail, purge, nick, slowmode, lookup by user id. Reply to a message when you run a command and it targets that author and attaches the message as proof.
* Warn thresholds: `/setwarnthreshold kick 3` and it kicks at 3 warns. Set ban and mute the same way.
* Automod: word filter. Actions are delete only, warn, or mute. Word list plus mute length plus warn expiry, all per server.
* Anti raid: too many joins in a few seconds triggers slowmode, lockdown, or kick new. Toggle plus limits in settings.
* Roles: add, remove, info, list, create, delete, color. Prefix shortcuts included.
* Logs: mod actions, message delete and edit, member join leave role nick, voice, channel create and delete, invite create. Audit log sync so manual bans and kicks still show up.
* Welcome: channel plus message with `{user}` `{name}` `{server}` `{count}` placeholders. Autorole on join. Jail channel plus role. Starboard with emoji plus threshold. Deadchat pings.
* Triggers: auto replies with text, image, or gif. Match on contains or startswith.
* Custom commands: `/addcommand <name> <message|ping|alias> <value>`. Alias runs another command. Per server cooldowns. Per command permissions plus display mode public, ephemeral, or timed.
* Fun and utils: meme, roast, 8ball, poll, remind, snipe, afk with auto clear and ping notices, topic, coinflip, dice, calc, urban dictionary, firstmessage, hug, slap, bite, punch, kick, avatar, banner, servericon, quote image from a reply, say, announce, pingrole, bookmarks with 🔖 plus `mybookmarks`.
* Info: userinfo, serverinfo, ping, help.

## Admin quick start

```
/setup
/setprefix ?
/setlogchannel mod #modlogs
/setwelcome #general Hi {user}!
/setautorole @Member
/setjail #jail @Jailed
/setstarboard #starboard ⭐ 3
/setwarnthreshold kick 3
/setwarnthreshold ban 5
/antiraidtoggle
/automod toggle
/automod addword badword
/automod setaction mute
```

## Env

* `TOKEN`: Discord bot token. Required.
* `PREFIX`: Default prefix. Optional, `?` when empty.

## Notes

* One `on_message` hub in `cogs/events.py`. Automod first, then triggers, then custom commands. No listener fights.
* Repeats like reminders, tempbans, and mute expiry run on background tasks.
* Quote images fall back to a plain embed when fonts are missing.
