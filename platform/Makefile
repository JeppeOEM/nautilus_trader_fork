REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
PLATFORM_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(PLATFORM_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: verify-up verify-down verify-wipe nightly consolidate backup-catalog build-candles prune prune-dry build-base build-base-vps up redeploy redeploy-all redeploy-no-paper up-live-paper up-dydx down-dydx build-insecure test test-live-paper bots-churn-check notebooks down logs logs-dash web enable-boot enable-firewall harden-ssh frontend-dev frontend-remote

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

## Start collecting data, rebuilding every default-profile service (the Bybit and Hyperliquid
## collectors included; dYdX's `collector` is behind the `dydx` profile since the venue cutover,
## Story 29.3 -- `make up-dydx`) first (seconds -- it's just the thin platform/ layer on top of
## the base image). Run `make build-base` once beforehand for the base image itself. Use this only for first
## bring-up or when capture/ itself changed -- otherwise use `make redeploy` so
## the collector's live WS connection/book state is never disturbed. Starts the `archive`
## service too (the nightly maintenance schedule, Story 25.1b; no profile). data/archive (its
## state), data/coverage (the collectors' coverage record, Story 31.2; they run as uid 1000) and
## the rclone config directory are created first, as the invoking user, for the same
## root-owned-bind-mount reason as `tui`'s mkdir.
## Also builds -- never starts -- dYdX's `collector` image: compose tags one image per service, and
## `make test`/`nightly`/`consolidate`/`prune`/`build-candles`/`hotpath-baseline` all `run` that
## one, so without this build they would run a stale image once `up` stopped naming `collector`.
## Built first, so a failed build stops the target before any running service is replaced.
up:
	@$(STACK_SINGLE_WRITER)
	mkdir -p $(PLATFORM_DIR)data/archive $(PLATFORM_DIR)data/coverage $(RCLONE_CONFIG_DIR)
	$(COMPOSE) build collector
	$(COMPOSE) up -d --build
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Rebuild and restart everything that doesn't collect market data (archive, ranking_engine, live-paper, bot_tui's image) -- never touches the collector
## container, so its WS connection/book state stays untouched. Use this for all
## non-collector iteration; reach for `make up` or `make redeploy-all` (plus `make up-dydx` while
## dYdX runs: neither restarts it) only when capture/ itself changed. live-paper is rebuilt+restarted
## under its profile same as `make up-live-paper` -- only run this if you actually want
## live-paper (re)started. bot_tui is `run`-only (never a daemon), so it's just rebuilt
## here for a fresh image; `make tui` also rebuilds it on every invocation regardless.
## Restarting `archive` mid-run kills the running maintenance step; every step is crash-safe and
## the scheduler repeats that venue-day on its next run (archive/application/scheduler.py).
redeploy:
	@$(STACK_SINGLE_WRITER)
	mkdir -p $(PLATFORM_DIR)data/archive $(RCLONE_CONFIG_DIR)
	$(COMPOSE) up -d --build archive ranking_engine data_api
	$(COMPOSE) --profile live-paper up -d --build live-paper
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Rebuild and restart EVERYTHING, the default collectors included -- `redeploy` plus
## `bybit_collector` and `hyperliquid_collector` (both built from the one collector image, so a
## capture/ change must restart both). Use when capture/ changed (e.g. the candle-store feed).
## Briefly drops each collector's WS connection and loses its in-memory book state; prefer
## `redeploy` otherwise. dYdX's `collector` is not named since the venue cutover (Story 29.3): it
## is behind the `dydx` profile, so this target never starts, restarts or rebuilds it -- a running
## dYdX container keeps its old image (the image itself is rebuilt, for the maintenance targets
## that `run` it, as in `up`). Restart a running dYdX on the new image with `make up-dydx`.
redeploy-all:
	@$(STACK_SINGLE_WRITER)
	mkdir -p $(PLATFORM_DIR)data/archive $(PLATFORM_DIR)data/coverage $(RCLONE_CONFIG_DIR)
	$(COMPOSE) build collector
	$(COMPOSE) up -d --build bybit_collector hyperliquid_collector archive ranking_engine data_api
	$(COMPOSE) --profile live-paper up -d --build live-paper
	$(COMPOSE) --profile tui build bot_tui
	@docker image prune -f
	@echo "Dashboard: http://localhost:$(DATA_API_PORT)"

