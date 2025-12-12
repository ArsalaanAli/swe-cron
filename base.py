import json
import os
import sys
from datetime import date, timedelta
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
			print(f"Visiting {site_name}")
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
			print(f"  Found {len(elems)} postings")

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


def prune_old_listings(
	listings: List[Dict[str, Optional[str]]],
	max_age_days: int = 60
) -> List[Dict[str, Optional[str]]]:
	today = date.today()
	cutoff = today - timedelta(days=max_age_days)

	pruned: List[Dict[str, Optional[str]]] = []
	for listing in listings:
		listing_date_str = listing.get("date")
		if not listing_date_str:
			pruned.append(listing)
			continue
		try:
			listing_date = date.fromisoformat(str(listing_date_str))
		except ValueError:
			pruned.append(listing)
			continue
		if listing_date >= cutoff:
			pruned.append(listing)
	return pruned


def save_listings(jobs: List[Dict[str, Optional[str]]]) -> None:	
	with LISTINGS_PATH.open("w", encoding="utf-8") as fh:
		json.dump(jobs, fh, indent=2, ensure_ascii=False)


def send_pushover(
	message: str,
	token: str,
	user: str,
	title: str = "SWE Cron"
) -> bool:
	if requests is None:
		print("Error: requests library is not installed. Install it with: pip install requests")
		return False
	
	url = "https://api.pushover.net/1/messages.json"
	payload = {
		"token": token,
		"user": user,
		"message": message,
		"title": title
	}
			
	try:
		response = requests.post(url, data=payload, timeout=10)
		response_json = response.json()
		print(f"Pushover API Response Body: {json.dumps(response_json, indent=2)}")
		response.raise_for_status()
		print(f"Notification sent successfully")
		return True
	except requests.exceptions.RequestException as e:
		print(f"Error sending notification: {e}")
		if hasattr(e, 'response') and e.response is not None:
			try:
				error_detail = e.response.json()
				print(f"Error details: {error_detail}")
			except Exception:
				print(f"Response status: {e.response.status_code}")
				print(f"Response text: {e.response.text}")
		return False

def notify_new_listings(new_listings: List[Dict[str, Optional[str]]], pushover_token: str, pushover_user: str) -> None:
	#send a notification containing details for each new listing
	message = "SWE Cron - New Listings:\n"
	sites = set()
	listings = ""
	for listing in new_listings:
		site = listing.get("site", "Unknown")
		sites.add(site)
		title = listing.get("title", "No title")
		url = listing.get("url", "")
		listings += f"[{site}]\n{title}\n{url}\n\n"
	
	message += f"{', '.join(sites)}\n\n{listings}"
	print(message)
	send_pushover(message, pushover_token, pushover_user)

def main() -> None:
	# Load environment variables from .env file
	if load_dotenv is not None:
		load_dotenv()
	else:
		print("Warning: python-dotenv not installed. Install it with: pip install python-dotenv")
	
	# Load Pushover configuration from environment variables
	pushover_token = os.getenv("PUSHOVER_TOKEN")
	pushover_user = os.getenv("PUSHOVER_USER")

	if not pushover_token or not pushover_user:
		print("Pushover configuration not found in .env file")
		sys.exit(1)
		
	write = "--write" in sys.argv
	
	company_name = None
	for i, arg in enumerate(sys.argv):
		if arg == "--company" and i + 1 < len(sys.argv):
			company_name = sys.argv[i + 1]
			break
		elif arg.startswith("--company="):
			company_name = arg.split("=", 1)[1]
			break

	sites = load_sites()
	
	if company_name:
		company_lower = company_name.lower()
		filtered_sites = {}
		for site_key, site_config in sites.items():
			if site_key.lower() == company_lower:
				filtered_sites[site_key] = site_config
				break
		
		if not filtered_sites:
			print(f"Error: Company '{company_name}' not found in links.json")
			print(f"Available companies: {', '.join(sites.keys())}")
			sys.exit(1)
		
		sites = filtered_sites
		print(f"Scraping only: {list(sites.keys())[0]}")
	
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

	today = date.today().isoformat()
	for listing in new_listings:
		listing["date"] = today

	print(f"\n{len(new_listings)} new intern job postings found")
	# for job in new_listings:
	# 	print(f"- [{job['site']}] {job['title']} -> {job['url']}")

	notify_new_listings(new_listings, pushover_token, pushover_user)

	all_listings = new_listings + existing
	all_listings = prune_old_listings(all_listings)
	save_listings(all_listings)
	print(f"\nListings saved. Total postings: {len(all_listings)}")


if __name__ == "__main__":
	main()


