# Contributing to Life360 Integration

Thank you for your interest in contributing to the Life360 Home Assistant integration!

## Development Setup

### Prerequisites

- Python 3.12 or newer
- Home Assistant development environment (optional but recommended)

### Local Development

1. Clone the repository:
   ```bash
   git clone https://github.com/pnbruckner/ha-life360.git
   cd ha-life360
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements_test.txt
   ```

3. Run tests locally:
   ```bash
   pytest
   ```

## CI/CD Workflows

### Validate Workflow (`.github/workflows/validate.yml`)

The validation workflow runs three jobs to ensure code quality:

| Job | Description |
|-----|-------------|
| `validate-hassfest` | Runs Home Assistant's hassfest validation to check manifest.json, code structure, and integration requirements |
| `validate-hacs` | Runs HACS validation to ensure compatibility with the Home Assistant Community Store |
| `validate-pytest` | Runs the test suite with pytest against multiple Home Assistant versions |

#### Trigger Configuration

The workflow is currently configured for **manual dispatch only** (`workflow_dispatch`). To re-enable automatic triggers on push/PR, uncomment these lines in `validate.yml`:

```yaml
on:
  pull_request:
  push:
```

#### Running the Workflow Manually

1. Go to the repository's **Actions** tab
2. Select **Validate** workflow
3. Click **Run workflow**

#### Test Matrix

Tests run against multiple Home Assistant versions:
- HA 2025.6.1 (Python 3.13)
- HA 2025.9.1 (Python 3.13)

## Code Style

- Follow PEP 8 guidelines
- Use type hints for function parameters and return values
- Add docstrings for public methods
- Keep imports organized (standard library, third-party, local)

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-new-feature`
3. Make your changes
4. Run tests: `pytest`
5. Commit with a descriptive message
6. Push to your fork
7. Open a Pull Request

### Commit Message Guidelines

- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Fix bug" not "Fixes bug")
- Keep the first line under 72 characters
- Reference issues when applicable: "Fix login error (#123)"

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_init.py

# Run with coverage
pytest --cov=custom_components/life360
```

### Writing Tests

- Place tests in the `tests/` directory
- Use `pytest` fixtures from `conftest.py`
- Mock external API calls
- Test both success and error scenarios

## API Documentation

If you discover new Life360 API endpoints while contributing:

1. Document them in `docs/api_endpoints.md`
2. Include the HTTP method, URL, and response format
3. Redact any sensitive data in examples

## Questions?

- Check existing [issues](https://github.com/pnbruckner/ha-life360/issues)
- Ask in the [Home Assistant Community Forum](https://community.home-assistant.io/)
