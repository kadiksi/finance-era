import asyncio
import re
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
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
ValueLoader = Callable[[str | None], Awaitable[list[str]]]
ProjectLoader = Callable[[], Awaitable[list[str]]]

MENU_TO_OPERATION = {
    "Пополнение": OperationType.MANAGER_TRANSFER,
    "Расход": OperationType.MANAGER_EXPENSE,
}


class OperationForm(StatesGroup):
    choosing_group = State()
    choosing_purpose = State()
    entering_amount = State()
    entering_comment = State()
    choosing_project = State()
    searching_project = State()
    entering_project = State()
    confirming = State()


def create_router(
    config: Config,
    append_operation: OperationWriter,
    load_groups: ValueLoader,
    load_purposes: ValueLoader,
    load_projects: ProjectLoader,
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

    @router.message(Command("справка", "help"))
    async def show_reference(message: Message) -> None:
        if not _is_allowed(message.from_user.id if message.from_user else None, config):
            await message.answer("У вас нет доступа к этому боту.")
            return

        nickname = _telegram_nickname(message.from_user)
        groups = await load_groups(nickname)
        purposes = await load_purposes(nickname)
        await message.answer(_format_reference(groups, purposes))

    @router.message(F.text.in_(MENU_TO_OPERATION.keys()))
    async def start_operation(message: Message, state: FSMContext) -> None:
        if not _is_allowed(message.from_user.id if message.from_user else None, config):
            await message.answer("У вас нет доступа к этому боту.")
            return

        operation_type = MENU_TO_OPERATION[str(message.text)]
        await state.clear()
        await state.update_data(operation_type=operation_type.value)

        nickname = _telegram_nickname(message.from_user)
        groups = await load_groups(nickname)
        await _refresh_project_options(state, load_projects)
        await state.update_data(group_options=groups)
        await state.set_state(OperationForm.choosing_group)
        await message.answer(
            "Выберите группу кнопкой или введите быстро:\n"
            "<группа> <назначение> <комментарий> <сумма> <проект>\n"
            "Пример: 1 2 продажа двери 100000 373\n"
            "Номера: /справка",
            reply_markup=_options_keyboard(groups, "group"),
        )

    @router.message(OperationForm.choosing_group)
    async def quick_input_or_hint(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        groups = list(data.get("group_options", []))
        text = (message.text or "").strip()
        if not text or not _looks_like_quick_input(text):
            await message.answer(
                "Выберите группу кнопкой или введите быстро:\n"
                "<группа> <назначение> <комментарий> <сумма> <проект>\n"
                "Пример: 1 2 продажа двери 100000 373\n"
                "Номера: /справка",
                reply_markup=_options_keyboard(groups, "group"),
            )
            return

        nickname = _telegram_nickname(message.from_user)
        purposes = await load_purposes(nickname)
        projects = await _refresh_project_options(state, load_projects)

        parsed = _parse_quick_input(text, groups, purposes)
        if isinstance(parsed, str):
            await message.answer(parsed)
            return

        project = _resolve_project(projects, parsed["project_query"])
        if project is None:
            matches = _filter_values(projects, parsed["project_query"])
            if not matches:
                await message.answer(f"Проект «{parsed['project_query']}» не найден.")
            else:
                preview = "\n".join(f"• {name}" for name in matches[:5])
                suffix = "\n..." if len(matches) > 5 else ""
                await message.answer(
                    f"Проект «{parsed['project_query']}»: найдено {len(matches)}.\n"
                    f"Уточните код:\n{preview}{suffix}"
                )
            return

        await state.update_data(
            group=parsed["group"],
            purpose=parsed["purpose"],
            comment=parsed["comment"],
            amount=parsed["amount"],
            project=project,
        )
        await _show_confirmation(message, state)

    @router.callback_query(OperationForm.choosing_group, F.data.startswith("group:"))
    async def choose_group(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        groups = list(data.get("group_options", []))
        group = _option_by_index(groups, str(callback.data).removeprefix("group:"))
        if group is None:
            await callback.answer("Группа не найдена, выберите заново.", show_alert=True)
            return

        await state.update_data(group=group)
        nickname = _telegram_nickname(callback.from_user)
        purposes = await load_purposes(nickname)
        await state.update_data(purpose_options=purposes)
        await state.set_state(OperationForm.choosing_purpose)
        await callback.message.answer(
            "Выберите назначение платежа:",
            reply_markup=_options_keyboard(purposes, "purpose"),
        )
        await callback.answer()

    @router.callback_query(OperationForm.choosing_purpose, F.data.startswith("purpose:"))
    async def choose_purpose(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        purposes = list(data.get("purpose_options", []))
        purpose = _option_by_index(purposes, str(callback.data).removeprefix("purpose:"))
        if purpose is None:
            await callback.answer("Назначение не найдено, выберите заново.", show_alert=True)
            return

        await state.update_data(purpose=purpose)
        await state.set_state(OperationForm.entering_amount)
        await callback.message.answer("Введите сумму:", reply_markup=_cancel_keyboard())
        await callback.answer()

    @router.message(OperationForm.entering_amount)
    async def enter_amount(message: Message, state: FSMContext) -> None:
        amount = _parse_amount(message.text or "")
        if amount is None or amount <= 0:
            await message.answer("Введите положительную сумму, например 1500 или 1500.50.")
            return

        await state.update_data(amount=amount)
        await state.set_state(OperationForm.entering_comment)
        await message.answer(
            "Введите комментарий или нажмите Пропустить:",
            reply_markup=_skip_comment_keyboard(),
        )

    @router.callback_query(OperationForm.entering_comment, F.data == "skip_comment")
    async def skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(comment="")
        await _ask_project(callback.message, state, load_projects)
        await callback.answer()

    @router.message(OperationForm.entering_comment)
    async def enter_comment(message: Message, state: FSMContext) -> None:
        await state.update_data(comment=(message.text or "").strip())
        await _ask_project(message, state, load_projects)

    @router.callback_query(OperationForm.choosing_project, F.data.startswith("project:"))
    async def choose_project(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        projects = list(data.get("project_options", []))
        project = _option_by_index(projects, str(callback.data).removeprefix("project:"))
        if project is None:
            projects = await _refresh_project_options(state, load_projects)
            await callback.answer("Проект не найден. Список обновлён — выберите заново.", show_alert=True)
            await callback.message.answer(
                "Выберите проект или нажмите Пропустить:",
                reply_markup=_projects_keyboard(projects),
            )
            return

        await state.update_data(project=project)
        await _show_confirmation(callback.message, state)
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "skip_project")
    async def skip_project_from_list(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(project="")
        await _show_confirmation(callback.message, state)
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "search_project")
    async def request_project_search(callback: CallbackQuery, state: FSMContext) -> None:
        await _refresh_project_options(state, load_projects)
        await state.set_state(OperationForm.searching_project)
        await callback.message.answer(
            "Введите часть названия проекта для поиска:",
            reply_markup=_cancel_keyboard(),
        )
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "show_all_projects")
    async def show_all_projects(callback: CallbackQuery, state: FSMContext) -> None:
        projects = await _refresh_project_options(state, load_projects)
        await callback.message.answer(
            "Выберите проект:",
            reply_markup=_projects_keyboard(projects),
        )
        await callback.answer()

    @router.callback_query(OperationForm.entering_project, F.data == "skip_project")
    async def skip_project_manual(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(project="")
        await _show_confirmation(callback.message, state)
        await callback.answer()

    @router.callback_query(OperationForm.choosing_project, F.data == "manual_project")
    async def request_manual_project(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OperationForm.entering_project)
        await callback.message.answer(
            "Введите название проекта или нажмите Пропустить:",
            reply_markup=_skip_project_keyboard(),
        )
        await callback.answer()

    @router.message(OperationForm.searching_project)
    async def search_project(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()
        if not query:
            await message.answer("Введите непустой запрос для поиска.")
            return

        projects = await _refresh_project_options(state, load_projects)
        matches = _filter_values(projects, query)
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
        await _show_confirmation(message, state)

    @router.callback_query(OperationForm.confirming, F.data == "confirm")
    async def confirm_operation(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        if data.get("submitted"):
            await callback.answer("Операция уже сохраняется…", show_alert=True)
            return

        await state.update_data(submitted=True)
        await state.set_state(None)

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

        await callback.answer("Сохраняю…")

        operation = Operation.from_state(
            data=data,
            timezone=config.timezone,
        )
        try:
            balance = await append_operation(
                operation,
                _telegram_nickname(callback.from_user),
            )
        except Exception:
            await state.set_state(OperationForm.confirming)
            await state.update_data(submitted=False)
            await callback.message.answer(
                "Не удалось записать операцию. Нажмите «Подтвердить» ещё раз.",
                reply_markup=_confirmation_keyboard(),
            )
            return

        await state.clear()
        await callback.message.answer(
            f"Операция записана в Google Sheets.\nОстаток: {balance}",
            reply_markup=_main_menu_keyboard(),
        )

    @router.callback_query(F.data == "confirm")
    async def stale_confirm(callback: CallbackQuery) -> None:
        await callback.answer("Эта операция уже обработана.", show_alert=True)

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


async def _refresh_project_options(
    state: FSMContext,
    load_projects: ProjectLoader,
) -> list[str]:
    projects = await load_projects()
    await state.update_data(all_project_options=projects, project_options=projects)
    return projects


async def _ask_project(
    message: Message,
    state: FSMContext,
    load_projects: ProjectLoader,
) -> None:
    projects = await _refresh_project_options(state, load_projects)
    if projects:
        await state.set_state(OperationForm.choosing_project)
        await message.answer(
            "Выберите проект или нажмите Пропустить:",
            reply_markup=_projects_keyboard(projects),
        )
    else:
        await state.set_state(OperationForm.entering_project)
        await message.answer(
            "Список проектов не найден. Введите название проекта текстом или нажмите Пропустить:",
            reply_markup=_skip_project_keyboard(),
        )


async def _show_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(OperationForm.confirming)
    await message.answer(
        _format_confirmation(data),
        reply_markup=_confirmation_keyboard(),
    )


def create_google_sheets_router(config: Config, sheets: GoogleSheetsClient) -> Router:
    async def append_operation(operation: Operation, user_nickname: str) -> str:
        return await asyncio.to_thread(sheets.append_operation, operation, user_nickname)

    async def load_groups(nickname: str | None) -> list[str]:
        return await asyncio.to_thread(sheets.get_groups, nickname)

    async def load_purposes(nickname: str | None) -> list[str]:
        return await asyncio.to_thread(sheets.get_payment_purposes, nickname)

    async def load_projects() -> list[str]:
        return await asyncio.to_thread(sheets.get_projects)

    return create_router(
        config,
        append_operation,
        load_groups,
        load_purposes,
        load_projects,
    )


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


def _options_keyboard(options: list[str], prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=option, callback_data=f"{prefix}:{index}")]
        for index, option in enumerate(options[:50])
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _projects_keyboard(projects: list[str], show_all: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=project, callback_data=f"project:{index}")]
        for index, project in enumerate(projects[:30])
    ]
    buttons.append([InlineKeyboardButton(text="Поиск проекта", callback_data="search_project")])
    if show_all:
        buttons.append([InlineKeyboardButton(text="Показать все", callback_data="show_all_projects")])
    buttons.append([InlineKeyboardButton(text="Ввести вручную", callback_data="manual_project")])
    buttons.append([InlineKeyboardButton(text="Пропустить", callback_data="skip_project")])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _skip_project_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_project")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )


def _skip_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_comment")],
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
        f"Группа: {data.get('group', '')}",
        f"Назначение платежа: {data.get('purpose', '')}",
        f"Сумма: {data['amount']}",
        f"Комментарий: {data.get('comment', '') or '—'}",
        f"Проект: {data.get('project', '') or '—'}",
    ]
    return "\n".join(lines)


def _parse_amount(value: str) -> float | None:
    normalized = value.replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _option_by_index(options: list[str], raw_index: str) -> str | None:
    try:
        return options[int(raw_index)]
    except (ValueError, IndexError):
        return None


_TOKEN_RE = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)
_FUZZY_THRESHOLD = 0.7


def _filter_values(values: list[str], query: str) -> list[str]:
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return []

    # 1) Прямое вхождение подстроки — самый предсказуемый случай.
    direct = [value for value in values if normalized_query in value.casefold()]
    if direct:
        return direct

    # 2) Совпадение по всем токенам запроса (подстрока или похожее слово).
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    matches: list[str] = []
    for value in values:
        candidate_text = value.casefold()
        candidate_tokens = _tokenize(value)
        if all(
            _token_matches(token, candidate_text, candidate_tokens)
            for token in query_tokens
        ):
            matches.append(value)
    return matches


def _tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_RE.split(text.casefold()) if token]


