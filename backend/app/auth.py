import os
import secrets

from fastapi import Request


def current_user(request: Request):
    return request.session.get("username")


def valid_credentials(username, password):
    return secrets.compare_digest(username, os.getenv("SUPERVISOR_USERNAME", "supervisor")) and secrets.compare_digest(
        password, os.getenv("SUPERVISOR_PASSWORD", "classroom-demo"))
