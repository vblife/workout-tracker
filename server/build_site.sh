#!/bin/sh
# Builds the static fallback site (identical HTML to the MCP App resources) into /home/box/workout/site
set -e; D=/home/box/workout/site; mkdir -p $D
cp /home/box/workout/server/ui/hiit.html /home/box/workout/server/ui/charts.html $D/
touch $D/.nojekyll
cat > $D/index.html <<'H'
<!DOCTYPE html><meta charset="utf-8"><title>Workout UI</title><p>Static viewer pages. All data is carried in the URL #fragment and never sent to this server.</p>
H
echo "site built in $D"; ls -la $D