## Same as `redeploy` but never touches live-paper -- for iterating on
## ranking_engine, data_api, bot_tui (e.g. the VPS-side half of
## troll-redeploy in ~/.zshrc) without disturbing a running paper-trading bot's
## position/order state.
redeploy-no-paper:
	@$(STACK_SINGLE_WRITER)
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

## Start (or rebuild and restart) the dYdX collector. Not part of plain `make up` since the venue
## cutover (Story 29.3) -- gated behind the `dydx` compose profile, the same pattern as
## `up-live-paper`. Runbook: docs/DEPLOY_CHECKLIST.md §8.
up-dydx:
	@$(STACK_SINGLE_WRITER)
	mkdir -p $(PLATFORM_DIR)data/coverage
	$(COMPOSE) --profile dydx up -d --build collector
	@docker image prune -f

## Stop AND remove the dYdX collector container (catalog, candle store and plan file are kept).
## Removed, not merely stopped: a stopped `restart: always` container is started again when the
## Docker daemon starts, so a plain stop would bring dYdX back on the next reboot.
down-dydx:
	$(COMPOSE) --profile dydx rm -sf collector

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
## docker-compose.test.yml lifts the `collector` service's runtime mem_limit to the suite's own
## measured bound (DW-244): the suite outgrew the dYdX collector's 1690m.
## pandas 3 (platform/requirements.txt pins 3.0.4 over nautilus_trader's own <3.0.0, DW-244) is held
## by the image runs: any pandas.errors.Pandas4Warning is an error, except the two that compiled
## nautilus_trader 1.229.0 raises itself and FORK-01 forbids fixing here, ignored by exact message
## (TEST-04): `pd.Timestamp.utcnow()` (backtest/engine.pyx:1418, :1601, backtest/node.py:347) and
## `floor(freq="d")` (data/aggregation.pyx:1626, :1634, :1646, :1786, data/engine.pyx:2041).
## research/tests/test_notebooks.py's UPSTREAM_PANDAS4_DEPRECATIONS holds the same two messages for
## the notebook harness; change both together. pytest escapes a -W message and matches it as a
## prefix, and a later -W wins, so `error::` comes first; a message must not contain a colon
## (tests/test_pandas_pin.py holds both lists equal and colon-free). By message, not by module:
## pandas attributes the warning to the first Python frame outside pandas, the caller of the
## compiled nautilus_trader code, so no module filter can name nautilus_trader. Known limit: -W
## reaches the pytest process only, not a `python3 -m ...` a test spawns. Upgrade path: pass the
## same filters as PYTHONWARNINGS once a spawned interpreter's pandas use needs proving.
PANDAS4_WARNINGS := -W error::pandas.errors.Pandas4Warning \
	-W "ignore:Timestamp.utcnow is deprecated and will be removed in a future version. Use Timestamp.now('UTC') instead.:pandas.errors.Pandas4Warning" \
	-W "ignore:'d' is deprecated and will be removed in a future version, please use 'D' instead.:pandas.errors.Pandas4Warning"
TEST_SOURCE_MOUNT := -v $(REPO_ROOT):/src:ro -e PLATFORM_SOURCE_DIR=/src/platform
test:
	$(COMPOSE) -f $(PLATFORM_DIR)docker-compose.test.yml run --rm --no-deps -e HOME=/tmp -e USER=collector $(TEST_SOURCE_MOUNT) collector \
		python3 -m pytest $(PANDAS4_WARNINGS) archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests verification/tests tests -q

