PYTHON   := python3
VERSION  := $(shell $(PYTHON) -c "import ghfs; print(ghfs.__version__)")
DIST_DIR := dist

.DEFAULT_GOAL := help

# ────────────────────────────────────────────────────────────────────────────
# Help
# ────────────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo "GHFS $(VERSION) — build & distribution targets"
	@echo ""
	@echo "Development:"
	@echo "  make install-dev     Install package + all optional deps in editable mode"
	@echo "  make test            Run unit tests"
	@echo "  make lint            Run ruff + mypy"
	@echo "  make fmt             Auto-format with ruff"
	@echo ""
	@echo "Building:"
	@echo "  make binary          Build standalone binary for the current OS"
	@echo "  make pypi-dist       Build PyPI sdist + wheel"
	@echo "  make deb             Build .deb package (Linux only)"
	@echo ""
	@echo "Publishing:"
	@echo "  make publish-pypi    Upload to PyPI (requires twine + API token)"
	@echo "  make tag             Create and push a git version tag → triggers CI release"
	@echo ""
	@echo "Utility:"
	@echo "  make clean           Remove build artifacts"
	@echo "  make checksums       Print SHA256 of all dist/ files"

# ────────────────────────────────────────────────────────────────────────────
# Development
# ────────────────────────────────────────────────────────────────────────────
.PHONY: install-dev
install-dev:
	$(PYTHON) -m pip install -e ".[all]"
	$(PYTHON) -m pip install pytest ruff mypy pyinstaller build twine

.PHONY: test
test:
	$(PYTHON) -m pytest tests/ -v

.PHONY: test-fast
test-fast:
	$(PYTHON) -m pytest tests/ -x -q

.PHONY: lint
lint:
	ruff check ghfs/ tests/
	mypy ghfs/ --ignore-missing-imports

.PHONY: fmt
fmt:
	ruff format ghfs/ tests/

# ────────────────────────────────────────────────────────────────────────────
# Standalone binary
# ────────────────────────────────────────────────────────────────────────────
.PHONY: binary
binary:
	$(PYTHON) -m pip install pyinstaller
	pyinstaller ghfs.spec
	@echo ""
	@echo "Binary built:"
	@ls -lh $(DIST_DIR)/ghfs* 2>/dev/null || ls -lh $(DIST_DIR)/

.PHONY: binary-linux
binary-linux:
	$(MAKE) binary
	mv $(DIST_DIR)/ghfs $(DIST_DIR)/ghfs-linux-x86_64

.PHONY: binary-macos
binary-macos:
	$(MAKE) binary
	mv $(DIST_DIR)/ghfs $(DIST_DIR)/ghfs-macos-x86_64

.PHONY: binary-windows
binary-windows:
	$(MAKE) binary
	move dist\ghfs.exe dist\ghfs-windows-x86_64.exe

# ────────────────────────────────────────────────────────────────────────────
# PyPI
# ────────────────────────────────────────────────────────────────────────────
.PHONY: pypi-dist
pypi-dist:
	$(PYTHON) -m pip install build
	$(PYTHON) -m build
	@ls -lh $(DIST_DIR)/*.whl $(DIST_DIR)/*.tar.gz

.PHONY: publish-pypi
publish-pypi: pypi-dist
	$(PYTHON) -m pip install twine
	$(PYTHON) -m twine upload $(DIST_DIR)/*.whl $(DIST_DIR)/*.tar.gz

.PHONY: publish-pypi-test
publish-pypi-test: pypi-dist
	$(PYTHON) -m twine upload --repository testpypi $(DIST_DIR)/*.whl $(DIST_DIR)/*.tar.gz

# ────────────────────────────────────────────────────────────────────────────
# OS packages
# ────────────────────────────────────────────────────────────────────────────
.PHONY: deb
deb: binary-linux
	bash packaging/deb/build_deb.sh $(VERSION)

# ────────────────────────────────────────────────────────────────────────────
# Release tagging
# ────────────────────────────────────────────────────────────────────────────
.PHONY: tag
tag:
	@echo "Tagging v$(VERSION) and pushing…"
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push origin "v$(VERSION)"
	@echo "GitHub Actions release pipeline triggered."

.PHONY: checksums
checksums:
	@find $(DIST_DIR) -type f | sort | xargs sha256sum

# ────────────────────────────────────────────────────────────────────────────
# Cleanup
# ────────────────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	rm -rf $(DIST_DIR)/ build/ *.egg-info/ .pytest_cache/ __pycache__/
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
