import sqlite3
import logging
from pathlib import Path

# --- Configuration ---
DB_FILE = Path(__file__).parent.parent / "data" / "bot.db"
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# --- Logging ---
log = logging.getLogger(__name__)

# --- Schema Definition ---

TABLE_DEFINITIONS = {
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER NOT NULL UNIQUE,
            is_admin BOOLEAN NOT NULL DEFAULT 0,
            language_code TEXT NOT NULL DEFAULT 'en',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "plans": """
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            price_stars INTEGER NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 30,
            max_accounts INTEGER NOT NULL,
            daily_group_limit INTEGER NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT 1
        );
    """,
    "subscriptions": """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            start_date TIMESTAMP NOT NULL,
            end_date TIMESTAMP NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (plan_id) REFERENCES plans (id)
        );
    """,
    "proxies": """
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY,
            proxy_string TEXT NOT NULL UNIQUE,
            is_working BOOLEAN NOT NULL DEFAULT 1,
            last_checked TIMESTAMP
        );
    """,
    "managed_accounts": """
        CREATE TABLE IF NOT EXISTS managed_accounts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            session_string TEXT NOT NULL,
            proxy_id INTEGER,
            is_active BOOLEAN NOT NULL DEFAULT 1, -- User-controlled activation
            is_running BOOLEAN NOT NULL DEFAULT 0, -- System-controlled running state
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (proxy_id) REFERENCES proxies (id)
        );
    """,
    "group_creation_log": """
        CREATE TABLE IF NOT EXISTS group_creation_log (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            group_name TEXT NOT NULL,
            creation_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES managed_accounts (id)
        );
    """
}

# --- Database Initialization ---

def initialize_database():
    """
    Initializes the database by creating all necessary tables if they don't exist.
    """
    log.info(f"Initializing database at: {DB_FILE.resolve()}")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Enable foreign key support
            cursor.execute("PRAGMA foreign_keys = ON;")

            for table_name, table_sql in TABLE_DEFINITIONS.items():
                log.debug(f"Creating table: {table_name}")
                cursor.execute(table_sql)

            conn.commit()
        log.info("Database initialized successfully.")
    except sqlite3.Error as e:
        log.error(f"Database error during initialization: {e}")
        raise

def get_db_connection():
    """
    Returns a connection to the SQLite database.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# --- Plan Management Functions ---

def add_plan(name: str, price_stars: int, duration_days: int, max_accounts: int, daily_group_limit: int):
    """Adds a new subscription plan to the database."""
    sql = """
        INSERT INTO plans (name, price_stars, duration_days, max_accounts, daily_group_limit)
        VALUES (?, ?, ?, ?, ?)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (name, price_stars, duration_days, max_accounts, daily_group_limit))
            conn.commit()
        log.info(f"Successfully added new plan: {name}")
        return True
    except sqlite3.IntegrityError:
        log.warning(f"Plan with name '{name}' already exists.")
        return False
    except sqlite3.Error as e:
        log.error(f"Failed to add plan '{name}': {e}")
        return False

def get_plan_by_id(plan_id: int):
    """Retrieves a single plan by its ID."""
    sql = "SELECT * FROM plans WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (plan_id,))
            plan = cursor.fetchone()
            return dict(plan) if plan else None
    except sqlite3.Error as e:
        log.error(f"Failed to retrieve plan {plan_id}: {e}")
        return None

def get_all_plans(active_only: bool = True):
    """Retrieves all subscription plans from the database."""
    sql = "SELECT * FROM plans"
    if active_only:
        sql += " WHERE is_active = 1"

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            plans = cursor.fetchall()
            return [dict(plan) for plan in plans] # Return as list of dicts
    except sqlite3.Error as e:
        log.error(f"Failed to retrieve plans: {e}")
        return []

# --- User Management Functions ---

def get_all_users():
    """Retrieves all users from the database."""
    sql = "SELECT id, telegram_id, is_admin, created_at FROM users ORDER BY created_at DESC"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            users = cursor.fetchall()
            return [dict(user) for user in users]
    except sqlite3.Error as e:
        log.error(f"Failed to retrieve users: {e}")
        return []

