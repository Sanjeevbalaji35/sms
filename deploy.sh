#!/usr/bin/env bash
# Put SMS on a Linux server with one command:
#
#     sudo ./deploy.sh
#
# The first time it asks for a web address and your admin email and password, installs Docker if needed, and
# starts SMS with HTTPS. Run it again any time to update to the latest version (your settings and data stay).
#
# Got a domain later? Point it at this server, then:  sudo ./deploy.sh domain clips.example.com
#
# For scripts, answer ahead with: SMS_DOMAIN=..., SMS_ADMIN_EMAIL=..., SMS_ADMIN_PASSWORD=... in the environment.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n  %s\n' "$*"; }
fail() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || fail "This is for Linux servers. On your own computer, double-click Start SMS instead."
[ "$(id -u)" -eq 0 ] || fail "Run it as the server's administrator:  sudo ./deploy.sh"

# ---------------------------------------------------------------- 1. Docker
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker (one time, a minute or two)..."
  command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
  curl -fsSL https://get.docker.com | sh >/dev/null
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker compose version >/dev/null 2>&1 || fail "Docker didn't install. Install Docker yourself (docs.docker.com/engine/install), then run this again."

# ---------------------------------------------------------------- 2. settings: asked once, kept in .env
set_env() {   # set_env KEY VALUE: uncomment or replace KEY's line in .env, or add it
  if grep -qE "^#? *$1=" .env; then
    awk -v k="$1" -v v="$2" 'BEGIN{done=0} { if (!done && $0 ~ "^#? *" k "=") { print k "=" v; done=1 } else print }' .env > .env.tmp
    mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
port_busy() { (ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]$1 "; }

valid_domain() { printf '%s' "$1" | grep -qE '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'; }

FIRST=0
if [ "${1:-}" = "domain" ]; then            # switch to a domain, with HTTPS
  [ -f .env ] || fail "Set SMS up first:  sudo ./deploy.sh"
  NEW="$(printf '%s' "${2:-}" | tr 'A-Z' 'a-z' | sed -E 's#^https?://##; s#/.*$##')"
  valid_domain "$NEW" || fail "Give the domain, like:  sudo ./deploy.sh domain clips.example.com"
  set_env SMS_DOMAIN "$NEW"
  set_env SMS_SCHEME https
  set_env SMS_CADDYFILE Caddyfile
  say "Switching SMS to https://$NEW (the padlock certificate is fetched by itself)..."
fi
if [ ! -f .env ]; then
  FIRST=1
  say "Setting up SMS. Three questions, once."
  DOMAIN="${SMS_DOMAIN:-}"
  if [ -z "$DOMAIN" ]; then
    printf '\n  Web address for SMS, like clips.example.com. Point the domain at this server first.\n'
    read -rp "  Leave it empty to use this server's own address without HTTPS: " DOMAIN
  fi
  DOMAIN="$(printf '%s' "$DOMAIN" | tr 'A-Z' 'a-z' | sed -E 's#^https?://##; s#/.*$##')"
  if [ -n "$DOMAIN" ] && ! valid_domain "$DOMAIN"; then
    fail "'$DOMAIN' doesn't look like a web address. Run it again and type one like clips.example.com."
  fi
  EMAIL="${SMS_ADMIN_EMAIL:-}"
  [ -n "$EMAIL" ] || read -rp "  Your email (you'll be the admin): " EMAIL
  PASS="${SMS_ADMIN_PASSWORD:-}"
  while [ ${#PASS} -lt 8 ]; do
    read -rsp "  Choose a password (8 or more characters): " PASS; echo
  done

  cp .env.example .env
  set_env POSTGRES_PASSWORD "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  if [ -n "$DOMAIN" ]; then
    set_env SMS_DOMAIN "$DOMAIN"
    URL="https://$DOMAIN"
  else
    IP="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
    set_env SMS_DOMAIN "$IP"
    set_env SMS_SCHEME http
    set_env SMS_CADDYFILE Caddyfile.http
    URL="http://$IP"
  fi
  if port_busy 80 || port_busy 443; then
    say "Another web server already uses this server's ports 80/443, so SMS listens on 127.0.0.1:8765 only."
    say "Point that web server at it: see deploy/nginx-sms.conf (or the Traefik file in deploy/)."
  else
    set_env COMPOSE_PROFILES own-https
  fi
else
  say "Updating SMS (your settings and data stay as they are)..."
  if [ -d .git ] && command -v git >/dev/null 2>&1; then
    git pull --ff-only -q || say "Couldn't get the newest version (this folder has its own changes?). Restarting this one."
  fi
  DOMAIN="$(grep -E '^SMS_DOMAIN=' .env | cut -d= -f2-)"
  SCHEME="$(grep -E '^SMS_SCHEME=' .env | cut -d= -f2- || true)"
  URL="${SCHEME:-https}://$DOMAIN"
fi

# ---------------------------------------------------------------- 3. build and start
say "Building and starting SMS. The first time takes a few minutes..."
docker compose up -d --build

say "Waiting for SMS to answer..."
for _ in $(seq 1 150); do
  if docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 2
done
[ "${READY:-0}" = 1 ] || fail "SMS didn't start. See why with:  docker compose logs app --tail 50"

# ---------------------------------------------------------------- 4. your admin account, the first time
if [ "$FIRST" = 1 ]; then
  printf '%s\n' "$PASS" | docker compose exec -T app python -m studio.admin create-admin "$EMAIL" >/dev/null \
    || fail "Couldn't make the admin account. Make it later with:  docker compose exec app python -m studio.admin create-admin $EMAIL"
fi

say "SMS is running at $URL"
[ "$FIRST" = 1 ] && say "Sign in with $EMAIL, then add your AI key in Settings, AI."
case "$URL" in http://*) say "No HTTPS yet. When you have a domain, point it at this server and run:  sudo ./deploy.sh domain your-domain.com" ;; esac
say "Update any time with:  sudo ./deploy.sh"
echo
