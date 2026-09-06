# Secrets

This directory holds the deployment's secrets, one value per file. It is what
`docker-compose.yml`'s top-level `secrets:` block points at; compose mounts each
file into the containers that declare it, at `/run/secrets/<name>`, and the
server reads it through the matching `<NAME>_FILE` environment variable.

Nothing in here is tracked except this README — `.gitignore` carries
`/secrets/*` with a `!/secrets/README.md` negation, and
`server/tests/test_repo_hygiene.py` fails the build if that ever stops being
true. Everything here is also excluded from the Tandem backups: the backup
archive holds the database and the covers, not the keys that decrypt it.

Why files instead of `environment:`: anything in a container's environment is
printed by `docker inspect`, sits in `/proc/1/environ`, and is readable by
every member of the `docker` group and by anything that reaches code execution
inside the container. A mounted file is readable by the container's user and
nobody else.

## The four files

| File | Read by | What it is |
|---|---|---|
| `jwt_secret_key` | `server` (`JWT_SECRET_KEY_FILE`) | Signs access, refresh and media tokens. Changing it signs every session out on every device |
| `postgres_password` | `server` and `db` (`POSTGRES_PASSWORD_FILE`) | The `booksync` role's password. One file for both containers, so they cannot drift apart. The server assembles `DATABASE_URL` from it |
| `credential_enc_keys` | `server` (`CREDENTIAL_ENC_KEYS_FILE`) | Comma-separated Fernet keys encrypting import-source credentials (the Audible auth blob, the Audiobookshelf API token). The first encrypts, all are tried for decryption, so rotation is "prepend a new key". **Losing these means those stored credentials are unrecoverable** |
| `database_url` | `server` (`DATABASE_URL_FILE`) | Optional, and not created by default. A complete connection URL, for an external Postgres whose URL differs from the assembled one. Both it and its `secrets:` entries ship commented out |

## Creating them

Run these once, from this directory, before the first `docker compose up`.
`umask 077` is what makes each file readable only by you — set it in the same
shell, before the redirects.

```bash
cd secrets
umask 077

python3 -c "import secrets; print(secrets.token_urlsafe(64))" > jwt_secret_key
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > postgres_password
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > credential_enc_keys

ls -l   # → -rw------- for all three
```

Each command writes one line with a trailing newline; the server strips exactly
that and nothing else, so there is no need to avoid it.

No `cryptography` on the host? Generate the Fernet key inside the server image
instead — it is a dependency there:

```bash
umask 077
docker compose run --rm --no-deps --entrypoint python server \
  -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
  > credential_enc_keys
```

## Migrating an existing install

Do **not** generate new values for an install that is already running. Copy the
existing ones out of your `.env` / `docker-compose.yml` instead: a new
`credential_enc_keys` orphans every stored import-source credential, and a new
`jwt_secret_key` signs every device out.

The step-by-step runbook — including how to copy the values without printing
them, how to verify afterwards, and how to roll back — is in
[`docs/operations.md`](../docs/operations.md#secrets).

## Rotating one

Replace the file's contents and recreate the containers that read it
(`docker compose up -d --force-recreate server`). Two of the three have
consequences beyond a restart:

- **`postgres_password`** is only applied by Postgres on the *first* init of the
  data volume. Changing the file alone leaves the server unable to authenticate;
  change the password in the database first — see
  [`docs/operations.md`](../docs/operations.md#rotating-the-postgres-password).
- **`credential_enc_keys`** rotates by *prepending*: put the new key first and
  keep every old key in the list, comma-separated, until you are certain no
  stored credential still needs it.

`jwt_secret_key` can be replaced freely; the only effect is that everyone signs
in again.