## The verification stack (Epic 31, Story 31.1): a clean-slate capture of this checkout's data/
## side by side with a live stack -- the Bybit and Hyperliquid collectors, archive, ranking_engine,
## data_api, redis and dozzle, plus the two independent reference recorders
## (`python3 -m verification.recorder`), as compose project `verify` with `verify-*` container
## names on their own ports (docker-compose.verify.yml; redis 26379, data_api 29100, dozzle
## 28080, all 127.0.0.1). The ports are passed explicitly because this Makefile exports the live
## stack's REDIS_PORT/DATA_API_PORT/DOZZLE_PORT. The dYdX `collector` is never started here.
## `verify-up` first creates every bind-mounted data/ directory as the invoking user (dockerd would
## create a missing one root-owned) and makes data/verification and data/errors group-writable:
## the recorders run as uid 1001 in the collectors' group 1000, so it refuses to run for a host
## user whose primary group is not 1000 (the directories would not be writable by the recorders).
## It refuses to start while a running container of another compose project bind-mounts this
## checkout's data/, or while a `verify` project started from another checkout runs (one project
## name per host: `up`/`down` here would recreate or remove that checkout's stack). It marks this
## data/ as a verify stack's (`data/.verify-stack`), which `verify-wipe` requires, and refuses a
## data/ holding anything but the committed plan and the preferences and alerts directories (kept
## in git by a `.gitkeep`) that is not marked yet (a live checkout's).
VERIFY_REDIS_PORT ?= 26379
VERIFY_DATA_API_PORT ?= 29100
VERIFY_DOZZLE_PORT ?= 28080
VERIFY_COMPOSE := REDIS_PORT=$(VERIFY_REDIS_PORT) DATA_API_PORT=$(VERIFY_DATA_API_PORT) DOZZLE_PORT=$(VERIFY_DOZZLE_PORT) docker compose -p verify -f $(PLATFORM_DIR)docker-compose.yml -f $(PLATFORM_DIR)docker-compose.verify.yml
VERIFY_SERVICES := redis bybit_collector hyperliquid_collector archive ranking_engine data_api dozzle reference_recorder_bybit reference_recorder_hyperliquid live-paper
# verification/raw and verification/scratch: the verify `archive` mounts them (its nightly
# `verify_day` step, Story 31.11); created here so dockerd never creates them root-owned.
VERIFY_DATA_DIRS := preferences catalog candles errors metrics archive verification verification/raw verification/scratch verification/bot_signals/live coverage live_paper alerts
# `verify-up` marks the data/ it starts on; `verify-wipe` deletes only a marked data/, so a
# `make verify-wipe` typed in the live checkout (its stack stopped) never deletes live data.
VERIFY_MARKER := .verify-stack
# What `verify-wipe` never deletes: the committed plan, the preferences and saved alerts'
# directories (each kept in git by a `.gitkeep`, their files untracked live state that data_api
# rewrites, the screener columns re-seeded from data_api/seeds/ when missing, DW-219/DW-291) and
# the mark.
VERIFY_KEEP := ! -name dydx_config.toml ! -name preferences ! -name alerts ! -name $(VERIFY_MARKER)
# Every guard below reads `docker ps`, whose failure (daemon down, socket permission) prints
# nothing -- the same as "no container": each verify target checks the daemon answers first.
VERIFY_DOCKER_UP = docker ps -q >/dev/null || { echo "$@: the docker daemon does not answer; refusing to guess what runs" >&2; exit 1; }
# This checkout's platform dir as given and with its symlinks resolved: compose labels a project
# with the path it was given, a bind mount may carry either, so every guard matches both.
VERIFY_HERE = $(patsubst %/,%,$(PLATFORM_DIR))
# The checkouts other than this one whose `verify` project has running containers.
VERIFY_OTHER_CHECKOUTS = docker ps --filter label=com.docker.compose.project=verify --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u | grep -vxF -e "$$(realpath $(VERIFY_HERE))" -e "$(VERIFY_HERE)"
# Running containers that bind-mount this checkout's data/, anything under it or any directory
# above it (a mount of platform/ or of the repo reaches data/ too), one "<container> <compose
# project>" per line: a stack of another project writing the same files would corrupt a
# clean-slate soak, and a wipe under any running writer would delete live files.
VERIFY_DATA_USERS = for id in $$(docker ps -q); do docker inspect -f '{{.Name}} {{with index .Config.Labels "com.docker.compose.project"}}{{.}}{{else}}-{{end}} {{range .Mounts}}{{.Source}} {{end}}' $$id; done | awk -v real="$$(realpath $(VERIFY_HERE)/data)" -v given="$(VERIFY_HERE)/data" 'function hit(m, d) { return m == d || index(m, d "/") == 1 || index(d, m "/") == 1 } { for (i = 3; i <= NF; i++) if (hit($$i, real) || hit($$i, given)) { print $$1, $$2; break } }'
# The default stack's own guard, the mirror of `verify-up`'s: refuses while a running container of
# another compose project (the `verify` stack, a second checkout's stack) bind-mounts this data/.
# Two collectors on one catalog write overlapping files, after which Nautilus's `write_data` refuses
# EVERY later write to that directory (its disjoint-interval check covers the existing files too):
# the 2026-10-08 local outage, when `frontend-dev` started beside a running verify stack.
# Known limit: a reboot restarts every `restart: always` container of both stacks with no make
# target in the loop, so this only guards a stack being started by hand; the upgrade path is a
# per-catalog writer lock the collectors take at start (capture/), refusing to run beside another.
STACK_SINGLE_WRITER = $(VERIFY_DOCKER_UP); project="$$($(COMPOSE) config | sed -n 's/^name: //p')"; users="$$($(VERIFY_DATA_USERS) | awk -v p="$$project" '$$2 != p')"; if [ -n "$$users" ]; then echo "$@: containers of another compose project use this checkout's data/ (two catalog writers make overlapping files, and Nautilus then refuses every write): $$users -- stop them first (make verify-down for the verify stack)" >&2; exit 1; fi
# What of data/ is not kept by a wipe: non-empty on a data/ `verify-up` never marked means a live
# checkout's data, which `verify-up` must never mark (a later wipe would delete it).
VERIFY_UNMARKED_DATA = [ ! -e $(PLATFORM_DIR)data/$(VERIFY_MARKER) ] && [ -d $(PLATFORM_DIR)data ] && cd $(PLATFORM_DIR)data && find . -mindepth 1 -maxdepth 1 $(VERIFY_KEEP) -print
verify-up:
	@$(VERIFY_DOCKER_UP)
	@unmarked="$$($(VERIFY_UNMARKED_DATA))"; if [ -n "$$unmarked" ]; then echo "verify-up: data/ holds data no verify-up started (no data/$(VERIFY_MARKER)): $$unmarked -- a verify stack starts from an empty data/ in its own checkout" >&2; exit 1; fi
	@if [ "$$(id -g)" != 1000 ]; then echo "verify-up: the recorders write as group 1000, but your primary group is $$(id -g)" >&2; exit 1; fi
	@users="$$($(VERIFY_DATA_USERS) | awk '$$2 != "verify"')"; if [ -n "$$users" ]; then echo "verify-up: containers of another project use this checkout's data/: $$users" >&2; exit 1; fi
	@others="$$($(VERIFY_OTHER_CHECKOUTS))"; if [ -n "$$others" ]; then echo "verify-up: a verify stack of another checkout runs: $$others" >&2; exit 1; fi
	mkdir -p $(RCLONE_CONFIG_DIR)
	cd $(PLATFORM_DIR)data && mkdir -p $(VERIFY_DATA_DIRS)
	chmod g+w $(PLATFORM_DIR)data/verification $(PLATFORM_DIR)data/verification/scratch $(PLATFORM_DIR)data/errors
