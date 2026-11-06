SCENARIO ?= scenarios/public_01_dev.yaml
PYTHON ?= python3
NAME ?= $(basename $(notdir $(SCENARIO)))

baseline:
	$(PYTHON) -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	$(PYTHON) -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME)
grade:
	mkdir -p reports && $(PYTHON) -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
test:
	$(PYTHON) -m pytest -q tests
