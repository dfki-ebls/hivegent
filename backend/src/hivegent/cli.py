"""CLI for Hivegent service account management."""

import ipaddress
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

__all__ = ["app"]

app = typer.Typer(
    help="Hivegent CLI for service account management.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    # pretty_exceptions_show_locals=False,
    # pretty_exceptions_short=True,
)

# Default paths
CONFIG_DIR = Path.home() / ".config" / "hivegent"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
DEFAULT_API_URL = "http://localhost:8000"


def _get_credentials() -> dict[str, Any] | None:
    """Load stored credentials."""
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_credentials(credentials: Mapping[str, Any]) -> None:
    """Save credentials securely."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
    # Set restrictive permissions (owner read/write only)
    CREDENTIALS_FILE.chmod(0o600)


def _clear_credentials() -> None:
    """Remove stored credentials."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()


def _get_api_url() -> str:
    """Get the API URL from credentials or default."""
    creds = _get_credentials()
    if creds and "api_url" in creds:
        return creds["api_url"]
    return DEFAULT_API_URL


def _get_token() -> str | None:
    """Get the stored access token."""
    creds = _get_credentials()
    if not creds:
        return None
    return creds.get("access_token")


def _require_auth() -> str:
    """Require authentication and return the token."""
    token = _get_token()
    if not token:
        typer.echo("Not authenticated. Run 'hivegent login' first.", err=True)
        raise typer.Exit(1)
    return token


def _make_request(
    method: str,
    path: str,
    token: str,
    json_data: Mapping[str, Any] | None = None,
    files: Mapping[str, Any] | None = None,
) -> httpx.Response:
    """Make an authenticated request to the API."""
    api_url = _get_api_url()
    url = f"{api_url}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "GET":
                return client.get(url, headers=headers)
            elif method == "POST":
                return client.post(url, headers=headers, json=json_data)
            elif method == "PUT":
                if files:
                    return client.put(url, headers=headers, files=files)
                return client.put(url, headers=headers, json=json_data)
            elif method == "DELETE":
                return client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unknown method: {method}")
    except httpx.HTTPError as e:
        typer.echo(f"Request failed: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def login(
    issuer: Annotated[
        str | None, typer.Option("--issuer", "-i", help="OIDC issuer URL")
    ] = None,
    client_id: Annotated[
        str | None, typer.Option("--client-id", "-c", help="Service account client ID")
    ] = None,
    client_secret: Annotated[
        str | None,
        typer.Option(
            "--client-secret",
            "-s",
            prompt=False,
            hide_input=True,
            help="Client secret (will prompt if not provided)",
        ),
    ] = None,
    api_url: Annotated[
        str, typer.Option("--api-url", "-u", help="API base URL")
    ] = DEFAULT_API_URL,
) -> None:
    """Authenticate with Hivegent using an OIDC client-credentials grant."""
    if not issuer or not client_id:
        typer.echo("Both --issuer and --client-id are required.", err=True)
        raise typer.Exit(1)

    # Prompt for client secret if not provided
    if not client_secret:
        client_secret = typer.prompt("Client secret", hide_input=True)

    # Resolve the token endpoint from the IdP's discovery document so the CLI
    # works against any OIDC provider rather than a hard-coded vendor path.
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"

    try:
        with httpx.Client(timeout=30.0) as client:
            discovery = client.get(discovery_url)
            discovery.raise_for_status()
            token_url = discovery.json()["token_endpoint"]
            response = client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            response.raise_for_status()
            token_data = response.json()
    except httpx.HTTPError as e:
        typer.echo(f"Failed to authenticate: {e}", err=True)
        raise typer.Exit(1)
    except (KeyError, ValueError) as e:
        typer.echo(f"Invalid OIDC discovery document: {e}", err=True)
        raise typer.Exit(1)

    access_token = token_data.get("access_token")
    if not access_token:
        typer.echo("No access token in response.", err=True)
        raise typer.Exit(1)

    _save_credentials(
        {
            "access_token": access_token,
            "refresh_token": token_data.get("refresh_token"),
            "issuer": issuer,
            "client_id": client_id,
            "client_secret": client_secret,
            "api_url": api_url,
        }
    )
    typer.echo("Logged in successfully.")


@app.command()
def logout() -> None:
    """Clear stored credentials."""
    _clear_credentials()
    typer.echo("Logged out.")


