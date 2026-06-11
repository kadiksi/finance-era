import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    google_sheet_id: str
    google_credentials_file: str | None
    google_credentials_json: str | None
    timezone: str
    allowed_user_ids: frozenset[int]
    reestr_sheet_name: str
    project_status_filter: str


def _parse_allowed_user_ids(value: str) -> frozenset[int]:
    if not value.strip():
        return frozenset()

    user_ids: set[int] = set()
    for raw_user_id in value.split(","):
        raw_user_id = raw_user_id.strip()
        if raw_user_id:
            user_ids.add(int(raw_user_id))
    return frozenset(user_ids)


def load_config() -> Config:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    google_sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    google_credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if google_credentials_file.startswith("{") and not google_credentials_json:
        google_credentials_json = google_credentials_file
        google_credentials_file = ""

    missing = [
        name
        for name, value in {
            "BOT_TOKEN": bot_token,
            "GOOGLE_SHEET_ID": google_sheet_id,
        }.items()
        if not value
    ]
    if not google_credentials_file and not google_credentials_json:
        missing.append("GOOGLE_CREDENTIALS_FILE or GOOGLE_CREDENTIALS_JSON")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    return Config(
        bot_token=bot_token,
        google_sheet_id=google_sheet_id,
        google_credentials_file=google_credentials_file or None,
        google_credentials_json=google_credentials_json or None,
        timezone=os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent",
        allowed_user_ids=_parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS", "")),
        reestr_sheet_name=os.getenv("REESTR_SHEET_NAME", "Реестр проектов").strip()
        or "Реестр проектов",
        project_status_filter=os.getenv("PROJECT_STATUS_FILTER", "в работе").strip()
        or "в работе",
    )
