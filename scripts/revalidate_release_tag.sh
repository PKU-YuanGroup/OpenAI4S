#!/usr/bin/env bash
# Prove a release tag still names the commit this run froze, right now.
#
# A tag is mutable and a platform build takes tens of minutes, so the checkout
# a job holds only proves which source it built — it says nothing about a ref
# rewrite that happened since. Every outward-facing boundary calls this
# immediately before it mutates GitHub, PyPI or a registry.
#
# This lived as three byte-identical inline blocks in release.yml. One copy per
# boundary means a fourth boundary silently gets none, and any hardening — a
# different local ref name, a signature check, a quoting fix — has to land in
# every copy or the boundaries disagree without saying so.
#
# Usage: revalidate_release_tag.sh <tag> <frozen-sha>   (run inside a work tree)
set -euo pipefail

TAG="${1:?usage: revalidate_release_tag.sh <tag> <frozen-sha>}"
SHA="${2:?usage: revalidate_release_tag.sh <tag> <frozen-sha>}"

# A fixed local name, force-updated: whatever a previous step left behind
# cannot be mistaken for what the remote holds now.
LOCAL_REF="refs/tags/openai4s-release-candidate"

git fetch --force --no-tags origin "refs/tags/${TAG}:${LOCAL_REF}"

if [ "$(git cat-file -t "$LOCAL_REF")" != "tag" ]; then
  echo "::error::$TAG is no longer an annotated tag"
  exit 1
fi

TAG_SHA="$(git rev-parse --verify "${LOCAL_REF}^{commit}")"
if [ "$TAG_SHA" != "$SHA" ]; then
  echo "::error::$TAG moved to $TAG_SHA after this run froze $SHA"
  exit 1
fi

echo "$TAG still names $SHA"
