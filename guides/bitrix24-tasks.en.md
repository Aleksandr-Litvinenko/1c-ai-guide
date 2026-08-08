# Reading Bitrix24 tasks through an incoming webhook

[Русский](bitrix24-tasks.md) · **English** · [← Back to the guide](../README.en.md)

## Verification status

This is a real pattern taken from the [`task2bitrix24`](https://github.com/Aleksandr-Litvinenko/task2bitrix24) project: an incoming webhook, the classic REST API, `POST application/x-www-form-urlencoded`, `next → start` pagination, bounded retries on rate limits, and `batch` with up to 50 commands.

The working integration reads tasks, results, logged time, users, related CRM companies, and projects. This public guide contains no webhook and no portal data; the example is covered by unit tests that make no network calls.

## Classic REST and REST 3.0 are not the same thing

The original project uses the classic endpoint:

```text
https://<portal>.bitrix24.com/rest/<user-id>/<webhook-code>/<method>
```

Do not add `/api/`: that is REST 3.0, with different field names, a different request body, and different pagination. A single guide cannot mix the two versions.

The official methods for this scenario:

- [`tasks.task.list`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-list.html) — a list of tasks;
- [`tasks.task.get`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-get.html) — one task by ID.

## Create a webhook with the `task` scope

In the current Bitrix24 interface, open **Developer resources → Common use cases → custom webhook** and create an incoming webhook with the tasks scope only. Access to data is still limited by the permissions of the user who created the webhook.

Official instructions: [Create webhooks and apps in Bitrix24](https://helpdesk.bitrix24.com/open/21133100/).

Do not use an administrator account just to see more tasks. If you later need companies, users, or workgroups, add each scope separately after review.

## Never paste a webhook into a prompt

A webhook URL is a password. Keep it in a secret manager or a local environment variable:

```bash
read -r BITRIX24_EXPECTED_HOST
read -rs BITRIX_WEBHOOK
export BITRIX24_EXPECTED_HOST BITRIX_WEBHOOK
```

The value you type looks like this:

```text
https://<portal>.bitrix24.com/rest/<user-id>/<webhook-code>/
```

`BITRIX24_EXPECTED_HOST` is only the portal host, with no `https://` and no path — for example `<portal>.bitrix24.com`. It is entered separately so the script stops if the endpoint is ever swapped by accident. If the webhook was pasted into an AI chat, an issue, a README, or a log, reissue it. Do not use `curl -v`, do not print the environment, and never pass the URL to client-side JavaScript.

## List the active tasks you can see

The [`bitrix24_webhook_example.py`](../scripts/bitrix24_webhook_example.py) script uses the same transport as `task2bitrix24` but keeps TLS verification on:

```bash
python3 scripts/bitrix24_webhook_example.py tasks-list
```

The default statuses are:

- `2` — pending;
- `3` — in progress;
- `6` — deferred.

Limit the list to one assignee:

```bash
python3 scripts/bitrix24_webhook_example.py tasks-list \
  --responsible-id 123 \
  --statuses '2,3,6'
```

Only `ID`, `TITLE`, `STATUS`, `RESPONSIBLE_ID`, `DEADLINE`, and `GROUP_ID` are requested. Title, assignee, deadline, and project are redacted by default so an agent's output does not push business data into the model context. For a deliberate local review, add `--show-sensitive`.

## Read a single task

```bash
python3 scripts/bitrix24_webhook_example.py task-get 12345
```

`tasks.task.get` retrieves only the selected fields, including `UF_CRM_TASK` when you need the CRM link. The response lives under `result.task`.

Bitrix24 may attach nested user and project objects even with a narrow `select`. That is why, without `--show-sensitive`, the example prints only `id`, `status`, and an edit flag; the full response is revealed only through the explicit flag in a trusted terminal.

`tasks.task.get` is a safe extension of the original scenario: the main working implementation fetched task cards through `tasks.task.list`, and this separate call was added to the guide from the official documentation.

## How pagination works

The classic `tasks.task.list` returns at most 50 tasks per page. When the response root contains `next`, its value must be sent as `start` in the following request:

```text
start = 0 → next = 50
start = 50 → next = 100
...
a response with no next → the end
```

The script checks that `next` keeps increasing and caps the number of pages with `--max-pages`. For bulk related lookups, the original project uses `batch`, splitting commands into chunks of no more than 50.

## Request and response fields

In a classic request, `select` names are normally written in upper case. In the response, a task may come back in camelCase:

| Requested | In the response |
|---|---|
| `ID` | `id` |
| `TITLE` | `title` |
| `RESPONSIBLE_ID` | `responsibleId` |
| `GROUP_ID` | `groupId` |

`REAL_STATUS` is used as a special filter field but is not requested in `select`; the actual status is read from `status`/`STATUS` in the response.

Do not hard-code your parsing to a single case convention without testing it against your own portal.

## What else task2bitrix24 used

| Method | Purpose | Additional scope |
|---|---|---|
| `tasks.task.result.list` | the result of a completed task | tasks |
| `task.elapseditem.getlist` | logged time | tasks |
| `user.get` | employee names | users |
| `crm.company.list` | related companies | CRM |
| `sonet_group.get` | project names | workgroups |
| `batch` | up to 50 independent sub-requests | the scope of each nested method |

Take hours from `task.elapseditem.getlist` rather than inferring them from comments. Errors from individual commands inside a `batch` appear in `result.result_error` and must be checked separately.

## Prompt for Codex or Claude

The secret must already be in a local environment variable, not in the request text:

```text
BITRIX24_EXPECTED_HOST and BITRIX_WEBHOOK are set in my local environment.
Do not reveal their values, do not print the environment, do not use curl -v.

Work in read-only mode:
1. Fetch the tasks available to me through tasks.task.list.
2. Request only ID, TITLE, STATUS, RESPONSIBLE_ID, DEADLINE and GROUP_ID.
3. Walk every page through next/start.
4. For one chosen ID, call tasks.task.get.
5. Show a short table with business fields redacted first. Ask me separately
   before --show-sensitive: that output becomes part of the model context.
6. Do not create or change anything in Bitrix24.
```

## Errors and limits

| Error | What to do |
|---|---|
| `QUERY_LIMIT_EXCEEDED` (usually HTTP `503`) | bounded exponential backoff with jitter |
| `OVERLOAD_LIMIT` | stop: the method was blocked manually and a retry will not help |
| `OPERATION_TIME_LIMIT`, HTTP `429` | stop the series and wait for the reset; do not retry immediately |
| `NO_AUTH_FOUND`, `INVALID_CREDENTIALS` | the webhook is wrong, revoked, or blocked; a retry will not help |
| `insufficient_scope`, `ACCESS_DENIED` | check the scope and the user's permissions |
| timeout | retry a bounded number of times; do not launch a storm of parallel requests |

Do not disable TLS verification with `curl -k` or equivalents. On a cloud portal, keep a conservative pace and use `batch` instead of dozens of parallel requests.

## Primary sources

- [Your first Bitrix24 REST API call](https://apidocs.bitrix24.com/first-steps/first-rest-api-call.html)
- [`tasks.task.list`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-list.html)
- [`tasks.task.get`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-get.html)
- [Pagination in list methods](https://apidocs.bitrix24.com/settings/how-to-call-rest-api/list-methods-pecularities.html)
- [`batch`](https://apidocs.bitrix24.com/settings/how-to-call-rest-api/batch.html)
- [REST API limits](https://apidocs.bitrix24.com/limits.html)
- [The task2bitrix24 implementation](https://github.com/Aleksandr-Litvinenko/task2bitrix24/blob/main/docs/BITRIX24.md)
