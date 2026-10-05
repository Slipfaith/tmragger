# Changelog

## 06.10.26

### RU
- **Проверка сплитов через Codex CLI вместо Gemini API**: API-ключ не нужен, кандидаты проверяются пачками (по 25, до 4 параллельно), Codex может исправить точки разреза; правки принимаются только если текст остаётся дословным.
- **Промпт**: текст из вкладки «Промпт Codex» (CLI: `--codex-prompt-file`) используется как промпт пакетной проверки.
- **Прогресс и «Стоп»**: во время проверки пачек виден прогресс «пачки N/M», «Стоп» завершает уже запущенные вызовы `codex`.
- **Сплиттер**: не режет внутри скобок; номера пунктов («…odds. 2. Click») остаются с пунктом; даты с сокращённым месяцем («Dec. 31.») не рвутся; строки вроде «[table]» и «• Small Blind» присоединяются к соседней части, а не отменяют весь сплит.
- **CLI**: добавлен `--cleanup-line-breaks`; он несовместим с `--split-line-breaks`. Флаги `--codex-*` вместо `--gemini-*` (старые остались алиасами), переменные `CODEX_MAX_PARALLEL`, `CODEX_MAX_CHECKS`.
- **Очистка от Gemini**: убраны подсчёт стоимости, API-ключ, старый клиент и иконки; внутренние имена и ключи отчётов переименованы (`gemini_*` → `verification_*`).
- Тесты больше не требуют внешнего `sample\*.tmx`.

### EN
- **Split verification via Codex CLI instead of the Gemini API**: no API key needed; candidates are checked in batches (25 each, up to 4 in parallel) and Codex may correct cut points; fixes are accepted only when the text stays verbatim.
- **Prompt**: the text from the "Codex prompt" tab (CLI: `--codex-prompt-file`) is used as the batch verification prompt.
- **Progress and Stop**: batch progress ("batches N/M") is shown while verifying; Stop kills the `codex` calls already running.
- **Splitter**: no cuts inside parentheses; list numbers ("…odds. 2. Click") stay with their item; dates with an abbreviated month ("Dec. 31.") are kept together; lines like "[table]" and "• Small Blind" are attached to a neighbouring part instead of cancelling the whole split.
- **CLI**: added `--cleanup-line-breaks` (mutually exclusive with `--split-line-breaks`). `--codex-*` flags replace `--gemini-*` (old names kept as aliases); env vars `CODEX_MAX_PARALLEL`, `CODEX_MAX_CHECKS`.
- **Gemini cleanup**: removed cost estimation, API key, the old client and icons; internal names and report keys renamed (`gemini_*` → `verification_*`).
- Tests no longer need an external `sample\*.tmx`.
