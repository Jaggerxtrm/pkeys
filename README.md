# pkeys

Provider key and usage dashboard for pi.

Answers four questions without leaving the terminal: which providers are
configured, are their keys alive, what have they consumed this month, and what
does it cost. Nothing is fetched from the network unless you ask for it with
`--live`.

```
$ pkeys
PROVIDER       AUTH             KEY SOURCE                   MODELS  SUB                      TOKENS  COST    LIMITS                 KEY
claude         active, 5h left  ~/.claude/.credentials.json  -       active, peak 22%         -       -       quota api              2217cc8e
commandcode *  active (1h ago)  models.json (conflict)       10      active, peak 12% +1 key  2B      $67.18  quota api              f6371afe
nano-gpt       active (1h ago)  models.json (conflict)       12      inactive (past_due)      2.3M    $0.06   quota + usage api      e4d1b425
nvidia         active (1h ago)  models.json                  4       no api                   4.5M    $0      none                   01e8d39e
openai-codex   expired 23d ago  auth.json (oauth)            8       no api                   0       $0      none                   -
opencode-go    active (1h ago)  auth.json                    28      active, peak 74% +1 key  9.7B    $80.03  quota api              17653b1c
```

## Install

```sh
git clone https://github.com/Jaggerxtrm/pkeys.git ~/dev/pkeys
ln -sf ~/dev/pkeys/pkeys ~/.local/bin/pkeys
```

Requires Python 3.10+ and nothing else. No dependencies, no virtualenv, no
build step: it is one executable file you can read in an afternoon.

## Where the numbers come from

| Data | Source |
| --- | --- |
| Providers, models, prices, context windows | `~/.pi/agent/models.json`, `~/.pi/agent/models-store.json` |
| Credentials, OAuth expiry | `~/.pi/agent/auth.json` |
| Default provider and model | `~/.pi/agent/settings.json` |
| Rotation candidates | `~/.secrets/provider-keys/<provider>-<label>.md` |
| Tokens and cost per model | pi session logs under `~/.pi/agent/sessions/` |

Local usage is parsed incrementally and cached in
`~/.secrets/provider-keys/.pkeys-cache.json`, keyed on each file's mtime and
size, so the first run takes a few seconds and later runs take about a second.

Keys are never printed. Every key is shown as the first eight characters of its
SHA-256, which is enough to tell two keys apart and useless to anyone who sees
your screen.

## Commands

```
pkeys [status]              overview: auth, subscription, tokens, cost per provider
pkeys limits [provider]     subscription state, quota windows, balance
pkeys usage [provider]      tokens per provider and model for a month
pkeys models [provider]     configured models, prices, and their usage
pkeys doctor                config health: bad JSON, duplicate keys, expired tokens
pkeys check [provider]      validate keys against the provider (--live)
pkeys rotate --provider X   swap to another key from the key store
pkeys help [command]        help for pkeys or one command
```

## Flags

```
--json            machine-readable output on stdout
--plain           one record per line, tab separated, no padding or colour
--no-color        never colourise (also honours NO_COLOR)
--month YYYY-MM   month to report (default: current UTC month)
--live            make network calls: validate keys, fetch provider usage
-q, --quiet       suppress progress messages on stderr
-h, --help        help for any command
```

Data goes to stdout, messages to stderr, so `pkeys --json | jq` and
`pkeys --plain | awk` both behave.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | fine |
| 1 | unexpected error |
| 2 | you typed something wrong |
| 3 | config problem (invalid JSON, missing key, broken default) |
| 4 | provider rejected a key, or the network failed |

`pkeys doctor` returns 3 on errors and 0 on warnings, so it can gate a script:

```sh
pkeys doctor -q || echo "fix your provider config before running an agent"
```

## Subscription limits, per provider

There *is* an API for this on most of your providers. `pkeys limits` reads them.

| Provider | Endpoint | What comes back |
| --- | --- | --- |
| `opencode-go` | `GET /inference/go/v1/usage` | rolling 5h, weekly, monthly — `status`, `percent`, `resetsAt` |
| `nano-gpt` | `GET /api/v1/subscription/usage` | `active`, `state`, `providerStatus`, `limits`, daily/weekly token quotas with used/remaining/percent/resetAt, `routing` advice |
| `nano-gpt` | `POST /api/check-balance` | `usd_balance` (`x-api-key` auth, not Bearer) |
| `zai` | `GET /api/monitor/usage/quota/limit` | GLM Coding Plan windows — returns "no coding plan" for a plain API key |
| `commandcode` | `GET /alpha/billing/credits` | plan credit pool plus the 5-hour and weekly windows, already reduced to `used` / `cap` / `resetAt` by the provider |
| `commandcode` | `GET /alpha/billing/subscriptions` | `status`, `planId` (`individual-goat`), current period start/end, `cancelAtPeriodEnd` |
| `claude` | `GET /api/oauth/usage` | subscription `five_hour` / `seven_day` utilisation and reset times, `extra_usage` (needs `anthropic-beta: oauth-2025-04-20`) |
| `nvidia`, `typesafe`, `openai-codex` | — | no public per-account quota route |

