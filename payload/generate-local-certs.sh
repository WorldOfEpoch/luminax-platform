#!/bin/sh
set -eu
base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cert_dir="$base_dir/certs"
if [ -e "$cert_dir/luminax.key" ] || [ -e "$cert_dir/luminax.crt" ]; then
    echo 'Local certificates already exist; this command never replaces keys.' >&2
    exit 1
fi
command -v openssl >/dev/null 2>&1 || { echo 'OpenSSL is required.' >&2; exit 1; }
mkdir -p "$cert_dir"
umask 077
MSYS2_ARG_CONV_EXCL='/CN=' openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
    -keyout "$cert_dir/luminax.key" -out "$cert_dir/luminax.crt" \
    -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
echo 'Created local-only self-signed certificates. No trust store or service was changed.'
