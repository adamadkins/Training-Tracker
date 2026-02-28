#!/usr/bin/env python3
"""One-off: SSH to server and run deploy.sh. Uses DEPLOY_HOST and DEPLOY_SSH_PASS env vars."""
import os
import sys

def main():
    host = os.environ.get("DEPLOY_HOST", "").strip()
    password = os.environ.get("DEPLOY_SSH_PASS", "").strip()
    if not host or not password:
        print("Set DEPLOY_HOST (e.g. root@159.203.111.92) and DEPLOY_SSH_PASS", file=sys.stderr)
        sys.exit(1)
    if "@" not in host:
        print("DEPLOY_HOST should be user@host", file=sys.stderr)
        sys.exit(1)
    user, hostname = host.split("@", 1)

    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname, username=user, password=password, timeout=30)
        cmd = "cd /opt/training-tracker && sudo -n bash ./deploy/deploy.sh"
        stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
        for line in stdout:
            print(line.rstrip())
        err = stderr.read().decode()
        if err:
            print(err, file=sys.stderr)
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        sys.exit(exit_code)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
