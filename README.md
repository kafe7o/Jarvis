# Jarvis

Личен AI асистент в духа на Jarvis, изграден върху Claude API.

Първата версия е конзолен чат, който:

- помни историята на разговора в рамките на сесията;
- може да казва текущата дата и час (инструмент `get_current_time`);
- има заглушка за търсене в интернет (`web_search`), която ще бъде свързана с истинска търсачка по-късно.

## Изисквания

- Python 3.10 или по-нов
- API ключ за Claude от [console.anthropic.com](https://console.anthropic.com/)

## Инсталация

```bash
git clone https://github.com/kafe7o/Jarvis.git
cd Jarvis
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Стартиране

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m jarvis
```

Команди в чата:

- `/reset` изчиства историята на разговора;
- `/exit` или Ctrl+D излиза.

По подразбиране се използва модел `claude-opus-5-5`. Друг модел може да се зададе с променливата `JARVIS_MODEL`.

## Структура

```
jarvis/
  __main__.py   конзолният чат (python -m jarvis)
  assistant.py  разговорът с Claude, историята и цикълът с инструменти
  tools.py      описанията и изпълнението на инструментите
tests/          тестове (pytest)
```

## Добавяне на нов инструмент

1. Добавете описание в списъка `TOOLS` в `jarvis/tools.py`.
2. Напишете функция, която връща текст.
3. Регистрирайте я в `_HANDLERS`.

## Тестове

```bash
pip install pytest
python -m pytest
```
