from aiogram.fsm.state import State, StatesGroup


class FileUpload(StatesGroup):
    choosing_file_type = State()
    awaiting_document = State()
    awaiting_photo = State()