# The recorders (uid 1001) write raw/: group-writable when this user just created it; one they
# created themselves (umask 002) already is, and is not this user's to chmod.
	find $(PLATFORM_DIR)data/verification/raw -maxdepth 0 -user "$$(id -u)" -exec chmod g+w {} +
	touch $(PLATFORM_DIR)data/$(VERIFY_MARKER)
	$(VERIFY_COMPOSE) up -d --build $(VERIFY_SERVICES)
	@echo "verify stack: data_api http://127.0.0.1:$(VERIFY_DATA_API_PORT), dozzle http://127.0.0.1:$(VERIFY_DOZZLE_PORT)"

## Stop and remove the verify stack's containers (its data/ is kept; `verify-wipe` deletes it).
## Refuses while the running `verify` project is another checkout's.
verify-down:
	@$(VERIFY_DOCKER_UP)
	@others="$$($(VERIFY_OTHER_CHECKOUTS))"; if [ -n "$$others" ]; then echo "verify-down: the running verify stack is another checkout's: $$others" >&2; exit 1; fi
	$(VERIFY_COMPOSE) down

## Delete this checkout's data/ for a clean-slate soak, keeping only its committed plan and its
## live preferences and alerts directories. Refuses on a data/ `verify-up` never marked (a live
## checkout's), while any container of the `verify` project runs (`make verify-down` first) or any
## running container of any project bind-mounts this data/, and lists every entry it deletes
## before deleting it.
verify-wipe:
	@$(VERIFY_DOCKER_UP)
	@if [ ! -e $(PLATFORM_DIR)data/$(VERIFY_MARKER) ]; then echo "verify-wipe: this data/ was never started by make verify-up (no data/$(VERIFY_MARKER)); refusing to delete what may be live data" >&2; exit 1; fi
	@if [ -n "$$(docker ps -q --filter label=com.docker.compose.project=verify)" ]; then echo "verify-wipe: the verify stack is running; run make verify-down first" >&2; exit 1; fi
	@users="$$($(VERIFY_DATA_USERS))"; if [ -n "$$users" ]; then echo "verify-wipe: running containers use this checkout's data/: $$users" >&2; exit 1; fi
	@echo "verify-wipe deletes:"; cd $(PLATFORM_DIR)data && find . -mindepth 1 -maxdepth 1 $(VERIFY_KEEP) -print
	cd $(PLATFORM_DIR)data && find . -mindepth 1 -maxdepth 1 $(VERIFY_KEEP) -exec rm -rf {} +

