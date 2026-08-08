# Чтение задач Bitrix24 через входящий webhook

**Русский** · [English](bitrix24-tasks.en.md) · [← К гайду](../README.md)

## Статус проверки

Это реальный паттерн из проекта [`task2bitrix24`](https://github.com/Aleksandr-Litvinenko/task2bitrix24): входящий webhook, классический REST API, `POST application/x-www-form-urlencoded`, пагинация `next → start`, ограниченные повторы при лимитах и `batch` до 50 команд.

Рабочая интеграция читает задачи, результаты, списанное время, пользователей, связанные CRM-компании и проекты. В публичном гайде нет webhook и данных портала; пример проверен unit-тестами без сетевого вызова.

## Классический REST и REST 3.0 — не одно и то же

Исходный проект использует классический endpoint:

```text
https://<portal>.bitrix24.ru/rest/<user-id>/<webhook-code>/<method>
```

Не добавляйте `/api/`: это уже REST 3.0 с другими именами полей, телом запроса и пагинацией. В одной инструкции нельзя смешивать форматы двух версий.

Официальные методы этого сценария:

- [`tasks.task.list`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-list.html) — список задач;
- [`tasks.task.get`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-get.html) — одна задача по ID.

## Создать webhook с правом `task`

В актуальном интерфейсе Bitrix24 откройте **Developer resources / Ресурсы разработчика → Common use cases → custom webhook** и создайте входящий webhook только с правом на задачи. Доступ к данным всё равно ограничивается правами пользователя, создавшего webhook.

Официальная инструкция: [Create webhooks and apps in Bitrix24](https://helpdesk.bitrix24.com/open/21133100/).

Не используйте административного пользователя только ради того, чтобы увидеть больше задач. Если затем понадобятся компании, пользователи или рабочие группы, добавляйте соответствующие права отдельно после review.

## Не вставлять webhook в prompt

URL webhook — это пароль. Сохраните его в secret manager или локальной переменной окружения:

```bash
read -r BITRIX24_EXPECTED_HOST
read -rs BITRIX_WEBHOOK
export BITRIX24_EXPECTED_HOST BITRIX_WEBHOOK
```

Вводимое значение должно выглядеть так:

```text
https://<portal>.bitrix24.ru/rest/<user-id>/<webhook-code>/
```

`BITRIX24_EXPECTED_HOST` — только имя портала без `https://` и пути, например `<portal>.bitrix24.ru`. Оно вводится отдельно, чтобы скрипт остановился при случайной подмене endpoint. Если webhook был вставлен в AI-чат, issue, README или лог, перевыпустите его. Не используйте `curl -v`, не печатайте окружение и не передавайте URL в клиентский JavaScript.

## Прочитать доступные активные задачи

Скрипт [`bitrix24_webhook_example.py`](../scripts/bitrix24_webhook_example.py) использует тот же транспорт, что и `task2bitrix24`, но сохраняет проверку TLS включённой:

```bash
python3 scripts/bitrix24_webhook_example.py tasks-list
```

По умолчанию выбираются статусы:

- `2` — ждёт выполнения;
- `3` — выполняется;
- `6` — отложена.

Ограничить список задачами конкретного ответственного:

```bash
python3 scripts/bitrix24_webhook_example.py tasks-list \
  --responsible-id 123 \
  --statuses '2,3,6'
```

Запрашиваются только `ID`, `TITLE`, `STATUS`, `RESPONSIBLE_ID`, `DEADLINE` и `GROUP_ID`. По умолчанию заголовок, ответственный, срок и проект скрыты, чтобы вывод агента не отправил бизнес-данные в контекст модели. Для осознанного локального просмотра добавьте `--show-sensitive`.

## Прочитать одну задачу

```bash
python3 scripts/bitrix24_webhook_example.py task-get 12345
```

Метод `tasks.task.get` получает только выбранные поля, включая `UF_CRM_TASK`, если нужна связь с CRM. Ответ лежит в `result.task`.

Bitrix24 может добавить в ответ вложенные объекты пользователя и проекта даже при узком `select`. Поэтому без `--show-sensitive` пример печатает только `id`, `status` и признак редактирования; полный ответ раскрывается лишь по явному флагу в доверенном терминале.

`tasks.task.get` — безопасное расширение исходного сценария: основная рабочая реализация получала карточки через `tasks.task.list`, а этот отдельный вызов добавлен в гайд по официальной документации.

## Как устроена пагинация

Классический `tasks.task.list` возвращает не более 50 задач на страницу. Если в корне ответа есть `next`, его значение нужно передать следующим запросом как `start`:

```text
start = 0 → next = 50
start = 50 → next = 100
...
ответ без next → конец
```

Скрипт проверяет, что `next` растёт, и ограничивает число страниц через `--max-pages`. Для массовых связанных запросов исходный проект использует `batch`, разбивая команды на части не больше 50.

## Поля запроса и ответа

В классическом запросе имена в `select` обычно записываются верхним регистром. В ответе задача может прийти в camelCase:

| Запрошено | В ответе |
|---|---|
| `ID` | `id` |
| `TITLE` | `title` |
| `RESPONSIBLE_ID` | `responsibleId` |
| `GROUP_ID` | `groupId` |

`REAL_STATUS` используется как специальное поле фильтра, но не запрашивается в `select`; фактический статус ответа читается из `status`/`STATUS`.

Не привязывайте разбор только к одному регистру без теста на своём портале.

## Что ещё использовалось в task2bitrix24

| Метод | Задача | Дополнительное право |
|---|---|---|
| `tasks.task.result.list` | результат завершённой задачи | задачи |
| `task.elapseditem.getlist` | списанное время | задачи |
| `user.get` | имена сотрудников | пользователи |
| `crm.company.list` | связанные компании | CRM |
| `sonet_group.get` | названия проектов | рабочие группы |
| `batch` | до 50 независимых подзапросов | права каждого вложенного метода |

Часы нужно брать из `task.elapseditem.getlist`, а не угадывать по комментариям. Ошибки отдельных команд внутри `batch` находятся в `result.result_error` и должны проверяться отдельно.

## Промпт для Codex или Claude

Секрет уже должен находиться в локальной переменной окружения, а не в тексте запроса:

```text
В локальном окружении настроены BITRIX24_EXPECTED_HOST и BITRIX_WEBHOOK.
Не показывай их значения, не печатай окружение и не используй curl -v.

Работай только в режиме чтения:
1. Получи доступные мне задачи через tasks.task.list.
2. Запрашивай только ID, TITLE, STATUS, RESPONSIBLE_ID, DEADLINE и GROUP_ID.
3. Пройди все страницы по next/start.
4. Для выбранного ID вызови tasks.task.get.
5. Сначала покажи короткую таблицу со скрытыми бизнес-полями. Перед
   --show-sensitive отдельно спроси разрешение: результат попадёт в контекст модели.
6. Ничего не создавай и не изменяй в Bitrix24.
```

## Ошибки и лимиты

| Ошибка | Действие |
|---|---|
| `QUERY_LIMIT_EXCEEDED` (обычно HTTP `503`) | ограниченный exponential backoff с jitter |
| `OVERLOAD_LIMIT` | остановиться: метод заблокирован вручную, retry не поможет |
| `OPERATION_TIME_LIMIT`, HTTP `429` | остановить серию и дождаться reset; не повторять немедленно |
| `NO_AUTH_FOUND`, `INVALID_CREDENTIALS` | webhook неверен, отозван или заблокирован; retry не поможет |
| `insufficient_scope`, `ACCESS_DENIED` | проверить scope и права пользователя |
| timeout | повторять ограниченно; не запускать параллельный шторм запросов |

Не отключайте проверку TLS через `curl -k` или аналоги. Для облачного портала используйте консервативный темп и `batch` вместо десятков параллельных запросов.

## Первоисточники

- [Первый вызов REST API Bitrix24](https://apidocs.bitrix24.com/first-steps/first-rest-api-call.html)
- [`tasks.task.list`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-list.html)
- [`tasks.task.get`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-get.html)
- [Пагинация списочных методов](https://apidocs.bitrix24.com/settings/how-to-call-rest-api/list-methods-pecularities.html)
- [`batch`](https://apidocs.bitrix24.com/settings/how-to-call-rest-api/batch.html)
- [Лимиты REST API](https://apidocs.bitrix24.com/limits.html)
- [Реализация task2bitrix24](https://github.com/Aleksandr-Litvinenko/task2bitrix24/blob/main/docs/BITRIX24.md)
