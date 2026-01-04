# Publishing Guide

This repository contains packages for multiple languages. Here is how to publish/deploy them.

## Python Package (`packages/healops_opentelemetry_python`)

The Python package is published to **PyPI**.

### Prerequisites
1.  Ensure you have a PyPI account and access to the project.
2.  Set the `PYPI_API_TOKEN` secret in the GitHub repository settings.

### How to Publish
1.  Update the `version` in `packages/healops_opentelemetry_python/pyproject.toml`.
2.  Commit the change.
3.  Create and push a git tag with the format `python/vX.Y.Z`:
    ```bash
    git tag python/v0.1.0
    git push origin python/v0.1.0
    ```
4.  The GitHub Action `python-publish.yml` will automatically build and upload the package to PyPI.

---

## Go Package (`packages/healops_opentelemetry_go`)

Go packages are distributed via **source control** (Git). There is no central registry upload step; the Go proxy fetches the code directly from GitHub based on tags.

### Prerequisites
1.  Ensure the `module` name in `packages/healops_opentelemetry_go/go.mod` matches the GitHub repository URL + path.
    *   *Example:* If your repo is `github.com/myorg/monorepo`, the module line should be:
        `module github.com/myorg/monorepo/packages/healops_opentelemetry_go`
    *   *Note:* If you intend to move this code to a standalone repository named `healops-opentelemetry-go`, leave the module name as `github.com/healops/healops-opentelemetry-go`.

### How to Publish
1.  Update your code and commit changes.
2.  Create and push a git tag with the format `packages/healops_opentelemetry_go/vX.Y.Z`:
    ```bash
    # Tag format: <path/to/module>/<version>
    git tag packages/healops_opentelemetry_go/v0.1.0
    git push origin packages/healops_opentelemetry_go/v0.1.0
    ```
3.  Go users can now run:
    ```bash
    go get github.com/healops/healops-opentelemetry-go@v0.1.0
    ```
    *(Adjust the path in `go get` to match your actual `go.mod` module path)*.

---

## Node.js Package (`packages/healops-opentelemetry_node`)

The Node.js package is published to **npm**.

### Prerequisites
1.  Set the `NPM_TOKEN` secret in GitHub repository settings.

### How to Publish
1.  Update the version in `package.json`.
2.  Create and push a git tag (e.g., `vX.Y.Z` or `node/vX.Y.Z` if configured):
    ```bash
    git tag v1.0.0
    git push origin v1.0.0
    ```
3.  The `node-publish.yml` workflow will handle the publishing.