## Sync every numbered research notebook pair (Story 27.2): `research/notebooks/<nn>_<name>.py`
## (jupytext percent format, the source of truth) <-> its output-free `.ipynb`, whichever was edited
## last wins (research/notebooks/jupytext.toml sets the pairing and strips all metadata). Runs on
## the host with the repo's locked dev dependencies; `research/tests/test_notebooks.py` (in `make
## test`) fails an unsynced pair, a stored output or a `.py` with no `.ipynb`.
notebooks:
	cd $(REPO_ROOT) && uv run jupytext --sync $(PLATFORM_DIR)research/notebooks/[0-9]*_*.py

## Record the capture hot path's cost baselines (audit D-65, spine AD-D5) into the checkout:
## tests/fixtures/hotpath_baseline.json (the `default` burst) and hotpath_baseline_scale.json (the
## `scale` burst, 30 instruments a venue, 200-level books; Story 28.2). The checkout is the one
## place the source tree is mounted writable, because `make test` mounts it read-only and a
## baseline recorded into the image's copy would vanish with the container. Only in a change that
## deliberately re-baselines (a changed burst, a Python or nautilus_trader bump, a reviewed
## per-message cost increase stated on audit row D-65, or a cost reduction locked in), never to
## make `make test` pass; the test's allocation checks say why.
## Measures the collector image, so build it from this checkout first (`make up`, which builds it).
HOTPATH_BASELINES := $(PLATFORM_DIR)tests/fixtures/hotpath_baseline.json $(PLATFORM_DIR)tests/fixtures/hotpath_baseline_scale.json
hotpath-baseline:
	rm -f $(HOTPATH_BASELINES)
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp -e USER=collector -v $(REPO_ROOT):/src -e PLATFORM_SOURCE_DIR=/src/platform collector \
		python3 -m pytest tests/test_hotpath.py -q
	@for f in $(HOTPATH_BASELINES); do \
		test -f $$f && echo "recorded $$f" || { echo "hotpath-baseline: $$f was not recorded" >&2; exit 1; }; \
	done

## Merge yesterday's (and any older closed day's) minute-sized Parquet files into one file per
## (data type, instrument) for every venue -- audit D-36: without this the catalog grows ~1,440
## files per instrument per type per day. Safe while the collectors run (today's files are never
## touched). Ends with one summary line (days, files/MB before -> after, wall s, peak RSS) and
## exits 1 when a leaf failed or a day was refused for a reason other than differing schemas,
## else 2 (findings, which make prints as "Error 2") when only mixed-schema days were refused,
## else 0. Extra flags via CONSOLIDATE_ARGS, e.g. `make consolidate CONSOLIDATE_ARGS="--days 3"`.
## A manual tool: the `archive` compose service (Story 25.1b) is the schedule -- it runs this after every nightly run and the
## `--closed-hours` merge intraday -- so no host cron line runs it any more.
consolidate:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.consolidate_catalog --catalog /app/catalog --apply $(CONSOLIDATE_ARGS)

