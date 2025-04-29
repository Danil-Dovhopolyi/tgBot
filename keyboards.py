from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters.callback_data import CallbackData


main_menu_authorized = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Обробити файл"), KeyboardButton(text="Список завантаженних файлів")],
        [KeyboardButton(text="Розлогінитись")]
    ],
    resize_keyboard=True
)

main_menu_unauthorized = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Авторизація")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)


class FileTypeCallbackData(CallbackData, prefix="file_type"):
    type: str

class DeleteFileCallbackData(CallbackData, prefix="delete_file"):
    file_id: int

file_type_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Документ",
                callback_data=FileTypeCallbackData(type="document").pack()
            ),
            InlineKeyboardButton(
                text="Фото",
                callback_data=FileTypeCallbackData(type="photo").pack()
            )
        ]
    ]
)

def create_delete_button(file_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Видалити",
                    callback_data=DeleteFileCallbackData(file_id=file_id).pack()
                )
            ]
        ]
    ) 