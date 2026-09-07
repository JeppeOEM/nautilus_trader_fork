#!/usr/bin/env bash
# Blocks docker-compose `ports:` entries that publish to all interfaces
# (0.0.0.0) instead of binding to localhost.
#
# Why: Docker inserts its own iptables ACCEPT rules ahead of ufw's chain, so
# a bare "8080:8080" (or short "8080") mapping is reachable from the public
# internet even with ufw configured to deny it -- ufw never sees the
# connection. See troll/CLAUDE.md's SEC-01.
#
# Fix: bind explicitly, e.g. "127.0.0.1:8080:8080". A service that only
# needs to be reached by other containers should use `expose:` instead of
# `ports:`, or no ports/expose entry at all.
#
# No escape hatch: every published port must be localhost-only, no
# exceptions. Remote access goes through an SSH tunnel, never a public port.

set -euo pipefail

RED='\033[0;31m'
NC='\033[0m'
VIOLATIONS=0

mapfile -t COMPOSE_FILES < <(git diff --cached --name-only --diff-filter=ACM -- \
  '*docker-compose*.yml' '*docker-compose*.yaml' 'compose*.yml' 'compose*.yaml' 2> /dev/null || true)

[[ ${#COMPOSE_FILES[@]} -eq 0 ]] && exit 0

for file in "${COMPOSE_FILES[@]}"; do
  [[ -f "$file" ]] || continue

  while IFS=: read -r line_num content; do
    [[ -z "$line_num" ]] && continue
    echo -e "${RED}Error (unbound port):${NC} $file:$line_num"
    echo "  Found: ${content#"${content%%[![:space:]]*}"}"
    echo "  Hint:  Bind to localhost, e.g. \"127.0.0.1:HOST:CONTAINER\", or use"
    echo "         expose: if only other containers need it. No exceptions --"
    echo "         use an SSH tunnel for remote access, never a public port."
    echo
    VIOLATIONS=$((VIOLATIONS + 1))
  done < <(awk '
    function indent(s,   t) { t = s; sub(/[^ \t].*/, "", t); return length(t) }
    {
      line = $0
      if (line ~ /^[ \t]*ports:[ \t]*(#.*)?$/) {
        in_ports = 1
        ports_indent = indent(line)
        next
      }
      if (in_ports) {
        ind = indent(line)
        if (line ~ /^[ \t]*-/ && ind > ports_indent) {
          item = line
          sub(/^[ \t]*-[ \t]*/, "", item)
          gsub(/["'"'"']/, "", item)
          sub(/[ \t]*#.*/, "", item)
          if (item ~ /^[0-9]/ && item !~ /^127\.0\.0\.1:/ && item !~ /^localhost:/) {
            print FNR ":" line
          }
        } else if (ind <= ports_indent && line !~ /^[ \t]*$/) {
          in_ports = 0
        }
      }
    }
  ' "$file")
done

if [ $VIOLATIONS -gt 0 ]; then
  echo -e "${RED}Found $VIOLATIONS unbound docker port mapping(s)${NC}"
  exit 1
fi

echo "✓ All docker-compose ports are bound to localhost"
exit 0
