from collections.abc import Sequence
import json
import re

import gspread
from gspread import Spreadsheet, Worksheet
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import rowcol_to_a1

from bot.config import Config
from bot.models import Operation, OperationType, format_amount


OPERATIONS_HEADERS = [
    "Дата",
    "Группа",
    "Назначение платежа",
    "Комментарий",
    "Поступление",
    "Выплата",
    "Название проекта",
    "ОСТАТКИ факт",
]

_MAX_DATA_ROWS = 1000
_REESTR_MAX_ROWS = 500
_REESTR_MAX_COLS = 25

# Справочник для выпадающего списка столбца "Группа" (начальные значения).
DEFAULT_GROUPS = [
    "Материалы",
    "Заказчики",
    "Сотрудники",
    "Счета",
    "Субподрядчики",
    "Контрагенты",
]

# Справочник для выпадающего списка столбца "Назначение платежа" (начальные значения).
DEFAULT_PAYMENT_PURPOSES = [
    "Материалы",
    "Оплата за проект",
    "Аренда",
    "Коммунальные услуги",
    "Зарплата прочего персонала",
    "Интернет, услуги связи",
    "Внутренние переводы",
    "Субподрядные работы",
    "Расходы цех",
    "Кредиты ПОЛУЧЕННЫЕ (выплата осн.долга)",
    "Реклама",
    "ГСМ",
    "Зарплата рабочих",
    "Расходы офис",
    "HR расходы",
    "Бонус за объекты",
    "Транспортные услуги",
    "Исполнение гарантийных обязательств",
    "Представительские расходы",
    "Возврат Инвестору",
    "Услуги Банка",
    "Возврат ДС Заказчику",
    "Вывоз мусора",
    "Транспортные расходы",
    "Проценты с займов, кредитов ПОЛУЧЕННЫХ (выплата)",
    "Обучение персонала",
    "Дивиденды",
    "Коммерческие расходы",
    "Субподрядные работы цех",
]

# 0-based индексы столбцов с выпадающими списками.
_GROUP_COLUMN = 1
_PURPOSE_COLUMN = 2
_PROJECT_COLUMN = 6

# Заголовки столбцов во вкладке "Реестр проектов".
_DEAL_HEADER = "уникальный номер сделки"
_STATUS_HEADER = "статус работ"


_PODOTCHET_PREFIX = "Подотчет "
_NUMBERED_OPTION_RE = re.compile(r"^\d+\.\s*")
# Строка 2 (1-based): validation задаётся с startRowIndex=1.
_VALIDATION_SAMPLE_ROW = 2


def _numbered_options(options: Sequence[str]) -> list[str]:
    return [f"{index}. {name}" for index, name in enumerate(options, start=1)]


def _strip_option_number(value: str) -> str:
    return _NUMBERED_OPTION_RE.sub("", value.strip())


def _resolve_effective_list(defaults: Sequence[str], document_values: Sequence[str]) -> list[str]:
    if not document_values:
        return list(defaults)
    document_raw = [_strip_option_number(value) for value in document_values]
    if document_raw == list(defaults):
        return list(defaults)

    merged = list(defaults)
    known = {item.casefold() for item in merged}
    for item in document_raw:
        key = item.casefold()
        if key not in known:
            merged.append(item)
            known.add(key)
    return merged


def _parse_validation_values(data_validation: dict) -> list[str]:
    condition = data_validation.get("condition", {})
    if condition.get("type") != "ONE_OF_LIST":
        return []
    return [
        value.get("userEnteredValue", "").strip()
        for value in condition.get("values", [])
        if value.get("userEnteredValue", "").strip()
    ]


def _read_cell_validation(
    spreadsheet: Spreadsheet,
    worksheet: Worksheet,
    row: int,
    column_index: int,
) -> list[str]:
    cell = rowcol_to_a1(row, column_index + 1)
    title = worksheet.title.replace("'", "''")
    range_a1 = f"'{title}'!{cell}"
    metadata = spreadsheet.fetch_sheet_metadata(
        params={
            "includeGridData": "true",
            "ranges": [range_a1],
            "fields": "sheets.data.rowData.values.dataValidation",
        }
    )
    try:
        cell_data = metadata["sheets"][0]["data"][0]["rowData"][0]["values"][0]
    except (KeyError, IndexError):
        return []
    return _parse_validation_values(cell_data.get("dataValidation", {}))