## One venue's nightly maintenance for one closed UTC day (story 22.13; the archive.nightly saga,
## Story 25.1), each step its own process: rebuild_seconds --apply --candles-dir ->
## consolidate_catalog --apply --days 2 -> build_candles --day --workers 1 ->
## compare_klines --rebuilt-by <this run> -> prune_catalog --apply (trade retention 7 days, gated
## on verified_days; for DYDX also the plan's dropped-instrument and per-coin delta retention,
## read from the mounted dYdX config.toml). A step's "findings" exit 2 continues; any other
## failure stops the chain; one summary line.
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

## Rebuild one venue's SQLite candle store from the raw 1s archive (first deploy, or after a
## "candle store write failed" / archive.repair_catalog run). VENUE is required
## (DYDX|BYBIT|HYPERLIQUID): one store per venue, so only that venue's ids are folded, into
## /app/candles_dir/candles_<venue>.db (candles.rebuild derives the path, AD-D12; DW-194). Run it
## for the venue whose collector logged the failure. Skips today unless that venue's collector is
## stopped and --include-open-day is added.
build-candles:
	@test -n "$(VENUE)" || { echo "build-candles: VENUE is required, e.g. make build-candles VENUE=BYBIT" >&2; exit 1; }
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m candles.rebuild --catalog /app/catalog --candles-dir /app/candles_dir \
		--venue $(VENUE)

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
		python3 -m pytest $(PANDAS4_WARNINGS) bots/tests observability/tests kernel/tests tests --ignore=tests/test_hotpath.py --ignore=tests/test_capture_archive_handoff.py \
		--deselect bots/tests/test_node.py::test_build_node_configures_redis_backed_cache \
		--deselect bots/tests/test_node.py::test_build_node_passes_redis_credentials_and_ssl_from_url -q

## Prove the bots' bracket-exit chain live (Story 29.6): one throwaway paper bot
## (bots/tests/fixtures/config.churn.toml, dYdX BTC mainnet data, Sandbox execution) must show a
## protected long, a clean flat and a second protected long on bots:status within 15 min. Uses
## its own Redis on CHURN_REDIS_PORT (default 6399), started only if none answers; the bot
## container is removed either way. Needs Docker and host python3 with redis-py.
bots-churn-check:
	bash $(PLATFORM_DIR)bots/tests/churn_check.sh

## Stop containers (catalog data is preserved)
down:
	$(COMPOSE) down

## Tail the three collectors' logs (dYdX's shows only while its container exists: `make up-dydx`)
logs:
	$(COMPOSE) logs -f collector bybit_collector hyperliquid_collector

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

## Prune order_book_deltas older than 14 days (run daily to bound storage) -- except a dYdX
## instrument with a plan delta entry, whose deltas follow its own `retain_hours` (None: kept
## forever, so the plan, not this target, bounds those; DW-215) -- plus the dYdX plan's retention
## (as `nightly` does): a dropped dYdX coin ages out (its definitions only once its trades are
## gone, DW-208). Trades are pruned by `make
## nightly` only (verification-gated, see archive/prune_catalog.py).
prune:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.prune_catalog --catalog /app/catalog \
		--types order_book_deltas --days 14 --dydx-plan /app/dydx_collector/config.toml --apply

## Preview what prune would delete without removing anything (the tool's default: report only),
## with the same dYdX plan as `prune`
prune-dry:
	$(COMPOSE) run --rm --no-deps -e HOME=/tmp collector \
		python3 -m archive.prune_catalog --catalog /app/catalog \
		--types order_book_deltas --days 14 --dydx-plan /app/dydx_collector/config.toml

## Local frontend dev, fully self-contained: brings up the Bybit and Hyperliquid collectors +
## ranking_engine + data_api + redis, all locally (network_mode: host, so data_api lands on
## 127.0.0.1:$(DATA_API_PORT) -- exactly where frontend/vite.config.ts's dev-server
## proxy expects it), then runs Vite in the foreground. Opens REAL Bybit and Hyperliquid WS
## connections (dYdX is behind the `dydx` profile since Story 29.3: `make up-dydx` adds it)
## and needs time to build up real ranking history from
## scratch via ranking_engine -- heavier than `frontend-remote` below, but fully
## self-contained (no VPS/tunnel dependency). Ctrl-C only stops the frontend -- the
## backend containers keep running (restart: always); `make down` stops those too.
frontend-dev:
	@$(STACK_SINGLE_WRITER)
	$(COMPOSE) up -d --build redis data_api bybit_collector hyperliquid_collector ranking_engine
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
