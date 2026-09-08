REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
TROLL_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(TROLL_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: build-base up redeploy up-live-paper build-insecure test test-live-paper down logs logs-dash web enable-boot enable-firewall harden-ssh remote-tui remote-web remote-web-stop remote-db remote-db-stop

REMOTE_HOST := nifelheim
REMOTE_DIR  := ~/nautilus_trader_fork/troll

## Build the base nautilus image (~15 min, only needed once or after core changes)
## On networks with TLS inspection (e.g. Zscaler), export your root CA to
## ~/.docker/corporate-ca.pem and it will be injected automatically.
build-base:
	@CA_SECRET=""; \
	if [ -f "$(HOME)/.docker/corporate-ca.pem" ]; then \
		CA_SECRET="--secret id=ca-cert,src=$(HOME)/.docker/corporate-ca.pem"; \
	fi; \
	docker build $$CA_SECRET --network=host \
		-f $(REPO_ROOT)/.docker/nautilus_trader.dockerfile \
		--target application \
		-t $(BASE_IMAGE) \
		$(REPO_ROOT)

## Start collecting data, rebuilding every default-profile service (collector included)
## first (seconds -- it's just the thin troll/ layer on top of the base image). Run
## `make build-base` once beforehand for the base image itself. Use this only for first
## bring-up or when dydx_collector/ itself changed -- otherwise use `make redeploy` so
## the collector's live WS connection/book state is never disturbed.
up:
	$(COMPOSE) up -d --build
	@echo "Dashboard: http://localhost:8765"

## Rebuild and restart everything that doesn't collect market data (dashboard,
## ranking_engine, live-paper, bot_tui's image) -- never touches the collector
## container, so its WS connection/book state stays untouched. Use this for all
## non-collector iteration; reach for `make up` (or `docker compose up -d --build
## collector`) only when dydx_collector/ itself changed. live-paper is rebuilt+restarted
## under its profile same as `make up-live-paper` -- only run this if you actually want
## live-paper (re)started. bot_tui is `run`-only (never a daemon), so it's just rebuilt
## here for a fresh image; `make tui` also rebuilds it on every invocation regardless.
redeploy:
	$(COMPOSE) up -d --build dashboard ranking_engine
	$(COMPOSE) --profile live-paper up -d --build live-paper
	$(COMPOSE) --profile tui build bot_tui
	@echo "Dashboard: http://localhost:8765"

## Start the paper-trading node (Epic 3, live_paper module). Not part of plain `make up`
## -- gated behind the `live-paper` compose profile so it's never started by accident.
## Runs in paper mode only by default (SandboxExecutionClientConfig, no wallet/private
## key) -- see troll/live_paper/config.py for how real-money mode is separately gated.
up-live-paper:
	$(COMPOSE) --profile live-paper up -d --build live-paper

## Build the collector/dashboard/live-paper images bypassing pip's TLS verification, for
## networks with a TLS-intercepting proxy (e.g. Zscaler) where pip can't validate
## PyPI's cert chain. No corporate CA is trusted into the image -- this just skips
## verification for pip's own install step. Run this (not plain `docker compose
## build`) whenever troll-requirements.txt or *.dockerfile changes, on
## a TLS-intercepting network -- `make up`/`make up-live-paper` no longer build for you.
## Story 4.1 note: ranking_engine was missing from this list since Story 1.8 first
## added its service (a pre-existing gap, not introduced here) -- added alongside
## bot_tui while touching this line for the same reason.
build-insecure:
	$(COMPOSE) build \
		--build-arg PIP_INSECURE_ARGS="--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org" \
		collector dashboard ranking_engine bot_tui live-paper

## Run the troll test suite inside the collector image. Can't run against host
## Python -- nautilus_trader is only compiled/installed inside this image (see
## build-base), not on the host. Build the image first with `make build-insecure`
## (or plain `docker compose -f docker-compose.yml build collector` off a TLS-
## intercepting network).
## uid 1000 has no /etc/passwd entry in the image, so pytest's tmp_path fixture
## (which shells out to getpass.getuser() to name its basetemp dir) raises OSError --
## same class of bug as the bot_tui_logs one above. test-live-paper already works
## around it with -e USER; mirror that here.
test:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp -e USER=collector collector \
		python3 -m pytest dydx_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests -q

## Launch the bot_tui terminal UI (Story 4.1) -- an interactive urwid app, not a
## daemon, so it's run via `docker compose run` (never `up -d`) and gated behind the
## `tui` profile so it's never started by `make up`. Must be run from an actual TTY
## (an SSH session or local terminal) -- piping/backgrounding this defeats the point.
## bot_tui_logs is gitignored, so it won't exist on a fresh clone. Compose's bind
## mount auto-creates missing host dirs as root (dockerd runs as root), but the
## container writes as uid 1000 -- permission denied on first run for everyone.
## Creating it here, as the invoking user, before Compose ever sees it avoids that.
tui:
	mkdir -p bot_tui_logs
	$(COMPOSE) --profile tui build bot_tui
	$(COMPOSE) --profile tui run --rm bot_tui python3 -m bot_tui.app

## Run live_paper's test suite inside its own image (not the collector image -- that
## image deliberately does not copy in live_paper/, per AD-8's module isolation).
## -e HOME=/tmp -e USER=collector works around the same pre-existing "OSError: No
## username set" pytest tmp_path issue documented in deferred-work.md for
## dydx_collector/tests (image runs as uid 1000 with no matching /etc/passwd entry).
test-live-paper:
	$(COMPOSE) --profile live-paper run --rm --no-deps -e HOME=/tmp -e USER=collector live-paper \
		python3 -m pytest live_paper/tests -q

## Stop containers (catalog data is preserved)
down:
	$(COMPOSE) down

## Tail collector logs
logs:
	$(COMPOSE) logs -f collector

## Tail dashboard logs
logs-dash:
	$(COMPOSE) logs -f dydx-dashboard

## Open Dozzle log viewer in browser
web:
	xdg-open http://localhost:8080

## Launch bot_tui on nifelheim over SSH (needs a real TTY, hence -t). Uses the
## `nifelheim` alias from ~/.ssh/config (host/port/user/key already set there).
remote-tui:
	ssh -t $(REMOTE_HOST) "cd $(REMOTE_DIR) && make tui"

## Tunnel the remote ml_signals dashboard (port 8765) to localhost and open it.
## The VPS firewall (make enable-firewall) only allows inbound on 22 + tailscale,
## so port 8765 isn't reachable directly -- an SSH tunnel is the way in.
## `-f` backgrounds ssh only after the tunnel is up, so the xdg-open below always
## races a live tunnel, not a not-yet-connected one. Stop it with `make remote-web-stop`.
remote-web:
	ssh -f -N -L 8765:localhost:8765 $(REMOTE_HOST)
	xdg-open http://localhost:8765

## Kill the SSH tunnel opened by `make remote-web`
remote-web-stop:
	pkill -f "ssh -f -N -L 8765:localhost:8765 $(REMOTE_HOST)" || true

## Tunnel the remote Redis instance (port 6379, SEC-01: localhost-only, no public
## port) to localhost so a DB GUI (RedisInsight, TablePlus, Another Redis Desktop
## Manager, etc.) on your machine can connect to localhost:6379 as if it were local.
## Same -f -N -L pattern as `make remote-web` -- backgrounds only once the tunnel is
## up. Stop it with `make remote-db-stop`.
remote-db:
	ssh -f -N -L 6379:localhost:6379 $(REMOTE_HOST)
	@echo "Redis reachable at localhost:6379"

## Kill the SSH tunnel opened by `make remote-db`
remote-db-stop:
	pkill -f "ssh -f -N -L 6379:localhost:6379 $(REMOTE_HOST)" || true

## One-time VPS setup: enable the Docker daemon on boot so `restart: always`
## containers actually come back after a host reboot (Docker's own auto-start
## does nothing if the daemon itself isn't enabled).
enable-boot:
	sudo systemctl enable docker

## One-time VPS hardening: default-deny inbound, rate-limit SSH (22/tcp) and allow
## the tailscale interface, then enable. Safe to allow tailscale0 broadly --
## Tailscale's own device auth/ACLs already gate the tailnet; ufw's only job
## here is blocking the public internet. Uses `limit` instead of `allow` for
## 22/tcp -- ufw's built-in brute-force throttle denies an IP once it hits 6
## connection attempts within 30s, no fail2ban needed for this alone. Pair with
## `make harden-ssh` (key-only auth) for the rest of the standard hardening stack.
## Uses 22/tcp directly rather than the "OpenSSH" app profile so it doesn't depend
## on that profile being registered.
## Run this over a console/out-of-band session first time, not your only SSH
## link, in case something's misconfigured and you get locked out.
enable-firewall:
	sudo ufw default deny incoming
	sudo ufw default allow outgoing
	sudo ufw limit 22/tcp
	sudo ufw allow in on tailscale0
	sudo ufw --force enable

## One-time VPS hardening: disable SSH password auth (key-only) and disallow
## root login except via key. Combined with `enable-firewall`'s rate limit, this
## is the standard brute-force mitigation pair -- rate limiting slows guessing,
## key-only auth makes guessing futile regardless of speed.
## Keeps a pristine backup at sshd_config.orig on first run (cp -n is a no-op
## on subsequent runs). Confirm your SSH key already works BEFORE running this
## -- password auth is turned off immediately after, over the same session.
## Run over a console/out-of-band session first time, not your only SSH link.
harden-ssh:
	sudo cp -n /etc/ssh/sshd_config /etc/ssh/sshd_config.orig
	sudo sed -i -E \
		-e 's/^#?PasswordAuthentication.*/PasswordAuthentication no/' \
		-e 's/^#?PermitRootLogin.*/PermitRootLogin prohibit-password/' \
		/etc/ssh/sshd_config
	sudo systemctl reload ssh

## Prune order_book_deltas older than 14 days (run daily to bound storage)
prune:
	python $(REPO_ROOT)/troll/dydx_collector/prune_catalog.py \
		--catalog $(REPO_ROOT)/troll/dydx_collector/catalog \
		--types order_book_deltas --days 14

## Preview what prune would delete without removing anything
prune-dry:
	python $(REPO_ROOT)/troll/dydx_collector/prune_catalog.py \
		--catalog $(REPO_ROOT)/troll/dydx_collector/catalog \
		--types order_book_deltas --days 14 --dry-run

## Run the ml_signals dashboard (OFI, Microprice, footprint charts) on port 8765
dashboard:
	fuser -k 8765/tcp || true
	cd $(REPO_ROOT) && PYTHONPATH=$(REPO_ROOT)/troll python -m ml_signals.dashboard --open