def _read_column_validation(
    spreadsheet: Spreadsheet,
    worksheet: Worksheet,
    column_index: int,
) -> list[str]:
    for row in (_VALIDATION_SAMPLE_ROW, 1):
        values = _read_cell_validation(spreadsheet, worksheet, row, column_index)
        if values:
            return values
    return []


def _is_podotchet_worksheet(worksheet: Worksheet) -> bool:
    if not worksheet.title.startswith(_PODOTCHET_PREFIX):
        return False
    try:
        return worksheet.row_values(1) == OPERATIONS_HEADERS
    except APIError:
        return False


class GoogleSheetsClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = _create_gspread_client(config)
        self._spreadsheet: Spreadsheet = self._client.open_by_key(config.google_sheet_id)
        self._worksheets: dict[str, Worksheet] = {}
        self._projects_cache: list[str] | None = None

    def append_operation(self, operation: Operation, user_nickname: str) -> str:
        title = _user_sheet_title(user_nickname)
        for attempt in range(2):
            try:
                worksheet = self._ensure_user_sheet(user_nickname)
                balance = self._get_last_fact_balance(worksheet) + _operation_delta(operation)
                row = operation.as_sheet_row()
                row.append(format_amount(balance))
                worksheet.append_row(
                    row,
                    value_input_option="USER_ENTERED",
                )
                return format_amount(balance)
            except APIError:
                if attempt == 0:
                    self._drop_user_sheet(title)
                    continue
                raise

    def get_groups(self, user_nickname: str | None = None) -> list[str]:
        return _numbered_options(self._effective_groups(user_nickname))

    def get_payment_purposes(self, user_nickname: str | None = None) -> list[str]:
        return _numbered_options(self._effective_purposes(user_nickname))

    def get_projects(self) -> list[str]:
        """Уникальные номера сделок из 'Реестр проектов' со статусом 'в работе'."""
        worksheet = self._get_reestr_worksheet()
        if worksheet is None:
            return []

        values = _read_sheet_range(
            worksheet,
            rows=_REESTR_MAX_ROWS,
            cols=_REESTR_MAX_COLS,
        )
        return _extract_deals(values, self._config.project_status_filter)

    def _get_reestr_worksheet(self) -> Worksheet | None:
        try:
            return self._spreadsheet.worksheet(self._config.reestr_sheet_name)
        except WorksheetNotFound:
            return None

    def _projects_for_validation(self) -> list[str]:
        if self._projects_cache is None:
            self._projects_cache = self.get_projects()
        return self._projects_cache

    def _drop_user_sheet(self, title: str) -> None:
        self._worksheets.pop(title, None)
        self._spreadsheet = self._client.open_by_key(self._config.google_sheet_id)

    def _find_podotchet_worksheet(self, user_nickname: str | None = None) -> Worksheet | None:
        if user_nickname:
            title = _user_sheet_title(user_nickname)
            try:
                worksheet = self._spreadsheet.worksheet(title)
            except WorksheetNotFound:
                pass
            else:
                if _is_podotchet_worksheet(worksheet):
                    return worksheet

        for worksheet in self._spreadsheet.worksheets():
            if _is_podotchet_worksheet(worksheet):
                return worksheet
        return None

    def _effective_groups(self, user_nickname: str | None = None) -> list[str]:
        document_values = self._read_reference_validation(_GROUP_COLUMN, user_nickname)
        return _resolve_effective_list(DEFAULT_GROUPS, document_values)

    def _effective_purposes(self, user_nickname: str | None = None) -> list[str]:
        document_values = self._read_reference_validation(_PURPOSE_COLUMN, user_nickname)
        return _resolve_effective_list(DEFAULT_PAYMENT_PURPOSES, document_values)

    def _read_reference_validation(
        self,
        column_index: int,
        user_nickname: str | None = None,
    ) -> list[str]:
        worksheet = self._find_podotchet_worksheet(user_nickname)
        if worksheet is None:
            return []
        return _read_column_validation(self._spreadsheet, worksheet, column_index)

    def _ensure_user_sheet(self, user_nickname: str) -> Worksheet:
        title = _user_sheet_title(user_nickname)
        cached = self._worksheets.get(title)
        if cached is not None:
            if _worksheet_is_accessible(cached):
                return cached
            self._worksheets.pop(title, None)

        created = False
        try:
            worksheet = self._spreadsheet.worksheet(title)
        except WorksheetNotFound:
            worksheet = self._spreadsheet.add_worksheet(
                title=title,
                rows=1000,
                cols=len(OPERATIONS_HEADERS),
            )
            created = True

        first_row = worksheet.row_values(1)
        headers_changed = first_row != OPERATIONS_HEADERS
        if headers_changed:
            worksheet.update(values=[OPERATIONS_HEADERS], range_name="A1")
            if len(first_row) > len(OPERATIONS_HEADERS):
                start_cell = rowcol_to_a1(1, len(OPERATIONS_HEADERS) + 1)
                end_cell = rowcol_to_a1(1, len(first_row))
                worksheet.batch_clear([f"{start_cell}:{end_cell}"])

        if created or headers_changed:
            self._apply_dropdowns(worksheet, user_nickname)

        self._worksheets[title] = worksheet
        return worksheet

    def _apply_dropdowns(self, worksheet: Worksheet, user_nickname: str) -> None:
        requests = [
            _one_of_list_request(
                worksheet.id,
                _GROUP_COLUMN,
                _numbered_options(self._effective_groups(user_nickname)),
            ),
            _one_of_list_request(
                worksheet.id,
                _PURPOSE_COLUMN,
                _numbered_options(self._effective_purposes(user_nickname)),
            ),
        ]
        projects = self._projects_for_validation()
        if projects:
            requests.append(
                _one_of_list_request(worksheet.id, _PROJECT_COLUMN, projects)
            )
        self._spreadsheet.batch_update({"requests": requests})

    def _get_last_fact_balance(self, worksheet: Worksheet) -> float:
        rows = _read_sheet_range(worksheet, cols=len(OPERATIONS_HEADERS))
        if len(rows) <= 1:
            return 0

        column_index = len(OPERATIONS_HEADERS) - 1
        for row in reversed(rows[1:]):
            if len(row) > column_index and row[column_index].strip():
                return _parse_sheet_amount(row[column_index])
        return 0


