# Дълготрайна памет

Jarvis помни факти за потребителя между сесиите. Фактите се пазят в локална
SQLite база (`~/.jarvis/memory.db`, или пътя от `JARVIS_MEMORY_PATH`) и нищо
не се праща никъде, освен подбраните факти в промпта към Claude.

## Как работи

- Преди всяка заявка `MemoryStore.prompt_section(съобщение)` връща текст за
  system prompt-а: фактите от категория `profile` (име, език, град...) винаги,
  плюс до 12 факта, които споделят думи със съобщението.
- Claude сам решава какво да запомни чрез три инструмента:
  `remember_fact`, `forget_fact` и `recall_facts` (`MEMORY_TOOLS`).

## Свързване с чат цикъла

```python
from jarvis.memory import MemoryStore, MEMORY_TOOLS, MEMORY_TOOL_NAMES

memory = MemoryStore()

system = BASE_PROMPT + memory.prompt_section(user_message)
response = client.messages.create(
    model=MODEL, system=system, tools=[*TOOLS, *MEMORY_TOOLS], messages=history,
)
for block in response.content:
    if block.type == "tool_use" and block.name in MEMORY_TOOL_NAMES:
        result = memory.handle_tool(block.name, block.input)
        # върни result като tool_result към Claude
```

## Ръчно управление

```sh
python -m jarvis.memory list
python -m jarvis.memory add "Обича зелен чай" --category preference
python -m jarvis.memory search чай
python -m jarvis.memory forget 3
python -m jarvis.memory export > backup.json
```

## Тестове

```sh
python -m pytest tests/test_memory.py
```
