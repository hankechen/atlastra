#!/usr/bin/env bash
# Is prod running what's in git? Read-only, no side effects -- safe to run
# any time, e.g. at the start of a bug-hunting session.
#
# Usage: ./check_deploy.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

HOST="ubuntu@16.59.15.84"
KEY="$HOME/.ssh/atlastra-ec2.pem"
REMOTE_DIR="/opt/atlastra"

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_INFO=$(ssh -i "$KEY" "$HOST" "cat $REMOTE_DIR/.deployed_sha 2>/dev/null" || true)

if [[ -z "$REMOTE_INFO" ]]; then
  echo "No .deployed_sha found on prod -- it predates deploy.sh, or nothing has"
  echo "been deployed through it yet. Run ./deploy.sh to establish a baseline."
  exit 1
fi

REMOTE_SHA=$(echo "$REMOTE_INFO" | sed -n 1p)
REMOTE_AT=$(echo "$REMOTE_INFO" | sed -n 2p)

echo "Local HEAD:    $(git rev-parse --short "$LOCAL_SHA") ($(git log -1 --format=%cd --date=relative "$LOCAL_SHA"))"
echo "Prod deployed: $(git rev-parse --short "$REMOTE_SHA" 2>/dev/null || echo "$REMOTE_SHA") (recorded $REMOTE_AT)"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  echo ""
  echo "✓ Prod is current."
  exit 0
fi

echo ""
if git merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA" 2>/dev/null; then
  echo "✗ Prod is behind. Pending commits:"
  git log --oneline "$REMOTE_SHA..$LOCAL_SHA"
  echo ""
  echo "Run ./deploy.sh to catch it up."
else
  echo "⚠ Prod's SHA ($REMOTE_SHA) isn't an ancestor of local HEAD -- either"
  echo "  local history was rewritten, or prod was deployed from a branch/"
  echo "  commit that's no longer reachable from here. Investigate before"
  echo "  deploying over it blindly."
fi
exit 1
