# Backup and restore PostgreSQL connection contract

`backup_database.py` and `restore_database.py` use PostgreSQL command-line
clients, not SQLAlchemy sessions. Their credentials and connection options
must match the application's configured `DATABASE_URL`.

The shared `scripts/postgres_cli_connection.py` adapter decodes URL-escaped
usernames/passwords and passes connection options through libpq environment
variables. Passwords are never included in process arguments. TLS parameters
such as `sslmode=verify-full` and `sslrootcert` are propagated instead of
silently being dropped. Unsupported query parameters fail before invoking
`pg_dump` or `pg_restore`; add support deliberately rather than ignoring a
new connection requirement.

The backup tool still validates the archive with `pg_restore --list` before
publishing the `.dump` file. Restore still rejects a target matching the
source database named by `DATABASE_URL`.

Operational checks before relying on backups:

1. Confirm the database URL and referenced client certificates/CA files are
   accessible inside the backup/restore container.
2. Run a backup and verify its exit status, artifact size, and archive listing.
3. Restore into a separate disposable database and verify expected data.
4. Test the procedure with production-equivalent TLS and credential encoding.
5. Retain backups in independently secured storage; local Docker volumes alone
   are not a disaster recovery strategy.

This change improves connection fidelity. It is not a certification of backup
scheduling, retention, off-host durability, or disaster recovery readiness.
