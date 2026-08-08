# AI-ассистент для Bitrix24

## Статус проверки

8 августа 2026 hosted endpoint официального docs MCP ответил на `initialize`, `tools/list`, `bitrix-search` и `bitrix-method-details`; server сообщил версию `0.2.0` и 5 tools. Runtime-доступ к пользовательскому порталу в рамках этого теста не выдавался.

Разделяйте знание API и доступ к порталу.

Практические runtime-инструкции из проектов автора:

- [чтение списка задач и одной задачи](bitrix24-tasks.md);
- [создание лидов через серверный webhook](bitrix24-leads.md).

## Слой 1: документация

[Официальный Bitrix24 MCP REST documentation](https://github.com/bitrix24/mcp-rest-doc) — hosted Streamable HTTP сервис по адресу:

```text
https://mcp-dev.bitrix24.com/mcp
```

Минимальный MCP config для клиента, поддерживающего прямой HTTP transport:

```json
{
  "mcpServers": {
    "bitrix-mcp-rest": {
      "url": "https://mcp-dev.bitrix24.com/mcp"
    }
  }
}
```

Границы доверия:

- сервису нужен интернет;
- он не должен получать webhook вашего портала;
- репозиторий содержит документацию, но не source hosted сервера и не LICENSE;
- live endpoint может измениться раньше README: при аудите он отдавал 5 tools, а README описывал 4;
- полученный метод и параметры всё равно проверяются перед runtime-вызовом.

## Слой 2: runtime

Runtime-коннектор выполняет разрешённые действия в конкретном Bitrix24:

- читает задачи;
- проверяет заполнение результатов;
- собирает часы;
- читает выбранные CRM-сущности;
- создаёт или меняет данные только при явно согласованном сценарии.

Варианты для изучения:

- [OpenIntegrations](https://github.com/Bayselonarrend/OpenIntegrations) — широкий `execute_method`, не read-only по умолчанию;
- [templates-mcp](https://github.com/bitrix24/templates-mcp) — tasks-focused reference implementation, pre-1.0, включает операции создания/изменения/удаления;
- [bitrix24-mcp](https://github.com/kartochka/bitrix24-mcp) — community-проект для контактов, сделок и смены стадии, последний push — май 2025.

Перед выбором откройте `access` и `verification` проекта в [каталоге](../catalog/tools.json).

## Первый безопасный сценарий

Read-only проверка закрытых задач за месяц:

1. Прочитать задачи со статусом «завершена».
2. Проверить списанное время.
3. Проверить наличие результата и даты выполнения.
4. Проверить привязку к разрешённой CRM-компании или сделке.
5. Сформировать отчёт без изменения портала.
6. Сравнить числа с контрольным отчётом Bitrix24.

## Минимальные правила runtime

- отдельный webhook или OAuth-приложение;
- только необходимые scopes;
- отдельный runtime для чтения и записи;
- секреты в secret manager или переменных окружения;
- пагинация, rate limits и retry с ограничением;
- журналирование методов без секретов и персональных payload;
- запись отключена по умолчанию;
- preview и human approval перед изменением;
- idempotency/duplicate protection для повторных запросов;
- тест на sandbox/тестовом портале до production.

## Не публикуйте

- URL входящего webhook;
- OAuth client secret;
- выгрузки с именами сотрудников и клиентов;
- полные production payload;
- cookie и session tokens;
- MCP config с реальными credentials.
