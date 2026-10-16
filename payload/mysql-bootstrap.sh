#!/bin/sh
set -eu
: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be generated locally}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD must be generated locally}"
case "$MYSQL_USER" in ''|*[!a-zA-Z0-9_]*) echo 'Unsupported database username.' >&2; exit 1;; esac
case "$MYSQL_PASSWORD" in ''|*[!a-zA-Z0-9_-]*) echo 'Use a generated URL-safe database password.' >&2; exit 1;; esac
export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
mysql -uroot <<EOF
CREATE DATABASE IF NOT EXISTS luminax CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS underfoot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT ALL PRIVILEGES ON luminax.* TO '${MYSQL_USER}'@'%';
GRANT ALL PRIVILEGES ON underfoot.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
EOF
unset MYSQL_PWD
