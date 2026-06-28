REPO_ROOT := $(shell git -C $(dir $(abspath $(lastword $(MAKEFILE_LIST)))) rev-parse --show-toplevel)
BASE_IMAGE := nautilus-trader-base:1.229.0

.PHONY: build-base up down logs web

## Build the base nautilus image (~15 min, only needed once or after core changes)
build-base:
	docker build --network=host \
		-f $(REPO_ROOT)/.docker/nautilus_trader.dockerfile \
		--target application \
		-t $(BASE_IMAGE) \
		$(REPO_ROOT)

## Build collector image and start collecting data
up:
	docker compose up -d --build

## Stop containers (catalog data is preserved)
down:
	docker compose down

## Tail collector logs
logs:
	docker compose logs -f collector

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
	xdg-open http://localhost:8765 &
	cd $(REPO_ROOT) && PYTHONPATH=$(REPO_ROOT)/troll python -m ml_signals.dashboard