def grant_subscription(telegram_id: int, plan_id: int, duration_days: int):
    """Grants a subscription to a user, deactivating any existing active ones."""
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    end_date = now_utc + timedelta(days=duration_days)

    get_user_sql = "SELECT id FROM users WHERE telegram_id = ?"
    deactivate_sql = "UPDATE subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1"
    insert_sql = """
        INSERT INTO subscriptions (user_id, plan_id, start_date, end_date, is_active)
        VALUES (?, ?, ?, ?, 1)
    """

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("BEGIN")
            cursor.execute(get_user_sql, (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                log.warning(f"No user found with telegram_id {telegram_id} to grant subscription.")
                conn.rollback()
                return False, "User not found."
            user_id = user_row['id']

            cursor.execute(deactivate_sql, (user_id,))
            cursor.execute(insert_sql, (user_id, plan_id, now_utc, end_date))
            conn.commit()

        log.info(f"Successfully granted plan {plan_id} to user {telegram_id} for {duration_days} days.")
        return True, "Subscription granted successfully."
    except sqlite3.Error as e:
        log.error(f"Failed to grant subscription to user {telegram_id}: {e}")
        conn.rollback()
        return False, "Database error."

def batch_insert_proxies(proxies: list[str]):
    """
    Inserts a list of proxy strings into the database, ignoring duplicates.
    Returns the number of newly inserted proxies.
    """
    if not proxies:
        return 0

    sql = "INSERT OR IGNORE INTO proxies (proxy_string) VALUES (?)"
    data = [(proxy,) for proxy in proxies if proxy.strip()] # Ensure no empty strings

    if not data:
        return 0

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(sql, data)
            conn.commit()
            log.info(f"Batch inserted proxies. {cursor.rowcount} new proxies were added.")
            return cursor.rowcount
    except sqlite3.Error as e:
        log.error(f"Failed to batch insert proxies: {e}")
        return 0

def get_random_proxy_id():
    """Retrieves the ID of a random, working proxy."""
    sql = "SELECT id FROM proxies WHERE is_working = 1 ORDER BY RANDOM() LIMIT 1"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            proxy = cursor.fetchone()
            return proxy['id'] if proxy else None
    except sqlite3.Error as e:
        log.error(f"Failed to retrieve a random proxy: {e}")
        return None

def add_managed_account(user_id: int, phone: str, session_string: str):
    """Adds a new managed account for a user and assigns a random proxy."""
    proxy_id = get_random_proxy_id()
    if proxy_id is None:
        log.warning(f"No available proxies to assign to account {phone}.")

    sql = """
        INSERT INTO managed_accounts (user_id, phone, session_string, proxy_id, is_active, is_running)
        VALUES (?, ?, ?, ?, 1, 0)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # We need the internal DB user ID, not the telegram_id
            internal_user_id_query = "SELECT id FROM users WHERE telegram_id = ?"
            cursor.execute(internal_user_id_query, (user_id,))
            internal_user_id_row = cursor.fetchone()
            if not internal_user_id_row:
                log.error(f"Cannot add account. User with Telegram ID {user_id} not found in users table.")
                return False

            internal_user_id = internal_user_id_row['id']
            cursor.execute(sql, (internal_user_id, phone, session_string, proxy_id))
            conn.commit()
        log.info(f"Successfully added account {phone} for user {user_id}.")
        return True
    except sqlite3.IntegrityError:
        log.warning(f"Account with phone number {phone} already exists.")
        return False
    except sqlite3.Error as e:
        log.error(f"Failed to add managed account {phone} for user {user_id}: {e}")
        return False

# --- User Dashboard Functions ---

def delete_managed_account(account_id: int, telegram_user_id: int):
    """Deletes a managed account, ensuring the user owns it."""
    sql = """
        DELETE FROM managed_accounts
        WHERE id = ? AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id, telegram_user_id))
            conn.commit()
            return cursor.rowcount > 0 # Returns True if a row was deleted
    except sqlite3.Error as e:
        log.error(f"Failed to delete account {account_id} for user {telegram_user_id}: {e}")
        return False

