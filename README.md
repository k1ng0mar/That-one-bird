# that one bird 🐦

Discord bot for the Blood Trials server. Built on discord.py with Groq AI (Umar-bot personality), Supabase polling, and a full moderation + utility suite.

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create `.env`
```env
TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
PREFIX=?
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_anon_key
```

### 3. Run
```bash
python main.py
```

The bot creates `bot.db` automatically on first run.

> **Note:** Delete `bot.db` before deploying a new version if the schema has changed.

---

## File Structure

```
that-one-bird/
├── main.py              # Entry point, DB init, bot class
├── requirements.txt
├── .env                 # Not included — create manually
└── cogs/
    ├── utils.py         # Shared helpers (no listeners)
    ├── utils_cog.py     # Shim to load utils as an extension
    ├── settings.py      # /setup and all /set* config commands
    ├── moderation.py    # warn/kick/ban/mute/jail/tempban/history/lookup
    ├── roles.py         # /role add/remove/info/list/create/delete/color
    ├── fun.py           # Groq chatbot, meme, roast, quote, avatar, etc.
    ├── info.py          # userinfo, serverinfo, ping, help
    ├── automod.py       # Word filter with configurable actions
    ├── triggers.py      # Custom text/image/GIF triggers
    ├── events.py        # Single on_message hub + all event logging
    └── bloodtrials.py   # Supabase polling for chapters + characters
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TOKEN` | ✅ | Discord bot token |
| `GROQ_API_KEY` | ✅ | Groq API key |
| `PREFIX` | ❌ | Default prefix (default: `?`) |
| `SUPABASE_URL` | ❌ | Supabase project URL |
| `SUPABASE_KEY` | ❌ | Supabase anon key |

---

## Key Features

### Moderation
- All commands available as both `/slash` and `?prefix`
- **Reply-to-target:** Run `?warn reason` while replying to a message → auto-targets replied user, attaches proof (jump link + message preview)
- **Configurable warn thresholds:** `/setwarnthreshold kick/ban/mute <count>` — replaces hardcoded 3-warn kick
- **Mute DM on expiry:** Background task polls timeouts and DMs users when their mute expires
- `/history @user` — all mod actions *against* a user (different from `/modlogs`)
- `/lookup <id>` — fetch any user by ID even if not in server

### AI Chatbot (Groq / Umar-bot)
- Mention the bot or chat in `#ai-chat` to activate
- Personality: Umar — 18-year-old Nigerian, analytical, blunt, dry humor
- Per-user conversation history (last 20 messages)
- Model: `llama-3.3-70b-versatile`

### Fun & Utilities
- `?quote` (reply to a message) — generates a styled image card with avatar
- `?coinflip`, `?dice [sides]`, `?calc <expr>`, `?urban <term>`, `?topic`
- `?firstmessage [@member]` — links to their first message in this channel
- `?avatar`, `/banner`, `?servericon`
- React 🔖 to any message → bookmarked, DM'd to you. View with `?mybookmarks`
- `/steal <emoji>` — steal an emoji from another server (add to yours)

### Response Visibility
`/setdisplay <command> <public|ephemeral|timed> [seconds]` — control whether any command's response is visible to everyone, only the user, or auto-deleted after N seconds.

### Roles
Full role management: `/role add/remove/info/list/create/delete/color` and prefix equivalents (`?roleadd`, `?roleremove`, `?roleinfo`, `?rolelist`).

### Event Logging
All events route through `events.py` — the single `on_message` hub. No listener conflicts.
- Message delete/edit
- Member join/leave/role update/nick change
- Voice state
- Channel create/delete
- Invite create
- Starboard (with parent message embed for replies)
- Bookmarks via 🔖 reaction
- Audit log sync (manual bans/kicks logged too)

### Blood Trials
- Supabase polling every 2 minutes for new published chapters and characters
- `/character <name>` lookup
- `/setup` shows configured channels

---

## Admin Quick-Start

```
/setup                          — view all current settings
/setprefix ?                    — set command prefix
/setlogchannel mod #mod-logs    — set mod log channel
/setwelcome #general Hi {user}! — set welcome message
/setautorole @Member            — auto-assign role on join
/setjail #jail @Jailed          — configure jail
/setstarboard #starboard ⭐ 3   — configure starboard
/setwarnthreshold kick 3        — auto-kick at 3 warns
/setwarnthreshold ban 5         — auto-ban at 5 warns
/setchapterchannel #chapters @Readers — Blood Trials announcements
/antiraidtoggle                 — toggle anti-raid protection
/automod toggle                 — toggle word filter
/automod addword badword        — add to filter
/automod setaction warn         — filter action: delete/warn/mute
```

---

## Notes

- `bot.db` is created automatically. Delete it to reset all settings.
- Wispbyte (Docker) replays log history on restart — pre-start errors are noise, not real.
- The `#ai-chat` channel name is matched case-insensitively.
- Blood Trials polling is disabled gracefully if `SUPABASE_URL`/`SUPABASE_KEY` are missing.
- Quote image generation falls back to a plain embed if Pillow font files are unavailable.
