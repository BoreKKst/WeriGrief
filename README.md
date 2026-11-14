# WeriGrief Staff Bot

Discord staff-management bot assembled from the current WeriGrief archive, retaining its configuration, role/channel IDs, Google Sheets integration, Pterodactyl integration, ticket UI, embeds, warnings, vacation state, and staff workflows. The AetherGlow archive was reviewed as the legacy feature/style source; WeriGrief's IDs and configuration take precedence.

## Install and run (Termux, without venv)

```bash
cd ~/WeriGrief
pkg update -y
pkg install python -y
pip install -r requirements.txt
cp .env.template .env
nano .env
python main.py
```

Do **not** create or activate `venv`, `.venv`, or `env`.

## Environment

Fill in the existing values in `.env`:
- `BOT_TOKEN`
- `GUILD_ID`
- `WARN_NOTIFY_CHANNEL`
- `STAFF_NOTIFY_CHANNEL`
- `SPREADSHEET_ID`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `POLL_INTERVAL`
- `PTERO_BASE_URL`
- `PTERO_API_KEY`
- `PTERO_SERVER_IDS`
- `MAIN_MC_SERVER_ID`
- `GAME_SERVERS`

`DAILY_NORM_HOURS` and `MC_STATS_DIRECTORY` are optional overrides. No API key or service-account JSON is bundled. Place your Google service-account JSON at the configured path and share the spreadsheet with that service-account email.

## Google Sheets layout

The `Списки` staff sheet is read and written as exactly six columns:

| Column | Value |
|---|---|
| A | Игровой никнейм |
| B | Должность |
| C | Discord username |
| D | Telegram / VK |
| E | Кол-во отыгранных часов |
| F | Предупреждения |

The separate `Учет предупреждений` sheet stores the warning history. The bot does not append extra staff columns.

## Commands

- `/appoint` — appoint a staff member (choose a Discord member where offered, provide the Minecraft nickname, and choose a position)
- `/promote`, `/demote` — change a staff member's position
- `/unstaff` — dismiss a staff member (`/dismiss` prefix alias)
- `/staff`, `/dismissed`
- `/warn`, `/unwarn`, `/warns`, `/clearwarns`
- `/vacation` — up to 15 days; cooldown is 13 days after the previous vacation ends
- `/norm`, `/check_norm`, `/norm_exempt`
- Existing ticket/rules UI components remain available

Position permissions are checked by the bot using WeriGrief's staff roles and leadership IDs. For role names whose IDs were not present in the supplied archive, the bot attempts an exact-name match on the guild; no IDs are fabricated.

## Daily norm

The background sync loop checks the `Europe/Moscow` clock and runs the daily process at the midnight polling window. It stores the completion date in `src/services/sheets_state.json` to avoid duplicate automatic runs and retries if Pterodactyl stats are unavailable. `/check_norm` is the live administrative run: it is not a dry-run and can send DMs, apply warnings, and update Sheets.

## Pterodactyl notes

The bot reads Minecraft `stats/<offline-uuid>.json` files through the Pterodactyl Client API and issues LuckPerms commands through the configured server IDs. Pterodactyl API access, server IDs, file path, game server configuration, Minecraft UUID mode, and bot permissions must match the live deployment. Failed or unavailable API calls are reported as failures; the bot does not report an API operation as successful unless the endpoint confirms it.

## Discord permissions

Enable the bot's Server Members Intent and Message Content Intent in the Discord Developer Portal if using member lookup and the retained message features. Give the bot Manage Roles, Manage Channels (for report tickets), View Channels, Send Messages, Embed Links, and Read Message History. Place the bot's highest role above every staff role it must manage.

## Security and persistence

- `.env`, Google service-account JSON, logs, and runtime state are not bundled.
- Keep `.env` and service-account JSON private.
- Vacation, warning sync, and norm snapshots persist in `src/services/sheets_state.json` after the bot starts.
- Automatic daily execution is guarded by a saved date to avoid duplicate runs after restarts.

## Проверка границ интеграции

В предоставленных архивах нет готового протокола получения ответа консоли Pterodactyl (WebSocket Client API). Поэтому текущая реализация:
- читает накопленное время из Minecraft `stats` JSON через Pterodactyl Files API;
- отправляет LuckPerms-команды через Pterodactyl Client API и отличает HTTP-подтверждение API от подтверждённого изменения группы;
- **не заявляет**, что умеет получать текущий список игроков или читать фактическую текущую LuckPerms-группу игрока из консоли.

Также в исходном WeriGrief `.env.template` поле `SPREADSHEET_ID` пустое. В архиве не найден реальный Spreadsheet ID, поэтому его нужно указать в `.env`; секреты и Google service-account JSON намеренно не включены.


## Исправления WeriGrief StaffBot (исправленная сборка)

- `/unstaff` зарегистрирован как отдельная Discord slash-команда, а не только как гибридная команда/алиас. После запуска в логе печатается список синхронизированных slash-команд.
- `/check_norm` выполняет только предварительный просмотр (`dry_run`): не выдаёт предупреждения, не начисляет часы и не меняет дату последней автоматической проверки.
- Автоматическая проверка нормы остаётся отдельной задачей планировщика на 00:00 `Europe/Moscow`. Для её работы должны быть доступны Google Sheets и статистика Minecraft из Pterodactyl.
- Предупреждения в Minecraft отправляются адресно игроку через `tellraw` по официальному Pterodactyl Client WebSocket API. Общий чат не используется. Игрок должен быть онлайн.
- `/staff` читает актуальный состав непосредственно из листа `Списки` (колонки A:F) и разбивает длинный список на несколько Embed, чтобы Discord не обрезал состав по лимиту 25 полей.
- Убраны жёсткие ограничения диапазонов в 50/100 строк для операций со списком персонала и часами.
- Команды LuckPerms также отправляются через консольный WebSocket Pterodactyl, а не через несуществующий REST endpoint `/command`.

**Важно:** в `.env` должны быть заполнены `BOT_TOKEN`, `SPREADSHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `PTERO_API_KEY`, `PTERO_SERVER_IDS`, `WARN_NOTIFY_CHANNEL` и `STAFF_NOTIFY_CHANNEL`. Файл сервисного аккаунта Google и `.env` в архив не включены. Реальную доставку команды можно подтвердить только после запуска с Вашими ключами и сервером.


## Minecraft / Pterodactyl

Для выполнения LuckPerms и личных `tellraw` команд нужен **Pterodactyl Client API key** (обычно `ptlc_...`) с доступом к нужному серверу и правом Console. Application API key (`ptla_...`) не подходит для `/api/client/servers/.../websocket`. Проверьте `PTERO_BASE_URL`, `PTERO_SERVER_IDS` (идентификатор сервера из панели) и наличие LuckPerms на целевом сервере. В логах `werigrief_staff_bot.log` теперь выводятся ошибки WebSocket, отказ авторизации и доступный ответ консоли.

Если должности в таблице имеют нестандартные названия, задайте соответствия групп LuckPerms в `.env` через `LP_GROUP_MAP`, например `LP_GROUP_MAP={"D.ADMIN":"admin","D.MODER":"moder"}`. Значения справа должны быть реальными именами групп LuckPerms, уже существующими на вашем Minecraft-сервере.

`/check_norm` вручную проверяет весь состав и публикует подробный отчёт в канале «Персонал», но работает в режиме без варнов, без начисления часов и без изменения отметки автоматической проверки.
