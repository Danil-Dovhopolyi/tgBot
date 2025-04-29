## Features

*   User registration via `/start`.
*   Authorization using `/auth <key>` with single-use keys stored in the database.
*   Reply keyboard menu for authorized users:
    *   **Обробити файл**: Initiate file upload process.
    *   **Список завантаженних файлів**: View a list of own uploaded files with details.
    *   **Розлогінитись**: Log out the user, requiring re-authorization.
*   File upload process:
    *   Choose between "Документ" (.pdf, .doc, .docx, .xlsx) and "Фото".
    *   Bot validates file type/extension upon upload.
    *   Files are saved locally in `temp/<user_id>/` directory.
    *   File metadata (path, uploader, timestamp, type) is stored in the `storage` database table.
*   File listing displays details and provides an inline button to delete each file.
*   File deletion removes the file from the disk and the corresponding record from the database.
*   User actions (login, logout, file upload/delete, button clicks) are logged to the `logs` table.
*   Persistent storage using PostgreSQL.

## Prerequisites

*   Python 3.8+ (or compatible with your aiogram version)
*   PostgreSQL server running

## Setup Instructions

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure the Bot:**
    *   Rename or copy `config.py.example` to `config.py` (if you have an example file, otherwise create `config.py`).
    *   Edit `config.py` and fill in your details:
        *   `BOT_TOKEN`: Your Telegram Bot Token obtained from @BotFather.
        *   `DB_USER`: Your PostgreSQL username.
        *   `DB_PASS`: Your PostgreSQL password.
        *   `DB_NAME`: The name for the bot's database (e.g., `bot_database`).
        *   `DB_HOST`: Hostname of your PostgreSQL server (usually `localhost`).
        *   `DB_PORT`: Port of your PostgreSQL server (usually `5432`).

4.  **Set up PostgreSQL Database:**
    *   Make sure your PostgreSQL server is running.
    *   Create the database specified in `DB_NAME`
    *   Ensure the user specified in `DB_USER` has permissions to connect to and modify this database.
    *   **Note:** On the first run, if the `auth_keys` table is empty, the bot will automatically populate it with the following test keys: `key123`, `secretkey`, `auth777`. You can use these for initial authorization testing.

## Running the Bot

Once the setup is complete, run the main script from your activated virtual environment:

```bash
python main.py
```
