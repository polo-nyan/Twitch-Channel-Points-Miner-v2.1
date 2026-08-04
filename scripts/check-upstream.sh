#!/usr/bin/env bash
#
# check-upstream.sh — read-only "is there anything worth pulling?" nudge.
#
# This fork (polo-nyan/Twitch-Channel-Points-Miner-v2.1) intentionally has NO
# `upstream` git remote. Keeping one caused `gh pr create` to default its base to
# the upstream repo and open an accidental PR there. Instead, this script fetches
# the upstream branch *by URL* into a private ref, compares it against our current
# branch using patch-id equivalence (so fixes we already cherry-picked are
# filtered out), and prints the commits that are actually worth pulling.
#
# HARD RULE — this script is READ-ONLY with respect to upstream:
#   * it only ever `git fetch`es from the upstream URL,
#   * it never adds a remote, never pushes, never opens a PR anywhere,
#   * you integrate changes by CHERRY-PICKING into a branch on OUR fork, never by
#     merging the whole tree (this fork has diverged heavily — Telemetry,
#     RateLimiter, dashboard, config editor — a full merge would clobber them).
#
# Usage:
#   scripts/check-upstream.sh            # human-readable report
#   scripts/check-upstream.sh --porcelain  # machine-readable: "<sha> <subject>" per line
#
set -euo pipefail

UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2.git}"
UPSTREAM_BRANCH="${UPSTREAM_BRANCH:-master}"
WATCH_REF="refs/upstream-watch/${UPSTREAM_BRANCH}"
# Files whose upstream changes keep the miner working against Twitch's rotating
# GQL API — these are the high-signal ones to pull promptly.
KEY_FILES_RE='TwitchChannelPointsMiner/constants\.py|TwitchChannelPointsMiner/classes/Twitch\.py'

PORCELAIN=0
[[ "${1:-}" == "--porcelain" ]] && PORCELAIN=1

cd "$(git rev-parse --show-toplevel)"

# Read-only fetch of the upstream branch into our private watch ref.
git fetch --quiet --no-tags "$UPSTREAM_URL" "${UPSTREAM_BRANCH}:${WATCH_REF}"

# Commits present upstream but with NO patch-equivalent on our current HEAD.
# `git cherry <ours> <theirs>` prints "+ <sha>" for unapplied, "- <sha>" for
# already-applied (by patch-id) — exactly what a cherry-picking fork wants.
mapfile -t UNAPPLIED < <(git cherry HEAD "$WATCH_REF" | awk '$1=="+"{print $2}')

if [[ ${#UNAPPLIED[@]} -eq 0 ]]; then
  [[ $PORCELAIN -eq 1 ]] || echo "up to date with upstream ($UPSTREAM_URL @ $UPSTREAM_BRANCH) — nothing to pull."
  exit 0
fi

if [[ $PORCELAIN -eq 1 ]]; then
  for sha in "${UNAPPLIED[@]}"; do
    printf '%s %s\n' "$sha" "$(git log -1 --format='%s' "$sha")"
  done
  exit 0
fi

echo "Upstream has ${#UNAPPLIED[@]} commit(s) with no patch-equivalent in this fork"
echo "  ($UPSTREAM_URL @ $UPSTREAM_BRANCH)"
echo "  Note: fixes landed here via a squashed commit or a hand edit can't be matched"
echo "  by patch-id and may still show below — eyeball the file before re-applying."
echo
key_hits=0
for sha in "${UNAPPLIED[@]}"; do
  subj="$(git log -1 --format='%s' "$sha")"
  files="$(git diff-tree --no-commit-id --name-only -r "$sha")"
  if grep -qE "$KEY_FILES_RE" <<<"$files"; then
    printf '  \xe2\x9a\xa1 %s  %s   (touches GQL/Twitch API code — pull promptly)\n' "${sha:0:9}" "$subj"
    key_hits=$((key_hits + 1))
  else
    printf '     %s  %s\n' "${sha:0:9}" "$subj"
  fi
done
echo
echo "To pull a fix, CHERRY-PICK it onto a branch on OUR fork (never merge, never PR upstream):"
echo "    git fetch $UPSTREAM_URL $UPSTREAM_BRANCH"
echo "    git checkout -b sync/upstream-fixes origin/master"
echo "    git cherry-pick <sha>        # or apply the change by hand"
echo "    # then: open a PR against polo-nyan/Twitch-Channel-Points-Miner-v2.1 (base master)"
[[ $key_hits -gt 0 ]] && echo && echo "($key_hits high-signal API-keepalive commit(s) above.)"
exit 0
