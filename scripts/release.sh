#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
    echo "Commit or stash all changes before releasing." >&2
    exit 1
fi

echo "Building release..."
./scripts/build.sh

echo "Commit release..."
git add .
git commit --message "chore: release latest"
git push origin HEAD

echo "Publishing release..."
git tag --force --message "Release latest" latest HEAD
git push --no-follow-tags origin +refs/tags/latest:refs/tags/latest
