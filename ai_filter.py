"""
ai_filter.py
Gemini AI classifier for airstrike headlines.

Uses LangChain + Gemini (lowest-latency model) with Pydantic structured output.
Features:
  - Primary model: gemini-3.1-flash-lite (fastest, cheapest stable)
  - Fallback model: gemini-2.5-flash-lite (budget fallback)
  - Exponential backoff retry on transient errors
  - Automatic fallback on quota exhaustion (429)
"""
import os
import time
import random
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from config import KEYWORD_PREFILTER

load_dotenv()

# Configurable via .env — defaults to latest stable models
PRIMARY_MODEL  = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")

MAX_RETRIES    = 3
BASE_DELAY     = 3.0   # seconds — first retry delay
MAX_DELAY      = 45.0  # seconds — cap on retry delay


class AirstrikeImpact(BaseModel):
    extracted_target: str = Field(
        description="Who or what was targeted by the strike? (e.g., 'Tren de Aragua', 'Iranian military base'). Leave empty if unknown."
    )
    target_classification: str = Field(
        description="Classify the target: SOVEREIGN_MILITARY, TERRORIST_ORG, CARTEL_GANG, REBEL_MILITIA, UNKNOWN."
    )
    geopolitical_reasoning: str = Field(
        description="Analyze whether a strike on this target causes major global market panic or war escalation."
    )
    timeline_reasoning: str = Field(
        description="Is this a breaking live event, or a delayed report/recap of an event that occurred earlier?"
    )
    is_trade_event: bool = Field(
        description=(
            "True if the headline describes a KINETIC, ACTUAL US military OFFENSIVE airstrike/missile strike against a SOVEREIGN NATION/STATE, "
            "OR a direct military attack on the US by a foreign military. "
            "FALSE for strikes against terrorist organizations (like ISIS/Al-Qaeda), purely verbal THREATS/WARNINGS of future bombings, defensive actions, routine news, unrelated events, or post-event reactions (funerals, memorials, anniversaries)."
        )
    )
    trade_direction: str = Field(
        description=(
            "Return 'SELL' for military strikes or escalations (market dumps). "
            "Return 'BUY' for peace deals or major de-escalations (market rallies). "
            "Return 'NONE' if unclear or not a trade event."
        )
    )
    is_current_event: bool = Field(
        description=(
            "True if the ACTUAL AIRSTRIKE/MILITARY ACTION is happening RIGHT NOW or is breaking news. "
            "FALSE if the event is purely a THREAT/WARNING of future action, or if the breaking news is merely a funeral, memorial, anniversary, ceremony, recap, or delayed reaction to an old airstrike."
        )
    )
    is_fake_news_retraction: bool = Field(
        description=(
            "True if the headline is a correction, denial, or explicitly states that "
            "an earlier airstrike report was false, unconfirmed, or retracted."
        )
    )
    is_geopolitically_significant: bool = Field(
        description=(
            "True ONLY if the target involves SOVEREIGN_MILITARY (e.g. US bombing a country, or a country bombing the US) being actually bombed/struck, "
            "as actual kinetic strikes between nations cause global market panic. "
            "FALSE for strikes on TERRORIST_ORG (e.g. ISIS, Al-Qaeda), purely verbal threats/warnings, CARTEL_GANG, or routine local law enforcement."
        )
    )
    is_duplicate: bool = Field(
        description=(
            "True if this headline is reporting on the EXACT SAME event as any of the "
            "recent headlines provided in the prompt context. False ONLY if it "
            "is a distinct, entirely new kinetic event."
        )
    )
    scrape_status: str = Field(
        description="The scrape status provided in the prompt, or 'N/A' if missing."
    )


def _build_model(model_name: str):
    """Build a structured LLM for a given model name."""
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or "your_" in key:
        return None
    try:
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0,
        )
        return llm.with_structured_output(AirstrikeImpact)
    except Exception as e:
        print(f"[ai_filter] Error building model '{model_name}': {e}")
        return None


def initialize_ai():
    """
    Initialize both primary and fallback models.
    Returns a dict with both models (either can be None if init fails).
    """
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or "your_" in key:
        print("[ai_filter] GEMINI_API_KEY is not set or invalid in .env")
        return None

    primary = _build_model(PRIMARY_MODEL)
    if primary:
        print(f"[ai_filter] Primary model ready: {PRIMARY_MODEL}")
    else:
        print(f"[ai_filter] WARNING: Primary model '{PRIMARY_MODEL}' failed to init")

    fallback = _build_model(FALLBACK_MODEL)
    if fallback:
        print(f"[ai_filter] Fallback model ready: {FALLBACK_MODEL}")
    else:
        print(f"[ai_filter] WARNING: Fallback model '{FALLBACK_MODEL}' failed to init")

    if not primary and not fallback:
        print("[ai_filter] CRITICAL: No AI models available!")
        return None

    return {"primary": primary, "fallback": fallback}


