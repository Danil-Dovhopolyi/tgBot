import asyncpg
import logging
import datetime
from pathlib import Path
import functools
from typing import Callable, Any, Awaitable, Optional, Union

from aiogram import Router, types, F, Bot
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramAPIError

from db.database import (
    get_user, add_user, authorize_user_with_key, set_user_authorization_status,
    add_file_record, get_user_files, get_file_record, delete_file_record,
    log_user_action
)
from keyboards import (
    main_menu_authorized, main_menu_unauthorized,
    file_type_keyboard, FileTypeCallbackData,
    create_delete_button, DeleteFileCallbackData
)
from states import FileUpload

logger = logging.getLogger(__name__)
router = Router()

AUTH_PROMPT = "\nБудь ласка, використайте команду `/auth <ваш_ключ>` або натисніть кнопку 'Авторизація'."
TEMP_DIR = Path("temp")
ALLOWED_DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xlsx"} 

TEMP_DIR.mkdir(parents=True, exist_ok=True)

def require_authorization(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(func)
    async def wrapper(event: Union[Message, CallbackQuery], *args, **kwargs):
        pool: asyncpg.Pool = kwargs.get('pool')
        bot: Bot = kwargs.get('bot')
        user: Optional[types.User] = None
        chat_id: Optional[int] = None

        if isinstance(event, Message):
            user = event.from_user
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            if event.message: 
                chat_id = event.message.chat.id

        if not user or not pool or not bot:
            logger.critical(f"Could not get user, pool, or bot in require_authorization for {func.__name__}. Event type: {type(event)}")
            if isinstance(event, CallbackQuery):
                try: await event.answer("Internal error during authorization check.", show_alert=True)
                except TelegramAPIError: pass
            return 

        user_record = await get_user(pool, user.id)

        if user_record and user_record['is_authorized']:
            return await func(event, *args, **kwargs)
        else:
            logger.warning(f"Unauthorized access attempt by user {user.id} ({user.username}) to {func.__name__}")

            unauthorized_message = "Ця функція доступна тільки авторизованим користувачам."
            reply_markup = main_menu_unauthorized

            if chat_id:
                try:
                    await bot.send_message(chat_id, unauthorized_message, reply_markup=reply_markup)
                except TelegramAPIError as e:
                    logger.error(f"Failed to send unauthorized message to chat {chat_id} for user {user.id}: {e}")

            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(unauthorized_message, show_alert=True)
                except TelegramAPIError:
                     logger.warning(f"Failed to answer callback query for unauthorized user {user.id}")
            return 

    return wrapper

def _format_file_info(file_record: asyncpg.Record) -> str:
    uploaded_time_str = file_record['uploaded_at'].strftime('%Y-%m-%d %H:%M:%S') if file_record['uploaded_at'] else 'N/A'
    display_file_type = "Фото" if file_record['file_type'] == 'photo' else "Документ"
    return (
        f"Файл № {file_record['id']}**\n"
        f"Завантажив: {file_record['username'] or 'Невідомо'}\n"
        f"Дата та час: {uploaded_time_str}\n"
        f"Тип файлу: {display_file_type}\n"
        f"Назва файлу: `{file_record['original_filename']}`"
    )

async def _save_uploaded_file(bot: Bot, file_id: str, user_id: int, desired_filename: str) -> Optional[Path]:
    user_temp_dir = TEMP_DIR / str(user_id)
    try:
        user_temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Could not create directory {user_temp_dir}: {e}")
        return None

    file_path_on_disk = user_temp_dir / desired_filename
    try:
        await bot.download(file=file_id, destination=str(file_path_on_disk))
        logger.info(f"File downloaded successfully to: {file_path_on_disk}")
        return file_path_on_disk
    except Exception as e:
        logger.exception(f"Failed to download file {file_id} to {file_path_on_disk}: {e}")
        return None

async def _delete_file_from_disk(file_path_str: str):
    if not file_path_str:
        logger.warning("Attempted to delete file from disk, but path was None.")
        return

    file_path = Path(file_path_str)
    try:
        if file_path.is_file():
            file_path.unlink()
            logger.info(f"Deleted file from disk: {file_path}")
        else:
            logger.warning(f"File not found on disk for deletion: {file_path}")
    except OSError as e:
        logger.error(f"Error deleting file {file_path} from disk: {e}")


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    username = message.from_user.username
    user_record = await get_user(pool, user_id)

    reply_markup = main_menu_unauthorized
    greeting = f"Привіт, {username}!"
    log_desc = f"User {user_id} used /start."

    if user_record:
        if user_record['is_authorized']:
            greeting = f"З поверненням, {user_record['username'] or 'user'}!"
            reply_markup = main_menu_authorized
            log_desc += " Already registered and authorized."
        else:
            greeting += " Ви зареєстровані, але ще не авторизовані."
            greeting += AUTH_PROMPT
            log_desc += " Already registered, but not authorized."
    else:
        try:
            new_user = await add_user(pool, user_id, username)
            logger.info(f"New user registered: {new_user['username']} (ID: {new_user['user_id']})")
            greeting += "Ви зареєстровані в системі."
            greeting += AUTH_PROMPT
            log_desc += "New user registration."
            await log_user_action(pool, user_id, f"User {user_id} registered.")
        except Exception as e:
            logger.exception(f"Failed to register user {user_id}: {e}")
            greeting = "Виникла помилка під час реєстрації. Спробуйте пізніше."
            log_desc += "Registration failed."
            await log_user_action(pool, user_id, f"User {user_id} registration failed.")

    await message.answer(greeting, reply_markup=reply_markup)
    if "registered" not in log_desc and "failed" not in log_desc:
         await log_user_action(pool, user_id, log_desc)

@router.message(Command("auth"))
async def cmd_auth(message: Message, command: CommandObject, pool: asyncpg.Pool):
    user_id = message.from_user.id
    username = message.from_user.username
    log_desc_base = f"User {user_id} attempted authorization"

    user_record = await get_user(pool, user_id)
    if not user_record:
        await message.answer(
            "Будь ласка, спочатку зареєструйтесь за допомогою команди /start.",
            reply_markup=main_menu_unauthorized
        )
        await log_user_action(pool, user_id, log_desc_base + " - failed: not registered.")
        return

    if user_record['is_authorized']:
        await message.answer(
            f"{username}, ви вже авторизовані.",
            reply_markup=main_menu_authorized
        )
        await log_user_action(pool, user_id, log_desc_base + " - failed: already authorized.")
        return

    auth_key = command.args
    if not auth_key:
        await message.answer("Будь ласка, вкажіть ключ авторизації після команди. Приклад: `/auth ваш_ключ`")
        await log_user_action(pool, user_id, log_desc_base + " - failed: no key provided.")
        return

    try:
        success, response_message = await authorize_user_with_key(pool, user_id, auth_key)
        reply_markup = main_menu_authorized if success else main_menu_unauthorized
        if success:
            response_message = f"Вітаємо, {username}! {response_message}"
            await log_user_action(pool, user_id, log_desc_base + f" - successful with key '{auth_key}'.")
        else:
            await log_user_action(pool, user_id, log_desc_base + f" - failed: {response_message}")

        await message.answer(response_message, reply_markup=reply_markup)

    except Exception as e:
        logger.exception(f"Error during authorization process for user {user_id}: {e}")
        await message.answer("Виникла внутрішня помилка під час авторизації. Спробуйте пізніше.")
        await log_user_action(pool, user_id, log_desc_base + " - failed: internal error.")



@router.message(F.text == "Авторизація")
async def handle_auth_button(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} pressed 'Авторизація' button.")
    user_record = await get_user(pool, message.from_user.id)
    if user_record and not user_record['is_authorized']:
        await message.answer("Будь ласка, введіть команду `/auth <ваш_ключ>` для авторизації.")
    elif user_record and user_record['is_authorized']:
        await message.answer("Ви вже авторизовані.", reply_markup=main_menu_authorized)
    else:
        await message.answer("Будь ласка, спочатку використайте /start для реєстрації.")


@router.message(F.text == "Розлогінитись")
@require_authorization
async def handle_logout(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await set_user_authorization_status(pool, user_id, is_authorized=False)
    await log_user_action(pool, user_id, f"User {user_id} logged out.")
    await message.answer(
        "Ви успішно розлогінились.",
        reply_markup=main_menu_unauthorized
    )
    logger.info(f"User {message.from_user.username} (ID: {user_id}) logged out.")

@router.message(F.text == "Обробити файл")
@require_authorization
async def handle_process_file(message: Message, state: FSMContext, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} pressed 'Обробити файл' button.")
    await message.answer(
        "Оберіть тип файлу для завантаження:",
        reply_markup=file_type_keyboard
    )
    await state.set_state(FileUpload.choosing_file_type)

@router.message(F.text == "Список завантаженних файлів")
@require_authorization
async def handle_list_files(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} pressed 'Список завантаженних файлів' button.")
    files = await get_user_files(pool, user_id)

    if not files:
        await message.answer("Ви ще не завантажували файли.")
        return

    await message.answer("Ваші завантажені файли:")
    for file_record in files:
        file_info = _format_file_info(file_record)
        await message.answer(
            file_info,
            reply_markup=create_delete_button(file_record['id']),
            parse_mode="Markdown"
        )


@router.callback_query(FileTypeCallbackData.filter(), StateFilter(FileUpload.choosing_file_type))
@require_authorization
async def handle_file_type_choice(query: CallbackQuery, callback_data: FileTypeCallbackData, state: FSMContext, pool: asyncpg.Pool):
    user_id = query.from_user.id
    file_type = callback_data.type
    await log_user_action(pool, user_id, f"User {user_id} chose file type '{file_type}'.")
    await query.answer()

    if file_type == "document":
        await query.message.edit_text(f"Будь ласка, завантажте документ ({', '.join(ALLOWED_DOC_EXTENSIONS)}). Або /cancel")
        await state.set_state(FileUpload.awaiting_document)
    elif file_type == "photo":
        await query.message.edit_text("Будь ласка, завантажте фото. Або /cancel")
        await state.set_state(FileUpload.awaiting_photo)
    else:
        logger.warning(f"Received unexpected file_type callback data: {file_type}")
        await query.message.edit_text("Невідомий тип файлу. Спробуйте ще раз.")
        await state.clear()

@router.callback_query(DeleteFileCallbackData.filter())
@require_authorization
async def handle_delete_file(query: CallbackQuery, callback_data: DeleteFileCallbackData, pool: asyncpg.Pool):
    file_id = callback_data.file_id
    user_id = query.from_user.id
    log_desc_base = f"User {user_id} attempted to delete file ID {file_id}"

    file_path_to_delete = await delete_file_record(pool, file_id)

    if file_path_to_delete:
        await _delete_file_from_disk(file_path_to_delete)
        await query.answer("Файл видалено.")
        await log_user_action(pool, user_id, log_desc_base + " - successful.")
        try:
            if query.message and query.message.text:
                await query.message.edit_text(f"{query.message.text}\n\n **Видалено**", parse_mode="Markdown")
            elif query.message:
                await query.message.delete()
        except TelegramAPIError as edit_error:
            logger.error(f"Could not edit message {query.message.message_id if query.message else 'N/A'} after deleting file {file_id}: {edit_error}")
            try:
                if query.message:
                    await query.message.delete()
            except TelegramAPIError as delete_error:
                logger.error(f"Could not delete message {query.message.message_id if query.message else 'N/A'} after failed edit: {delete_error}")
    else:
        file_record = await get_file_record(pool, file_id)
        if file_record and file_record['user_id'] != user_id:
            await query.answer("Помилка: Неможливо видалити файл іншого користувача.", show_alert=True)
            await log_user_action(pool, user_id, log_desc_base + " - failed: permission denied.")
        else:
            await query.answer("Помилка: Файл не знайдено або вже видалено.", show_alert=True)
            if query.message and query.message.text and "Видалено" not in query.message.text:
                try:
                    await query.message.edit_text(f"{query.message.text}\n\n*Помилка видалення або вже видалено*", parse_mode="Markdown")
                except TelegramAPIError:
                    pass


@router.message(StateFilter(FileUpload.awaiting_document), F.document)
@require_authorization
async def handle_document_upload(message: Message, state: FSMContext, pool: asyncpg.Pool, bot: Bot):
    document = message.document
    original_filename = document.file_name or "unknown_document"
    file_extension = Path(original_filename).suffix.lower()

    if file_extension not in ALLOWED_DOC_EXTENSIONS:
        allowed_formats = ", ".join(ALLOWED_DOC_EXTENSIONS)
        await message.reply(f"Невірний формат ({file_extension}). Дозволено: {allowed_formats}.\nНадішліть інший файл або /cancel.")
        await log_user_action(pool, message.from_user.id, f"User {message.from_user.id} sent wrong input while awaiting document. Invalid format ({file_extension}).")
        return

    save_filename = f"{document.file_unique_id}{file_extension}"
    saved_path = await _save_uploaded_file(bot, document.file_id, message.from_user.id, save_filename)

    if not saved_path:
        await message.reply("Не вдалося зберегти файл. Спробуйте ще раз або /cancel.")
        await log_user_action(pool, message.from_user.id, f"User {message.from_user.id} sent wrong input while awaiting document. Could not save file.")
        return

    try:
        await add_file_record(
            pool=pool,
            user_id=message.from_user.id,
            file_path=str(saved_path),
            original_filename=original_filename,
            file_type='document'
        )
        await message.answer(f"Документ '{original_filename}' завантажено!", reply_markup=main_menu_authorized)
        await log_user_action(pool, message.from_user.id, f"User {message.from_user.id} successfully uploaded document '{original_filename}'")
        await state.clear()
    except Exception as db_error:
        logger.exception(f"Failed to add DB record for document {original_filename} (user {message.from_user.id}): {db_error}")
        await message.reply("Файл завантажено, але сталася помилка при збереженні запису в базу. Зверніться до адміністратора.")
        await log_user_action(pool, message.from_user.id, f"User {message.from_user.id} failed to save document '{original_filename}' to database.")
        await state.clear()

@router.message(StateFilter(FileUpload.awaiting_photo), F.photo)
@require_authorization
async def handle_photo_upload(message: Message, state: FSMContext, pool: asyncpg.Pool, bot: Bot):
    photo = message.photo[-1]
    user_id = message.from_user.id

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_filename = f"photo_{photo.file_unique_id}_{timestamp}.jpg"

    log_desc_base = f"User {user_id} attempted to upload photo '{save_filename}'"

    saved_path = await _save_uploaded_file(bot, photo.file_id, user_id, save_filename)

    if not saved_path:
        await message.reply("Не вдалося зберегти фото. Спробуйте ще раз або /cancel.")
        await log_user_action(pool, user_id, log_desc_base + " - failed: could not save photo.")
        return

    try:
        await add_file_record(
            pool=pool,
            user_id=user_id,
            file_path=str(saved_path),
            original_filename=save_filename,
            file_type='photo'
        )
        await message.answer("Фото успішно завантажено!", reply_markup=main_menu_authorized)
        await log_user_action(pool, user_id, log_desc_base + " - successful.")
        await state.clear()
    except Exception as db_error:
        logger.exception(f"Failed to add DB record for photo {save_filename} (user {user_id}): {db_error}")
        await message.reply("Фото завантажено, але сталася помилка при збереженні запису в базу. Зверніться до адміністратора.")
        await log_user_action(pool, user_id, log_desc_base + " - failed: database error saving record.")
        await state.clear()


@router.message(StateFilter(FileUpload.awaiting_document))
@require_authorization
async def handle_wrong_document_input(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} sent wrong input while awaiting document.")
    allowed_formats = ", ".join(ALLOWED_DOC_EXTENSIONS)
    await message.reply(f"Очікується файл документа ({allowed_formats}). Надішліть файл або скасуйте: /cancel.")

@router.message(StateFilter(FileUpload.awaiting_photo))
@require_authorization
async def handle_wrong_photo_input(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} sent wrong input while awaiting photo.")
    await message.reply("Очікується фото. Надішліть фото або скасуйте: /cancel.")

@router.message(StateFilter(FileUpload.choosing_file_type))
@require_authorization
async def handle_text_instead_of_callback(message: Message, pool: asyncpg.Pool):
    user_id = message.from_user.id
    await log_user_action(pool, user_id, f"User {user_id} sent text instead of choosing file type button.")
    await message.reply("Будь ласка, натисніть одну з кнопок вище ('Документ' або 'Фото') або скасуйте: /cancel.")


@router.message(Command("cancel"), StateFilter(FileUpload))
async def cancel_upload_command(message: Message, state: FSMContext, pool: asyncpg.Pool):
    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state is None:
        await message.reply("Немає активної дії для скасування.", reply_markup=main_menu_authorized)
        return

    logger.info(f"User {user_id} cancelled state {current_state}")
    await log_user_action(pool, user_id, f"User {user_id} cancelled action in state {current_state}.")
    await state.clear()
    await message.answer("Дію скасовано.", reply_markup=main_menu_authorized) 