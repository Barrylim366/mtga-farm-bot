"""Reading Arena's login boundary out of Player.log for the Account Manager.

The Account Manager's **Log In** button has to know one thing before it types
anything: whether the client is already sitting on the login screen (nothing to
log out of) or is signed in to some account (log out first). The screen answers
most of it -- the login screen has no Arena UI anchor, so the bot's own window
detection fails there -- but "no anchor" is also what a loading frame and a
full-screen announcement look like. Player.log separates them.

Measured in a live Player.log (2026-11-05), a logout/login round trip reads:

    [Info   :MTGA Enhancement Suite] Scene loaded: Login
    ALT_Prefab.RegisterOrLoginPanelPrefab
    ALT_Prefab.LoginPanelPrefab          <- the login screen is up
    ...
    [Accounts - Login] Attempting Login with credentials.
    [Accounts - Login] Logged in successfully. Display Name: luteus#52668

so the newest record of the two kinds says where the client is, and a login
written after the login panel means the panel is history. This module is that
comparison and nothing else: no Controller, no screen, no Tk, no I/O.
"""

from __future__ import annotations

import re

import account_balances

# Why a whole tail is needed rather than "the last few lines": after a login the
# client can play for hours without writing either marker, so the newest login
# record sits megabytes back. 1.5 MB is far more than the login screen ever
# writes, and a client sitting on it produces almost no log at all -- so a
# missing login marker in this window really does mean "not on the login screen".
LOGIN_SCREEN_TAIL_BYTES = 1_500_000

# MTGA's own records that the login UI is (or is about to be) on screen.
#
# The panel prefab is the direct one: the Client dumps its prefab list when the
# Login scene loads, and LoginPanelPrefab is in it.
#
# "Sending player back to Login screen." is written when the client is pushed
# back to the login screen. It was measured once AFTER a failed reconnect
# ("Fast login error: -1"), not after a user logout -- the login panel followed
# it either way, so it is still a login-screen state, but it is a hint rather
# than proof, which is why the panel prefab is checked alongside it.
_LOGIN_SCREEN_MARKERS = (
    "ALT_Prefab.LoginPanelPrefab",
    "Sending player back to Login screen.",
)

# Records that only exist once an account is signed in. "MainNav load in" is the
# main navigation bar finishing its load, written on every arrival at Home and
# never before a login.
_SIGNED_IN_MARKERS = (
    "Logged in successfully",
    "MainNav load in",
)


def _newest(text: str, markers: tuple[str, ...]) -> int:
    return max((text.rfind(marker) for marker in markers), default=-1)


def client_is_at_login_screen(log_text: str) -> bool | None:
    """True/False when the log says where the client is, None when it cannot.

    The NEWEST of the two kinds of record wins, so a login written after the
    login panel means the account is in. None is returned when the text holds
    neither kind: a tail from before the client ever logged in during this log
    is not evidence of anything, and guessing there is what would type
    credentials into an unknown screen.
    """
    text = str(log_text or "")
    if not text:
        return None
    login_at = _newest(text, _LOGIN_SCREEN_MARKERS)
    signed_in_at = _newest(text, _SIGNED_IN_MARKERS)
    if login_at < 0 and signed_in_at < 0:
        return None
    return login_at > signed_in_at


def same_account(first: str | None, second: str | None) -> bool:
    """True when two spellings name the same Arena account.

    The same account is logged with and without its `#digits` discriminator (see
    account_balances), so a spelling without one is matched on the visible name
    and ignores case. When BOTH carry a discriminator it is compared whole: two
    accounts can share a visible name and differ only by those digits, and
    treating them as one would report "already signed in" for the wrong account.
    An empty name matches nothing -- it is not an identity.
    """
    left = str(first or "").strip()
    right = str(second or "").strip()
    if not left or not right:
        return False
    if "#" in left and "#" in right:
        return left.casefold() == right.casefold()
    key = account_balances.account_key(left)
    return bool(key) and key == account_balances.account_key(right)


# The two records that name the account that just signed in, in the order MTGA
# writes them: the login record carries the discriminator, the match-server
# handshake often does not.
_LOGIN_NAME_RE = re.compile(r"Logged in successfully\.\s*Display Name:\s*([^()\r\n]+)")
_HANDSHAKE_NAME_RE = re.compile(r'"screenName"\s*:\s*"([^"]+)"')


def newest_login_name(log_text: str) -> str | None:
    """The account named by the NEWEST sign-in record in the text, or None.

    Which account is signed in right now is a fact of the log, not of the
    configuration: the user can change accounts inside Arena by hand, and the
    Account Manager should mark the row that is actually in. Taking the last
    match in the tail (of either record) keeps that current.
    """
    text = str(log_text or "")
    if not text:
        return None
    best_position = -1
    best_name: str | None = None
    for pattern in (_LOGIN_NAME_RE, _HANDSHAKE_NAME_RE):
        for match in pattern.finditer(text):
            if match.start() > best_position:
                name = str(match.group(1) or "").strip()
                if name:
                    best_position = match.start()
                    best_name = name
    return best_name


def display_name(name: str | None) -> str:
    """The account name as a person reads it (no discriminator)."""
    return account_balances.display_name(name or "")
