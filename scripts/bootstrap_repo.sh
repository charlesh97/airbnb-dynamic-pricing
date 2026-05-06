#!/usr/bin/env bash
# Bootstrap script — create the GitHub repo and push initial structure
# Usage: bash scripts/bootstrap_repo.sh

set -e

REPO_NAME="igms-dynamic-pricing"
ORG="charlesh97"
TARGET_DIR="/Users/charlesclaw/documents/git/igms-dynamic-pricing"
GIT_TOKEN="${GITHUB_TOKEN}"

cd "$TARGET_DIR"

# Check if remote already set
if git remote get-url origin &>/dev/null; then
    echo "Remote already configured — skipping repo creation"
else
    echo "Creating GitHub repo: $ORG/$REPO_NAME"
    RESPONSE=$(curl -s -X POST \
        -H "Authorization: token $GIT_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        https://api.github.com/user/repos \
        -d "{\"name\":\"$REPO_NAME\",\"private\":true,\"description\":\"Black-box dynamic pricing engine for iGMS short-term rental properties\"}" \
    )
    echo "GitHub API response: $RESPONSE"

    REPO_URL="https://$ORG:$GIT_TOKEN@github.com/$ORG/$REPO_NAME.git"
    git remote add origin "$REPO_URL"
    echo "Remote added."
fi

# Stage all files
git add .

# Show what would be committed
echo ""
echo "=== Staged files ==="
git status

echo ""
echo "=== Commit message ==="
echo "Initial commit: igms-dynamic-pricing engine"
echo ""
echo "Run: git commit -m 'Initial commit: igms-dynamic-pricing engine'"
echo "Then: git push origin main"
