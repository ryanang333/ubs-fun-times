import subprocess

from flask import jsonify


def health():
    try:
        commit_message = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit_message = None

    return jsonify({"status": "ok", "latestCommitMessage": commit_message})
