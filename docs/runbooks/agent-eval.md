# Run the MCP agent evaluation

The harness compares full, compact and trimmed compact on the development
cohort. The contract is [MCP evaluation](../design/mcp-results.md#evaluation).
Run from `apps/agenteval`; default results go to `work/agenteval/<run-id>/` at
the repository root. `work/` is already ignored.

## Prerequisites

- Install Python 3.14, uv, the repository's Go toolchain, PostgreSQL client
  tools and `lsof`. Run `uv sync --frozen` once to install pinned dependencies.
- Start native PostgreSQL as in [corpus-local](corpus-local.md). Restore
  `corpus_vn_20261114_5` and start its matching local query embedder.
- Supply `CORPUS_TEST_ADMIN_URL` through the process environment. Its default is
  `postgres://trachuan@127.0.0.1:25433/postgres`. The role must create and drop
  temporary auth databases. Credentials stay outside command arguments and
  results. Password-bearing `CORPUS_TEST_ADMIN_URL` values are rejected at
  startup; use `PGPASSWORD` or `PGPASSFILE`. The harness never loads environment
  files. `psql -d` receives the password-free administrator or temporary auth
  URL.
- Supply `AGENTEVAL_CORPUS_DATABASE_URL`, or `SERVER_CORPUS_DATABASE_URL`, as a
  loopback PostgreSQL URL for a separate read-only corpus role, as in
  [server-local](server-local.md). Both URLs must name their roles explicitly;
  the corpus role must differ from the auth administrator. The harness replaces
  the corpus database path with `{database}` and also forces
  `default_transaction_read_only=on`. Only `sslmode` and `connect_timeout` URL
  options are accepted. `PGPASSWORD` and `PGPASSFILE` are inherited by local
  PostgreSQL clients and servers only. Corpus reader URLs may contain passwords;
  they go only into server environments, never command arguments or results.
- Include the sibling server change that implements
  `SERVER_COMPACT_RELATED=omit` before measuring trimmed compact.
- Fill the OpenAI row in `apps/agenteval/src/trachuan_agenteval/prices.json`
  with the exact model ID, input, output, cache-read and cache-write USD rates
  per million tokens and the source URL. Paid OpenAI runs refuse an empty or
  mismatched row. The Anthropic row uses the coordinator's table cached on
  2026-11-03.

The harness builds the server, fake Google provider and matcher offline. It
creates one fresh `trachuan_e2e_<random>` auth database using the same `psql`
mechanism as `scripts/e2e.sh`. It migrates only that auth database and selects
the existing corpus. It starts two loopback servers and one fake Google process
for each server, since fake Google accepts one exact callback. Normal and
trimmed servers use ports 29080 and 29082; fake Google uses 29081 and 29083.
Override all four with `--ports 29080,29082,29081,29083`. The account encryption
key and Google credentials are synthetic local values. Every child receives an
explicit environment for its purpose. Builds, the matcher, fake Google and
`lsof` receive no PostgreSQL credentials. No child inherits parent API keys,
tokens or secrets; only the server receives the harness's synthetic Google
credential explicitly.

Python follows the existing authorization-code flow with S256 PKCE, fake Google
login and CSRF-protected consent for a preregistered `agenteval` client. Cookies
are held in memory and scoped to each local origin, including secure cookies
over loopback HTTP, as in the e2e client. Access tokens refresh in memory. No
server test hook or personal token is used.

## Dry run

No model key or completed OpenAI price row is needed. This command starts both
servers, completes OAuth, discovers the tools and prints task counts, tool names
and the USD scheduling ceiling. It writes only a manifest.

```sh
cd apps/agenteval
uv run --frozen --offline agenteval run \
  --models claude-sonnet-5-5,OPENAI_MODEL \
  --modes full,compact,trimmed --cases all --runs 2 \
  --budget-usd 60 --query-embedder-url http://127.0.0.1:8091 --dry-run
```

## Pilot and full run

Paid runs need the coordinator's approval of models and budget. Source keys in a
subshell for each command. Replace `<key file>` with the owner's path. The
harness reads only `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` from its own
environment. Never print keys or put their values in arguments. The example
contains names only.

Pilot with two cases, one run per mode, one model and a USD 3 ceiling:

```sh
(set -a; . <key file>; set +a;
 ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_DANNY2" \
 uv run --frozen --offline agenteval run \
   --models claude-sonnet-5-5 --modes full,compact,trimmed \
   --cases 2 --runs 1 --budget-usd 3 \
   --query-embedder-url http://127.0.0.1:8091)
```

After the OpenAI row and key are ready, run both models on all 65 cases. The
default two runs give 780 tasks. Replace `OPENAI_MODEL` with the exact ID in the
price table. Export the owner's chosen OpenAI key as `OPENAI_API_KEY` in the
same subshell if the key file uses another variable name.

```sh
(set -a; . <key file>; set +a;
 ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_DANNY2" \
 uv run --frozen --offline agenteval run \
   --models claude-sonnet-5-5,OPENAI_MODEL \
   --modes full,compact,trimmed --cases all --runs 2 --budget-usd 60 \
   --query-embedder-url http://127.0.0.1:8091)
```

`--effort high` controls Anthropic adaptive thinking. The defaults are 16 model
requests, 48 tool calls and 8192 output tokens per task. Override with
`--max-turns`, `--max-tool-calls` and `--max-output-tokens`. Each task starts
with fresh history and the same system prompt. Every tool comes from MCP
`tools/list`, including paging. Search calls force `detail:full` in full mode
and `detail:compact` in compact and trimmed modes; other arguments, including an
explicit limit, remain the model's. Without a limit, the server uses its mode
default. Models receive no shell, browser or provider tools.

Tasks run serially in case, repetition, model, mode order. After every model
request, the harness adds provider-reported usage times the price row. It checks
the shared budget before every request. Once spend reaches the ceiling, it
schedules no further request or task. An interrupted task records `budget` and
is incomplete. The in-flight request can finish and overshoot the ceiling; the
remainder of that task does not run. SDK retries are disabled. Provider,
transport or scorer errors stop scheduling because spend or execution may be
incomplete. Model refusals score zero; task caps mark the task incomplete. Exit
0 means execution completed, 2 means incomplete, and 1 means setup failed. A
failed quality gate does not change the execution exit.

`--out <dir>` selects a fresh output directory; an existing directory is
refused. Use a location under `work/agenteval/` to retain ignore coverage.
Normal exit, exceptions, SIGINT and SIGTERM stop child processes, drop only the
temporary auth database and remove temporary stack files. SIGKILL cannot run
cleanup; after checking ownership, stop any remaining children and remove only
the named temporary auth database. Never drop the corpus database.

## Read results

- `manifest.json`: requested model IDs, selected case IDs, modes, repetitions,
  caps, price snapshot, SDK versions, system prompt hash, fixture hashes and
  discovered tool-schema hashes. It contains no environment values.
- `tasks.jsonl`: one row per scheduled task, case ID only, resolved model IDs,
  cited handles, expected and matched counts, uncached input, cache writes,
  cache reads, output, summed input, tool calls, tool-result code points,
  elapsed seconds, cost and stop reason. It stores no question, answer,
  reasoning, tool arguments, tool bodies or credentials.
- `summary.json`: execution status, planned and scheduled task counts, spend,
  means from completed tasks per model and mode, completed and incomplete task
  counts, and both gate verdicts. Incomplete tasks stay in JSONL but enter none
  of the means. Missing, duplicate, unpaired or incomplete tasks make the
  relevant gate unavailable.

Citations use `[[cite:vn:vbpl:KEY#ANCHOR]]`, or a document handle without an
anchor. The tiny `apps/corpus/cmd/agentmatch` command loads the embedded
fixtures and calls `evaluation.MatchProduct`; Python does not duplicate that
matcher. Only cited search identities, returned document metadata, units from
completed read chains and complete search fragments enter scoring. Document
citations earn only document credit. Ancestor paths, complete fragments and
explicit relation hashes retain the product matcher's rules. No child fragment
is invented from read text or from an anchor's spelling. Range leads establish
ancestry only for the first provision and its parts. Later units reuse only
their own previously observed search paths from the same release, or earn direct
identity credit without ancestor credit. These scores measure citations, not the
legal correctness of the answer.

Summed input includes all three Anthropic input fields and every OpenAI input
token over all requests, including cached input. Tool-result size counts the
Unicode code points in the exact text returned to the model, once per call; it
is separate from provider token usage. Full MCP structured JSON is retained in
memory for scoring; the duplicate structured block is not sent to models.

For each model, compact passes against full when mean cited expected provisions
are at least full and mean summed input is strictly lower. Trimmed passes
against compact under the same inequalities. A subset pilot is diagnostic;
promotion needs both models on the full development cohort, two repetitions per
mode, with complete execution. The cohort is reused and small; these results
make no claim about unseen questions.

Provider behavior references:
[Anthropic thinking](https://platform.claude.com/docs/en/build-with-claude/thinking),
[Anthropic cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[OpenAI tool calling](https://developers.openai.com/api/docs/guides/function-calling)
and
[OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## Offline checks

From `apps/agenteval`:

```sh
uv run --frozen --offline pytest
uv run --frozen --offline ruff check .
uv run --frozen --offline ruff format --check .
```

From the repository root:

```sh
go test ./apps/corpus/internal/agentmatch ./apps/corpus/cmd/agentmatch
golangci-lint run ./apps/corpus/internal/agentmatch/... ./apps/corpus/cmd/agentmatch/...
bash scripts/check-lengths.sh
```

Unit tests block socket connections and inject recorded provider and OAuth
responses. `tests/fixtures/matcher_ranges.json` records requests and scores from
the real Go matcher for a range crossing two articles. CI runs the app's tests
and Ruff checks for `apps/agenteval/**` changes. The primary runs
`make pre-push`, the real-corpus dry run and the paid pilot separately. No paid
result is implied by the offline tests.
