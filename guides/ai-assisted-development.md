# AI-assisted разработка в 1С

## Статус проверки

На macOS выполнены ограниченные smoke-tests `cc-1c-skills` (Python runtime, создание XML-каркаса EPF) и binary `mcp-1c v1.14.0` (`checksum`, `--version`, `--help`). Загрузка в 1С, EDT tools, BSL Language Server и тестовые фреймворки end-to-end здесь не запускались.

Для code review и генерации BSL рабочие бизнес-данные обычно не нужны. Начинайте с копии исходников.

## Минимальная связка

```text
копия XML/EDT
  → Agent Skills / правила проекта
  → offline контекст или BSL-индекс
  → BSL Language Server
  → unit / scenario tests
  → review разработчиком
```

## Вариант A: Agent Skills без живой базы

Upstream: [cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills).

Prerequisites:

- Windows: PowerShell 5.1+ для основного runtime;
- Linux/macOS: Python 3.9+ и зависимости из `requirements.txt`;
- Node.js 18+ только для `web-test`;
- платформа 1С 8.3 нужна для реальной сборки/загрузки EPF, ERF, CF или CFE.

Для воспроизводимости фиксируйте commit/release. Пример подготовки Python-варианта для Codex:

```bash
git clone https://github.com/Nikolay-Shirokov/cc-1c-skills.git tools/cc-1c-skills
cd tools/cc-1c-skills
python3 -m pip install -r requirements.txt
python3 scripts/switch.py codex --runtime python
```

На системах, где команда `python` не существует, используйте `python3`. После установки сначала попросите агента только проанализировать копию проекта; не разрешайте `db-load-*`, `db-update`, `meta-remove`, `form-remove` и `web-unpublish` до отдельной проверки.

## Вариант B: offline-контекст конфигурации

[mcp-1c](https://github.com/feenlace/mcp-1c) выпускается готовым binary. Проверенный здесь безопасный минимум — скачать release `v1.14.0`, сверить опубликованный checksum и выполнить `--version`/`--help`.

Важно:

- «один binary без зависимостей» относится только к MCP executable;
- live-режим требует расширение 1С и опубликованный HTTP-сервис;
- HTTP-сервис 1С размещается на Windows/Linux; macOS-клиенту нужен удалённый host или VM;
- открытая, Extended и Professional редакции имеют разный набор функций;
- для первого pilot предпочтительнее offline dump без бизнес-данных.

## Вариант C: 1C:EDT

[EDT-MCP](https://github.com/DitriXNew/EDT-MCP) на дату аудита поддерживает EDT 2026.1 и 2026.2.

После установки:

1. отключите `All Tools`;
2. выберите preset `Analysis Only` для навигации или `Code Review` для чтения BSL;
3. отдельно согласуйте `write_module_source`, `update_database`, `delete_project`, `delete_infobase` и `delete_metadata`;
4. для screenshots форм запустите EDT с `-DnativeFormBufferedLayoutRender=true`;
5. проверьте результат на отдельном workspace и тестовой базе.

Релизный ZIP и Update Site были проверены как артефакты, но плагин не запускался внутри EDT в рамках этого гида.

## Большой BSL-репозиторий

[code-index-mcp](https://github.com/Regsorm/code-index-mcp) имеет два разных binaries:

- `code-index` из npm/MCP Registry — без поддержки 1С;
- `bsl-indexer` из GitHub Releases или сборки из исходников — с XML/EDT parsers и BSL tools.

Для 1С выбирайте именно `bsl-indexer` и сначала индексируйте небольшую тестовую выгрузку. [mcp-1c-v1](https://github.com/fserg/mcp-1c-v1) решает другую задачу: RAG по экспортированной структуре конфигурации через Python, Docker и Qdrant.

## Статический анализ и тесты

Проект должен сам зафиксировать инструменты и команды. Кандидаты для проверки:

- [BSL Language Server](https://github.com/1c-syntax/bsl-language-server) — статический анализ;
- [YAxUnit](https://github.com/bia-technologies/yaxunit) — unit-тестирование;
- [Vanessa Automation](https://github.com/Pr-Mex/vanessa-automation) — сценарные тесты.

Наличие ссылки здесь не означает, что конкретная версия проверена в этой матрице.

## Что дать агенту

- структуру репозитория и отдельную рабочую копию;
- правила именования и стандарты разработки;
- релевантные модули и метаданные;
- точные команды сборки, статического анализа и тестов;
- expected output и список допустимых изменений;
- список файлов и операций, которые менять нельзя.

## Definition of done

- diff минимален и относится к задаче;
- BSL-проверки и тесты проходят зафиксированными командами;
- выгрузка собирается обратно в тестовой среде;
- запрещённые tools не включались;
- нет секретов и персональных данных;
- поведение описано тестом или документацией;
- diff просмотрен человеком;
- rollback проверен до доступа к production.
