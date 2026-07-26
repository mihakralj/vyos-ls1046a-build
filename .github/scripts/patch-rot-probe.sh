#!/usr/bin/env bash
# Probe whether a bucket of unified-diff patches still applies to upstream HEAD.
#
# Usage: patch-rot-probe.sh <repo-url> <branch> <dest-dir> <patch-glob>
#
# This deliberately MIRRORS the real build's apply semantics rather than doing
# an independent per-patch `git apply --check`:
#
#   * cumulative — every patch lands on the tree left by its predecessors, in
#     glob order. These patches stack (024/025/031/034 all edit
#     interfaces_ethernet.xml.in); checking each against a pristine tree would
#     report drift that the build never sees, and miss drift that it does.
#   * `git apply --3way --whitespace=nowarn`, with Mergiraf wired as the merge
#     driver via a dropped .gitattributes — identical to
#     bin/ci-setup-vyos1x.sh / bin/ci-setup-vyos-build.sh.
#   * the same `--reverse --check` idempotency guard the build uses to skip
#     patches upstream has already absorbed.
#
# Three failure classes are detected, and all three are fatal:
#
#   FAIL      git apply returned non-zero — context drift or a corrupt patch
#             (wrong @@ line counts from hand-editing; see vyos-1x-010 in 2026-07
#             and vyos-1x-031/034 in 2026-07-26).
#   CONFLICT  git apply returned ZERO but left conflict markers in the tree.
#             Without Mergiraf this is the normal degradation mode, and it is
#             how a broken patch reaches a shipped .deb looking healthy.
#   MISSING   the glob matched nothing — a bucket silently vanished.
#
# SKIP is reported but not fatal. Treat a NEW skip with suspicion: the
# `--reverse --check` guard false-positives on repetitive context (an XML
# leafNode block whose neighbours share the same closing lines), which silently
# no-ops the patch. vyos-1x-034 hit exactly this and was fixed by regenerating
# it with -U8 so the reverse hunk can no longer match at the wrong offset.

set -euo pipefail

if [ "$#" -ne 4 ]; then
    echo "usage: $0 <repo-url> <branch> <dest-dir> <patch-glob>" >&2
    exit 2
fi

repo_url="$1"
branch="$2"
dest="$3"
glob="$4"

root="$(pwd)"

# shellcheck disable=SC2206  # word-splitting the glob is the point
patches=( $glob )
if [ "${#patches[@]}" -eq 0 ] || [ ! -e "${patches[0]}" ]; then
    echo "::error::patch glob '$glob' matched nothing on this branch"
    exit 1
fi

echo "::group::clone $repo_url ($branch)"
rm -rf "$dest"
# An explicit branch is used rather than the remote default so a future rename
# fails loudly here instead of silently degrading the check — the 2026-05-30
# `current` -> `rolling` rename left this workflow inert for eight weeks.
git clone --branch "$branch" --depth 1 "$repo_url" "$dest"
echo "::endgroup::"

cd "$dest"

cat > .gitattributes <<'GITATTR'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.cc    merge=mergiraf
*.cpp   merge=mergiraf
*.hpp   merge=mergiraf
*.py    merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
GITATTR

rc=0
n_ok=0
n_skip=0
skipped=()

for p in "${patches[@]}"; do
    abs="$root/$p"
    b="$(basename "$p")"

    if git apply --reverse --check --whitespace=nowarn "$abs" >/dev/null 2>&1; then
        echo "SKIP     $b (reverse-applies — already upstream?)"
        n_skip=$((n_skip + 1))
        skipped+=("$b")
        continue
    fi

    if out=$(git apply --3way --whitespace=nowarn "$abs" 2>&1); then
        if git grep -lIE '^<{7} ' -- . >/dev/null 2>&1; then
            echo "CONFLICT $b — applied with conflict markers left in tree"
            echo "::error file=$p::Applied with CONFLICT MARKERS against $repo_url@$branch — refresh patch"
            rc=1
        else
            echo "ok       $b"
            n_ok=$((n_ok + 1))
        fi
    else
        echo "FAIL     $b"
        printf '%s\n' "$out" | sed 's/^/           /'
        # "corrupt patch at line N" means the @@ header line counts disagree
        # with the hunk body — a hand-edit bug, not upstream drift.
        if printf '%s' "$out" | grep -q 'corrupt patch'; then
            echo "::error file=$p::CORRUPT patch (bad @@ hunk line counts — regenerate with git diff, do not hand-edit)"
        else
            echo "::error file=$p::Does not apply against $repo_url@$branch — refresh patch"
        fi
        rc=1
    fi
done

echo
echo "=== $glob vs $repo_url@$branch: ${n_ok} ok, ${n_skip} skipped, $( [ "$rc" -eq 0 ] && echo 0 || echo '>=1' ) failed ==="
if [ "${#skipped[@]}" -gt 0 ]; then
    echo "::notice::Skipped (verify these are genuinely upstream, not false-positive reverse-check matches): ${skipped[*]}"
fi

exit "$rc"
