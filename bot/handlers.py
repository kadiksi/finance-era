import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bot.config import Config
from bot.models import OPERATION_LABELS, Operation, OperationType
from bot.sheets import GoogleSheetsClient


OperationWriter = Callable[[Operation, str], Awaitable[str]]
ValueLoader = Callable[[], Awaitable[list[str]]]

MENU_TO_OPERATION = {
    "Пополнение": OperationType.MANAGER_TRANSFER,
    "Расход": OperationType.MANAGER_EXPENSE,
}


class OperationForm(StatesGroup):
    choosing_project = State()
    searching_project = State()
    entering_project = State()
    entering_amount = State()
    choosing_category = State()
    entering_category = State()
    entering_description = State()
    confirming = State()


def create_router(
    config: Config,
    append_operation: OperationWriter,
    load_projects: ValueLoader,
    load_categories: ValueLoader,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        if not _is_allowed(message.from_user.id if message.from_user else None, config):
            await message.answer("У вас нет доступа к этому боту.")
            return

        await state.clear()
        await message.answer(
            "Выберите операцию:",
            reply_markup=_main_menu_keyboard(),
        )

    @router.message(F.text.in_(MENU_TO_OPERATION.keys()))
    async def start_operation(message: Message, state: FSMContext) -> None:
        if not _is_allowed(message.from_user.id if message.from_user else None, config):
            await message.answer("У вас нет доступа к этому боту.")
            return

        operation_type = MENU_TO_OPERATION[str(message.text)]
        await state.clear()
        await state.update_data(operation_type=operation_type.value)

        projects = await load_projects()
        if projects:
            await state.update_data(all_project_options=projects, project_options=projects)
            await state.set_state(OperationForm.choosing_project)
            await message.answer(
                "Выберите проект:",
                reply_markup=_projects_keyboard(projects),
            )
        else:
            await state.set_state(OperationForm.entering_project)
            await message.answer(
                "Список проектов не найден. Введите название проекта текстом:",
                reply_markup=_cancel_keyboard(),
            )

    @router.callback_query(OperationForm.choosing_project, F.data.startswith("project:"))
    async def choose_project(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        projects = list(data.get("project_options", []))
        project_index = int(str(callback.data).removeprefix("project:"))
        try:
            project = projects[project_index]
        except IndexError:
            await callback.answer("Проект не найден, выберите заново.", show_alert=True)
            return

        await state.update_data(project=project)
        await state.set_state(OperationForm.entering_amount)
        await callback.message.answer("Введите сумму:", reply_markup=_cancel_keyboard())
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "search_project")
    async def request_project_search(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OperationForm.searching_project)
        await callback.message.answer(
            "Введите часть названия проекта для поиска:",
            reply_markup=_cancel_keyboard(),
        )
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "show_all_projects")
    async def show_all_projects(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        projects = list(data.get("all_project_options", []))
        await state.update_data(project_options=projects)
        await callback.message.answer(
            "Выберите проект:",
            reply_markup=_projects_keyboard(projects),
        )
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "manual_project")
    async def request_manual_project(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OperationForm.entering_project)
        await callback.message.answer("Введите название проекта:", reply_markup=_cancel_keyboard())
        await callback.answer()

    @router.message(OperationForm.searching_project)
    async def search_project(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()
        if not query:
            await message.answer("Введите непустой запрос для поиска.")
            return

        data = await state.get_data()
        projects = list(data.get("all_project_options", []))
        matches = _filter_projects(projects, query)
        if not matches:
            await message.answer("Проекты не найдены. Введите другой запрос или нажмите Отмена.")
            return

        await state.update_data(project_options=matches)
        await state.set_state(OperationForm.choosing_project)
        await message.answer(
            "Найденные проекты:",
            reply_markup=_projects_keyboard(matches, show_all=True),
        )

    @router.message(OperationForm.entering_project)
    async def enter_project(message: Message, state: FSMContext) -> None:
        if not message.text or not message.text.strip():
            await message.answer("Введите непустое название проекта.")
            return

        await state.update_data(project=message.text.strip())
        await state.set_state(OperationForm.entering_amount)
        await message.answer("Введите сумму:")

    @router.message(OperationForm.entering_amount)
    async def enter_amount(message: Message, state: FSMContext) -> None:
        amount = _parse_amount(message.text or "")
        if amount is None or amount <= 0:
            await message.answer("Введите положительную сумму, например 1500 или 1500.50.")
            return

        await state.update_data(amount=amount)
        data = await state.get_data()
        operation_type = OperationType(str(data["operation_type"]))
        if operation_type == OperationType.MANAGER_TRANSFER:
            await state.update_data(category="")
            await state.set_state(OperationForm.entering_description)
            await message.answer(
                "Введите комментарий или нажмите Пропустить:",
                reply_markup=_skip_comment_keyboard(),
            )
            return

        categories = await load_categories()
        if categories:
            await state.update_data(category_options=categories)
            await state.set_state(OperationForm.choosing_category)
            await message.answer(
                "Выберите категорию:",
                reply_markup=_categories_keyboard(categories),
            )
        else:
            await state.set_state(OperationForm.entering_category)
            await message.answer(
                "Список категорий не найден. Введите категорию текстом:",
                reply_markup=_cancel_keyboard(),
            )

    @router.callback_query(OperationForm.choosing_category, F.data.startswith("category:"))
    async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        categories = list(data.get("category_options", []))
        category_index = int(str(callback.data).removeprefix("category:"))
        try:
            category = categories[category_index]
        except IndexError:
            await callback.answer("Категория не найдена, выберите заново.", show_alert=True)
            return

        await state.update_data(category=category)
        await state.set_state(OperationForm.entering_description)
        await callback.message.answer("Введите описание или комментарий:", reply_markup=_cancel_keyboard())
        await callback.answer()

    @router.message(OperationForm.entering_category)
    async def enter_category(message: Message, state: FSMContext) -> None:
        if not message.text or not message.text.strip():
            await message.answer("Введите категорию.")
            return

        await state.update_data(category=message.text.strip())
        await state.set_state(OperationForm.entering_description)
        await message.answer("Введите описание или комментарий:")

    @router.callback_query(OperationForm.entering_description, F.data == "skip_description")
    async def skip_description(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(description="")
        data = await state.get_data()
        await state.set_state(OperationForm.confirming)
        await callback.message.answer(
            _format_confirmation(data),
            reply_markup=_confirmation_keyboard(),
        )
        await callback.answer()

    @router.message(OperationForm.entering_description)
    async def enter_description(message: Message, state: FSMContext) -> None:
        await state.update_data(description=(message.text or "").strip())
        data = await state.get_data()
        await state.set_state(OperationForm.confirming)
        await message.answer(
            _format_confirmation(data),
            reply_markup=_confirmation_keyboard(),
        )

    @router.callback_query(OperationForm.confirming, F.data == "confirm")
    async def confirm_operation(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        operation = Operation.from_state(
            data=data,
            timezone=config.timezone,
        )
        balance = await append_operation(operation, _telegram_nickname(callback.from_user))
        await state.clear()
        await callback.message.answer(
            f"Операция записана в Google Sheets.\nОстаток: {balance}",
            reply_markup=_main_menu_keyboard(),
        )
        await callback.answer()

    @router.callback_query(F.data == "cancel")
    async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.answer(
            "Операция отменена.",
            reply_markup=_main_menu_keyboard(),
        )
        await callback.answer()

    @router.message(F.text == "Отмена")
    async def cancel_message(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Операция отменена.",
            reply_markup=_main_menu_keyboard(),
        )

    @router.message()
    async def fallback(message: Message) -> None:
        await message.answer("Выберите операцию в меню или нажмите /start.")

    return router


def create_google_sheets_router(config: Config, sheets: GoogleSheetsClient) -> Router:
    async def append_operation(operation: Operation, user_nickname: str) -> str:
        return await asyncio.to_thread(sheets.append_operation, operation, user_nickname)

    async def load_projects() -> list[str]:
        return await asyncio.to_thread(sheets.get_projects)

    async def load_categories() -> list[str]:
        return await asyncio.to_thread(sheets.get_categories)

    return create_router(config, append_operation, load_projects, load_categories)


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Пополнение"), KeyboardButton(text="Расход")],
        ],
        resize_keyboard=True,
    )


def _cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
    )


