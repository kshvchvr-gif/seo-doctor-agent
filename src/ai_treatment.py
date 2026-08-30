"""AI treatment engine: turns SEO diagnoses into concrete, ready-to-use fixes."""
import json
import re
import time
import requests
from . import config

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("No JSON object in Gemini response")


def generate_treatment(page: dict, page_diagnosis: dict, related_pages: list, max_retries: int = 2) -> dict:
    """Generate title/meta/keyword/content/internal-link treatment for one priority page."""
    if not config.GEMINI_API_KEY:
        return fallback_treatment(page, page_diagnosis, related_pages)

    query_lines = []
    for q in page_diagnosis.get("top_queries", [])[:5]:
        query_lines.append(
            f'- {q["query"]} | impressions={q["impressions"]} | position={q["position"]:.1f} | CTR={q["ctr"]:.2%}'
        )
    related_lines = [f'- {p.get("title", "")} | {p.get("url", "")}' for p in related_pages[:5]]

    prompt = f"""You are the SEO Doctor for krishnbhakti.com, a Hindi/English Bhagavad Gita and Krishna devotional website.
Analyze the page and its Search Console evidence. Give practical treatment, not generic SEO advice.
Do NOT invent search volume, rankings, backlinks, or facts not supplied. Do not keyword-stuff. Preserve the page's actual topic.
Return ONLY valid JSON with these keys:
primary_keyword, secondary_keywords, recommended_title, recommended_meta_description, recommended_h1,
content_changes (array), faq_questions (array), internal_link_plan (array of objects with url and anchor),
image_seo (object with filename and alt_text), schema_recommendation, priority, expected_reason, caution.
Title should normally be 50-65 characters, meta description 140-165 characters, and H1 should be natural.

CURRENT PAGE
URL: {page.get('url')}
Title: {page.get('title')}
Meta: {page.get('meta_description')}
H1 count/text: {page.get('h1_count')} / {page.get('h1_text')}
Word count: {page.get('word_count')}
Canonical: {page.get('canonical')}
Diagnosis: {page_diagnosis.get('diagnoses')}

SEARCH CONSOLE QUERIES
{chr(10).join(query_lines)}

RELATED EXISTING PAGES
{chr(10).join(related_lines)}

CONTENT EXCERPT
{page.get('text','')[:5000]}
"""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    last_error = ""
    # 429/5xx are transient (retry); 4xx with "no longer available"/"not found" means the model
    # was deprecated - swap to the next model in the chain instead of silently falling back.
    for current in models:
        url = GENERATE_URL.format(model=current, key=config.GEMINI_API_KEY)
        for attempt in range(max_retries):
            try:
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 429 or r.status_code >= 500:
                    last_error = f"{current}: HTTP {r.status_code} (transient)"
                    print(f"   warn: {last_error}, retrying...")
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code != 200:
                    detail = r.json().get("error", {}).get("message", r.text[:200])
                    last_error = f"{current}: HTTP {r.status_code} - {detail}"
                    if "no longer available" in detail or "not found" in detail.lower() or r.status_code == 404:
                        print(f"   warn: {last_error} -> switching model")
                        break  # try next model
                    raise requests.HTTPError(last_error)
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return _normalise(_extract_json(text), page, page_diagnosis, related_pages, model=current)
            except requests.HTTPError:
                print(f"   error: {last_error}")
                if attempt == max_retries - 1:
                    break
                time.sleep(2 ** attempt)
            except Exception as e:
                last_error = f"{current}: {type(e).__name__}: {e}"
                print(f"   error: {last_error}")
                if attempt == max_retries - 1:
                    break
                time.sleep(2 ** attempt)
    return fallback_treatment(page, page_diagnosis, related_pages, reason=last_error)


def _normalise(data, page, diagnosis, related_pages, model=""):
    data.setdefault("primary_keyword", (diagnosis.get("top_queries") or [{"query": ""}])[0].get("query", ""))
    data.setdefault("secondary_keywords", [])
    data.setdefault("recommended_title", page.get("title", ""))
    data.setdefault("recommended_meta_description", page.get("meta_description", ""))
    data.setdefault("recommended_h1", page.get("h1_text", "") or page.get("title", ""))
    data.setdefault("content_changes", [])
    data.setdefault("faq_questions", [])
    data.setdefault("internal_link_plan", [])
    data.setdefault("image_seo", {})
    data.setdefault("schema_recommendation", "Article + BreadcrumbList where appropriate")
    data.setdefault("priority", "HIGH")
    data.setdefault("expected_reason", "")
    data.setdefault("caution", "Validate changes before publishing.")
    data["_source"] = f"gemini:{model}" if model else "fallback"
    return data


def fallback_treatment(page, diagnosis, related_pages, reason=""):
    top = (diagnosis.get("top_queries") or [{}])[0]
    keyword = top.get("query", "").strip()
    title = page.get("title", "")
    meta = page.get("meta_description", "")
    treatment = diagnosis.get("diagnoses", ["SEO_REVIEW"])[0]
    changes = []
    if treatment in ("TITLE_WEAK", "RANK_LOW_AND_WEAK"):
        changes.append("Rewrite title around the highest-impression query while keeping the exact page intent.")
        changes.append("Rewrite meta description with a clear benefit and natural primary keyword.")
    if treatment in ("RANK_LOW", "RANK_LOW_AND_WEAK"):
        changes.extend(["Expand missing subtopics and add genuinely useful examples.", "Add contextual internal links from closely related high-authority pages."])
    if treatment == "SNIPPET_LOSS":
        changes.append("Add unique depth beyond the direct answer: modern-life application, examples, comparison, or FAQ.")
    return {
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "recommended_title": title,
        "recommended_meta_description": meta,
        "recommended_h1": page.get("h1_text", "") or title,
        "content_changes": changes,
        "faq_questions": [],
        "internal_link_plan": [{"url": p.get("url"), "anchor": p.get("title", "")[:70]} for p in related_pages[:3]],
        "image_seo": {"filename": "", "alt_text": ""},
        "schema_recommendation": "Article + BreadcrumbList where appropriate",
        "priority": "HIGH",
        "_source": "fallback",
        "expected_reason": f"Deterministic fallback used because AI generation was unavailable. Last error: {reason or 'GEMINI_API_KEY not set'}.",
        "caution": "Review before publishing; this fallback does not claim search-volume data.",
    }
