# Contributing to Creams Macro - Anime Expeditions

Thank you for considering contributing to Creams Macro! This document provides guidelines and instructions for developers who want to contribute code, documentation, or assets to the project.

---

## Requirements

Before starting development, ensure you have the following prerequisites installed on your system:

- **Python 3.10+**: Core backend logic, automation, and unit tests require Python 3.10 or higher.
- **Node.js**: Required for syntax checking and inspecting frontend JavaScript components (`ui/app.js`).
- **pytest**: Test runner used for local unit testing (included in `requirements-dev.txt`).

### Setting Up Your Environment

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Cweamy/Anime-Expeditions-Creams-Macro.git
   cd Anime-Expeditions-Creams-Macro
   ```

2. **Set up a Python virtual environment**:
   ```bash
   python -m venv venv
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

---

## Development & Local Testing

Before submitting changes or opening a Pull Request, verify that all local checks pass.

### Running Unit Tests

Run the full unit test suite using `pytest`:

```bash
python -m pytest tests/
```

Ensure all tests pass without errors or regressions.

### Syntax Checking Frontend JavaScript

The web interface logic resides in `ui/app.js`. Validate frontend JavaScript syntax using Node.js:

```bash
node --check ui/app.js
```

---

## Image Asset Structure

Image assets ship loose alongside the executable in the `Assets/` directory rather than being bundled inside the binary. This design ensures assets remain user-editable and extensible without requiring a complete application rebuild.

### Folder Layout & Conventions

- **`Assets/ui/`**: Reference images for UI buttons, dialogs, and screen elements searched via `core.vision.find_image`.
- **`Assets/maps/`**: Reference crops for map-name detection handled in `core.stage_select`.

### Image Variant Rules

- **Folder-per-Name**: Each image target is represented by a subfolder named after the UI target or map (e.g., `Assets/ui/start_button/` or `Assets/maps/map_1/`).
- **Interchangeable `.png` Variants**: Any `.png` file placed inside a target's folder is evaluated as an interchangeable variant during image matching. This allows support for different resolutions, color variations, or UI states.

---

## Git Tag and Release Workflow

Releases are automated through CI/CD pipelines triggered by annotated Git tags adhering to Semantic Versioning (`vX.Y.Z`).

### Release Steps

1. **Update `VERSION`**: Update the version string in the `VERSION` file if required.
2. **Create an Annotated Tag**:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   ```
3. **Push the Tag**:
   ```bash
   git push origin vX.Y.Z
   ```

Pushing an annotated tag `vX.Y.Z` triggers the GitHub Actions workflow to build release executables and publish a GitHub Release.

---

## Submitting Pull Requests

1. **Fork the repository** and create a descriptive branch:
   ```bash
   git checkout -b feature/your-feature-name
   # or for documentation updates:
   git checkout -b docs/your-doc-update
   ```
2. **Make your changes** while keeping commits focused and cleanly formatted.
3. **Run local tests & checks**:
   - `python -m pytest tests/`
   - `node --check ui/app.js`
4. **Push your branch** to your fork:
   ```bash
   git push -u origin feature/your-feature-name
   ```
5. **Open a Pull Request** against `main` on the upstream repository using the provided Pull Request template.