def toggle_account_status(account_id: int, telegram_user_id: int):
    """Toggles the is_active status of a managed account."""
    sql = """
        UPDATE managed_accounts
        SET is_active = NOT is_active
        WHERE id = ? AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
    """
    get_status_sql = "SELECT is_active FROM managed_accounts WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id, telegram_user_id))
            conn.commit()
            if cursor.rowcount > 0:
                cursor.execute(get_status_sql, (account_id,))
                new_status = cursor.fetchone()
                return new_status['is_active'] if new_status else None
            return None
    except sqlite3.Error as e:
        log.error(f"Failed to toggle status for account {account_id}: {e}")
        return None

def reassign_proxy(account_id: int, telegram_user_id: int):
    """Assigns a new random proxy to a managed account."""
    new_proxy_id = get_random_proxy_id()
    if new_proxy_id is None:
        return False, "No available proxies."

    sql = """
        UPDATE managed_accounts
        SET proxy_id = ?
        WHERE id = ? AND user_id = (SELECT id FROM users WHERE telegram_id = ?)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (new_proxy_id, account_id, telegram_user_id))
            conn.commit()
            return cursor.rowcount > 0, "Proxy updated."
    except sqlite3.Error as e:
        log.error(f"Failed to reassign proxy for account {account_id}: {e}")
        return False, "Database error."

def get_account_session_string(account_id: int):
    """Retrieves the session string for a specific managed account."""
    sql = "SELECT session_string FROM managed_accounts WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id,))
            row = cursor.fetchone()
            return row['session_string'] if row else None
    except sqlite3.Error as e:
        log.error(f"Failed to get session string for account {account_id}: {e}")
        return None

def get_account_stats(account_id: int):
    """Gets creation stats for a specific managed account."""
    sql = "SELECT COUNT(id) as total_groups FROM group_creation_log WHERE account_id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id,))
            stats = cursor.fetchone()
            return stats['total_groups'] if stats else 0
    except sqlite3.Error as e:
        log.error(f"Failed to get stats for account {account_id}: {e}")
        return 0

def get_groups_for_account(account_id: int):
    """Retrieves all groups created by a specific managed account."""
    sql = "SELECT id, group_id, group_name, creation_timestamp FROM group_creation_log WHERE account_id = ? ORDER BY creation_timestamp DESC"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id,))
            groups = cursor.fetchall()
            return [dict(group) for group in groups]
    except sqlite3.Error as e:
        log.error(f"Failed to get groups for account {account_id}: {e}")
        return []

def get_group_log_details(group_log_id: int):
    """Retrieves the details of a single group from the creation log."""
    sql = "SELECT group_name, creation_timestamp FROM group_creation_log WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (group_log_id,))
            group_details = cursor.fetchone()
            return dict(group_details) if group_details else None
    except sqlite3.Error as e:
        log.error(f"Failed to get group log details for log {group_log_id}: {e}")
        return None

def mark_proxy_as_bad(proxy_id: int):
    """Marks a proxy as not working."""
    if proxy_id is None:
        return
    sql = "UPDATE proxies SET is_working = 0, last_checked = CURRENT_TIMESTAMP WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (proxy_id,))
            conn.commit()
        log.warning(f"Marked proxy {proxy_id} as bad.")
    except sqlite3.Error as e:
        log.error(f"Failed to mark proxy {proxy_id} as bad: {e}")

def get_proxy_string(proxy_id: int):
    """Gets the proxy string for a given proxy ID."""
    if proxy_id is None:
        return None
    sql = "SELECT proxy_string FROM proxies WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (proxy_id,))
            row = cursor.fetchone()
            return row['proxy_string'] if row else None
    except sqlite3.Error as e:
        log.error(f"Failed to get proxy string for id {proxy_id}: {e}")
        return None

def get_eligible_accounts():
    """
    Retrieves all accounts that are active and belong to a user with an active subscription.
    Returns a list of dicts, each containing account and plan details.
    """
    sql = """
        SELECT
            ma.id as account_id,
            ma.session_string,
            ma.proxy_id,
            p.daily_group_limit,
            u.telegram_id
        FROM managed_accounts ma
        JOIN users u ON ma.user_id = u.id
        JOIN subscriptions s ON u.id = s.user_id
        JOIN plans p ON s.plan_id = p.id
        WHERE ma.is_active = 1
          AND s.is_active = 1
          AND s.end_date >= datetime('now')
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            accounts = cursor.fetchall()
            return [dict(acc) for acc in accounts]
    except sqlite3.Error as e:
        log.error(f"Failed to retrieve eligible accounts: {e}")
        return []

