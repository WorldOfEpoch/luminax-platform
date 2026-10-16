#!/bin/sh
set -eu
source_dir=${LUMINAX_CONFIG_SEED_DIR:-/seed}
target_dir=${LUMINAX_CONFIG_TARGET_DIR:-/target}
if [ ! -d "$source_dir" ] || [ ! -d "$target_dir" ]; then
    echo '[config-init] Source and target directories must already exist.' >&2
    exit 1
fi
if [ -f "$target_dir/.initialized" ]; then
    echo '[config-init] Existing runtime configuration retained.'
    exit 0
fi
if [ -n "$(find "$target_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo '[config-init] Refusing to overwrite a nonempty configuration volume.' >&2
    exit 1
fi
cp -R "$source_dir/." "$target_dir/"
touch "$target_dir/.initialized"
echo '[config-init] Public samples copied once into the runtime configuration volume.'
