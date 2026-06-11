from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo


class OperationType(StrEnum):
    MANAGER_TRANSFER = "manager_transfer"
    MANAGER_EXPENSE = "manager_expense"


OPERATION_LABELS: dict[OperationType, str] = {
    OperationType.MANAGER_TRANSFER: "Пополнение",
    OperationType.MANAGER_EXPENSE: "Расход",
}


@dataclass(slots=True)
class Operation:
    operation_type: OperationType
    group: str
    purpose: str
    comment: str
    amount: float
    project: str
    created_at: datetime

    @classmethod
    def from_state(
        cls,
        data: dict[str, object],
        timezone: str,
    ) -> "Operation":
        return cls(
            operation_type=OperationType(str(data["operation_type"])),
            group=str(data.get("group", "")),
            purpose=str(data.get("purpose", "")),
            comment=str(data.get("comment", "")),
            amount=float(data["amount"]),
            project=str(data.get("project", "")),
            created_at=datetime.now(ZoneInfo(timezone)),
        )

    def as_sheet_row(self) -> list[str]:
        income = ""
        payout = ""
        if self.operation_type == OperationType.MANAGER_TRANSFER:
            income = format_amount(self.amount)
        else:
            payout = f"-{format_amount(self.amount)}"

        # Дата, Группа, Назначение платежа, Комментарий, Поступление, Выплата, Название проекта.
        # Столбец "ОСТАТКИ факт" (8-й) заполняется в sheets.append_operation.
        return [
            self.created_at.strftime("%d.%m.%y"),
            self.group,
            self.purpose,
            self.comment,
            income,
            payout,
            self.project,
        ]


def format_amount(amount: float) -> str:
    if amount.is_integer():
        formatted = f"{int(amount):,}"
    else:
        formatted = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return formatted.replace(",", "\u00a0")
