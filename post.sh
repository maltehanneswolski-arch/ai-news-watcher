#!/usr/bin/env bash
# Post the verdicts the judge just pushed. Called by post.yml, which runs from the "verdicts" branch's
# copy of the workflow; all logic lives here, on main, so changes take effect without touching that branch.
# Needs BEFORE and AFTER (the push's commits), SLACK_BOT_TOKEN and SLACK_CHANNEL.
set -euo pipefail
git fetch -q --depth=20 origin state verdicts
git show origin/state:state.json > state.json
if [ -n "${BEFORE:-}" ] && git cat-file -e "$BEFORE^{commit}" 2>/dev/null; then
  files=$(git diff --name-only --diff-filter=A "$BEFORE" "$AFTER" -- verdicts/)
else
  files=$(git diff-tree -r --no-commit-id --name-only --diff-filter=A "$AFTER" -- verdicts/)
fi
if [ -z "$files" ]; then echo "No new verdicts."; exit 0; fi
mkdir -p new
for f in $files; do git show "$AFTER:$f" > "new/$(basename "$f")"; done
python post_verdicts.py state.json new/*.json
