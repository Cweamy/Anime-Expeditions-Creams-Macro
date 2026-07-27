# Contributing Guidelines

Guidelines for contributing code, documentation, and assets to Creams Macro.

---

## Requirements

Install prerequisites before starting development:

- **Python 3.10+**: Core backend logic and tests.
- **Node.js**: Syntax validation for `ui/app.js`.
- **pytest**: Test runner (listed in `requirements-dev.txt`).

### Environment Setup

1. Clone repository:
   ```bash
   git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
   cd Anime-Expeditions-Creams-Macro
   ```

2. Create virtual environment:
   ```bash
   python -m venv venv
   # Windows PowerShell:
   .\venv\Scripts\Activate.ps1
   # Linux/macOS:
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

---

## Development and Testing

Run local checks before opening a Pull Request.

### Unit Tests

Run unit tests:

```bash
python -m pytest tests/
```

### Frontend Syntax Check

Validate `ui/app.js` syntax:

```bash
node --check ui/app.js
```

---

## Image Asset Structure

The application loads image assets from `Assets/` at runtime instead of bundling them inside the binary, allowing direct asset editing.

### Directory Layout

- **`Assets/ui/`**: Target templates searched by `core.vision.find_image`.
- **`Assets/maps/`**: Map templates used by `core.stage_select`.

### Image Variant Rules

- **Folder Per Target**: Store assets in subfolders named after the target element (e.g., `Assets/ui/start_button/` or `Assets/maps/map_1/`).
- **PNG Variants**: The image matcher treats all `.png` files inside a target folder as valid variants for matching different resolutions or color schemes.

---

## Release Workflow

GitHub Actions automates release builds when pushing an annotated Git tag adhering to Semantic Versioning (`vX.Y.Z`).

### Release Steps

1. Update `VERSION` if necessary.
2. Create an annotated tag:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   ```
3. Push the tag:
   ```bash
   git push origin vX.Y.Z
   ```

---

## Submitting Pull Requests

1. Fork the repository and create a branch:
   ```bash
   git checkout -b feature/name
   # or
   git checkout -b docs/name
   ```
2. Commit changes cleanly.
3. Run tests and syntax checks locally:
   - `python -m pytest tests/`
   - `node --check ui/app.js`
4. Push branch to fork:
   ```bash
   git push -u origin feature/name
   ```
5. Open a Pull Request against `main` using the PR template.