def _is_quota_exhausted(error: Exception) -> bool:
    """Check if the error is a quota/rate-limit exhaustion (429)."""
    err_str = str(error).lower()
    return any(keyword in err_str for keyword in [
        "429", "resource_exhausted", "quota", "rate_limit", "rate limit"
    ])


def evaluate_headline(ai_models, headline: str, recent_headlines: list = None, news_context: str = "", scrape_status: str = "N/A") -> AirstrikeImpact:
    """
    Evaluate a headline using primary model with retry + fallback.
    
    ai_models: dict returned by initialize_ai() with 'primary' and 'fallback' keys,
               OR a single structured_llm (backwards compat).
    """
    _FAIL_SAFE = AirstrikeImpact(
        extracted_target="N/A",
        target_classification="UNKNOWN",
        geopolitical_reasoning="Failed to evaluate",
        timeline_reasoning="Failed to evaluate",
        is_trade_event=False,
        trade_direction="NONE",
        is_current_event=False,
        is_fake_news_retraction=False,
        is_geopolitically_significant=False,
        is_duplicate=False,
        scrape_status=scrape_status
    )

    prompt = f"Headline: {headline}\nScrape Status: {scrape_status}\n"
    if news_context:
        prompt += f"\nExtracted Article Context:\n{news_context}\n"

    if recent_headlines:
        prompt += "\n\nRecent headlines we already traded on (for deduplication):\n"
        for rh in recent_headlines:
            prompt += f"- {rh}\n"
        prompt += "\nPlease set 'is_duplicate' to True if the Headline is reporting the same underlying event as any of these recent ones."

    import config
    if getattr(config, 'REQUIRE_GEOPOLITICAL', True):
        geo_prompt = getattr(config, 'GEOPOLITICAL_PROMPT', "True if the event involves geopolitical escalation that could move global markets or cause widespread panic. False for routine, localized strikes against small militant outposts or low-level targets with no broader global consequence.")
        prompt += f"\n\nIMPORTANT GEOPOLITICAL RULE for 'is_geopolitically_significant':\n{geo_prompt}\n\n"
    else:
        prompt += "\n\nNOTE: The geopolitical significance requirement is DISABLED. Set 'is_geopolitically_significant' to True for ALL US airstrikes regardless of market impact.\n\n"


    # Backwards compatibility: if ai_models is a single model (not a dict)
    if not isinstance(ai_models, dict):
        try:
            return ai_models.invoke(prompt)
        except Exception as e:
            print(f"[ai_filter] Evaluation error: {e}")
            return _FAIL_SAFE

    primary  = ai_models.get("primary")
    fallback = ai_models.get("fallback")

    # Try primary model with retries
    if primary:
        result = _invoke_with_retry(primary, prompt, PRIMARY_MODEL)
        if result is not None:
            return result
        print(f"[ai_filter] Primary model '{PRIMARY_MODEL}' exhausted — switching to fallback")

    # Try fallback model with retries
    if fallback:
        print(f"[ai_filter] Using fallback model: {FALLBACK_MODEL}")
        result = _invoke_with_retry(fallback, prompt, FALLBACK_MODEL)
        if result is not None:
            return result
        print(f"[ai_filter] Fallback model '{FALLBACK_MODEL}' also failed")

    print("[ai_filter] All models failed — returning fail-safe (no trade)")
    return _FAIL_SAFE


def _invoke_with_retry(model, prompt_text: str, model_name: str):
    """
    Invoke a model with exponential backoff retry.
    Returns AirstrikeImpact on success, None if all retries exhausted.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return model.invoke(prompt_text)
        except Exception as e:
            print(f"[ai_filter] Raw error: {type(e).__name__}: {e}")
            is_quota = _is_quota_exhausted(e)

            if is_quota and attempt == 1:
                # Quota exhausted on first try — don't waste time retrying, move to fallback
                print(f"[ai_filter] {model_name} quota exhausted on first attempt — skipping retries")
                return None

            if attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
                jitter = random.uniform(0, delay * 0.3)
                wait = delay + jitter
                print(
                    f"[ai_filter] {model_name} attempt {attempt}/{MAX_RETRIES} failed "
                    f"({'quota' if is_quota else 'transient'}), retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
            else:
                print(f"[ai_filter] {model_name} failed after {MAX_RETRIES} attempts: {e}")
                return None

    return None


if __name__ == "__main__":
    ai = initialize_ai()
    if ai:
        tests = [
            "Senior ISIS leader Ali Husayn al Ulaywi killed in US airstrike in northwest Syria",
            "Progress Cited in U.S.-Iran Talks Despite Trump’s Threat to Resume Bombing",
            "US military says it has launched new strikes on southern Iran",
            "Pentagon strikes alleged drug boat in Caribbean, killing two",
        ]
        for t in tests:
            print(f"\nHeadline: {t}")
            print("Result:", evaluate_headline(ai, t))
