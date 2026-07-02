REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
TROLL_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE    := docker compose -f $(TROLL_DIR)docker-compose.yml
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: build-base up down logs logs-dash web

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

## Build collector image and start collecting data
up:
	$(COMPOSE) up -d --build
	@echo "Dashboard: http://localhost:8765"

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
