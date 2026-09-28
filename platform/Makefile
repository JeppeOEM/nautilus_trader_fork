REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
PLATFORM_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(PLATFORM_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: nightly consolidate backup-catalog build-candles prune prune-dry build-base build-base-vps up redeploy redeploy-all redeploy-no-paper up-live-paper build-insecure test test-live-paper notebooks down logs logs-dash web enable-boot enable-firewall harden-ssh frontend-dev frontend-remote

# Personal, gitignored overrides (see .env-example for the documented default).
-include $(PLATFORM_DIR).env

# LOCAL_DEV=true (set in platform/.env, or `LOCAL_DEV=true make up`) shifts every port
# this stack publishes off its production defaults, onto ones that don't collide with
# the SSH-tunnel-forwarded ports (6379/9100/8080) that
# troll-web/troll-tui/troll-logs (~/.zshrc) use to reach the real VPS instance -- see
# platform/CLAUDE.md's "Desktop <-> VPS Connection" section. Never set on the VPS itself,
# so its .env needs no changes; `?=` still lets an explicit port in .env win over either
# default. Four explicit vars rather than a generic port-offset scheme -- add one if a
# 5th port ever needs it.
LOCAL_DEV ?= false
ifeq ($(LOCAL_DEV),true)
REDIS_PORT ?= 16379
DATA_API_PORT ?= 19100
DOZZLE_PORT ?= 18080
else
REDIS_PORT ?= 6379
DATA_API_PORT ?= 9100
DOZZLE_PORT ?= 8080
endif
export REDIS_PORT DATA_API_PORT DOZZLE_PORT

# The operator's rclone config directory (`rclone config` on the host), mounted read-only into the
# `archive` service for its nightly backup and `make backup-catalog` (Story 25.1b). Override in
# platform/.env when it lives elsewhere. Created by `up`/`redeploy*` below: a bind-mount source
# that does not exist would be created by dockerd as an empty root-owned directory.
RCLONE_CONFIG_DIR ?= $(HOME)/.config/rclone
export RCLONE_CONFIG_DIR

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
## first (seconds -- it's just the thin platform/ layer on top of the base image). Run
## `make build-base` once beforehand for the base image itself. Use this only for first
## bring-up or when capture/ itself changed -- otherwise use `make redeploy` so
## the collector's live WS connection/book state is never disturbed. Starts the `archive`
## service too (the nightly maintenance schedule, Story 25.1b; no profile). data/archive (its
## state) and the rclone config directory are created first, as the invoking user, for the same
## root-owned-bind-mount reason as `tui`'s mkdir.
up:
	mkdir -p $(PLATFORM_DIR)data/archive $(RCLONE_CONFIG_DIR)
	$(COMPOSE) up -d --build
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Rebuild and restart everything that doesn't collect market data (archive, ranking_engine, live-paper, bot_tui's image) -- never touches the collector
## container, so its WS connection/book state stays untouched. Use this for all
## non-collector iteration; reach for `make up` (or `docker compose up -d --build
## collector`) only when capture/ itself changed. live-paper is rebuilt+restarted
## under its profile same as `make up-live-paper` -- only run this if you actually want
## live-paper (re)started. bot_tui is `run`-only (never a daemon), so it's just rebuilt
## here for a fresh image; `make tui` also rebuilds it on every invocation regardless.
## Restarting `archive` mid-run kills the running maintenance step; every step is crash-safe and
## the scheduler repeats that venue-day on its next run (archive/application/scheduler.py).
redeploy:
	mkdir -p $(PLATFORM_DIR)data/archive $(RCLONE_CONFIG_DIR)
	$(COMPOSE) up -d --build archive ranking_engine data_api
	$(COMPOSE) --profile live-paper up -d --build live-paper
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Rebuild and restart EVERYTHING, the three collectors included -- `redeploy` plus `collector`,
## `bybit_collector` and `hyperliquid_collector` (all built from the one collector image, so a
## capture/ change must restart all three: before Story 26.2 this target restarted only dYdX's).
## Use when capture/ changed (e.g. the candle-store feed). Briefly drops each collector's WS
## connection and loses its in-memory book state; prefer `redeploy` otherwise.
redeploy-all:
	mkdir -p $(PLATFORM_DIR)data/archive $(RCLONE_CONFIG_DIR)
	$(COMPOSE) up -d --build collector bybit_collector hyperliquid_collector archive ranking_engine data_api
	$(COMPOSE) --profile live-paper up -d --build live-paper
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Same as `redeploy` but never touches live-paper -- for iterating on
## ranking_engine, data_api, bot_tui (e.g. the VPS-side half of
## troll-redeploy in ~/.zshrc) without disturbing a running paper-trading bot's
## position/order state.
redeploy-no-paper:
	mkdir -p $(PLATFORM_DIR)data/archive $(RCLONE_CONFIG_DIR)
	$(COMPOSE) up -d --build archive ranking_engine data_api
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Start the paper-trading node (Epic 3; the bots context, `python3 -m bots`, since Story 25.3).
## Not part of plain `make up` -- gated behind the `live-paper` compose profile so it's never
## started by accident. Runs in paper mode only by default (SandboxExecutionClientConfig, no
## wallet/private key) -- see platform/bots/infrastructure/config.py for how real-money mode is
## separately gated.
up-live-paper:
	$(COMPOSE) --profile live-paper up -d --build live-paper
	@docker image prune -f