def get_groups_created_today(account_id: int):
    """Counts the number of groups created by an account in the last 24 hours."""
    sql = """
        SELECT COUNT(id) as count
        FROM group_creation_log
        WHERE account_id = ? AND creation_timestamp >= datetime('now', '-24 hours')
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0
    except sqlite3.Error as e:
        log.error(f"Failed to count groups for account {account_id}: {e}")
        return 0

def log_group_creation(account_id: int, group_id: int, group_name: str):
    """Logs a successful group creation event."""
    sql = "INSERT INTO group_creation_log (account_id, group_id, group_name) VALUES (?, ?, ?)"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (account_id, group_id, group_name))
            conn.commit()
            return True
    except sqlite3.Error as e:
        log.error(f"Failed to log group creation for account {account_id}: {e}")
        return False

def get_or_create_user(telegram_id: int):
    """
    Retrieves a user by their telegram_id, creating them if they don't exist.
    Returns the user as a dict.
    """
    select_sql = "SELECT * FROM users WHERE telegram_id = ?"
    # Note: Default language_code is 'en' via the table schema
    insert_sql = "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Use INSERT OR IGNORE and then SELECT to handle race conditions gracefully
            # and avoid a separate SELECT call first in the common case.
            cursor.execute(insert_sql, (telegram_id,))
            if cursor.rowcount > 0:
                log.info(f"Created new user record for telegram_id: {telegram_id}")

            cursor.execute(select_sql, (telegram_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    except sqlite3.Error as e:
        log.error(f"Database error in get_or_create_user for {telegram_id}: {e}")
        return None

def set_user_language(telegram_id: int, lang_code: str):
    """Sets the preferred language for a user."""
    get_or_create_user(telegram_id)  # Ensure user exists before setting language
    sql = "UPDATE users SET language_code = ? WHERE telegram_id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (lang_code, telegram_id))
            conn.commit()
        return True
    except sqlite3.Error as e:
        log.error(f"Failed to set language for user {telegram_id}: {e}")
        return False

def get_user_language(telegram_id: int):
    """
    Gets the preferred language for a user, creating the user if they don't exist.
    """
    user = get_or_create_user(telegram_id)
    if user and user.get('language_code'):
        return user['language_code']
    return 'en' # Default to 'en'


def get_user_details(telegram_id: int):
    """Retrieves details for a user, including their active subscription and managed accounts."""
    details = {}
    user_sql = "SELECT * FROM users WHERE telegram_id = ?"
    sub_sql = """
        SELECT s.*, p.name as plan_name FROM subscriptions s
        JOIN plans p ON s.plan_id = p.id
        WHERE s.user_id = ? AND s.is_active = 1
        ORDER BY s.end_date DESC LIMIT 1
    """
    accounts_sql = "SELECT id, phone, is_active FROM managed_accounts WHERE user_id = ?"

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(user_sql, (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return None
            details['user'] = dict(user_row)
            user_id = user_row['id']

            cursor.execute(sub_sql, (user_id,))
            sub_row = cursor.fetchone()
            details['subscription'] = dict(sub_row) if sub_row else None

            cursor.execute(accounts_sql, (user_id,))
            accounts = cursor.fetchall()
            details['accounts'] = [dict(acc) for acc in accounts]

            return details
    except sqlite3.Error as e:
        log.error(f"Failed to get details for user {telegram_id}: {e}")
        return None

if __name__ == '__main__':
    # This allows running the script directly to create the DB
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("Running database initialization...")
    initialize_database()
    print("Done.")
