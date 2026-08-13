# 1C-Connect + Jira + Bitrix24

[Русский](connect-jira-bitrix24.md) · **English** · [← Back to the guide](../README.en.md)

## Verification status

| Part of the chain | What was actually done | What it does not prove |
|---|---|---|
| Jira Data Center | **Live smoke on 2026-08-13**: anonymous calls against the public `https://issues.apache.org/jira` — JQL search, `startAt`/`maxResults`/`total`, reading one issue by key, and the HTTP `400` error shape. Reproducible without an account | How a locked-down corporate instance behaves, permissions, custom fields |
| Jira Cloud | **Docs**: the endpoint, the pagination model, and the mandatory `fields` list come from Atlassian's documentation | No call was made against Cloud here |
| 1C-Connect | **Docs**: operation names, parameters, the `ValueTable` response shape, and the hourly limits come from the official public API documentation. The SOAP envelope is built to match the published sample | Not a single live call: the author has no 1C-Connect portal access |
| Bitrix24 | Reuses the verified read-only path from the [tasks guide](bitrix24-tasks.en.md) | Nothing beyond what that guide already covers |
| Correlation logic and call budget | 21 unit tests with no network and no credentials | That your three systems are wired together this way |

This is **not** an end-to-end verification of the chain. Only the Jira half was exercised live. Reproduce steps 2 and 3 on your own stand before trusting the report.

## Why this exists

For an IT service company, one piece of work lives in three systems at once:

```text
the client writes in 1C-Connect  → a service request
a consultant does the work       → a Bitrix24 task
a developer changes code         → a Jira issue
```

None of the three knows about the other two, which produces four recurring gaps:

- the request is closed while the Jira bug is still open — the client was told "done" too early;
- the Jira issue is closed while the request is still open — nobody told the client;
- a request exists with no task anywhere — the work is untracked and unbilled;
- a task exists with no request — work is happening without a client asking for it.

The [`connect_jira_bridge_example.py`](../scripts/connect_jira_bridge_example.py) script reads all three and prints only the divergences. It creates and changes nothing.

## Three systems, three different worlds

This is the part to understand before writing any code. An AI agent that "just calls the API" breaks on every row of this table.

| | 1C-Connect | Jira Data Center | Jira Cloud | Bitrix24 |
|---|---|---|---|---|
| Protocol | SOAP 1.2 (1C:Enterprise 8.3 web services) | REST | REST | REST |
| Listing | `ServiceRequestRead` with `PeriodFrom` | `GET /rest/api/2/search` | `POST /rest/api/3/search/jql` | `tasks.task.list` |
| Pagination | none: it returns objects changed since a moment | `startAt` + `total` | `nextPageToken`, `total` is **not returned** | `next` → `start` |
| Outgoing webhooks | **none** | yes | yes | yes |
| Rate limit | 120 calls/hour daytime, 600 at night | — | — | per-method limits |
| Auth | Basic, portal administrator rights | Bearer PAT | Basic `email:token` | secret inside the webhook URL |

### Consequence 1. 1C-Connect has no webhooks — only polling

1C-Connect cannot call you. The only way to learn about a new request is to call `ServiceRequestRead` periodically, passing the moment of your last successful read. The documentation is explicit that the API returns objects changed since a given moment and that the external side must keep its own watermark.

That changes the architecture: you need a scheduler with state, not an event handler.

### Consequence 2. The hourly limit is the binding constraint

`ServiceRequestRead` allows 120 calls per hour during the day. That is one poll every 30 seconds, and no more. `ServiceRequestHistory` allows only 50 per hour — but it accepts a batch of up to 550 IDs at once.

Hence the rule: **never request history one ticket at a time in a loop**. An agent that decides to "walk every ticket and check its history" burns the hourly limit on 50 tickets and locks the service out for the rest of the window.

The script keeps a local budget and stops on its own:

```bash
python3 scripts/connect_jira_bridge_example.py budget
```

```text
ServiceRequestHistory: осталось 50 из 50 вызовов в час
ServiceRequestRead: осталось 120 из 120 вызовов в час
```

A local budget does not replace the server-side limit. It only stops an agent loop from burning the window before a human notices.

### Consequence 3. Jira Cloud and Data Center are two different APIs

In Jira **Cloud**, the old `/rest/api/2/search` and `/rest/api/3/search` endpoints are gone — they were shut down progressively between August and October 2025. The replacement is `POST /rest/api/3/search/jql`, where pagination uses `nextPageToken`, `total` is no longer returned, and the `fields` list must be passed explicitly.

In Jira **Data Center** none of that happened: `GET /rest/api/2/search` with `startAt` and `total` still works — verified with a live request while writing this guide.