def _worksheet_is_accessible(worksheet: Worksheet) -> bool:
    try:
        worksheet.get_values("A1")
        return True
    except APIError:
        return False


def _read_sheet_range(
    worksheet: Worksheet,
    rows: int = _MAX_DATA_ROWS,
    cols: int = len(OPERATIONS_HEADERS),
) -> list[list[str]]:
    end_cell = rowcol_to_a1(rows, cols)
    values = worksheet.get_values(f"A1:{end_cell}")
    return values or []


def _user_sheet_title(user_nickname: str) -> str:
    normalized = user_nickname.strip() or "unknown"
    safe_nickname = "".join("_" if char in "[]:*?/\\" else char for char in normalized)
    title = f"Подотчет {safe_nickname}"
    return title[:100]


def _one_of_list_request(
    sheet_id: int,
    column_index: int,
    values: Sequence[str],
    start_row: int = 1,
    end_row: int = 1000,
) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": value} for value in values],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }


def _extract_deals(values: Sequence[Sequence[str]], status_filter: str) -> list[str]:
    header_index = _find_header_row(values)
    if header_index is None:
        return []

    header = [cell.strip().casefold() for cell in values[header_index]]
    deal_col = header.index(_DEAL_HEADER)
    status_col = header.index(_STATUS_HEADER)
    target_status = status_filter.strip().casefold()

    seen: set[str] = set()
    result: list[str] = []
    for row in values[header_index + 1:]:
        if len(row) <= max(deal_col, status_col):
            continue
        if row[status_col].strip().casefold() != target_status:
            continue
        deal = row[deal_col].strip()
        key = deal.casefold()
        if deal and key not in seen:
            seen.add(key)
            result.append(deal)

    return result


def _find_header_row(values: Sequence[Sequence[str]]) -> int | None:
    for index, row in enumerate(values[:20]):
        normalized = {cell.strip().casefold() for cell in row}
        if _DEAL_HEADER in normalized and _STATUS_HEADER in normalized:
            return index
    return None


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
