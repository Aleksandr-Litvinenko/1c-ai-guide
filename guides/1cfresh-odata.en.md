# Connecting to OData in 1C:Fresh

[Русский](1cfresh-odata.md) · **English** · [← Back to the guide](../README.en.md)

## Verification status

The author reports that on 2026-08-08 the private `task2bitrix24` integration passed HTTPS authentication and performed a bounded catalog read from a 1C:UNF application hosted in 1C:Fresh. The same local working code also implements duplicate detection and creation of unposted `Document_УчетВремени` and `Document_ЗаданиеНаРаботу` documents, with posting kept as a separate call.

That is an **author-reported private live smoke test for reads**, not an independently reproducible public test. The write path was not re-run while this guide was written: reproduce the `POST` example on a disposable test copy before raising its status to end-to-end.

Original case: [OData documentation in task2bitrix24](https://github.com/Aleksandr-Litvinenko/task2bitrix24/blob/main/docs/ODATA.md).

## What was actually connected

Claude and Codex did not reach the database by magic. The agent helped write and run an ordinary local HTTP client:

```text
Codex / Claude
  → local client with a limited command set
  → HTTPS + HTTP Basic Auth
  → standard.odata in 1C:Fresh
  → test or production 1C:UNF database
```

That boundary matters: the local process knows the password, and the password never has to be pasted into a prompt, an MCP config, a README, or a chat.

## Preparation inside 1C:Fresh

1. In the service manager, open the application and choose **automatic REST service settings**.
2. On the authorization tab, create a dedicated 1C service user with minimal permissions.
3. On the composition tab, publish only the configuration objects you actually need.
4. For read access, do not grant add, change, delete, or post permissions.
5. For a write test, use a separate disposable copy with synthetic data and a verified snapshot.

The address in the original case has this shape:

```text
https://1cfresh.com/a/sbm/<base-id>/odata/standard.odata
```

Official instructions: [1C:Fresh — working through the standard OData interface](https://1cfresh.com/articles/data_odata) (in Russian).

## Keep secrets in the local environment only

Enter the address, the service user name, and the password one at a time through stdin so the real values never land in shell history:

```bash
read -r ONEC_ODATA_BASE_URL
read -r ONEC_ODATA_USER
read -rs ONEC_ODATA_PASSWORD
export ONEC_ODATA_BASE_URL ONEC_ODATA_USER ONEC_ODATA_PASSWORD
```

The address you type has the form `https://1cfresh.com/a/sbm/<base-id>/odata/standard.odata`. For a permanent service, use a secret manager rather than a shell profile or a `.env` file inside the repository.

Do not run `env`, `set`, `curl -v`, or header-dumping debug output next to these values. If the password reached a chat or a log, rotate it.

The [`fresh_odata_example.py`](../scripts/fresh_odata_example.py) script uses only the Python standard library, validates the TLS certificate, and never prints `Authorization`.

It accepts only an HTTPS endpoint on the `1cfresh.com` domain and refuses HTTP redirects: Basic Auth must not follow a redirect to a different origin.

## Step 1. Read `$metadata`

Start by finding the real entity names in your specific configuration:

```bash
python3 scripts/fresh_odata_example.py entity-sets --contains 'Document_'
```

Do not guess a name from the user-facing caption. The resource name is built from the object's type and its configuration name — for example `Document_УчетВремени` or `Catalog_Сотрудники`.

## Step 2. List documents

```bash
python3 scripts/fresh_odata_example.py list 'Document_УчетВремени' \
  --select 'Ref_Key,Number,Date,Posted,Комментарий' \
  --orderby 'Date desc' \
  --top 10
```

Business values are redacted by default, but `Ref_Key` stays visible so you can use it in the next step. The `--show-sensitive` flag reveals the selected values; use it only in a trusted local terminal, knowing that the output of a command run by an agent becomes part of the Codex or Claude context.

What happens:

- the request is sent as `GET`;
- `$select` limits the fields;
- `$top` limits the response size;
- the Cyrillic entity name is URL-encoded;
- the script understands both the `value` response shape and the legacy `d.results` one.

Use `--skip 10`, then `--skip 20` for the next pages. On a large database, start with a narrow `$filter` on a date range and only then increase the volume.

## Step 3. Read a single document

Take a `Ref_Key` from the previous response and use it instead of the sample UUID:

```bash
python3 scripts/fresh_odata_example.py get \
  'Document_УчетВремени' \
  '11111111-1111-1111-1111-111111111111' \
  --select 'Ref_Key,Number,Date,Posted,Комментарий'
```

The actual resource looks like this:

```text
Document_УчетВремени(guid'<Ref_Key>')
```

Do not store a full production response in an issue or a public log: comments, employees, and amounts may contain personal and commercial data.

## Step 4. Prepare a document fixture

Which fields are mandatory depends on the configuration and its version. For `Document_УчетВремени` in the original 1C:UNF setup the structure was:

```json
{
  "Date": "2026-08-08T12:00:00",
  "DeletionMark": false,
  "Posted": false,
  "Организация_Key": "<guid-from-disposable-test-base>",
  "СтруктурнаяЕдиница_Key": "<guid-from-disposable-test-base>",
  "Сотрудник_Key": "<guid-from-disposable-test-base>",
  "Автор_Key": "<guid-from-disposable-test-base>",
  "ХозяйственнаяОперация_Key": "<guid-from-disposable-test-base>",
  "ДатаС": "2026-08-03T00:00:00",
  "ДатаПо": "2026-08-09T00:00:00",
  "Комментарий": "Will be replaced with a unique test marker",
  "Операции": [
    {
      "LineNumber": "1",
      "ВидРабот_Key": "<guid-from-disposable-test-base>",
      "Номенклатура_Key": "<guid-from-disposable-test-base>",
      "Всего": 1,
      "ПнДлительность": 1,
      "Сумма": 0
    }
  ]
}
```

This is the shape of a real case, not a universal ready-made payload. Read `$metadata` first and look at one synthetic sample document in your own test database. Never carry `Ref_Key`, `Number`, `DataVersion`, or GUIDs over from production.

## Step 5. Create an unposted test document

`Posted: false` still means a real write, not a dry run. First take a fingerprint of the **disposable endpoint you have already verified**:

```bash
python3 scripts/fresh_odata_example.py write-fingerprint
```

The command opens no network connection and prints a line shaped like `DISPOSABLE_TEST_BASE_ONLY:<fingerprint>`. Check the database address by hand, then feed that line into `ONEC_ODATA_ALLOW_WRITE`. The fingerprint binds your confirmation to one exact URL, but it cannot by itself prove that the database is a test one.

```bash
read -r ONEC_ODATA_ALLOW_WRITE
export ONEC_ODATA_ALLOW_WRITE

python3 scripts/fresh_odata_example.py create \
  'Document_УчетВремени' \
  './fixture.json' \
  --request-id 'work-time-guide-001' \
  --confirm-test-write
```

The script:

1. checks the confirmation against the fingerprint of the current endpoint;
2. allows writes only to `Document_*` and rejects a fixture with service fields or `Posted: true`;
3. builds a stable marker from `--request-id` and searches for it before writing;
4. performs a single `POST`, receives `Ref_Key`, and reads the object back;
5. on an inconclusive `POST` result, repeats the marker search rather than the write;
6. verifies that the document exists, the marker matches, and `Posted` is still `false`.

The script deliberately does **not** call `.../Post()` and does **not** delete the document. After the check, restore the disposable database from its snapshot and confirm the fixture is gone.

## Duplicate protection

The public script writes a stable `--request-id` into the string field `Комментарий`. To use a different attribute, set `--marker-field` only after confirming its string type in `$metadata`, and add it to the fixture. Service fields, `Posted`, `DeletionMark`, `Date`, and reference keys are rejected. Re-running the same command finds the existing marker first. Do not change the request ID after a timeout, or the replay protection loses its meaning.

The original integration additionally ran an exact document search by employee and period. For your own document, pick a natural key, run a `$filter`, and create the object only when the result is empty.

Checking before the write reduces risk but does not eliminate a race between two parallel requests. A critical integration needs server-side uniqueness, a queue, or an application-level lock.

## What not to do on a production database

- Do not probe permissions with a trial `POST`, `PATCH`, or `DELETE` in production.
- Do not call `.../Post()` without a separate preview and human approval.
- Do not disable TLS with `verify=False`, `curl -k`, or equivalents.
- Do not use a 1C administrator account.
- Do not publish every OData entity "just in case".
- Do not treat OData as read-only: the interface can create, change, delete, and post objects.

## Common errors

| Error | What to check |
|---|---|
| `401` | login, password, and the user's permission to use OData |
| `403` | permissions for the specific object and operation |
| `404` | the database address, the OData publication, and the exact EntitySet name |
| `400` | mandatory attributes and their types in `$metadata`; this is not proof that writes are denied |
| timeout | selection size, a narrow `$select`, a date range in `$filter`, network access |

## Prompt for Codex or Claude

The values must already be in the local environment, not in the request text:

```text
ONEC_ODATA_BASE_URL, ONEC_ODATA_USER and ONEC_ODATA_PASSWORD are set in my
local environment. Do not reveal their values, do not print the environment,
the Authorization header, or the full database URL.

Work in read-only mode:
1. Fetch $metadata and find the exact EntitySet names.
2. Run list with a narrow $select, a $filter, and $top no larger than 10.
3. Do not add --show-sensitive without asking me separately.
4. For one chosen Ref_Key, run get.
5. Never call create, POST, PATCH, DELETE, Post(), or Unpost().
6. Do not save the raw response into the repository, an issue, or a log.
```

## Primary sources

- [1C:Fresh — working through the standard OData interface](https://1cfresh.com/articles/data_odata)
- [1C:Enterprise — Standard OData interface](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Developer_Guides/1C_Enterprise_8.3.23_Developer_Guide/Chapter_17._Integration_with_external_systems/17.4._Standard_OData_interface/17.4.1._General_information/?language=en)
- [1C:Enterprise — OData data presentation](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Developer_Guides/1C_Enterprise_8.3.23_Developer_Guide/Chapter_17._Integration_with_external_systems/17.4._Standard_OData_interface/17.4.3._Data_presentation/)
- [1C:Enterprise — OData query parameters](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Developer_Guides/1C_Enterprise_8.3.23_Developer_Guide/Chapter_17._Integration_with_external_systems/17.4._Standard_OData_interface/17.4.6._Query_parameters/)
