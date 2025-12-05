import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
try:
	import requests
except ImportError:
	requests = None
try:
	from dotenv import load_dotenv
except ImportError:
	load_dotenv = None


CONFIG_PATH = Path(__file__).with_name("links.json")
LISTINGS_PATH = Path(__file__).with_name("listings.json")


def load_sites() -> Dict[str, Dict[str, str]]:
	if not CONFIG_PATH.exists():
		print(f"Error: {CONFIG_PATH} not found.")
		sys.exit(1)
	with CONFIG_PATH.open("r", encoding="utf-8") as fh:
		return json.load(fh)


def normalize_selector(tag: Optional[str]) -> Optional[str]:
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
	try:
		from playwright.sync_api import sync_playwright
	except ImportError:
		print("Playwright is not installed.")
		sys.exit(2)

	results: List[Dict[str, Optional[str]]] = []
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		# for site_name, cfg in list(sites.items())[-1:]:#ONLY SELECTING LAST FOR TESTING
		for site_name, cfg in sites.items():
			url = cfg.get("link")
			tag = cfg.get("tag")
			selector = normalize_selector(tag)
			print(f"Visiting {site_name}: {url} (selector={selector})")
			if not url or not selector:
				print(f"  Skipping {site_name}: missing link or tag")
				continue

			stealth_browser = None
			context = None
			UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
			try:
				stealth_browser = p.chromium.launch(headless=True, channel="chrome", args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox", "--disable-dev-shm-usage"], slow_mo=0)
				context = stealth_browser.new_context(user_agent=UA, viewport={"width":1280, "height":800}, locale="en-US")
				context.add_init_script("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]}); window.chrome = { runtime: {} }; }")
				context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
				page = context.new_page()
			except Exception:
				try:
					stealth_browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox"], slow_mo=0)
					context = stealth_browser.new_context(user_agent=UA, viewport={"width":1280, "height":800}, locale="en-US")
					context.add_init_script("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]}); window.chrome = { runtime: {} }; }")
					context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
					page = context.new_page()
				except Exception as e2:
					print(f"  Failed to start headless-stealth browser for site {site_name}: {e2}")
					page = browser.new_page()
			page.goto(url, wait_until="domcontentloaded", timeout=60000)


			page.wait_for_timeout(2000)

			try:
				page.wait_for_selector(selector, timeout=5000)
			except Exception:
				pass

			elems = page.query_selector_all(selector)
			print(f"  Found {len(elems)} elements using selector '{selector}'")

			for el in elems:
				title = el.evaluate("el => { const h3 = el.querySelector('h3'); return h3 ? h3.innerText : '' }")
				title = (title or "").strip()
				
				if not title:
					title = el.evaluate("el => { const pos = el.querySelector('.position-title'); return pos ? pos.innerText : '' }")
					title = (title or "").strip()
				
				if not title:
					title = (el.inner_text() or "").strip()
				
				href = el.get_attribute("href")
				if not href:
					href = el.evaluate("el => { const a = el.querySelector('a'); return a ? a.href : null }")
				if not href:
					href = el.evaluate("el => { const a = el.closest('a'); return a ? a.href : null }")
				
				if not title:
					continue
				if "intern" in title.lower() or "grad" in title.lower() or "early" in title.lower() or site_name.lower() == "":
					if href and not href.startswith("http"):
						from urllib.parse import urljoin
						base_url = url.split("?")[0] if "?" in url else url
						href = urljoin(base_url, href)
					results.append({"site": site_name, "title": title, "url": href})

			page.close()
			try:
				if context:
					context.close()
			except Exception:
				pass
			try:
				if stealth_browser:
					stealth_browser.close()
			except Exception:
				pass
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


def send_sms(
	to_number: str,
	message: str,
	api_key: str,
	from_number: str
) -> bool:
	"""
	Send an SMS message using the Telnyx API.
	
	Args:
		to_number: Recipient phone number in E.164 format (e.g., +1234567890)
		message: The SMS message text to send
		api_key: Telnyx API key for authentication
		from_number: Sender phone number in E.164 format (your Telnyx number)
	
	Returns:
		True if SMS was sent successfully, False otherwise
	"""
	if requests is None:
		print("Error: requests library is not installed. Install it with: pip install requests")
		return False
	
	url = "https://api.telnyx.com/v2/messages"
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json"
	}
	payload = {
		"from": from_number,
		"to": to_number,
		"text": message
	}
	
	try:
		response = requests.post(url, json=payload, headers=headers, timeout=10)
		response.raise_for_status()
		print(f"SMS sent successfully to {to_number}")
		return True
	except requests.exceptions.RequestException as e:
		print(f"Error sending SMS: {e}")
		if hasattr(e, 'response') and e.response is not None:
			try:
				error_detail = e.response.json()
				print(f"Error details: {error_detail}")
			except Exception:
				print(f"Response status: {e.response.status_code}")
				print(f"Response text: {e.response.text}")
		return False


def main() -> None:
	# Load environment variables from .env file
	if load_dotenv is not None:
		load_dotenv()
	else:
		print("Warning: python-dotenv not installed. Install it with: pip install python-dotenv")
	
	# Load Telnyx configuration from environment variables
	telnyx_api_key = os.getenv("TELNYX_API_KEY")
	telnyx_from_number = os.getenv("TELNYX_FROM_NUMBER")
	telnyx_to_number = os.getenv("TELNYX_TO_NUMBER")

	if not telnyx_api_key or not telnyx_from_number or not telnyx_to_number:
		print("telnyx configuration not found in .env file")
		sys.exit(1)
		
	write = "--write" in sys.argv

	sites = load_sites()
	jobs = scrape_sites(sites)
	unique = dedupe_jobs(jobs)

	if not write:
		if not unique:
			print("No intern postings found (scraped results empty).")
			return
		print(f"\n{len(unique)} intern job postings (scraped):")
		for job in unique:
			print(f"- [{job['site']}] {job['title']} -> {job['url']}")
		return

	existing = load_listings()
	new_listings = find_new_listings(unique, existing)

	if not new_listings:
		print("No new intern postings found.")
		return

	print(f"\n{len(new_listings)} new intern job postings found:")
	for job in new_listings:
		print(f"- [{job['site']}] {job['title']} -> {job['url']}")

	all_listings = existing + new_listings
	save_listings(all_listings)
	print(f"\nListings saved. Total postings: {len(all_listings)}")


if __name__ == "__main__":
	main()


