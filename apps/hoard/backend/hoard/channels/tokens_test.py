"""Store tokens are kept until shortly before they expire, or forgotten."""

from __future__ import annotations

from hoard.channels.tokens import EXPIRY_SKEW_SECONDS, Tokens


async def test_a_token_is_kept_while_fresh_and_asked_for_again_after():
    tokens, issued = Tokens(), []

    def issuing(lifetime: float):
        async def issue() -> tuple[str, float]:
            issued.append(lifetime)
            return f"token-{len(issued)}", lifetime

        return issue

    assert await tokens.get("shop", issuing(86400)) == "token-1"
    assert await tokens.get("shop", issuing(86400)) == "token-1"
    assert await tokens.get("other", issuing(86400)) == "token-2"
    tokens.forget("shop")
    assert await tokens.get("shop", issuing(EXPIRY_SKEW_SECONDS)) == "token-3"
    # One that lasts no longer than the skew is never kept.
    assert await tokens.get("shop", issuing(86400)) == "token-4"