def _projects_keyboard(projects: list[str], show_all: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=project, callback_data=f"project:{index}")]
        for index, project in enumerate(projects[:30])
    ]
    buttons.append([InlineKeyboardButton(text="Поиск проекта", callback_data="search_project")])
    if show_all:
        buttons.append([InlineKeyboardButton(text="Показать все", callback_data="show_all_projects")])
    buttons.append([InlineKeyboardButton(text="Ввести вручную", callback_data="manual_project")])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=category, callback_data=f"category:{index}")]
        for index, category in enumerate(categories[:30])
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _skip_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_description")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


def _format_confirmation(data: dict[str, Any]) -> str:
    operation_type = OperationType(str(data["operation_type"]))
    lines = [
        "Проверьте операцию:",
        f"Тип: {OPERATION_LABELS[operation_type]}",
        f"Проект: {data['project']}",
        f"Сумма: {data['amount']}",
    ]
    if operation_type == OperationType.MANAGER_EXPENSE:
        lines.append(f"Категория: {data['category']}")
        lines.append(f"Описание: {data['description']}")
    else:
        lines.append(f"Комментарий: {data['description']}")
    return "\n".join(lines)


def _parse_amount(value: str) -> float | None:
    normalized = value.replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _filter_projects(projects: list[str], query: str) -> list[str]:
    return _filter_values(projects, query)


def _filter_values(values: list[str], query: str) -> list[str]:
    normalized_query = query.casefold()
    return [value for value in values if normalized_query in value.casefold()]


def _is_allowed(user_id: int | None, config: Config) -> bool:
    if user_id is None:
        return False
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def _telegram_nickname(user: Any) -> str:
    if user is None:
        return "unknown"
    if user.username:
        return user.username
    if user.full_name:
        return user.full_name
    return f"user_{user.id}"
