# Telegram-бот для учета финансов проекта

Бот записывает в Google Sheets два типа операций:

- `Пополнение` — передача денег менеджеру на закупки.
- `Расход` — фактический расход менеджера на закупки.

## Как устроена таблица

Бот пишет только в листы, которые создаёт сам — `Подотчет user_nickname` для каждого пользователя. Существующие вкладки таблицы он не изменяет.

Колонки листов подотчета:

```text
Дата, Группа, Назначение платежа, Комментарий, Поступление, Выплата, Название проекта, ОСТАТКИ факт
```

- `Дата` — формат `dd.MM.YY`, например `14.05.26`.
- `Группа` — выпадающий список (Расходы, Заказчики, Сотрудники, Счета, Субподрядчики, Контрагенты).
- `Назначение платежа` — выпадающий список заранее заданных назначений.
- `Комментарий` — свободный текст (можно пропустить).
- `Поступление` — заполняется при операции `Пополнение`.
- `Выплата` — заполняется при операции `Расход`, со знаком минус, например `-1 300 000`.
- `Название проекта` — выпадающий список. Значения берутся из вкладки `Реестр проектов`, столбец `Уникальный номер Сделки`, только для строк со статусом работ `в работе`.
- `ОСТАТКИ факт` — нарастающий остаток: предыдущее значение + поступление − расход.

Названия справочной вкладки и фильтра статуса настраиваются через `REESTR_SHEET_NAME` (по умолчанию `Реестр проектов`) и `PROJECT_STATUS_FILTER` (по умолчанию `в работе`).

## Настройка Google Sheets

1. Создайте проект в Google Cloud.
2. Включите Google Sheets API.
3. Создайте Service Account и скачайте JSON-ключ.
4. Положите ключ в корень проекта, например `service-account.json`.
5. Откройте Google Sheets и выдайте доступ на редактирование email-адресу сервисного аккаунта.

## Настройка Telegram

1. Создайте бота через [BotFather](https://t.me/BotFather).
2. Скопируйте token.
3. Узнайте свой Telegram user ID, если хотите ограничить доступ к боту.

## Запуск через Docker

Создайте `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Положите JSON-ключ Google Service Account в корень проекта с именем `service-account.json`.

Заполните `.env`:

```env
BOT_TOKEN=your_telegram_bot_token
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_CREDENTIALS_FILE=./service-account.json
TIMEZONE=Asia/Tashkent
ALLOWED_USER_IDS=
```

Для Railway вместо файла используйте переменную `GOOGLE_CREDENTIALS_JSON`:

```env
BOT_TOKEN=your_telegram_bot_token
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}
TIMEZONE=Asia/Tashkent
ALLOWED_USER_IDS=
```

Запустите бота одной командой:

```bash
docker compose up -d --build
```

Посмотреть логи:

```bash
docker compose logs -f
```

Остановить бота:

```bash
docker compose down
```

## Локальный запуск без Docker

Создайте виртуальное окружение и установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Создайте `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Заполните значения:

```env
BOT_TOKEN=your_telegram_bot_token
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_CREDENTIALS_FILE=./service-account.json
TIMEZONE=Asia/Tashkent
ALLOWED_USER_IDS=
```

Запустите бота:

```bash
python -m bot.main
```

## Работа с ботом

1. Напишите боту `/start`.
2. Выберите одну из кнопок: `Пополнение`, `Расход`.
3. Выберите группу из списка.
4. Выберите назначение платежа из списка.
5. Укажите сумму.
6. Добавьте комментарий или нажмите `Пропустить`.
7. Выберите проект (есть поиск и ручной ввод).
8. Подтвердите операцию, после чего она появится во вкладке `Подотчет user_nickname`, а бот покажет текущий остаток.
