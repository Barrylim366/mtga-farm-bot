"""Per-account Arena balances, read straight from the Player.log.

The Account Manager shows what every configured account owns while the bot is
**not** running, so nothing here touches a Controller, the screen or the MTGA
window: it reads log files and nothing else.

The hard part is attribution. MTGA's `InventoryInfo` event states a balance and
never names the account it belongs to -- the same fact behind the inflated
"Affinity2004: 18700" row described in tests/test_account_identity.py. The
running bot only ever needs the current account's balance, so searching backwards
for the newest entry is good enough there. "What does account X have" cannot be
answered that way, and a backward search would book every account's balance
against whichever name happened to log in last. So the file is walked IN ORDER
and each balance is booked against the login that precedes it.

Two things about the identity are measured, not assumed (live Player.log,
2026-11-05):

  * The same account is logged under two spellings. A login record carries the
    discriminator (`mycoides#47724`) while the match-server handshake often does
    not (`mycoides`). Both are the account's own records, so the pair has to
    collapse to one row -- otherwise the table shows every account twice, one
    copy holding a stale balance.
  * Collapsing by visible name alone would be wrong: two accounts can share one
    visible name and differ only by discriminator. A bare spelling is therefore
    merged only when exactly one discriminated spelling exists for that base.

MTGA rotates Player.log to Player-prev.log on restart, so yesterday's balances
of accounts that have not logged in since are only in the older file. Both are
walked, older first, which keeps the attribution sequence intact.
"""

import os
import re

# The two records MTGA writes when an account signs in. Neither outranks the
# other: the later one wins.
_LOGIN_PREFIX = "[Accounts - Login]"
_DISPLAY_NAME_RE = re.compile(r"Logged in successfully\.\s*Display Name:\s*([^()\r\n]+)")
_SCREEN_NAME_RE = re.compile(r'"screenName"\s*:\s*"([^"]+)"')

_INVENTORY_MARKER = '"InventoryInfo"'

# `InventoryInfo` writes its scalars before CustomTokens/Boosters/Cosmetics, all
# inside the first few hundred characters; the window keeps these searches off
# the cosmetics blob (which reaches tens of KB) without needing a JSON parse of
# a line that a truncated log can leave half-written.
_SCAN_WINDOW = 4000

# Field names exactly as MTGA spells them. `WildCardUnCommons` really does carry
# the capital C; `DraftToken` really is singular, and lives under CustomTokens.
_FIELD_PATTERNS = {
    "gold": re.compile(r'"Gold"\s*:\s*(\d+)'),
    "gems": re.compile(r'"Gems"\s*:\s*(\d+)'),
    "draft_tokens": re.compile(r'"DraftToken"\s*:\s*(\d+)'),
    "mythic_wildcards": re.compile(r'"WildCardMythics"\s*:\s*(\d+)'),
    "rare_wildcards": re.compile(r'"WildCardRares"\s*:\s*(\d+)'),
}

BALANCE_FIELDS = ("gold", "gems", "draft_tokens", "mythic_wildcards", "rare_wildcards")

# Provenance carried alongside the numbers. `logged_name` is the spelling the
# balance was actually booked under, which is what a wrong row has to be
# explained with; `sequence` orders two readings of the same account.
LOGGED_NAME_FIELD = "logged_name"
SEQUENCE_FIELD = "sequence"


def _blank_balance() -> dict:
    return {field: 0 for field in BALANCE_FIELDS}


class _Walker:
    """One forward pass over the log, booking each balance against the last login.

    A balance belongs to whoever logged in most recently before it was written,
    so the walk only ever has to answer "who was that" as it goes.
    """

    def __init__(self) -> None:
        self.balances: dict[str, dict] = {}
        self.current: str | None = None
        self.sequence = 0

    # --- the login side ---------------------------------------------------

    def _note_login(self, line: str) -> None:
        if _LOGIN_PREFIX in line:
            match = _DISPLAY_NAME_RE.search(line)
            if match:
                self.current = _clean_name(match.group(1))
                return
        # Guarded by a plain substring test: this runs for millions of lines.
        if '"screenName"' in line:
            match = _SCREEN_NAME_RE.search(line)
            if match:
                self.current = _clean_name(match.group(1))

    # --- the balance side -------------------------------------------------

    def feed(self, line: str) -> None:
        if _INVENTORY_MARKER in line:
            # An InventoryInfo line is never a login record, even when a
            # screenName-shaped field rides along on it.
            if self.current is None:
                # A rotated log can start mid-session. A balance with no login
                # before it cannot be attributed, and guessing is what produced
                # the wrong rows.
                return
            balance = _parse_inventory(line)
            if balance is None:
                return
            self.sequence += 1
            balance[LOGGED_NAME_FIELD] = self.current
            balance[SEQUENCE_FIELD] = self.sequence
            self.balances[self.current] = balance
        else:
            self._note_login(line)

    # --- what comes back --------------------------------------------------

    def finish(self) -> dict[str, dict]:
        _merge_bare_spellings(self.balances)
        return self.balances


