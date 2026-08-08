# Creating Bitrix24 leads through an incoming webhook

[Русский](bitrix24-leads.md) · **English** · [← Back to the guide](../README.en.md)

## Verification status

The author reports that on 2026-08-08, in the private working environment of the [`1cProductMap`](https://github.com/Aleksandr-Litvinenko/1cProductMap) project, this live scenario ran end to end:

```text
website form
  → POST /api/lead
  → server-side Python service
  → Bitrix24 incoming webhook
  → crm.lead.add
  → crm.lead.get as a verification read
```

The create call returned an ID, and the follow-up read confirmed `TITLE`, `NAME`, `PHONE`, `EMAIL`, `IM`, `COMMENTS`, `SOURCE_ID`, and `SOURCE_DESCRIPTION`. [Commit `7839874`](https://github.com/Aleksandr-Litvinenko/1cProductMap/commit/7839874dace10a4fbc0e10e275d6314a48763db7) publicly documents the architecture and the setup instructions, but contains neither the server source nor a reproducible transcript of the live test.

Evidence boundary: the live run used legacy methods. The current example below uses the recommended `crm.item.add`, which you must verify separately with a synthetic lead on your own portal.

## A critical API update

[`crm.lead.add`](https://apidocs.bitrix24.com/api-reference/crm/leads/crm-lead-add.html), `crm.lead.get`, and `crm.lead.list` still work and were used in the real case, but Bitrix24 now officially marks them as deprecated.

For a new integration, use the universal methods:

- [`crm.item.add`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html) with `entityTypeId: 1`;
- [`crm.item.get`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-get.html) to verify;
- [`crm.item.list`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html) to search by an external ID.

## The webhook exists on the backend only

The correct shape:

```text
browser → your HTTPS endpoint → server-side validation → Bitrix24 webhook
```

The wrong shape:

```text
browser → Bitrix24 webhook
```

In the second one, the key ends up in JavaScript, DevTools, and the network log of every visitor.

Under **Developer resources → Common use cases → custom webhook**, create an incoming webhook with the `crm` scope, owned by a dedicated user who can add and read leads but has no unnecessary edit or delete rights. Store the URL in a secret manager or a file with `600` permissions, never in the repository.

If the webhook URL was pasted into an AI chat, an issue, a setup script, or a log, issue a new key. The URL itself is a secret carrying that user's permissions.

## What the request that actually worked looked like

```http
POST https://<portal>.bitrix24.com/rest/<user>/<secret>/crm.lead.add
Content-Type: application/json
```

```json
{
  "fields": {
    "TITLE": "[TEST] AI × 1C enquiry",
    "NAME": "Test user",
    "PHONE": [{"VALUE": "+70000000000", "VALUE_TYPE": "WORK"}],
    "EMAIL": [{"VALUE": "qa@example.invalid", "VALUE_TYPE": "WORK"}],
    "COMMENTS": "Synthetic integration check",
    "SOURCE_ID": "WEB",
    "SOURCE_DESCRIPTION": "AI × 1C Guide",
    "OPENED": "N",
    "ORIGINATOR_ID": "ai-1c-guide",
    "ORIGIN_ID": "<unique-request-id>"
  },
  "params": {"REGISTER_SONET_EVENT": "N"}
}
```

A successful `crm.lead.add` returns a numeric ID. A `crm.lead.get` with that ID then confirms the fields were really stored. Do not use this legacy code as the base of a new service without a migration plan.

## Start with an offline preview

In [`bitrix24_webhook_example.py`](../scripts/bitrix24_webhook_example.py), the preview command builds the `crm.item.add` payload, redacts the form fields, and **does not read the webhook, open a network connection, or create anything**:

```bash
python3 scripts/bitrix24_webhook_example.py lead-preview \
  --request-id 'product-map-test-001'
```

Bitrix24 has no server-side dry run for lead creation. The preview therefore only checks the structure locally.

`--show-sensitive` reveals the synthetic preview fields. Do not use it with real contact data in an agent session: stdout becomes part of the model context.

`--request-id` must be an opaque technical key of Latin letters, digits, and the characters `._:-`, up to 64 characters. Do not put a phone number, an email, or an internal customer ID into it; in a normal preview it is redacted as well.

## Create a synthetic lead with the modern method

First set up the webhook locally, without pasting it into a prompt:

```bash
read -r BITRIX24_EXPECTED_HOST
read -rs BITRIX_WEBHOOK
export BITRIX24_EXPECTED_HOST BITRIX_WEBHOOK
```

`BITRIX24_EXPECTED_HOST` holds only the host of the test portal you chose in advance. Then take a fingerprint of the current webhook, with no network call:

```bash
python3 scripts/bitrix24_webhook_example.py write-fingerprint
read -r BITRIX24_ALLOW_WRITE
export BITRIX24_ALLOW_WRITE
```

Into the second command, paste the `TEST_PORTAL_ONLY:<fingerprint>` line the script printed. It binds the confirmation to one exact webhook, but it does not replace your manual check that the portal really is a test one.

After you have reviewed the preview by hand:

```bash
python3 scripts/bitrix24_webhook_example.py lead-create \
  --request-id 'product-map-test-001' \
  --confirm-test-write
```

The guardrails in the example:

1. the webhook host must match the separately entered `BITRIX24_EXPECTED_HOST`;
2. the title must start with `[TEST]`;
3. both a fingerprint-bound confirmation for that exact webhook and a separate flag are required;
4. a `crm.item.list` lookup by `originatorId + originId` runs before creation;
5. the new lead is created through `crm.item.add`, where Lead is `entityTypeId = 1`;
6. the result is read back through `crm.item.get`, and `originatorId` and `originId` are compared against the request;
7. the write call is never blindly retried after a timeout: a search by `originId` runs first;
8. neither the secret nor the contact data is printed.

Do not run this test on a production portal without the CRM owner's agreement. A separate test portal is preferable; otherwise use synthetic values only, and agree in advance who will manually close or delete that exact test lead.

## Idempotency and duplicates

`ORIGINATOR_ID`/`ORIGIN_ID` in the legacy API and `originatorId`/`originId` in the universal API help you find a repeat, but on their own they do not guarantee uniqueness across two parallel requests.

A reliable backend should:

1. accept its own `request_id`;
2. store it in a local database with a `UNIQUE` constraint;
3. pass the same ID to Bitrix24;
4. after a timeout, search for the existing lead before retrying `add`;
5. return the previously stored result on a repeated submission.

A person contacting you twice is a different problem. For phone and email, use [`crm.duplicate.findbycomm`](https://apidocs.bitrix24.com/api-reference/crm/duplicates/crm-duplicate-find-by-comm.html), but never merge or delete the matches automatically.

## Backend checks before Bitrix24

- require a name and a phone number;
- limit the size of every field and of the whole JSON body;
- normalize phone numbers and email addresses;
- use a field allowlist instead of forwarding arbitrary JSON into CRM;
- add a honeypot or CAPTCHA to a public form;
- rate-limit by IP and by request ID;
- log your own enquiry ID, not the webhook or the full payload;
- return a neutral error to the client, without the Bitrix24 `error_description`;
- use a queue instead of a risky fallback that can create a second lead after a timeout.

## Errors and retries

| Error | What to do |
|---|---|
| `NO_AUTH_FOUND` | the webhook is wrong or revoked; do not retry |
| `INVALID_CREDENTIALS`, `insufficient_scope`, access denied | fix the scope or the user's permissions |
| `QUERY_LIMIT_EXCEEDED` (usually HTTP `503`) | bounded backoff with jitter |
| `OVERLOAD_LIMIT` | stop: the method was blocked manually and a retry will not help |
| `OPERATION_TIME_LIMIT`, HTTP `429` | wait for the reset |
| timeout after `add` | search by the external ID first; the lead may already exist |
| any other `5xx` | retry only after an idempotency check |

Do not return the internal lead ID to the visitor. Give them your own opaque enquiry ID and an acceptance status.

## Prompt for Codex or Claude

```text
BITRIX24_EXPECTED_HOST and BITRIX_WEBHOOK are set in my local environment.
Do not reveal their values, do not print the environment, do not use curl -v.

1. Start by running lead-preview only, with synthetic values.
2. Do not add --show-sensitive and do not send anything over the network.
3. Show the field structure and explain what would be created.
4. Do not run lead-create until I separately confirm the test portal,
   a stable request ID, and the exact preview.
5. Never put the webhook into browser JavaScript or into Git.
```

## Primary sources

- [Create webhooks and apps in Bitrix24](https://helpdesk.bitrix24.com/open/21133100/)
- [`crm.item.add`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html)
- [`crm.item.get`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-get.html)
- [`crm.item.list`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html)
- [`crm.lead.add` — legacy, deprecated](https://apidocs.bitrix24.com/api-reference/crm/leads/crm-lead-add.html)
- [ProductMap: how enquiries reach Bitrix24](https://github.com/Aleksandr-Litvinenko/1cProductMap/blob/main/docs/bitrix-webhook.md)