## Build the collector/data_api/live-paper images bypassing pip's TLS verification, for
## networks with a TLS-intercepting proxy (e.g. Zscaler) where pip can't validate
## PyPI's cert chain. No corporate CA is trusted into the image -- this just skips
## verification for pip's own install step. Run this (not plain `docker compose
## build`) whenever requirements.txt or *.dockerfile changes, on
## a TLS-intercepting network -- `make up`/`make up-live-paper` no longer build for you.
## Story 4.1 note: ranking_engine was missing from this list since Story 1.8 first
## added its service (a pre-existing gap, not introduced here) -- added alongside
## bot_tui while touching this line for the same reason.
build-insecure:
	$(COMPOSE) build \
		--build-arg PIP_INSECURE_ARGS="--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org" \
		collector archive ranking_engine data_api bot_tui live-paper

## Run the platform test suite inside the collector image. Can't run against host
## Python -- nautilus_trader is only compiled/installed inside this image (see
## build-base), not on the host. Build the image first with `make build-insecure`
## (or plain `docker compose -f docker-compose.yml build collector` off a TLS-
## intercepting network).
## uid 1000 has no /etc/passwd entry in the image, so pytest's tmp_path fixture
## (which shells out to getpass.getuser() to name its basetemp dir) raises OSError --
## same class of bug as the bot_tui_logs one above. test-live-paper already works
## around it with -e USER; mirror that here.
## The cross-cutting guards in tests/ (import boundaries, image closures, legacy names --
## Story 23.1)
## read the whole checkout, mounted read-only at /src: no image holds every package, and /app is
## not named `platform`, so reading the image would silently check a subset.
TEST_SOURCE_MOUNT := -v $(REPO_ROOT):/src:ro -e PLATFORM_SOURCE_DIR=/src/platform
test:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp -e USER=collector $(TEST_SOURCE_MOUNT) collector \
		python3 -m pytest archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q

## Sync every numbered research notebook pair (Story 27.2): `research/notebooks/<nn>_<name>.py`
## (jupytext percent format, the source of truth) <-> its output-free `.ipynb`, whichever was edited
## last wins (research/notebooks/jupytext.toml sets the pairing and strips all metadata). Runs on
## the host with the repo's locked dev dependencies; `research/tests/test_notebooks.py` (in `make
## test`) fails an unsynced pair, a stored output or a `.py` with no `.ipynb`.
notebooks:
	cd $(REPO_ROOT) && uv run jupytext --sync $(PLATFORM_DIR)research/notebooks/[0-9]*_*.py

## Record the capture hot path's cost baseline (tests/fixtures/hotpath_baseline.json, audit D-65,
## spine AD-D5) into the checkout: the one place the source tree is mounted writable, because
## `make test` mounts it read-only and a baseline recorded into the image's copy would vanish with
## the container. Only in a change that deliberately re-baselines (a changed burst, a Python or
## nautilus_trader bump, or a reviewed per-message cost increase stated on audit row D-65),
## never to make `make test` pass; the test's allocation checks say why.
## Measures the collector image, so build it from this checkout first (`make up` / `make build`).
HOTPATH_BASELINE := $(PLATFORM_DIR)tests/fixtures/hotpath_baseline.json
hotpath-baseline:
	rm -f $(HOTPATH_BASELINE)
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp -e USER=collector -v $(REPO_ROOT):/src -e PLATFORM_SOURCE_DIR=/src/platform collector \
		python3 -m pytest tests/test_hotpath.py -q
	@test -f $(HOTPATH_BASELINE) && echo "recorded $(HOTPATH_BASELINE)" || { echo "hotpath-baseline: nothing was recorded" >&2; exit 1; }

