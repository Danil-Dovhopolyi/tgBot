import asyncpg
import logging
import config
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


async def create_pool():
    logger.info(f"Attempting to create database pool for {config.DB_NAME} at {config.DB_HOST}:{config.DB_PORT}")
    try:
        pool = await asyncpg.create_pool(
            user=config.DB_USER,
            password=config.DB_PASS,
            database=config.DB_NAME,
            host=config.DB_HOST,
            port=config.DB_PORT,
        )
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
        logger.info("Database pool created and connection verified.")
        return pool

    except asyncpg.exceptions.InvalidPasswordError:
        logger.critical(f"Invalid database password for user '{config.DB_USER}'. Exiting.")
        raise
    except OSError as e:
        logger.critical(f"Network error connecting to database at {config.DB_HOST}:{config.DB_PORT}. Is it running? Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error creating database pool: {e}")
        raise


async def create_tables(pool: asyncpg.Pool):
    async with pool.acquire() as connection:
        async with connection.transaction():
            logger.info("Checking/Creating users table...")
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE NOT NULL,
                    username VARCHAR(255),
                    registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    is_authorized BOOLEAN DEFAULT FALSE
                );
            """)

            logger.info("Checking/Creating auth_keys table...")
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS auth_keys (
                    id SERIAL PRIMARY KEY,
                    auth_key VARCHAR(255) UNIQUE NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)

            logger.info("Checking/Creating storage table...")
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS storage (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    file_path VARCHAR(1024) NOT NULL,
                    original_filename VARCHAR(255) NOT NULL,
                    file_type VARCHAR(50) NOT NULL CHECK (file_type IN ('photo', 'document')),
                    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)

            logger.info("Checking/Creating logs table...")
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NULL, -- Allow NULL if user is deleted
                    action_description TEXT NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
                );
            """)

            key_count = await connection.fetchval("SELECT COUNT(*) FROM auth_keys")
            if key_count == 0:
                logger.info("Auth_keys table is empty, populating with test keys...")
                test_keys = [('key123',), ('secretkey',), ('auth777',)]
                await connection.executemany("INSERT INTO auth_keys (auth_key) VALUES ($1)", test_keys)
                logger.info(f"Added {len(test_keys)} test authentication keys.")
            else:
                 logger.info("Auth_keys table already contains keys, skipping population.")

        logger.info("Database tables checked/created successfully.")


async def get_user(pool: asyncpg.Pool, user_id: int) -> Optional[asyncpg.Record]:
    query = "SELECT id, user_id, username, registered_at, is_authorized FROM users WHERE user_id = $1"
    return await pool.fetchrow(query, user_id)


async def add_user(pool: asyncpg.Pool, user_id: int, username: Optional[str]) -> asyncpg.Record:
    query = """
            INSERT INTO users(user_id, username, registered_at, is_authorized)
            VALUES($1, $2, NOW(), FALSE)
            RETURNING id, user_id, username, registered_at, is_authorized
            """
    return await pool.fetchrow(query, user_id, username)


async def check_auth_key(connection: asyncpg.Connection, auth_key: str) -> Optional[asyncpg.Record]:
    query = "SELECT id, is_used FROM auth_keys WHERE auth_key = $1 FOR UPDATE"
    return await connection.fetchrow(query, auth_key)


async def mark_key_used(connection: asyncpg.Connection, auth_key: str):
    query = "UPDATE auth_keys SET is_used = TRUE WHERE auth_key = $1"
    await connection.execute(query, auth_key)


async def set_user_authorized_in_transaction(connection: asyncpg.Connection, user_id: int):
    query = "UPDATE users SET is_authorized = TRUE WHERE user_id = $1"
    await connection.execute(query, user_id)


async def set_user_authorization_status(pool: asyncpg.Pool, user_id: int, is_authorized: bool):
    query = "UPDATE users SET is_authorized = $1 WHERE user_id = $2"
    status = await pool.execute(query, is_authorized, user_id)
    if status == "UPDATE 1":
        logger.info(f"Set user {user_id} authorization status to {is_authorized}")
    else:
        logger.warning(f"Failed to update authorization status for user {user_id} (perhaps user doesn't exist?)")


async def authorize_user_with_key(pool: asyncpg.Pool, user_id: int, auth_key: str) -> Tuple[bool, str]:
    async with pool.acquire() as connection:
        async with connection.transaction():
            key_record = await check_auth_key(connection, auth_key)
            if not key_record:
                logger.warning(f"Authorization attempt failed for user {user_id}: Invalid key '{auth_key}'")
                return False, "Недійсний ключ авторизації."
            if key_record['is_used']:
                logger.warning(f"Authorization attempt failed for user {user_id}: Key '{auth_key}' already used.")
                return False, "Цей ключ авторизації вже було використано."

            await mark_key_used(connection, auth_key)

            await set_user_authorized_in_transaction(connection, user_id)

            logger.info(f"User {user_id} successfully authorized using key '{auth_key}'.")
            return True, "Авторизація успішна."


async def add_file_record(pool: asyncpg.Pool, user_id: int, file_path: str, original_filename: str, file_type: str):
    query = """
            INSERT INTO storage (user_id, file_path, original_filename, file_type)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """
    try:
        file_id = await pool.fetchval(query, user_id, file_path, original_filename, file_type)
        logger.info(f"Added file record (ID: {file_id}) for user {user_id}: {original_filename}")
    except asyncpg.IntegrityConstraintViolationError as e:
        logger.error(f"Failed to add file record for user {user_id}. User might not exist? Error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error adding file record for user {user_id}: {e}")
        raise


async def get_user_files(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    query = """
            SELECT
                s.id, s.user_id, s.file_path, s.original_filename, s.file_type,
                s.uploaded_at,
                u.username
            FROM storage s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.user_id = $1
            ORDER BY s.uploaded_at DESC
            """
    return await pool.fetch(query, user_id)


async def get_file_record(pool: asyncpg.Pool, file_id: int) -> Optional[asyncpg.Record]:
    query = "SELECT id, user_id, file_path, original_filename, file_type, uploaded_at FROM storage WHERE id = $1"
    return await pool.fetchrow(query, file_id)


async def delete_file_record(pool: asyncpg.Pool, file_id: int) -> Optional[str]:
    file_record = await get_file_record(pool, file_id)
    if not file_record:
        logger.warning(f"Attempted to delete non-existent file record with ID: {file_id}")
        return None

    file_path_to_delete = file_record['file_path']

    query = "DELETE FROM storage WHERE id = $1 RETURNING id"
    deleted_id = await pool.fetchval(query, file_id)

    if deleted_id:
        logger.info(f"Deleted file record with ID: {file_id} (Path: {file_path_to_delete})")
        return file_path_to_delete
    else:
        logger.error(f"Failed to delete file record with ID: {file_id} despite finding it initially.")
        return None


async def log_user_action(pool: asyncpg.Pool, user_id: Optional[int], description: str):
    query = "INSERT INTO logs (user_id, action_description) VALUES ($1, $2)"
    try:
        await pool.execute(query, user_id, description)
    except Exception as e:
        logger.error(f"Failed to log user action to DB for user {user_id}: {description}. Error: {e}")