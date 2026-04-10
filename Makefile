help:
	@echo "Available commands:"
	@echo "  run   - Run Home Assistant with the custom components"

install:
	uv sync

link:
	ln -fs $(PWD)/custom_components ./config/custom_components

run:
	uv run hass -c ./config
