"""
Internship Scanner Plugin — scrapes public career/internship pages using requests + BeautifulSoup.
Results are stored in the applications table for tracking.
"""
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from app.core.logger import logger

# default headers to avoid bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept-Language": "en-US,en;q=0.9",
}

# configurable list of career pages to scan
SCAN_TARGETS = [
    {
        "name": "Internshala",
        "url": "https://internshala.com/internships/computer-science-internship",
        "parser": "internshala"
    },
]


def _parse_internshala(html: str) -> List[Dict[str, str]]:
    """Parse internshala search results page."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    cards = soup.select(".individual_internship, .internship_meta, [class*='internship-card']")
    if not cards:
        # fallback: try generic heading + company pattern
        headings = soup.select("h3, h4, .heading_4_5, .profile")
        companies = soup.select(".company_name, .company-name, .link_display_like_text")
        for i, h in enumerate(headings[:10]):
            role = h.get_text(strip=True)
            company = companies[i].get_text(strip=True) if i < len(companies) else "Unknown"
            if role and len(role) > 3:
                results.append({"company": company, "role": role, "source": "Internshala"})
    else:
        for card in cards[:10]:
            role_el = card.select_one("h3, .profile, .heading_4_5")
            company_el = card.select_one(".company_name, .company-name, .link_display_like_text")
            role = role_el.get_text(strip=True) if role_el else ""
            company = company_el.get_text(strip=True) if company_el else "Unknown"
            if role and len(role) > 3:
                results.append({"company": company, "role": role, "source": "Internshala"})

    return results


def _parse_generic(html: str, source_name: str) -> List[Dict[str, str]]:
    """Generic parser for career pages — looks for job title patterns."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # look for common job listing patterns
    for tag in soup.select("h2, h3, h4, [class*='title'], [class*='job'], [class*='position']"):
        text = tag.get_text(strip=True)
        if text and 5 < len(text) < 120:
            results.append({"company": source_name, "role": text, "source": source_name})
        if len(results) >= 10:
            break

    return results


PARSERS = {
    "internshala": _parse_internshala,
    "generic": _parse_generic,
}


def scan_internships(targets: Optional[List[Dict]] = None) -> List[Dict[str, str]]:
    """
    Scan configured career pages and return list of found positions.
    Each item: {"company": str, "role": str, "source": str}
    """
    targets = targets or SCAN_TARGETS
    all_results = []

    for target in targets:
        url = target["url"]
        parser_name = target.get("parser", "generic")
        source_name = target.get("name", url)

        try:
            logger.info(f"InternshipScanner: fetching {source_name}...")
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            parser_fn = PARSERS.get(parser_name, _parse_generic)
            if parser_name == "generic":
                results = parser_fn(resp.text, source_name)
            else:
                results = parser_fn(resp.text)

            logger.info(f"InternshipScanner: found {len(results)} listings from {source_name}")
            all_results.extend(results)

        except requests.RequestException as e:
            logger.error(f"InternshipScanner: failed to fetch {source_name}: {e}")
        except Exception as e:
            logger.error(f"InternshipScanner: parse error for {source_name}: {e}")

    return all_results


def scan_and_store() -> str:
    """Scan all targets and store new findings in the applications table."""
    from app.memory.memory_manager import MemoryManager

    results = scan_internships()
    if not results:
        return "No new internships found from configured sources."

    # dedup against existing applications
    existing_apps = MemoryManager.get_applications()
    existing_keys = {(a.company.lower(), a.role.lower()) for a in existing_apps}

    new_count = 0
    for r in results:
        key = (r["company"].lower(), r["role"].lower())
        if key not in existing_keys:
            MemoryManager.add_application(
                company=r["company"],
                role=r["role"],
                status="discovered",
            )
            existing_keys.add(key)
            new_count += 1

    lines = [f"Scan complete. Found {len(results)} total listings."]
    if new_count:
        lines.append(f"Added {new_count} new positions to tracker.")
    else:
        lines.append("No new positions (all already tracked).")

    return "\n".join(lines)
