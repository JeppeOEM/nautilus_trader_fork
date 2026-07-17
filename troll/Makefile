REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
TROLL_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(TROLL_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: build-base up build-insecure test down logs logs-dash web enable-boot enable-firewall harden-ssh

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

## Start collecting data. Does NOT build -- run `make build-base` (once) and
## `make build-insecure` (or `docker compose build` on a non-intercepting network)
## first, and again after any change to troll-requirements.txt or collector.dockerfile.
up:
	$(COMPOSE) up -d
	@echo "Dashboard: http://localhost:8765"

## Build the collector/dashboard image bypassing pip's TLS verification, for
## networks with a TLS-intercepting proxy (e.g. Zscaler) where pip can't validate
## PyPI's cert chain. No corporate CA is trusted into the image -- this just skips
## verification for pip's own install step. Run this (not plain `docker compose
## build`) whenever troll-requirements.txt or collector.dockerfile changes, on
## a TLS-intercepting network -- `make up` no longer builds for you.
build-insecure:
	$(COMPOSE) build \
		--build-arg PIP_INSECURE_ARGS="--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org" \
		collector dashboard

## Run the troll test suite inside the collector image. Can't run against host
## Python -- nautilus_trader is only compiled/installed inside this image (see
## build-base), not on the host. Build the image first with `make build-insecure`
## (or plain `docker compose -f docker-compose.yml build collector` off a TLS-
## intercepting network).
test:
	$(COMPOSE) run --rm --no-deps collector \
		python3 -m pytest dydx_collector/tests ml_signals/tests -q

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