## Merge yesterday's (and any older closed day's) minute-sized Parquet files into one file per
## (data type, instrument) for every venue -- audit D-36: without this the catalog grows ~1,440
## files per instrument per type per day. Safe while the collectors run (today's files are never
## touched). Ends with one summary line (days, files/MB before -> after, wall s, peak RSS) and
## exits 1 when any day was refused. Extra flags via CONSOLIDATE_ARGS, e.g.
## `make consolidate CONSOLIDATE_ARGS="--days 3"`. A manual tool: the `archive` compose service
## (Story 25.1b) is the schedule -- it runs this after every nightly run and the
## `--closed-hours` merge intraday -- so no host cron line runs it any more.
consolidate:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.consolidate_catalog --catalog /app/catalog --apply $(CONSOLIDATE_ARGS)

## One venue's nightly maintenance for one closed UTC day (story 22.13; the archive.nightly saga,
## Story 25.1), each step its own process: rebuild_seconds --apply -> consolidate_catalog --apply
## --days 2 -> build_candles --day --workers 1 -> compare_klines --rebuilt-by <this run> ->
## prune_catalog --apply (trade retention 7 days, gated on verified_days; for DYDX also the plan's
## dropped-instrument and per-coin delta retention, read from the mounted dYdX config.toml). A
## step's "findings" exit 2 continues; any other failure stops the chain; one summary line.
## VENUE is required (DYDX|BYBIT|HYPERLIQUID); DAY defaults to yesterday (UTC). A manual tool (a
## re-run, or a day past the catch-up cap): the `archive` compose service runs this saga every
## night for every venue in archive/config.toml (Story 25.1b; no host cron line). It shares the
## catalog maintenance lock with that service, which waits for it (bounded) rather than overlap.
DAY ?= $(shell date -u -d yesterday +%Y-%m-%d)
nightly:
	@test -n "$(VENUE)" || { echo "nightly: VENUE is required, e.g. make nightly VENUE=BYBIT [DAY=YYYY-MM-DD]" >&2; exit 1; }
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.nightly --catalog /app/catalog --candles-dir /app/candles_dir \
		--venue $(VENUE) --day $(DAY) --dydx-plan /app/dydx_collector/config.toml

## Copy the catalog's closed-day files to object storage (audit D-33: there is no other copy), by
## hand. A manual tool: the `archive` compose service runs the same module after every nightly run
## (Story 25.1b; no host cron line). Runs `python3 -m archive.backup_catalog` in the `archive`
## service's container, so rclone comes from the image (not the host) and reads the operator's
## rclone config directory (RCLONE_CONFIG_DIR, default ~/.config/rclone, mounted read-only; the
## remote -- R2, B2, ... -- is configured with `rclone config`, never in this repo). RCLONE_REMOTE
## and RCLONE_BUCKET come from platform/.env. Run AFTER `make consolidate`, or the upload is
## millions of tiny objects. The module's docstring has the guards (an unset target, an absent or
## empty catalog: refused, ledgered, exit 1) and the exact `rclone sync ... --backup-dir
## .../catalog-replaced/<stamp>` command: today's files and *.tmp leftovers are excluded, and
## objects the sync would delete or overwrite move under a dated catalog-replaced/ prefix, so a
## wiped local catalog can never erase the only backup. Prune catalog-replaced/ only by hand, after
## checking that catalog/data on the remote is intact -- never with an automatic expiry rule. Values
## in .env are taken literally: no quotes, and the remote is its bare name (a trailing ':' is
## tolerated).
backup-catalog:
	mkdir -p $(RCLONE_CONFIG_DIR)
	$(COMPOSE) run --rm --no-deps archive python3 -m archive.backup_catalog --catalog /app/catalog

## Rebuild the SQLite candle store from the raw 1s archive (first deploy, or after a
## "candle store write failed" / archive.repair_catalog run). Skips today unless the collector is stopped
## and --include-open-day is added.
build-candles:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m candles.rebuild --catalog /app/catalog --db /app/candles_dir/candles_dydx.db

## Launch the bot_tui terminal UI (Story 4.1) -- an interactive urwid app, not a
## daemon, so it's run via `docker compose run` (never `up -d`) and gated behind the
## `tui` profile so it's never started by `make up`. Must be run from an actual TTY
## (an SSH session or local terminal) -- piping/backgrounding this defeats the point.
## bot_tui_logs is gitignored, so it won't exist on a fresh clone. Compose's bind
## mount auto-creates missing host dirs as root (dockerd runs as root), but the
## container writes as uid 1000 -- permission denied on first run for everyone.
## Creating it here, as the invoking user, before Compose ever sees it avoids that.
tui:
	mkdir -p data/bot_tui_logs
	$(COMPOSE) --profile tui build bot_tui
	$(COMPOSE) --profile tui run --rm bot_tui python3 -m bot_tui.app

