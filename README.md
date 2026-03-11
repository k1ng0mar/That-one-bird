# that one bird 🐦 — Setup Guide

## File Structure
```
that-one-bird/
├── main.py
├── requirements.txt
├── .env
└── cogs/
    ├── __init__.py
    ├── utils.py
    ├── settings.py
    ├── moderation.py
    ├── fun.py
    ├── info.py
    ├── automod.py
    ├── triggers.py
    ├── logging.py
    └── bloodtrials.py
```

## Wispbyte Setup
1. Upload ALL files maintaining the folder structure above
2. Set your `.env` variables (see below)
3. Delete `bot.db` if upgrading from the old single-file version
4. Start the bot

## .env Variables
```
TOKEN=your_discord_bot_token
GEMINI_API_KEY=your_gemini_api_key
PREFIX=?
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_anon_public_key
```

## First-Time Server Setup
Run `/setup` to see all current settings, then configure:

1. `/setlogchannel mod #mod-logs` — moderation actions
2. `/setlogchannel messages #message-logs` — edits/deletes  
3. `/setlogchannel members #member-logs` — joins/leaves/roles
4. `/setlogchannel server #server-logs` — voice/channels/invites
5. `/setwelcome #welcome Welcome {user}!`
6. `/setautorole @Member`
7. `/setjail #jail @Jailed`
8. `/setstarboard #starboard ⭐ 3`
9. `/setchapterchannel #announcements @everyone`
10. `/setcharacterchannel #characters`
11. `/automod toggle` — enable automod
12. `/automod addword <word>` — add filtered words
13. `/automod setaction warn` — or delete_only / mute

## Command Summary

### Moderation (slash + prefix)
All mod commands work as both `/slash` and `?prefix`:
- warn, unwarn, clearwarns, warns, modlogs
- mute, unmute, kick, ban, tempban
- jail, unjail, purge, nick, slowmode

### Fun (slash + prefix)
- meme, roast, 8ball, poll, remind, snipe, afk, deadchat
- hug, slap, bite, punch, kick_fun
- say, announce, pingrole

### Custom Commands
- `/addcommand name message Hello!` → `?name` sends "Hello!"
- `/addcommand myban alias ban` → `?myban @user` runs ban
- `/addcommand ping_mods ping @Mods` → `?ping_mods` pings the role

### Triggers
- `/settrigger gg Good game! contains`
- `/settrigger http://... [image URL] contains`

### Per-command Permissions
- `/setpermission warn @Moderator false` — only Moderators can warn
- `/setpermission deadchat @Member true` — Members only, silent fail

## Notes
- Bot checks Supabase every 2 minutes for new chapters/characters
- Chapters announce when `published` flips to `true`
- Characters announce when a new row is inserted
- Prefix is per-guild and stored in DB; change with `/setprefix`
- Gemini responds to mentions or in channels named `#ai-chat`