def _token_matches(token: str, candidate_text: str, candidate_tokens: list[str]) -> bool:
    if token in candidate_text:
        return True
    return any(
        SequenceMatcher(None, token, candidate_token).ratio() >= _FUZZY_THRESHOLD
        for candidate_token in candidate_tokens
    )


def _format_reference(groups: list[str], purposes: list[str]) -> str:
    lines = [
        "Быстрый ввод (после Пополнение или Расход):",
        "<группа> <назначение> <комментарий> <сумма> <проект>",
        "Пример: 1 2 продажа двери 100000 373",
        "",
        "Группы:",
    ]
    lines.extend(f"  {name}" for name in groups)
    lines.append("")
    lines.append("Назначение платежа:")
    lines.extend(f"  {name}" for name in purposes)
    return "\n".join(lines)


def _looks_like_quick_input(text: str) -> bool:
    parts = text.strip().split()
    if len(parts) < 5:
        return False
    if not parts[0].isdigit() or not parts[1].isdigit():
        return False
    amount = _parse_amount(parts[-2])
    return amount is not None and amount > 0 and bool(parts[-1].strip())


def _parse_quick_input(
    text: str,
    groups: list[str],
    purposes: list[str],
) -> dict[str, Any] | str:
    parts = text.strip().split()
    if len(parts) < 5:
        return "Мало параметров. Формат: <группа> <назначение> <комментарий> <сумма> <проект>"

    try:
        group_num = int(parts[0])
        purpose_num = int(parts[1])
    except ValueError:
        return "Первые два параметра должны быть номерами группы и назначения."

    amount = _parse_amount(parts[-2])
    if amount is None or amount <= 0:
        return "Сумма должна быть положительным числом."

    project_query = parts[-1]
    comment = " ".join(parts[2:-2])

    if not (1 <= group_num <= len(groups)):
        return f"Группа: укажите число от 1 до {len(groups)}."
    if not (1 <= purpose_num <= len(purposes)):
        return f"Назначение: укажите число от 1 до {len(purposes)}."

    return {
        "group": groups[group_num - 1],
        "purpose": purposes[purpose_num - 1],
        "comment": comment,
        "amount": amount,
        "project_query": project_query,
    }


def _resolve_project(projects: list[str], query: str) -> str | None:
    matches = _filter_values(projects, query)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    normalized_query = query.casefold()
    exact = [
        project
        for project in matches
        if project.casefold().startswith(f"{normalized_query}.")
        or project.split(".", 1)[0].casefold() == normalized_query
    ]
    if len(exact) == 1:
        return exact[0]
    return None


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
