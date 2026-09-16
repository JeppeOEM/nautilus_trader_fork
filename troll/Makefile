REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
TROLL_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(TROLL_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: build-base build-base-vps up redeploy redeploy-no-paper up-live-paper build-insecure test test-live-paper down logs logs-dash web enable-boot enable-firewall harden-ssh frontend-dev frontend-remote

# Personal, gitignored overrides (see .env-example for the documented default).
-include $(TROLL_DIR).env

# LOCAL_DEV=true (set in troll/.env, or `LOCAL_DEV=true make up`) shifts every port
# this stack publishes off its production defaults, onto ones that don't collide with
# the SSH-tunnel-forwarded ports (6379/9100/8080) or dashboard port (8765) that
# troll-web/troll-tui/troll-logs (~/.zshrc) use to reach the real VPS instance -- see
# troll/CLAUDE.md's "Desktop <-> VPS Connection" section. Never set on the VPS itself,
# so its .env needs no changes; `?=` still lets an explicit port in .env win over either
# default. ponytail: 4 vars is simpler than a generic port-offset scheme -- add one if a
# 5th port ever needs it.
LOCAL_DEV ?= false
ifeq ($(LOCAL_DEV),true)
WEB_PORT ?= 18765
REDIS_PORT ?= 16379
DATA_API_PORT ?= 19100
DOZZLE_PORT ?= 18080
else
WEB_PORT ?= 8765
REDIS_PORT ?= 6379
DATA_API_PORT ?= 9100
DOZZLE_PORT ?= 8080
endif
export WEB_PORT REDIS_PORT DATA_API_PORT DOZZLE_PORT

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
	@docker image prune -f

## Build the base image on a low-RAM VPS. Runs the Rust compile as a single job
## (CARGO_BUILD_JOBS=1) instead of build-base's default 4-wide, capping peak
## memory at ~2-4GB instead of ~8-16GB -- see .docker/nautilus_trader.dockerfile's
## CARGO_BUILD_JOBS comment. Much slower than build-base (serial, not parallel) --
## budget well over an hour on a small VPS, not ~15 min. --progress=plain forces
## linear, always-flushed log lines instead of BuildKit's default redrawing TTY
## UI, which can look like nothing is happening over a slow/non-interactive SSH
## session -- use this if `make build-base` printed nothing before failing.
build-base-vps:
	@CA_SECRET=""; \
	if [ -f "$(HOME)/.docker/corporate-ca.pem" ]; then \
		CA_SECRET="--secret id=ca-cert,src=$(HOME)/.docker/corporate-ca.pem"; \
	fi; \
	DOCKER_BUILDKIT=1 docker build $$CA_SECRET --network=host \
		--progress=plain \
		--build-arg CARGO_BUILD_JOBS=1 \
		-f $(REPO_ROOT)/.docker/nautilus_trader.dockerfile \
		--target application \
		-t $(BASE_IMAGE) \
		$(REPO_ROOT)
	@docker image prune -f

## Start collecting data, rebuilding every default-profile service (collector included)
## first (seconds -- it's just the thin troll/ layer on top of the base image). Run
## `make build-base` once beforehand for the base image itself. Use this only for first
## bring-up or when dydx_collector/ itself changed -- otherwise use `make redeploy` so
## the collector's live WS connection/book state is never disturbed.
up:
	$(COMPOSE) up -d --build
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(WEB_PORT)"

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
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(WEB_PORT)"

## Same as `redeploy` but never touches live-paper -- for iterating on
## dashboard/ranking_engine/data_api/bot_tui (e.g. the VPS-side half of
## troll-redeploy in ~/.zshrc) without disturbing a running paper-trading bot's
## position/order state. Includes data_api (redeploy itself omits it today).
redeploy-no-paper:
	$(COMPOSE) up -d --build dashboard ranking_engine data_api
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(WEB_PORT)"

## Start the paper-trading node (Epic 3, live_paper module). Not part of plain `make up`
## -- gated behind the `live-paper` compose profile so it's never started by accident.
## Runs in paper mode only by default (SandboxExecutionClientConfig, no wallet/private
## key) -- see troll/live_paper/config.py for how real-money mode is separately gated.
up-live-paper:
	$(COMPOSE) --profile live-paper up -d --build live-paper
	@docker image prune -f

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
		python3 -m pytest dydx_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests -q

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
	xdg-open http://localhost:$(DOZZLE_PORT)

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

## Run the ml_signals dashboard (OFI, Microprice, footprint charts) directly on the host
## (not Docker), on WEB_PORT (default 8765, override via .env -- see .env-example).
dashboard:
	fuser -k $(WEB_PORT)/tcp || true
	cd $(REPO_ROOT) && PYTHONPATH=$(REPO_ROOT)/troll DASHBOARD_PORT=$(WEB_PORT) python -m ml_signals.dashboard --open

## Local frontend dev, fully self-contained: brings up collector + ranking_engine +
## data_api + redis, all locally (network_mode: host, so data_api lands on
## 127.0.0.1:$(DATA_API_PORT) -- exactly where frontend/vite.config.ts's dev-server
## proxy expects it), then runs Vite in the foreground. Opens a REAL dYdX WS
## connection via collector and needs time to build up real ranking history from
## scratch via ranking_engine -- heavier than `frontend-remote` below, but fully
## self-contained (no VPS/tunnel dependency). Ctrl-C only stops the frontend -- the
## backend containers keep running (restart: always); `make down` stops those too.
frontend-dev:
	$(COMPOSE) up -d --build redis data_api collector ranking_engine
	cd $(TROLL_DIR)frontend && npm run dev

## Local frontend dev against the VPS's real, already-running data instead --
## collector/ranking_engine stay on the VPS, nothing heavy runs locally. Opens one SSH
## tunnel to $(TROLL_VPS_HOST) forwarding its data_api port (9100 -- the VPS's own
## default, NOT $(DATA_API_PORT), which is LOCAL_DEV's shifted port for the
## locally-run containers `frontend-dev` starts above) to the same local port,
## idempotent via fuser -- same pattern as ~/.zshrc's `_troll_tunnel_ensure`
## (troll/CLAUDE.md's "Desktop <-> VPS Connection"), self-contained here since that
## shell function lives outside this repo. Then runs Vite with DATA_API_PORT
## overridden to the tunnel's local end. Only the data_api tunnel is needed --
## data_api is the sole thing the frontend talks to; it owns its own redis
## subscription on the VPS side.
TROLL_VPS_HOST ?= nifelheim
frontend-remote:
	fuser 9100/tcp >/dev/null 2>&1 || ssh -f -N -L 9100:127.0.0.1:9100 $(TROLL_VPS_HOST)
	cd $(TROLL_DIR)frontend && DATA_API_PORT=9100 npm run dev
