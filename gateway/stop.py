"""Stop this checkout's gateway, Node-RED, Ollama, and their descendants."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from gateway.config import PROJECT, load_settings


def local_port(url):
    address = urlsplit(url)
    if address.hostname in ('localhost', '127.0.0.1', '::1'):
        return address.port or (443 if address.scheme == 'https' else 80)
    return None


def project_service(process, service_ports):
    arguments = process.info['cmdline'] or []
    if not arguments:
        return False
    executable = Path(arguments[0]).name.lower()
    gateway = executable.startswith('python') and any(
        arguments[index:index + 2] == ['-m', 'gateway.run'] for index in range(len(arguments) - 1))
    node_red = executable == 'node-red' or (
        executable == 'node' and any('/node-red/' in argument for argument in arguments[1:]))
    ollama = executable == 'ollama' and len(arguments) > 1 and arguments[1] in ('serve', 'runner')
    launcher = executable in ('start_system.sh', 'start_node_red.sh', 'npm start') or (
        executable == 'npm' and 'start' in arguments[1:])
    if not (gateway or node_red or ollama or launcher):
        return False
    cwd = process.info['cwd']
    if cwd and Path(cwd).resolve().is_relative_to(PROJECT):
        return True
    # The launcher can reuse an existing local Ollama/Node-RED service.
    port = service_ports.get('ollama' if ollama else 'node_red' if node_red else '')
    return port is not None and any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
                                    for connection in process.net_connections(kind='tcp'))


def main():
    settings = load_settings()
    ports = {'ollama': local_port(settings.ollama_url), 'node_red': local_port(settings.workflow_url)}
    targets = set()
    for process in psutil.process_iter(['cmdline', 'cwd'], ad_value=None):
        try:
            if project_service(process, ports):
                targets.add(process)
                targets.update(process.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    targets = {process for process in targets if process.pid != os.getpid()}
    if not targets:
        print('System is already stopped.')
        return
    for process in targets:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(targets, timeout=8)
    for process in remaining:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(remaining, timeout=2)
    if remaining:
        raise SystemExit('Could not stop system processes: ' + ', '.join(str(process.pid) for process in remaining))
    print('System stopped. Gateway, Node-RED, Ollama, and their worker processes are closed.')


if __name__ == '__main__':
    main()
