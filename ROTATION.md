# Key rotation — draft

Status: **manual, on demand.** `pkeys rotate` works today. Nothing watches
anything, nothing calls a provider without `--live`, and no daemon exists.

That is deliberate. Rotation rewrites `auth.json` and `models.json`, so the
safe default is a human in the loop until the trigger conditions below have
been observed on real failures.

## The key store

```
~/.secrets/provider-keys/
├── commandcode-jagger-xtrm.md      # user_…
├── commandcode-dawid-jgg.md        # user_…
├── .pkeys-cache.json               # usage parse cache (no keys)
├── .pkeys-state.json               # last live check per provider (no keys)
└── .rotation-log.jsonl             # append-only audit trail
```

One key per file, named `<provider>-<label>.<md|txt|key>`. The file name is the
identity: the provider is the prefix before the first `-`, the label is
everything after it. Content is the bare key, nothing else.

```sh
printf '%s' 'sk-...' > ~/.secrets/provider-keys/commandcode-<label>.md
chmod 600 ~/.secrets/provider-keys/commandcode-<label>.md
```

`chmod 600` matters: `pkeys doctor` warns on anything group- or world-readable,
because these files sit next to keys for every provider on the machine.

A new provider needs no code change. Drop a file, and `pkeys status` lists it.

## Selecting the next key

Default is round-robin over the provider's key files:

1. Find the file whose value equals the key currently in config.
2. Pick the next file in name order, wrapping.
3. If the live key matches no file, pick the first and say so — an unmanaged
   live key cannot be rotated back to, which is exactly the kind of thing that
   should be printed, not guessed at.

Override with `--key-file <name>` when you want a specific key:

```sh
pkeys rotate --provider commandcode --key-file commandcode-dawid-jgg.md --apply
```

## What `--apply` does

1. Copies `auth.json` and `models.json` to `<file>.bak-pkeys-<UTC timestamp>`.
2. Writes the new key into **every** slot holding the old one:
   `auth.json[<provider>].key`, `auth.json.providers[<provider>].apiKey`,
   `models.json.providers[<provider>].apiKey`. This matters because duplicated
   keys are common here — `pkeys doctor` currently reports conflicts for both
   `commandcode` and `nano-gpt` — and rotating one copy while leaving another
   stale produces the worst failure mode: a working-looking config that 401s.
3. Clears the cached validation result for that provider, so the next
   `pkeys status` cannot show a stale "active".
4. Appends one JSON line to `.rotation-log.jsonl` with from/to fingerprints,
   the key file used, which files changed, the reason, and backup paths.
5. Prints what changed. Nothing is silent.

Every write goes through a temp file plus `os.replace`, so an interrupted
rotation leaves either the old config or the new one, never half of each.

Nothing rotates until you pass `--apply`. `pkeys rotate --provider X` alone is a
dry run and is safe to run constantly.

## Automatic rotation — the design, not yet enabled

Rotation should eventually be triggered by an auth failure, not by a clock.
A clock rotates working keys and invalidates the audit trail's meaning.

Proposed trigger set, in priority order:

| Signal | Action |
| --- | --- |
| HTTP 401 / 403 from a provider | rotate that provider immediately |
| HTTP 402, or usage endpoint reports exhaustion | rotate, and record the exhausted key as quarantined |
| HTTP 429 with `Retry-After` beyond a threshold | rotate only if the key is also flagged |
| `pkeys check --live` reports `invalid` | rotate on the next explicit run, not by itself |
| OAuth `expires` within 24h | do not rotate; re-authenticate (a key swap cannot fix OAuth) |

Guard rails any future automation must keep:

- **One rotation per provider per cooldown window** (suggest 10 minutes). A
  broken key file, or a provider that 401s on everything, must not burn through
  the entire store in a loop.
- **Stop after one full cycle.** If every candidate has been tried and all fail,
  abort and report — do not wrap.
- **Never rotate on timeout or 5xx.** Those are network and provider faults, not
  credential faults, and rotating on them destroys a good key for nothing.
- **Keep the audit line.** Every automatic rotation logs the same shape as a
  manual one, with `"reason": "auto:401"`.
- **Backups are already there.** Reverting is `cp auth.json.bak-pkeys-<stamp>
  auth.json`, but the un-rotated key must still be in the store for that to be
  reversible — hence the "live key is not in the store" warning.

Where it would live: a `pkeys rotate --on-failure` mode invoked by the caller
that saw the 401 (a wrapper, a CI step, an agent runtime), not a background
daemon. Keeps the "nothing happens unless you run something" property that the
dashboard already has.

## Verifying after a rotation

```sh
pkeys check --provider commandcode --live   # the new key works?
pkeys status --live                         # auth column reflects reality, not cache
pkeys doctor                                # no conflicts, no permissive modes
```

If `check` says `failed`: restore the backup printed by the rotation, and
inspect the key file — a trailing newline, a truncated paste, or an expired key
all look identical from the HTTP side.

## Current state of your store

At the time of writing:

- `commandcode` has two keys, neither matching the live key in config. The live
  key is unmanaged — it can be replaced but not selected again.
- `commandcode` exposes no limits API, so an automatic rotation cannot consult
  remaining credits before swapping: react to 401/402/429, never to a quota
  reading. `opencode-go`, `nano-gpt` and `zai` do report quotas
  (`pkeys limits --live`), which makes them safe to rotate on a *throttle*
  signal rather than only on a hard auth failure.
- `commandcode` and `nano-gpt` each have their key defined twice in config with
  different values (`models.json` wins over `auth.json`).
- Key files are mode `664`, group/world readable.
- `openai-codex` OAuth token expired 23 days ago.

`pkeys doctor` reports all of the above.