`pkeys limits` without `--live` prints the last fetched values with their age;
with `--live` it refreshes. The `SUB` column in `pkeys status` comes from the
same cache, so nothing is fetched unless you ask.

nano-gpt tells us what it thinks of your subscription right now: `inactive`,
`providerStatus: past_due`, `routing.recommendedMode: paygo`. That is the
provider's own verdict, not something inferred from token counts.

The commandcode route is undocumented but public: it is what `cmd`'s own
`/usage` command calls, and the pi key authenticates it directly, so the CLI
does not have to be installed. Both windows are paced by the provider, so
`used` is the real credit value spent in the open window — there is no
arithmetic over request logs on our side, and a `resetAt` of 0 (window never
opened) is shown as no reset rather than as 1970. Caps come from the API; the
documented plan table only fills in a cap of 0, for a plan the API does not
model.

The Studio Usage page (`/alpha/usage/summary`) is a *different* ledger: it
counts CLI-harness requests, and a Provider-API request from pi moves the
credit windows without appearing in the summary. Read the windows, not the
summary, when you want to know how much of a window is gone.

Weakness worth knowing: nano-gpt input-token quotas only appear when a
subscription configures them. A null quota means "not configured", not "zero",
so the dashboard prints `-` rather than inventing a limit.

For nano-gpt you can also create a *Usage only* management token (Settings →
Security → Management API tokens) and read
`GET /api/management/v1/subscription/usage` instead, which cannot spend your
balance. That would need a key file named `nano-gpt-<label>.md` plus a small
manual edit to point the adapter at the management path.

## More than one key for the same provider

Two keys are two quotas. `pkeys limits` asks every key you have, not just the
one pi is spending, and prints them lane by lane with the live key starred:

```
$ pkeys limits commandcode --live
commandcode (default)  just now  2 keys
  * dawid-jgg ce3fdb46  just now
      rolling 5h    ....................   0.0%   $0.0000 of $14.00   resets in 1h
      weekly        ....................   0.0%   $0.0000 of $35.00   resets in 6d
    jagger-xtrm 97d8e48c  just now
      rolling 5h    #...................   1.5%   $0.21 of $14.00   resets in 2h
      weekly        #########...........  45.9%   $16.07 of $35.00   resets in 3d
```

When the live key is not the roomiest one, the command names the key that is
and the command that would switch to it. `pkeys status` marks the provider with
`+1 key` so a spare is never invisible.

Selection ranks keys by their *tightest* window (a key with 90% of its month
left but 100% of its 5-hour window has no headroom right now):

```sh
pkeys limits --live                              # refresh every key's windows
pkeys rotate --provider opencode-go --list       # each key with its tightest window
pkeys rotate --provider opencode-go --best       # preview switching to the roomiest
pkeys rotate --provider opencode-go --best --apply
```

`--best` refuses to guess: with no cached limits it tells you to run
`pkeys limits --live` first. If the live key is already the best one, rotation
has nothing to do and says so.

Key file names are matched against the longest provider name they start with,
so `opencode-go-dawid-jgg.md` belongs to `opencode-go` (not to an `opencode`
provider that does not exist).

## Claude subscriptions

Claude plan usage lives in the Claude Code CLI, not in pi, so it is read from
the CLI's own credentials: `~/.claude/.credentials.json`, plus one
`~/.claude-<label>/` directory per extra account (that is what
`CLAUDE_CONFIG_DIR` points at). Each account becomes its own provider with its
own 5-hour and weekly windows, because two subscriptions are two separate
limits, not two keys of one.

The numbers come from Anthropic's own utilisation endpoint — the same figures
the CLI's `/usage` shows — so nothing is inferred from transcripts. The token is
never refreshed by pkeys: rotating an OAuth token would break the copy the CLI
is holding. An expired token is reported as such, with the fix (run `claude`
once).

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

Eight tests, no network, no keys needed: key-file name matching, lane
construction, headroom ranking, and a cached multi-key render. Everything they
touch is loaded from a temporary `PKEYS_HOME`.

## Rate-limit headers

Checked, and absent on error responses: neither `commandcode` nor `nano-gpt` nor
`opencode-go` returns `x-ratelimit-*` or credit headers on a request.
Quota has to come from the dedicated endpoints above, not from response headers.

## Rotation

See [ROTATION.md](ROTATION.md). Short version: drop keys in
`~/.secrets/provider-keys/<provider>-<label>.md`, then

```sh
pkeys rotate --provider commandcode            # preview, writes nothing
pkeys rotate --provider commandcode --apply    # swap, with backups and an audit line
pkeys rotate --provider commandcode --best     # pick the key with the most headroom
```
