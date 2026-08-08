# Создание лидов Bitrix24 через входящий webhook

## Статус проверки

По подтверждению автора, 8 августа 2026 года в приватном рабочем окружении проекта [`1cProductMap`](https://github.com/Aleksandr-Litvinenko/1cProductMap) был пройден живой сценарий:

```text
форма сайта
  → POST /api/lead
  → серверный Python-сервис
  → входящий webhook Bitrix24
  → crm.lead.add
  → crm.lead.get для контрольного чтения
```

Создание вернуло ID, а повторное чтение подтвердило `TITLE`, `NAME`, `PHONE`, `EMAIL`, `IM`, `COMMENTS`, `SOURCE_ID` и `SOURCE_DESCRIPTION`. [Commit `7839874`](https://github.com/Aleksandr-Litvinenko/1cProductMap/commit/7839874dace10a4fbc0e10e275d6314a48763db7) публично подтверждает архитектуру и инструкцию, но не содержит server source или воспроизводимый transcript live-теста.

Граница доказательства: live-сценарий прошёл на legacy-методах. Актуальный пример ниже использует рекомендуемый `crm.item.add`; его нужно отдельно проверить синтетическим лидом на вашем портале.

## Критическое обновление API

Методы [`crm.lead.add`](https://apidocs.bitrix24.com/api-reference/crm/leads/crm-lead-add.html), `crm.lead.get` и `crm.lead.list` всё ещё работают и использовались в реальном кейсе, но теперь официально помечены Bitrix24 как deprecated.

Для новой интеграции используйте универсальные методы:

- [`crm.item.add`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html) с `entityTypeId: 1`;
- [`crm.item.get`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-get.html) для проверки;
- [`crm.item.list`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html) для поиска по внешнему ID.

## Webhook существует только на backend

Правильная схема:

```text
браузер → ваш HTTPS endpoint → серверная валидация → Bitrix24 webhook
```

Неправильная схема:

```text
браузер → Bitrix24 webhook
```

Во втором варианте ключ оказывается в JavaScript, DevTools и сетевых логах каждого посетителя.

В **Developer resources / Ресурсы разработчика → Common use cases → custom webhook** создайте входящий webhook со scope `crm` от отдельного пользователя, который может добавлять и читать лиды, но не имеет ненужных прав изменения и удаления. URL храните в secret manager или файле с правами `600`, а не в репозитории.

Если URL webhook был вставлен в AI-чат, issue, setup-скрипт или лог, выпустите новый ключ. Сам URL является секретом с правами пользователя.

## Как выглядел реально сработавший legacy-запрос

```http
POST https://<portal>.bitrix24.ru/rest/<user>/<secret>/crm.lead.add
Content-Type: application/json
```

```json
{
  "fields": {
    "TITLE": "[TEST] Заявка AI × 1C",
    "NAME": "Тестовый пользователь",
    "PHONE": [{"VALUE": "+70000000000", "VALUE_TYPE": "WORK"}],
    "EMAIL": [{"VALUE": "qa@example.invalid", "VALUE_TYPE": "WORK"}],
    "COMMENTS": "Синтетическая проверка интеграции",
    "SOURCE_ID": "WEB",
    "SOURCE_DESCRIPTION": "AI × 1C Guide",
    "OPENED": "N",
    "ORIGINATOR_ID": "ai-1c-guide",
    "ORIGIN_ID": "<unique-request-id>"
  },
  "params": {"REGISTER_SONET_EVENT": "N"}
}
```

Успешный `crm.lead.add` возвращает числовой ID. Затем `crm.lead.get` с этим ID подтверждает, что поля действительно записались. Не используйте этот legacy-код как основу нового сервиса без плана миграции.

## Сначала preview без сети

В [`bitrix24_webhook_example.py`](../scripts/bitrix24_webhook_example.py) команда preview строит payload для `crm.item.add`, маскирует поля формы и **не читает webhook, не открывает сеть и ничего не создаёт**:

```bash
python3 scripts/bitrix24_webhook_example.py lead-preview \
  --request-id 'product-map-test-001'
```

У Bitrix24 нет server-side dry-run для создания лида. Поэтому preview означает только локальную проверку структуры.

`--show-sensitive` раскрывает синтетические поля preview. Не используйте его с реальными контактами в агентской сессии: stdout станет частью контекста модели.

`--request-id` должен быть непрозрачным техническим ключом из латиницы, цифр и символов `._:-` длиной до 64 знаков. Не помещайте в него телефон, email или внутренний ID клиента; в обычном preview он тоже маскируется.

## Создать синтетический лид современным методом

Сначала настройте webhook локально, не вставляя его в prompt:

```bash
read -r BITRIX24_EXPECTED_HOST
read -rs BITRIX_WEBHOOK
export BITRIX24_EXPECTED_HOST BITRIX_WEBHOOK
```

`BITRIX24_EXPECTED_HOST` содержит только имя заранее выбранного тестового портала. Затем получите отпечаток текущего webhook без сетевого вызова:

```bash
python3 scripts/bitrix24_webhook_example.py write-fingerprint
read -r BITRIX24_ALLOW_WRITE
export BITRIX24_ALLOW_WRITE
```

Во вторую команду вставьте строку `TEST_PORTAL_ONLY:<fingerprint>`, которую напечатал скрипт. Она привязывает подтверждение к точному webhook, но не заменяет ручную проверку, что портал действительно тестовый.

После ручной проверки preview:

```bash
python3 scripts/bitrix24_webhook_example.py lead-create \
  --request-id 'product-map-test-001' \
  --confirm-test-write
```

Защита в примере:

1. хост webhook обязан совпасть с отдельно введённым `BITRIX24_EXPECTED_HOST`;
2. заголовок обязан начинаться с `[TEST]`;
3. нужны подтверждение с отпечатком exact webhook и отдельный флаг;
4. перед созданием выполняется `crm.item.list` по `originatorId + originId`;
5. новый лид создаётся через `crm.item.add`, где Lead имеет `entityTypeId = 1`;
6. результат перечитывается через `crm.item.get`, а `originatorId` и `originId` сверяются с запросом;
7. write-вызов не повторяется вслепую после timeout: сначала выполняется поиск по `originId`;
8. секрет и контактные данные не печатаются.

Не запускайте этот тест на рабочем портале без согласования с владельцем CRM. Предпочтителен отдельный тестовый портал; иначе используйте только синтетические значения и заранее договоритесь, кто вручную закроет или удалит точный тестовый лид.

## Идемпотентность и дубли

`ORIGINATOR_ID`/`ORIGIN_ID` в legacy API и `originatorId`/`originId` в universal API помогают найти повтор, но сами по себе не гарантируют уникальность при двух параллельных запросах.

Надёжный backend должен:

1. принять собственный `request_id`;
2. сохранить его в локальной БД с `UNIQUE`-ограничением;
3. передать тот же ID в Bitrix24;
4. после timeout сначала искать существующий лид, а не сразу повторять `add`;
5. вернуть ранее сохранённый результат при повторной отправке.

Повторное обращение человека — другая задача. Для телефона и email используйте [`crm.duplicate.findbycomm`](https://apidocs.bitrix24.com/api-reference/crm/duplicates/crm-duplicate-find-by-comm.html), но не объединяйте и не удаляйте найденные карточки автоматически.

## Backend-проверки до Bitrix24

- обязательные имя и телефон;
- ограничение размера каждого поля и всего JSON;
- нормализация телефона и email;
- allowlist полей вместо передачи произвольного JSON в CRM;
- honeypot или CAPTCHA для публичной формы;
- rate limit по IP и request ID;
- журналирование собственного ID заявки без webhook и полного payload;
- нейтральная ошибка клиенту без `error_description` Bitrix24;
- очередь вместо опасного fallback, который может создать второй лид после timeout.

## Ошибки и повторы

| Ошибка | Действие |
|---|---|
| `NO_AUTH_FOUND` | webhook неверный или отозван; не повторять |
| `INVALID_CREDENTIALS`, `insufficient_scope`, access denied | исправить scope или права пользователя |
| `QUERY_LIMIT_EXCEEDED` (обычно HTTP `503`) | ограниченный backoff с jitter |
| `OVERLOAD_LIMIT` | остановиться: метод заблокирован вручную, retry не поможет |
| `OPERATION_TIME_LIMIT`, HTTP `429` | дождаться reset |
| timeout после `add` | сначала искать по внешнему ID; лид мог быть создан |
| другая `5xx` | повторять только после проверки идемпотентности |

Не возвращайте посетителю внутренний ID лида. Отдавайте собственный непрозрачный ID заявки и статус приёма.

## Промпт для Codex или Claude

```text
В локальном окружении настроены BITRIX24_EXPECTED_HOST и BITRIX_WEBHOOK.
Не показывай их значения, не печатай окружение и не используй curl -v.

1. Сначала выполни только lead-preview с синтетическими значениями.
2. Не добавляй --show-sensitive и не отправляй запрос в сеть.
3. Покажи структуру полей и объясни, что будет создано.
4. Не запускай lead-create, пока я отдельно не подтвержу тестовый портал,
   стабильный request ID и точный preview.
5. Никогда не помещай webhook в браузерный JavaScript или Git.
```

## Первоисточники

- [Создание входящего webhook](https://helpdesk.bitrix24.com/open/21133100/)
- [`crm.item.add`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html)
- [`crm.item.get`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-get.html)
- [`crm.item.list`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html)
- [`crm.lead.add` — legacy/deprecated](https://apidocs.bitrix24.com/api-reference/crm/leads/crm-lead-add.html)
- [ProductMap: как заявки попадают в Bitrix24](https://github.com/Aleksandr-Litvinenko/1cProductMap/blob/main/docs/bitrix-webhook.md)