This matters well beyond one region. Any team still on Data Center will find that current documentation, most blog posts, and most model output describe Cloud. An agent confidently writes Cloud code, receives `404` or `410`, and then starts "fixing" authentication instead of the endpoint.

The script picks the endpoint from the host:

```python
def jira_flavor(base_url: str) -> str:
    host = urlparse(base_url).netloc.lower()
    return "cloud" if host.endswith(".atlassian.net") else "datacenter"
```

That is a heuristic, not a guarantee: Cloud on a custom domain, or a proxy in front of Data Center, will fool it. When in doubt, set the flavour explicitly and check one request by hand.

## The external key: Field1…Field5

The three systems share no natural key. But a 1C-Connect service request carries five configurable string fields, `Field1`…`Field5`, of 1000 characters each — exactly what such links are for.

Agree on the convention once and write it into your process: for example, `Field1` holds

```text
JIRA:PROJ-123 B24-456
```

The script extracts both keys with regular expressions:

```bash
python3 scripts/connect_jira_bridge_example.py reconcile \
  --days 7 \
  --jql 'project = PROJ AND updated >= -7d' \
  --key-field Field1
```

Requests with no key land in their own "no external key" bucket. That is more honest than guessing the link from timestamps and client names: fuzzy matching produces false pairs, and conclusions about money and deadlines then get built on them.

## Access setup

### 1C-Connect

1. In the administrator cabinet, open **Administration → API settings** and enable server API access. The same section sets the time zone the service reports times in, and shows call statistics.
2. Note the constraint: API operations run as a user with 1C-Connect administrator rights. The public documentation describes no read-only API role — a significant limitation you have to compensate for on your side.
3. The web service address is `https://cus.1c-connect.com/cus/ws` for Russia, Kazakhstan, Belarus, Turkmenistan, Tajikistan, Uzbekistan, Kyrgyzstan and Vietnam, and `https://eu-cus.1c-connect.com/cus/ws` for the EU. The service is named `PartnerWebAPI2`.

Because the key carries administrator rights, access to it must be narrower than access to the portal itself: a separate secret, a separate process, a call log, and no agent that can read the secret directly.

### Jira

Data Center uses a personal access token; Cloud uses an `email` plus an API token:

```bash
read -r JIRA_BASE_URL
read -r JIRA_EMAIL          # Cloud only; leave empty for Data Center
read -rs JIRA_TOKEN
export JIRA_BASE_URL JIRA_EMAIL JIRA_TOKEN
```

Browse permission on the relevant projects is enough. Do not grant transition rights: the script cannot call them anyway, and the extra permission will outlive the script.

### Bitrix24

Reuse the tasks-scope webhook from the [tasks guide](bitrix24-tasks.en.md):

```bash
read -r BITRIX24_EXPECTED_HOST
read -rs BITRIX_WEBHOOK
export BITRIX24_EXPECTED_HOST BITRIX_WEBHOOK
```

## Step 1. Check Jira without connecting anything

You can run this right now, with no account and no instance of your own — it queries the Apache Software Foundation's public Jira:

```bash
export JIRA_BASE_URL='https://issues.apache.org/jira'
python3 scripts/connect_jira_bridge_example.py jira-issues \
  --jql 'project=KAFKA AND created>=-3d ORDER BY created DESC' \
  --top 3
```

The actual output while writing this guide:

```text
Jira (datacenter): найдено 3
  KAFKA-20934 | Open (new) | <скрыто:109>
  KAFKA-20933 | Patch Available (indeterminate) | <скрыто:51>
  KAFKA-20932 | Patch Available (indeterminate) | <скрыто:76>
```

Summaries are redacted by default. `--show-sensitive` reveals them — remember that the output of a command run by an agent becomes part of the model context.

The value in brackets is the status **category**, not the status name: `new`, `indeterminate`, `done`. Status names differ per project and cannot be compared across them; the category is the only portable signal, and the reconciliation is built on it.

## Step 2. Read the 1C-Connect service requests

```bash
python3 scripts/connect_jira_bridge_example.py connect-tickets --days 7 --top 10
```

The command builds a SOAP envelope matching the published sample:

```xml
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:par="http://buhphone.com/PartnerWebAPI2"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <soap:Header/>
  <soap:Body>
    <par:ServiceRequestRead>
      <par:Params>
        <Property xmlns="http://v8.1c.ru/8.1/data/core" name="PeriodFrom">
          <Value xsi:type="xs:dateTime">2026-08-06T00:00:00</Value>
        </Property>
      </par:Params>
    </par:ServiceRequestRead>
  </soap:Body>
</soap:Envelope>
```