@app.command()
def upload(
    file: Annotated[Path, typer.Argument(help="File to upload", exists=True)],
    filename: Annotated[
        str | None, typer.Option("--filename", "-n", help="Target filename")
    ] = None,
) -> None:
    """Upload a document."""
    token = _require_auth()
    target_name = filename or file.name

    with file.open("rb") as f:
        response = _make_request(
            "PUT",
            f"/api/documents/{target_name}",
            token,
            files={"file": (target_name, f, "application/octet-stream")},
        )

    if response.status_code == 200:
        typer.echo(f"Uploaded: {target_name}")
    else:
        try:
            error = response.json().get("detail", "Unknown error")
        except (json.JSONDecodeError, KeyError):
            error = response.text
        typer.echo(f"Upload failed: {error}", err=True)
        raise typer.Exit(1)


@app.command()
def download(
    filename: Annotated[str, typer.Argument(help="Document to download")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output path")
    ] = None,
) -> None:
    """Download a document."""
    token = _require_auth()

    response = _make_request("GET", f"/api/documents/{filename}", token)

    if response.status_code == 200:
        out_path = output or Path(filename)
        out_path.write_text(response.text, encoding="utf-8")
        typer.echo(f"Downloaded: {out_path}")
    elif response.status_code == 404:
        typer.echo(f"Document not found: {filename}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo(f"Download failed: {response.text}", err=True)
        raise typer.Exit(1)


@app.command("list")
def list_docs() -> None:
    """List all documents."""
    token = _require_auth()

    response = _make_request("GET", "/api/documents", token)

    if response.status_code == 200:
        data = response.json()
        documents = data.get("documents", [])
        if not documents:
            typer.echo("No documents found.")
            return

        typer.echo(f"{'Filename':<40} {'Size':>10}")
        typer.echo("-" * 52)
        for doc in documents:
            size = doc["size_bytes"]
            if size >= 1024 * 1024:
                size_str = f"{size / 1024 / 1024:.1f} MB"
            elif size >= 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"
            typer.echo(f"{doc['filename']:<40} {size_str:>10}")
    else:
        typer.echo(f"Failed to list documents: {response.text}", err=True)
        raise typer.Exit(1)


@app.command()
def delete(
    filename: Annotated[str, typer.Argument(help="Document to delete")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
) -> None:
    """Delete a document."""
    if not yes:
        confirm = typer.confirm(f"Delete '{filename}'?")
        if not confirm:
            raise typer.Abort()

    token = _require_auth()

    response = _make_request("DELETE", f"/api/documents/{filename}", token)

    if response.status_code == 200:
        typer.echo(f"Deleted: {filename}")
    elif response.status_code == 404:
        typer.echo(f"Document not found: {filename}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo(f"Delete failed: {response.text}", err=True)
        raise typer.Exit(1)


@app.command()
def whoami() -> None:
    """Show current authentication status."""
    creds = _get_credentials()
    if not creds:
        typer.echo("Not authenticated.")
        return

    api_url = creds.get("api_url", DEFAULT_API_URL)

    typer.echo(f"API URL: {api_url}")
    typer.echo(f"Issuer: {creds.get('issuer', 'N/A')}")
    typer.echo(f"Client ID: {creds.get('client_id', 'N/A')}")


def _is_loopback_host(host: str) -> bool:
    """Return whether *host* binds only to the local machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.command()
def migrate(
    revision: Annotated[
        str,
        typer.Argument(
            help="Target revision; defaults to 'head'. Pass a specific hash "
            "to roll forward/back to that point."
        ),
    ] = "head",
) -> None:
    """Run Alembic migrations against the configured database.

    The API server upgrades the database automatically on startup, so this
    command is only needed for ad-hoc operator use (dry runs, rolling back,
    or applying migrations from a maintenance window).
    """
    from alembic import command

    from .db.migrations import build_alembic_config

    command.upgrade(build_alembic_config(), revision)


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind to")] = 8000,
    reload: Annotated[
        bool, typer.Option("--reload", "-r", help="Enable auto-reload")
    ] = False,
    allow_unsafe_auth_disabled_bind: Annotated[
        bool,
        typer.Option(
            "--allow-unsafe-auth-disabled-bind",
            help="Allow auth-disabled mode on a non-loopback listener.",
        ),
    ] = False,
) -> None:
    """Start the Hivegent API server."""
    from .config import settings

    if not settings.auth.enable and not settings.auth.allow_disabled:
        typer.echo(
            "Authentication is disabled but HIVEGENT_AUTH__ALLOW_DISABLED is not set.",
            err=True,
        )
        raise typer.Exit(1)
    if (
        not settings.auth.enable
        and not _is_loopback_host(host)
        and not allow_unsafe_auth_disabled_bind
    ):
        typer.echo(
            "Refusing to bind an auth-disabled server to a non-loopback host. "
            "Use --host 127.0.0.1 or pass --allow-unsafe-auth-disabled-bind.",
            err=True,
        )
        raise typer.Exit(1)

    import uvicorn

    uvicorn.run(
        "hivegent.server:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
