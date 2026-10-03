#!/usr/bin/env bash
# Fetch the pinned firstmate source for the two-home contract test.
#
# Usage: scripts/fetch-firstmate.sh <dest-dir> [<sha>]
#
# Shallow-fetches exactly one commit of the captain's firstmate fork (no history, no
# vendoring). The SHA defaults to tests/firstmate_contract/FIRSTMATE_PIN; pass another
# to try a candidate before bumping the pin. See docs/firstmate-contract-test.md.
set -euo pipefail

REPO_URL="${FIRSTMATE_REPO_URL:-https://github.com/yehezkieled/firstmate}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${1:?usage: fetch-firstmate.sh <dest-dir> [<sha>]}"
sha="${2:-$(tr -d '[:space:]' < "$ROOT/tests/firstmate_contract/FIRSTMATE_PIN")}"
case "$sha" in
  *[!0-9a-f]* | '') echo "fetch-firstmate: '$sha' is not a full commit SHA" >&2; exit 2 ;;
esac
[ "${#sha}" -eq 40 ] || { echo "fetch-firstmate: '$sha' is not a full 40-char SHA" >&2; exit 2; }

rm -rf "$dest"
git init -q "$dest"
git -C "$dest" fetch -q --depth 1 "$REPO_URL" "$sha"
git -C "$dest" -c advice.detachedHead=false checkout -q FETCH_HEAD
got="$(git -C "$dest" rev-parse HEAD)"
[ "$got" = "$sha" ] || { echo "fetch-firstmate: fetched $got, wanted $sha" >&2; exit 1; }
echo "firstmate $got -> $dest"
