"""Anonymous user sessions and password-protected monitoring."""
from collections import deque
import hmac
import os
import secrets
import time

from aiohttp import web
from dotenv import dotenv_values

from gateway.config import PROJECT

CLIENT = web.RequestKey('client_id', str)
ADMIN = web.RequestKey('admin', bool)
USER_COOKIE = 'ioe_user'
ADMIN_COOKIE = 'ioe_admin'


class Access:
    def __init__(self):
        values = dotenv_values(PROJECT / '.env')
        self.password = os.environ.get('ADMIN_PASSWORD', values.get('ADMIN_PASSWORD') or 'admin')
        self.sessions = {}
        self.failures = {}

    def is_admin(self, request):
        now = time.monotonic()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        return request.cookies.get(ADMIN_COOKIE) in self.sessions

    async def login(self, request):
        now = time.monotonic()
        self.failures = {key: values for key, values in self.failures.items() if now - values[-1] < 60}
        attempts = self.failures.get(request.remote, deque())
        while attempts and now - attempts[0] >= 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise web.HTTPTooManyRequests(text='Too many login attempts. Try again in one minute.')
        params = await request.json()
        password = params.get('password', '')
        if not isinstance(password, str) or not hmac.compare_digest(password.encode(), self.password.encode()):
            attempts.append(now)
            if len(self.failures) < 1024 or request.remote in self.failures:
                self.failures[request.remote] = attempts
            raise web.HTTPUnauthorized(text='Incorrect admin password.')
        self.failures.pop(request.remote, None)
        if len(self.sessions) >= 100:
            self.sessions.pop(next(iter(self.sessions)))
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + 8 * 3600
        response = web.json_response({'admin': True})
        response.set_cookie(ADMIN_COOKIE, token, httponly=True, samesite='Strict',
                            secure=request.secure, max_age=8 * 3600)
        return response

    async def logout(self, request):
        self.sessions.pop(request.cookies.get(ADMIN_COOKIE), None)
        response = web.json_response({'admin': False})
        response.del_cookie(ADMIN_COOKIE)
        return response
