from collections.abc import Sequence
import json

import gspread
from gspread import Spreadsheet, Worksheet
from gspread.exceptions import WorksheetNotFound
from gspread.utils import rowcol_to_a1

from bot.config import Config
from bot.models import Operation, OperationType, format_amount


OPERATIONS_HEADERS = [
    "Дата",
    "Назначение платежа",
    "Наименование",
    "Поступление",
    "Выплата",
    "Название проекта",
]


class GoogleSheetsClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = _create_gspread_client(config)
        self._spreadsheet: Spreadsheet = self._client.open_by_key(config.google_sheet_id)
        self._worksheets: dict[str, Worksheet] = {}

    def append_operation(self, operation: Operation, user_nickname: str) -> str:
        worksheet = self._ensure_user_sheet(user_nickname)
        balance = self._get_balance(worksheet) + _operation_delta(operation)
        worksheet.append_row(
            operation.as_sheet_row(),
            value_input_option="USER_ENTERED",
        )
        return format_amount(balance)

    def get_projects(self) -> list[str]:
        return self._get_first_sheet_column(2, {"project", "projects", "проект"})

    def get_categories(self) -> list[str]:
        return self._get_first_sheet_column(
            3,
            {"category", "categories", "категория", "категории"}
        )

    def _get_first_sheet_column(self, column: int, headers: set[str]) -> list[str]:
        worksheets = self._spreadsheet.worksheets()
        if not worksheets:
            return []

        values = worksheets[0].col_values(column)
        if values and values[0].strip().lower() in headers:
            values = values[1:]

        return _unique_non_empty(values)

    def _ensure_user_sheet(self, user_nickname: str) -> Worksheet:
        title = _user_sheet_title(user_nickname)
        if title in self._worksheets:
            return self._worksheets[title]

        try:
            worksheet = self._spreadsheet.worksheet(title)
        except WorksheetNotFound:
            worksheet = self._spreadsheet.add_worksheet(
                title=title,
                rows=1000,
                cols=len(OPERATIONS_HEADERS),
            )

        first_row = worksheet.row_values(1)
        if first_row != OPERATIONS_HEADERS:
            worksheet.update(values=[OPERATIONS_HEADERS], range_name="A1")
            if len(first_row) > len(OPERATIONS_HEADERS):
                start_cell = rowcol_to_a1(1, len(OPERATIONS_HEADERS) + 1)
                end_cell = rowcol_to_a1(1, len(first_row))
                worksheet.batch_clear([f"{start_cell}:{end_cell}"])

        self._worksheets[title] = worksheet
        return worksheet

    def _get_balance(self, worksheet: Worksheet) -> float:
        income_values = worksheet.col_values(4)[1:]
        payout_values = worksheet.col_values(5)[1:]
        income = sum(_parse_sheet_amount(value) for value in income_values)
        payout = sum(_parse_sheet_amount(value) for value in payout_values)
        return income + payout


def _user_sheet_title(user_nickname: str) -> str:
    normalized = user_nickname.strip() or "unknown"
    safe_nickname = "".join("_" if char in "[]:*?/\\" else char for char in normalized)
    title = f"Подотчет {safe_nickname}"
    return title[:100]


def _create_gspread_client(config: Config) -> gspread.Client:
    if config.google_credentials_json:
        try:
            credentials = json.loads(config.google_credentials_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("GOOGLE_CREDENTIALS_JSON contains invalid JSON.") from error
        return gspread.service_account_from_dict(credentials)

    if config.google_credentials_file:
        return gspread.service_account(filename=config.google_credentials_file)

    raise RuntimeError("Google credentials are not configured.")


def _operation_delta(operation: Operation) -> float:
    if operation.operation_type == OperationType.MANAGER_TRANSFER:
        return operation.amount
    return -operation.amount


def _parse_sheet_amount(value: str) -> float:
    normalized = (
        value.replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    if not normalized:
        return 0
    try:
        return float(normalized)
    except ValueError:
        return 0


def _unique_non_empty(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)

    return result
