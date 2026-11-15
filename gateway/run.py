"""Start the local workflow, media gateway, and AI services together."""
import argparse
import asyncio
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess

from aiohttp import ClientSession, ClientTimeout, web

from gateway.config import LOCAL, PROJECT, control_token, load_settings
from gateway.server import create_app


async def reachable(client, url, token):
    try:
        async with client.get(url, headers={"Authorization": "Bearer " + token},
                              timeout=ClientTimeout(total=2)) as response:
            return response.status == 200
    except (OSError, TimeoutError):
        return False


async def ensure_service(client, url, command, token, children, environment):
    if await reachable(client, url, token):
        return
    child = subprocess.Popen(command, cwd=PROJECT, env=environment)
    children.append(child)
    for _ in range(80):
        if child.poll() is not None:
            raise RuntimeError(f"Service exited during startup: {command[0]}")
        if await reachable(client, url, token):
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Service did not start: {url}")


def wifi_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 80))
            return connection.getsockname()[0]
    except OSError:
        return "<gateway Wi-Fi address>"


async def serve(http_only=False):
    settings = load_settings()
    token = control_token()
    children = []
    runner = None
    environment = {**os.environ, "OLLAMA_MODELS": str(LOCAL / "ollama/models"),
                   "OLLAMA_HOST": "127.0.0.1:11434", "LANGSMITH_TRACING": "false",
                   "MPLCONFIGDIR": str(LOCAL / "matplotlib"), "HF_HOME": str(LOCAL / "models/huggingface")}
    try:
        async with ClientSession() as client:
            ollama = shutil.which("ollama")
            if ollama is None:
                raise RuntimeError("Install Ollama, then run tools/setup.sh.")
            await ensure_service(client, settings.ollama_url + "/api/tags", [ollama, "serve"],
                                 token, children, environment)
            async with client.get(settings.ollama_url + "/api/tags") as response:
                models = await response.json()
            if not any(item["name"] == settings.ollama_model for item in models.get("models", [])):
                raise RuntimeError(f"Model {settings.ollama_model} missing. Run tools/setup.sh first.")
            await ensure_service(client, settings.workflow_url.replace("/event", "/health"),
                                 [str(PROJECT / "tools/start_node_red.sh")], token, children, environment)
        app = create_app(system=True)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 8080).start()
        if not http_only:
            certificate = LOCAL / "tls/gateway.crt"
            key = LOCAL / "tls/gateway.key"
            if not certificate.exists() or not key.exists():
                raise RuntimeError("Phone HTTPS certificate missing. See README.md for certificate setup.")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certificate, key)
            await web.TCPSite(runner, "0.0.0.0", 8443, ssl_context=context).start()
        print("\nUser interface: http://localhost:8080/", flush=True)
        print("Admin monitor: use the Admin button in the app", flush=True)
        print("Node-RED workflow: http://localhost:1880/", flush=True)
        if not http_only:
            print(f"Phone: https://{wifi_address()}:8443/", flush=True)
        print(f"Device mode: SIMULATED | Local LLM: {settings.ollama_model}\n", flush=True)
        await asyncio.Event().wait()
    finally:
        if runner:
            await runner.cleanup()
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    await asyncio.to_thread(child.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    await asyncio.to_thread(child.wait)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-only", action="store_true", help="Local browser testing without phone HTTPS")
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.http_only))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Startup failed: {error}\n")


if __name__ == "__main__":
    main()
