# Releasing `matcha-core`

## 1. Prepare the version

Update `matcha_core/__init__.py` and set `__version__` to the release version.

## 2. Build in a clean environment

```bash
python3 -m venv .venv-release
source .venv-release/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install setuptools wheel build twine
python3 -m build --no-isolation
python3 -m twine check dist/*
```

## 3. Verify install locally

```bash
python3 -m pip install --force-reinstall dist/matcha_core-<version>-py3-none-any.whl
matcha-core --help
```

## 4. Publish to TestPyPI

```bash
python3 -m twine upload --repository testpypi dist/*
```

## 5. Publish to PyPI

```bash
python3 -m twine upload dist/*
```

## Notes

- Use API tokens via `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<token>`.
- If the public license changes, update both `LICENSE` and the `license` metadata in `pyproject.toml`.
- Rebuild artifacts after any metadata change; do not reuse older files from `dist/`.
