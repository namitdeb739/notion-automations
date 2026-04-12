# List available recipes
default:
    @just --list


# Create GitHub repo, push, and configure branch protection + Pages (run once)
init-remote visibility="public":
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v gh &>/dev/null || ! gh auth status &>/dev/null 2>&1; then
        echo "gh CLI not found or not authenticated — run: gh auth login"
        exit 1
    fi

    # Create repo if it doesn't exist yet
    if ! gh repo view namitdeb739/notion-automations &>/dev/null 2>&1; then
        gh repo create namitdeb739/notion-automations \
            --{{visibility}} \
            --description "Automation scripts to enhance personal notion usage" \
            --source . \
            --remote origin \
            --push
        echo "✓ Created namitdeb739/notion-automations and pushed"
    else
        # Repo exists — ensure remote is set and push
        if ! git remote get-url origin &>/dev/null 2>&1; then
            git remote add origin https://github.com/namitdeb739/notion-automations.git
        fi
        git push -u origin main
        echo "✓ Pushed to existing repo namitdeb739/notion-automations"
    fi

    REPO=namitdeb739/notion-automations
    BP_JSON=$(mktemp)
    cat > "$BP_JSON" <<'BPEOF'
    {
      "required_status_checks": {
        "strict": true,
        "contexts": ["lint", "type-check", "test (3.11)", "test (3.12)", "test (3.13)", "audit"]
      },
      "enforce_admins": false,
      "required_pull_request_reviews": null,
      "restrictions": null,
      "allow_force_pushes": false,
      "allow_deletions": false
    }
    BPEOF
    if gh api "repos/${REPO}/branches/main/protection" -X PUT --silent --input "$BP_JSON" 2>/dev/null; then
        echo "✓ Branch protection enabled on main"
    else
        echo "⚠ Could not set branch protection (check gh auth permissions)"
    fi
    rm -f "$BP_JSON"
    if gh api "repos/${REPO}/pages" -X POST -f build_type=workflow --silent 2>/dev/null; then
        echo "✓ GitHub Pages enabled (Actions source)"
    else
        echo "⚠ Could not enable GitHub Pages (may already be enabled, or enable manually in Settings > Pages)"
    fi


# Install dependencies and set up dev environment
setup:
    git config core.longpaths true
    uv sync --dev
    uv run pre-commit install

# Run all checks (mirrors CI)
check: lint typecheck test

# Lint and check formatting
lint:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/

# Auto-fix lint and formatting issues
fix:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/


# Type check
typecheck:
    uv run mypy src/


# Run tests
test *args:
    uv run pytest -v {{ args }}


# Run tests with coverage

coverage:
    uv run pytest --cov=src --cov-report=term-missing





# Serve documentation locally
docs:
    uv run mkdocs serve

# Build documentation
docs-build:
    uv run mkdocs build





# Audit dependencies for vulnerabilities
audit:
    uv run pip-audit


# Build package
build:
    uv build

# Bump version, create git tag, and push (usage: just release patch|minor|major)
release bump:
    #!/usr/bin/env bash
    set -euo pipefail
    just check
    uvx bump-my-version bump {{ bump }}
    git push --follow-tags





# Run the CLI application
run *args:
    uv run notion-automations {{ args }}




# Clean build artifacts
clean:
    rm -rf dist/ build/ site/ .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/
    find . -type d -name __pycache__ -exec rm -rf {} +
