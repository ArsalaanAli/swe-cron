import json
import sys
from pathlib import Path
from typing import Dict, List, Optional


CONFIG_PATH = Path(__file__).with_name("links.json")
LISTINGS_PATH = Path(__file__).with_name("listings.json")


def load_sites() -> Dict[str, Dict[str, str]]:
	if not CONFIG_PATH.exists():
		print(f"Error: {CONFIG_PATH} not found.")
		sys.exit(1)
	with CONFIG_PATH.open("r", encoding="utf-8") as fh:
		return json.load(fh)


def normalize_selector(tag: Optional[str]) -> Optional[str]:
	"""Convert a plain token into a class selector; otherwise return tag.

	If tag already contains selector characters (like '.' or '#'), it's
	returned unchanged. If tag is empty or None, returns None.
	"""
	if not tag:
		return None
	t = tag.strip()
	if not t:
		return None
	special = set(".#:[>+~,*()\"'=")
	if any(c in special for c in t):
		return t
	return "." + t


def scrape_sites(sites: Dict[str, Dict[str, str]]) -> List[Dict[str, Optional[str]]]:
	# Import here so script can fail with a clear message if Playwright
	# isn't installed (we avoid catching other errors to keep code simple).
	try:
		from playwright.sync_api import sync_playwright
	except ImportError:
		print("Playwright is not installed. Install with: pip install -r requirements.txt")
		print("Then run: python -m playwright install")
		sys.exit(2)

	results: List[Dict[str, Optional[str]]] = []
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		for site_name, cfg in sites.items():
			url = cfg.get("link")
			tag = cfg.get("tag")
			selector = normalize_selector(tag)
			print(f"Visiting {site_name}: {url} (selector={selector})")
			if not url or not selector:
				print(f"  Skipping {site_name}: missing link or tag")
				continue

			page = browser.new_page()
			page.goto(url, wait_until="domcontentloaded", timeout=60000)
			page.wait_for_timeout(2000)

			elems = page.query_selector_all(selector)
			for el in elems:
				# First, try to find an h3 inside the element (works for Meta/Google)
				title = el.evaluate("el => { const h3 = el.querySelector('h3'); return h3 ? h3.innerText : '' }")
				title = (title or "").strip()
				
				# Try .position-title for Netflix
				if not title:
					title = el.evaluate("el => { const pos = el.querySelector('.position-title'); return pos ? pos.innerText : '' }")
					title = (title or "").strip()
				
				# If no title found yet, use the element's direct text
				if not title:
					title = (el.inner_text() or "").strip()
				
				# Get href: try direct attribute first, then look for nested <a>, then closest <a>
				href = el.get_attribute("href")
				if not href:
					href = el.evaluate("el => { const a = el.querySelector('a'); return a ? a.href : null }")
				if not href:
					href = el.evaluate("el => { const a = el.closest('a'); return a ? a.href : null }")
				
				if not title:
					continue
				if "intern" in title.lower():
					# Convert relative URLs to absolute
					if href and not href.startswith("http"):
						from urllib.parse import urljoin
						base_url = url.split("?")[0] if "?" in url else url
						href = urljoin(base_url, href)
					results.append({"site": site_name, "title": title, "url": href})

			page.close()
		browser.close()

	return results


def dedupe_jobs(jobs: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
	seen = set()
	out: List[Dict[str, Optional[str]]] = []
	for j in jobs:
		key = (j.get("site"), j.get("title"), j.get("url"))
		if key not in seen:
			seen.add(key)
			out.append(j)
	return out


def load_listings() -> List[Dict[str, Optional[str]]]:
	if not LISTINGS_PATH.exists():
		return []
	with LISTINGS_PATH.open("r", encoding="utf-8") as fh:
		return json.load(fh)


def find_new_listings(current: List[Dict[str, Optional[str]]], existing: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
	existing_keys = set()
	for job in existing:
		key = (job.get("site"), job.get("title"), job.get("url"))
		existing_keys.add(key)
	
	new = []
	for job in current:
		key = (job.get("site"), job.get("title"), job.get("url"))
		if key not in existing_keys:
			new.append(job)
	return new


def save_listings(jobs: List[Dict[str, Optional[str]]]) -> None:
	with LISTINGS_PATH.open("w", encoding="utf-8") as fh:
		json.dump(jobs, fh, indent=2, ensure_ascii=False)


def main() -> None:
	sites = load_sites()
	jobs = scrape_sites(sites)
	unique = dedupe_jobs(jobs)

	# Load existing listings
	existing = load_listings()
	
	# Find only new listings
	new_listings = find_new_listings(unique, existing)

	if not new_listings:
		print("No new intern postings found.")
		return

	print(f"\n{len(new_listings)} new intern job postings found:")
	for job in new_listings:
		print(f"- [{job['site']}] {job['title']} -> {job['url']}")

	# Append new listings to the document
	all_listings = existing + new_listings
	save_listings(all_listings)
	print(f"\nListings saved. Total postings: {len(all_listings)}")


if __name__ == "__main__":
	main()


