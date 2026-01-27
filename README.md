# Telegram Group Creation Bot

This is a comprehensive Telegram bot designed to manage multiple Telegram user accounts for the purpose of automated group creation. The bot is multi-user, subscription-based (using Telegram Stars), and includes a full admin panel for management.

It uses a dual-library architecture:
- **`python-telegram-bot`**: For the user-facing bot interface (commands, buttons, conversations).
- **`Pyrogram`**: For the backend automation involving user accounts (logging in, creating groups).

## Features

- **Multi-User & Subscriptions**: Regular users can subscribe to plans to use the bot's features.
- **Admin Panel**: A full-featured admin dashboard inside the bot for managing users, plans, and subscriptions.
- **Telegram Account Management**: Users can interactively add their own Telegram accounts to the bot for automation.
- **Automated Group Creation**: Managed accounts automatically create new private supergroups based on user-defined limits.
- **Proxy Management**: Automatically downloads and rotates proxies to avoid rate-limiting and bans.
- **User Dashboard**: Users can manage their added accounts, view stats, and control automation.
- **Multi-Language Support**: Interface is available in English and Arabic, with easy extension to other languages.

## Project Structure

```
.
├── data/                  # Holds the SQLite database and session files (ignored by git)
├── src/                   # Main source code
│   ├── admin_handlers.py    # Command handlers for the admin panel
│   ├── automation.py        # Core background automation logic
│   ├── config.py            # Configuration loader
│   ├── database.py          # Database schema and interaction logic
│   ├── main.py              # Main entry point of the bot
│   ├── proxy_manager.py     # Logic for downloading and managing proxies
│   ├── translation.py       # Internationalization (i18n) setup
│   └── user_handlers.py     # Command handlers for regular users
├── .env.example           # Example environment variables file
├── .gitignore             # Git ignore rules
├── babel.cfg              # Babel configuration for i18n
├── locales/               # Translation files
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

## Setup and Installation

Follow these steps to get the bot running.

### 1. Clone the Repository

```bash
git clone <repository_url>
cd <repository_directory>
```

### 2. Create a Virtual Environment

It is highly recommended to use a virtual environment to manage dependencies.

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

Install all the required Python packages using pip.

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

The bot is configured using environment variables. Create a `.env` file in the root directory by copying the example file.

```bash
cp .env.example .env
```

Now, open the `.env` file and fill in the required values:

- `BOT_TOKEN`: Your main bot's token from @BotFather.
- `API_ID` and `API_HASH`: Your Telegram API credentials from [my.telegram.org](https://my.telegram.org).
- `ADMIN_IDS`: Your numeric Telegram user ID. This will give you admin access to the bot.
- `PAYMENT_PROVIDER_TOKEN`: The payment token for Telegram Stars, obtained from @BotFather.

### 5. Initialize the Database

The database is created automatically when you first run the bot. Alternatively, you can initialize it manually:

```bash
python3 src/database.py
```

## Running the Bot

Once the setup is complete, you can run the bot with the following command from the root directory of the project:

```bash
python3 -m src.main
```

The bot will start, connect to Telegram, and the automation engine will begin its cycles. You can interact with the bot from the Telegram account you designated as the admin.

### Key Commands

- `/start`: Initialize the bot.
- `/subscribe`: View and subscribe to a plan.
- `/add_account`: Start the process to add a new Telegram account to manage.
- `/my_accounts`: View and manage your added accounts.
- `/language`: Change the interface language.

### Admin Commands

- `/create_plan <name> <price> <days> <accounts> <limit>`: Create a new subscription plan.
- `/list_plans`: View all created plans.
- `/list_users`: See all users of the bot.
- `/view_user <user_id>`: Get details for a specific user.
- `/grant_subscription <user_id> <plan_id> <days>`: Manually give a subscription to a user.
