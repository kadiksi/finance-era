# Telegram-бот для учета финансов проекта

Бот записывает в Google Sheets два типа операций:

- `Пополнение` — передача денег менеджеру на закупки.
- `Расход` — фактический расход менеджера на закупки.

## Как устроена таблица

Создайте Google Sheets:

1. В первой вкладке укажите список проектов во втором столбце, а список категорий в третьем столбце. Первая строка может быть заголовком `project`, `проект`, `category` или `категория`.
2. Листы подотчета бот создаст автоматически для каждого пользователя в формате `Подотчет user_nickname`.

Колонки листов подотчета:

```text
Дата, Назначение платежа, Наименование, Поступление, Выплата, Название проекта
```

`Дата` записывается в формате `dd.MM.YY`, например `14.05.26`.
`Назначение платежа` — это `Пополнение` или `Расход`.
`Наименование` — это категория. Для `Пополнение` поле остается пустым.
Для `Пополнение` сумма записывается в `Поступление`, для `Расход` — в `Выплата` со знаком минус, например `-1 300 000`.

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
3. Для `Пополнение` выберите проект, укажите сумму и добавьте комментарий или нажмите `Пропустить`.
4. Для `Расход` выберите проект, укажите сумму, выберите категорию из списка и добавьте описание.
5. Подтвердите операцию, после чего она появится во вкладке `Подотчет user_nickname`, а бот покажет текущий остаток.