## Run the bots context's test suite inside its own image (not the collector image -- that
## image deliberately does not copy in bots/, per AD-8's module isolation).
## -e HOME=/tmp -e USER=collector works around the same pre-existing "OSError: No
## username set" pytest tmp_path issue documented in deferred-work.md for
## capture/venues/dydx/tests (image runs as uid 1000 with no matching /etc/passwd entry).
## tests/test_hotpath.py replays the collector's ingest path, which this image deliberately
## does not ship; it runs in `make test`.
## Every build_node test in test_node.py connects the node's Redis-backed Cache at construction,
## so this target needs the compose Redis up (`--no-deps` does not start it) -- without it those
## tests block, not fail; on a host run set REDIS_URL=redis://127.0.0.1:$(REDIS_PORT) likewise.
## The two deselected tests cannot pass against that Redis: `..._configures_redis_backed_cache`
## asserts the default port 6379 (true only where REDIS_URL is unset) and
## `..._passes_redis_credentials_and_ssl_from_url` blocks on the unreachable
## `rediss://myhost:6380` it builds against. Their URL parsing is still covered, host-independently,
## by `test_cache_config_*` in the same file.
test-live-paper:
	$(COMPOSE) --profile live-paper run --rm --no-deps -e HOME=/tmp -e USER=collector $(TEST_SOURCE_MOUNT) live-paper \
		python3 -m pytest bots/tests observability/tests kernel/tests tests --ignore=tests/test_hotpath.py --ignore=tests/test_capture_archive_handoff.py \
		--deselect bots/tests/test_node.py::test_build_node_configures_redis_backed_cache \
		--deselect bots/tests/test_node.py::test_build_node_passes_redis_credentials_and_ssl_from_url -q

## Stop containers (catalog data is preserved)
down:
	$(COMPOSE) down

## Tail collector logs
logs:
	$(COMPOSE) logs -f collector

## Tail data_api (dashboard) logs
logs-dash:
	$(COMPOSE) logs -f dydx-data-api

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

## Prune order_book_deltas older than 14 days (run daily to bound storage). Trades are pruned
## by `make nightly` only (verification-gated, see archive/prune_catalog.py).
prune:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.prune_catalog --catalog /app/catalog \
		--types order_book_deltas --days 14 --apply

## Preview what prune would delete without removing anything (the tool's default: report only)
prune-dry:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.prune_catalog --catalog /app/catalog \
		--types order_book_deltas --days 14

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
	cd $(PLATFORM_DIR)frontend && npm run dev

## Local frontend dev against the VPS's real, already-running data instead --
## collector and ranking_engine stay on the VPS, nothing heavy runs locally. Opens one SSH
## tunnel to $(TROLL_VPS_HOST) forwarding its data_api port (9100 -- the VPS's own
## default, NOT $(DATA_API_PORT), which is LOCAL_DEV's shifted port for the
## locally-run containers `frontend-dev` starts above) to the same local port,
## idempotent via fuser -- same pattern as ~/.zshrc's `_troll_tunnel_ensure`
## (platform/CLAUDE.md's "Desktop <-> VPS Connection"), self-contained here since that
## shell function lives outside this repo. Then runs Vite with DATA_API_PORT
## overridden to the tunnel's local end. Only the data_api tunnel is needed --
## data_api is the sole thing the frontend talks to; it owns its own redis
## subscription on the VPS side.
TROLL_VPS_HOST ?= nifelheim
## A listener on 9100 is NOT proof the tunnel works -- a stale `ssh -f` keeps the local
## port open after its connection dies, and every /api call then fails while Vite still
## looks fine (blank chart). So health-check THROUGH the tunnel, and recycle it if it
## doesn't answer. ExitOnForwardFailure/ServerAlive* make a dead tunnel exit on its own.
frontend-remote:
	curl -sf -m 5 http://127.0.0.1:9100/api/health >/dev/null 2>&1 || { \
		pkill -f "^ssh .*-L 9100:127.0.0.1:9100" || true; sleep 1; \
		ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
			-L 9100:127.0.0.1:9100 $(TROLL_VPS_HOST); }
	curl -sf -m 10 http://127.0.0.1:9100/api/health >/dev/null || { \
		echo "data_api unreachable via tunnel on 9100 (VPS data_api down/restarting?)"; exit 1; }
	cd $(PLATFORM_DIR)frontend && DATA_API_PORT=9100 npm run dev