The `buhphone.com` namespace is historical — that is what the service used to be called. It is not a typo and must not be changed.

The response arrives as a value table: column definitions first, then rows whose values are **positional**.

```xml
<Property name="ResultData" xmlns="http://v8.1c.ru/8.1/data/core">
  <Value xsi:type="ValueTable">
    <column><Name>ServiceRequestID</Name>…</column>
    <column><Name>Number</Name>…</column>
    <row>
      <Value xsi:type="xs:string">721b4536-df5c-11e9-…</Value>
      <Value xsi:type="xs:string">AAKUT-0000083</Value>
    </row>
  </Value>
</Property>
```

So the parser must zip each row against the column list in order, not look up names inside the row — there are no names there. Do not hard-code the field order from the documentation either: take it from the `column` block of the actual response.

Useful request fields: `Number`, `CreateTime`, `ClientID`, `ExecutorID`, `ServiceRequestStatusID`, `Priority` (`LOW`, `STANDARD`, `HIGH`), `ResultValidation` (`NO_VALIDATION`, `NOT_REQUIRED`, `CONFIRMED`, `REJECTED`), `Description`, `Result`, `Duration` in seconds, and `Field1`…`Field5`.

## Step 3. Reconcile the three systems

```bash
python3 scripts/connect_jira_bridge_example.py reconcile \
  --days 7 \
  --jql 'project = PROJ AND updated >= -7d' \
  --key-field Field1
```

The report has five buckets: requests with no external key, requests with no Jira issue, requests with no Bitrix24 task, "Jira issue closed while the request is open", and "request closed while the Jira issue is in progress". Matching rows are not printed: the point of the report is a short list of things that need a human.

## What the script deliberately does not do

- It calls no write operation: the allowed 1C-Connect operations are `ServiceRequestRead` and `ServiceRequestHistory`, Bitrix24 is limited to `tasks.task.list`, and building an envelope for anything else raises an error.
- It does not create tasks from requests and does not change statuses. Auto-creation looks like the obvious next step, but it is exactly what turns one matching error into garbage in three systems at once.
- It does not follow redirects: credentials must not travel to another origin.
- It does not print summaries, descriptions, or markers without `--show-sensitive`.
- It stores no secrets: everything comes from environment variables.

## Prompt for Codex or Claude

```text
CONNECT_WS_BASE_URL, CONNECT_WS_USER, CONNECT_WS_PASSWORD, JIRA_BASE_URL,
JIRA_TOKEN, BITRIX24_EXPECTED_HOST and BITRIX_WEBHOOK are set in my local
environment. Do not reveal their values and do not print the environment.

1. Run budget first and show the remaining hourly allowance.
2. If ServiceRequestRead has fewer than 5 calls left, stop and tell me.
3. Run reconcile over 7 days with my JQL.
4. Do not add --show-sensitive without asking me separately.
5. Do not propose creating tasks and do not call write methods in any of
   the three systems.
6. If Jira answers 404 or 410, check the instance flavour (Cloud or Data
   Center) before touching the token.
```

## Common errors

| Symptom | What to check |
|---|---|
| Jira `404` or `410` on search | Cloud or Data Center: on Cloud the old `/search` is gone, use `POST /rest/api/3/search/jql` |
| Jira `400` with `errorMessages` | The JQL references a project or field that does not exist; the message is in the body |
| 1C-Connect `ResultCode` is not `SUCCESS` | The error text is in `ResultData`; check that API access is enabled and the user has administrator rights |
| Empty request table | `PeriodFrom` is later than the last change; mind the time zone from the API settings |
| Local budget exhausted | Wait for the window. Retrying does not help: the server counts the same calls |
| Request rows are shifted across fields | The parser follows the documented field order instead of the `column` block of the actual response |

## Primary sources

- [1C-Connect — server API](https://1c-connect.atlassian.net/wiki/spaces/PUBLIC/pages/975241472/API)
- [1C-Connect — reading the service request list (`ServiceRequestRead`)](https://1c-connect.atlassian.net/wiki/spaces/PUBLIC/pages/981041299)
- [1C-Connect — service request change history (`ServiceRequestHistory`)](https://1c-connect.atlassian.net/wiki/spaces/PUBLIC/pages/980943072)
- [1C-Connect — ready-made integrations](https://connect.ru/api/)
- [Jira Cloud REST API — issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)
- [Atlassian — migrating to the enhanced JQL endpoints](https://community.atlassian.com/forums/Jira-articles/Avoiding-Pitfalls-A-Guide-to-Smooth-Migration-to-Enhanced-JQL/ba-p/2985433)
- [`tasks.task.list`](https://apidocs.bitrix24.com/api-reference/tasks/tasks-task-list.html)
