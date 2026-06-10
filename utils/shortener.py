#@suhanibots

import aiohttp
from config import LOGGER
#@suhanibots
log = LOGGER(__name__)
#@suhanibots

async def shorten_url(url: str, api_key: str, domain: str) -> str:
    """
    Shorten a URL using an AdLinkFly-compatible shortener API.

    Args:
        url: The URL to shorten.
        api_key: The user's shortener API key.
        domain: The shortener domain (e.g., 'example.com').

    Returns:
        The shortened URL, or the original URL on failure.
    """
    if not api_key or not domain:
        return url

    # Normalize domain
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    domain = domain.rstrip("/")

    api_url = f"{domain}/api"
    params = {
        "api": api_key,
        "url": url,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        shortened = data.get("shortenedUrl", "")
                        if shortened:
                            log.info(f"URL shortened: {url[:50]}... → {shortened[:50]}...")
                            return shortened

                    # Some APIs return the shortened URL directly as text
                    text = await resp.text()
                    if text.startswith("http"):
                        return text.strip()

                log.warning(
                    f"Shortener API returned status {resp.status} for {url[:50]}..."
                )
                return url

    except aiohttp.ClientError as e:
        log.error(f"Shortener API network error: {e}")
        return url
    except Exception as e:
        log.error(f"Shortener API error: {e}")
        return url
