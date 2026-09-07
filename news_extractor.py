import httpx
import asyncio
from bs4 import BeautifulSoup
from typing import Tuple

async def fetch_news_context(url: str, timeout: int = 3) -> Tuple[str, str]:
    """
    Fetches the content of a news URL to provide context to the AI.
    Returns a tuple of (extracted_text, scrape_status).
    Limits extraction to the first ~1000 characters to keep LLM context tight.
    """
    if not url or url.lower() == "no url provided":
        return "", "Skipped - No URL"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            
            # Extract text from paragraph tags
            paragraphs = soup.find_all("p")
            text_blocks = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
            
            if not text_blocks:
                return "", "Success - No text paragraphs found"

            full_text = " ".join(text_blocks)
            
            # Truncate to ~1000 chars to keep the LLM fast and cheap
            truncated_text = full_text[:1000]
            if len(full_text) > 1000:
                truncated_text += "..."

            return truncated_text, "Success"

    except httpx.TimeoutException:
        return "", "Skipped - Timeout"
    except httpx.HTTPStatusError as e:
        if e.response.status_code in [403, 401]:
             return "", f"Skipped - Blocked ({e.response.status_code})"
        return "", f"Skipped - HTTP Error {e.response.status_code}"
    except Exception as e:
        return "", f"Skipped - Error ({type(e).__name__})"
