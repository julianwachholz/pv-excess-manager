help:
	@echo "Available commands:"
	@echo "  run   - Run Home Assistant with the custom components"

install:
	uv sync

run:
	uv run hass -c ./config