def _clean_name(name: str) -> str:
    return str(name).strip().strip(",").strip()


def _parse_inventory(line: str) -> dict | None:
    """The balances carried by one InventoryInfo line, or None if it carries none.

    A missing field reads as 0: the client omits a token/key it has none of, so
    an absent DraftToken is an account with no draft tokens, not an unknown.
    """
    idx = line.find(_INVENTORY_MARKER)
    if idx == -1:
        return None
    window = line[idx:idx + _SCAN_WINDOW]
    balance = _blank_balance()
    found_any = False
    for field, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(window)
        if match:
            balance[field] = int(match.group(1))
            found_any = True
    return balance if found_any else None


def _merge_bare_spellings(balances: dict[str, dict]) -> None:
    """Collapse `Name` and `Name#12345` into one row when they are one account.

    A bare spelling only merges when the base names exactly one discriminated
    account. With two (`Player#11111`, `Player#22222`) the bare record cannot be
    attributed and is left alone rather than being guessed onto one of them.
    """
    groups: dict[str, list[str]] = {}
    for name in balances:
        groups.setdefault(account_key(name), []).append(name)
    for names in groups.values():
        if len(names) < 2:
            continue
        discriminated = [name for name in names if "#" in name]
        if len(discriminated) != 1:
            continue
        keeper = discriminated[0]
        newest = max(names, key=lambda name: balances[name][SEQUENCE_FIELD])
        merged = dict(balances[newest])
        merged[LOGGED_NAME_FIELD] = keeper
        for name in names:
            balances.pop(name, None)
        balances[keeper] = merged


def parse_balances(text: str) -> dict[str, dict]:
    """Balances for every account named in a chunk of log text, newest reading each.

    Keys are the accounts' Arena display names, in the order they first appear.
    """
    walker = _Walker()
    for line in text.splitlines():
        walker.feed(line)
    return walker.finish()


def read_balances(log_paths) -> dict[str, dict]:
    """Balances from Player.log and any older log files, oldest file first.

    Streams the files: a long-lived Player.log reaches 130+ MB, and the whole
    file is never needed at once.
    """
    walker = _Walker()
    for path in log_paths:
        try:
            # UTF-8 with non-Latin display names; replacement keeps one bad byte
            # from losing the rest of the log.
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    walker.feed(line)
        except OSError:
            # A missing Player-prev.log is normal on a fresh install.
            continue
    return walker.finish()


def player_log_paths(log_path: str) -> list[str]:
    """The logs to read for balances: the rotated previous log, then the current one."""
    if not log_path:
        return []
    log_path = str(log_path)
    folder, name = os.path.split(log_path)
    stem, ext = os.path.splitext(name)
    previous = os.path.join(folder, f"{stem}-prev{ext}")
    return [previous, log_path]


def account_key(name: str) -> str:
    """Normalised identity for matching a configured row to a logged balance.

    Arena logs `Name#12345` whenever it knows the discriminator while the
    configured screen name may be the bare `Name`, so the discriminator is
    dropped here. Matching is also case-insensitive: the label is typed by hand.
    """
    return display_name(name).casefold()


def display_name(name: str) -> str:
    """The account name without its discriminator, for showing to the user.

    `Name#12345` becomes `Name`. The digits only disambiguate two accounts that
    share a visible name, which is a matter for matching, not for a list the
    user reads.
    """
    return str(name or "").strip().split("#")[0].strip()


def find_balance(balances: dict[str, dict], *names: str) -> dict | None:
    """The balance for an account, trying each of its known names.

    An exact name wins over a discriminator-free match: with two accounts called
    `Player` the exact spelling is the only evidence about which one this is.
    Within a tier the newest reading wins, because a bare handshake spelling can
    hand the same account a fresher balance than its login record did.
    """
    def sequence_of(row: dict) -> int:
        return int(row.get(SEQUENCE_FIELD, -1))

    # A blank name is not an identity: an unconfigured row must show no balance
    # rather than matching whatever an empty key happens to hold.
    wanted_names = [str(name or "").strip() for name in names]
    wanted_names = [name for name in wanted_names if name]

    exact = [balances[name] for name in wanted_names if name in balances]
    if exact:
        return max(exact, key=sequence_of)

    wanted_keys = {account_key(name) for name in wanted_names}
    loose = [
        row for logged, row in balances.items()
        if account_key(logged) in wanted_keys
    ]
    if loose:
        return max(loose, key=sequence_of)
    return None
