#!/usr/bin/env python3
"""
CPH Deal Radar: finds cheap return flights from Copenhagen and flags real deals.

Runs on GitHub Actions every few hours, for free. No Claude and no paid services.
Price data: Travelpayouts / Aviasales Data API (free token). The prices come from
Aviasales' cache of searches made in the last few days, so always confirm a deal
on Google Flights or Skyscanner before booking.

Environment variables (set as GitHub repository secrets):
  TRAVELPAYOUTS_TOKEN  required  your Travelpayouts API token
  NTFY_TOPIC           optional  ntfy topic name for phone alerts
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regions import region_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
HISTORY_PATH = ROOT / "data" / "history.json"
OUTPUT_PATH = ROOT / "docs" / "deals.json"

API = "https://api.travelpayouts.com"
USER_AGENT = "cph-deal-radar/1.0 (+github actions)"


# --------------------------------------------------------------------------- helpers

def log(*args):
    print(*args, flush=True)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj, pretty: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(obj, ensure_ascii=False, indent=1)
    else:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


def http_get_json(url: str, params: dict | None = None, token: str | None = None, retries: int = 3):
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        url = url + "?" + urllib.parse.urlencode(clean)
    headers = {"Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
    if token:
        headers["X-Access-Token"] = token
    last_error = "unknown error"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
            last_error = f"HTTP {e.code}: {body.decode('utf-8', 'replace')[:200]}"
            if e.code in (400, 401, 403, 404):
                break  # retrying will not help
        except Exception as e:  # network hiccup, timeout, bad json
            last_error = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_error)


def http_post_json(url: str, payload: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def kr(amount) -> str:
    return f"{int(round(amount)):,}".replace(",", ".") + " kr"


def nice_date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{d.day} {d.strftime('%b')}"


# --------------------------------------------------------------------------- reference data

def load_places(errors: list) -> dict:
    """City/country/airline names from Travelpayouts' free JSON files."""
    places = {"cities": {}, "countries": {}, "airports": {}, "airlines": {}}
    files = {
        "countries": f"{API}/data/en/countries.json",
        "cities": f"{API}/data/en/cities.json",
        "airports": f"{API}/data/en/airports.json",
        "airlines": f"{API}/data/en/airlines.json",
    }
    for kind, url in files.items():
        try:
            rows = http_get_json(url)
        except Exception as e:
            errors.append(f"Could not load {kind} list: {e}")
            continue
        for r in rows:
            code = r.get("code")
            if not code:
                continue
            if kind == "countries":
                places[kind][code] = r.get("name") or code
            elif kind == "cities":
                places[kind][code] = {"name": r.get("name") or code, "country": r.get("country_code")}
            elif kind == "airports":
                places[kind][code] = {"city": r.get("city_code"), "country": r.get("country_code")}
            else:
                places[kind][code] = r.get("name") or code
    return places


def describe_place(code: str, places: dict):
    city = places["cities"].get(code)
    if not city and code in places["airports"]:
        ap = places["airports"][code]
        city = places["cities"].get(ap.get("city")) or {"name": code, "country": ap.get("country")}
    name = city["name"] if city else code
    cc = city.get("country") if city else None
    return name, cc, places["countries"].get(cc, cc or "")


# --------------------------------------------------------------------------- fetching

def months_to_scan(cfg: dict, today: date) -> list[str]:
    out = []
    for offset in range(cfg["scan"]["months_from"], cfg["scan"]["months_to"] + 1):
        m = today.month - 1 + offset
        out.append(f"{today.year + m // 12:04d}-{m % 12 + 1:02d}")
    return out


def _day(s) -> str:
    return (s or "")[:10]


def norm_v3(item: dict):
    dest, dep, ret = item.get("destination"), _day(item.get("departure_at")), _day(item.get("return_at"))
    if not (dest and dep and ret and item.get("price")):
        return None
    return {
        "dest": dest,
        "dest_airport": item.get("destination_airport") or dest,
        "depart": dep,
        "return": ret,
        "price": round(float(item["price"])),
        "stops_out": item.get("transfers"),
        "stops_back": item.get("return_transfers"),
        "airline": item.get("airline"),
        "link": item.get("link"),
    }


def norm_v1(dest: str, stops_key, item: dict):
    dep, ret = _day(item.get("departure_at")), _day(item.get("return_at"))
    if not (dest and dep and ret and item.get("price")):
        return None
    stops = int(stops_key) if str(stops_key).isdigit() else None
    return {
        "dest": dest,
        "dest_airport": dest,
        "depart": dep,
        "return": ret,
        "price": round(float(item["price"])),
        "stops_out": stops,
        "stops_back": None,
        "airline": item.get("airline"),
        "link": None,
    }


def fetch_offers(cfg: dict, token: str, months: list[str], errors: list):
    """Cheapest known round trips from the origin to everywhere, per departure month."""
    origin, currency = cfg["origin"], cfg["currency"]
    max_pages = cfg["scan"].get("max_pages_per_month", 2)
    offers: dict[tuple, dict] = {}
    calls = 0

    def add(o):
        if not o or o["dest"] == origin:
            return
        key = (o["dest"], o["depart"], o["return"])
        old = offers.get(key)
        if old is None or o["price"] < old["price"]:
            if old and not o.get("link"):
                o["link"] = old.get("link")
            offers[key] = o

    for market in cfg["scan"].get("markets") or [""]:
        for month in months:
            # 1) every destination, many date combinations
            for page in range(1, max_pages + 1):
                try:
                    res = http_get_json(f"{API}/aviasales/v3/prices_for_dates", {
                        "origin": origin, "departure_at": month, "one_way": "false",
                        "direct": "false", "sorting": "price", "unique": "false",
                        "limit": 1000, "page": page, "currency": currency, "market": market,
                    }, token)
                    calls += 1
                except Exception as e:
                    errors.append(f"prices_for_dates {month} page {page}: {e}")
                    break
                if res.get("success") is False:
                    errors.append(f"prices_for_dates {month}: {res.get('error')}")
                    break
                rows = res.get("data") or []
                for it in rows:
                    add(norm_v3(it))
                if len(rows) < 1000:
                    break
                time.sleep(0.4)
            # 2) cheapest ticket per destination and number of stops
            for page in range(1, max_pages + 1):
                try:
                    res = http_get_json(f"{API}/v1/prices/cheap", {
                        "origin": origin, "destination": "-", "depart_date": month,
                        "page": page, "currency": currency, "market": market,
                    }, token)
                    calls += 1
                except Exception as e:
                    errors.append(f"prices/cheap {month} page {page}: {e}")
                    break
                data = res.get("data") or {}
                if not isinstance(data, dict) or not data:
                    break
                for dest, by_stops in data.items():
                    for stops_key, it in (by_stops or {}).items():
                        if isinstance(it, dict):
                            add(norm_v1(dest, stops_key, it))
                if len(data) < 100:
                    break
                time.sleep(0.4)
            time.sleep(0.4)
    return list(offers.values()), calls


# --------------------------------------------------------------------------- price history / medians

def update_history(history: dict, offers: list[dict], today: date, window_days: int) -> dict:
    """history[dest|YYYY-MM][YYYY-MM-DD] = [median price that day, number of offers]."""
    groups: dict[str, list[int]] = {}
    for o in offers:
        groups.setdefault(f"{o['dest']}|{o['depart'][:7]}", []).append(o["price"])
    t = today.isoformat()
    for key, prices in groups.items():
        prices.sort()
        n = len(prices)
        med = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
        day = history.setdefault(key, {})
        prev = day.get(t)
        day[t] = [round(med), max(n, prev[1] if prev else 0)]

    cutoff = (today - timedelta(days=window_days)).isoformat()
    this_month = today.isoformat()[:7]
    for key in list(history):
        if key.split("|")[1] < this_month:
            del history[key]
            continue
        days = {d: v for d, v in history[key].items() if d >= cutoff}
        if days:
            history[key] = days
        else:
            del history[key]
    return history


def period_median(history: dict, key: str):
    """Count-weighted median of the daily medians in the window -> (median, samples)."""
    days = list((history.get(key) or {}).values())
    if not days:
        return None, 0
    days.sort(key=lambda v: v[0])
    total = sum(c for _, c in days)
    acc = 0
    for med, count in days:
        acc += count
        if acc * 2 >= total:
            return med, total
    return days[-1][0], total


# --------------------------------------------------------------------------- deals

def build_links(origin: str, o: dict) -> dict:
    dep, ret = o["depart"], o["return"]
    to = o.get("dest_airport") or o["dest"]
    ymd = lambda s: s[2:4] + s[5:7] + s[8:10]
    google = "https://www.google.com/travel/flights?" + urllib.parse.urlencode({
        "q": f"Flights from {origin} to {to} on {dep} through {ret}", "curr": "DKK", "hl": "en",
    })
    skyscanner = (f"https://www.skyscanner.dk/transport/flights/{origin.lower()}/{to.lower()}/"
                  f"{ymd(dep)}/{ymd(ret)}/?adultsv2=1&cabinclass=economy&currency=DKK")
    if o.get("link"):
        aviasales = "https://www.aviasales.com" + o["link"]
    else:
        aviasales = (f"https://www.aviasales.com/search/{origin}{dep[8:10]}{dep[5:7]}"
                     f"{o['dest']}{ret[8:10]}{ret[5:7]}1")
    return {"google": google, "skyscanner": skyscanner, "aviasales": aviasales}


def filter_offers(cfg: dict, offers: list[dict], places: dict, today: date) -> list[dict]:
    regions = cfg["regions"]
    rule = cfg["deal_rule"]
    earliest = today + timedelta(days=cfg["scan"].get("min_days_until_departure", 7))
    max_stops = rule.get("max_stops_each_way", 2)
    excluded = {c.upper() for c in cfg.get("exclude_countries") or []}
    kept = []
    for o in offers:
        try:
            dep, ret = date.fromisoformat(o["depart"]), date.fromisoformat(o["return"])
        except ValueError:
            continue
        if dep < earliest or ret <= dep:
            continue
        name, cc, country = describe_place(o["dest"], places)
        if (cc or "").upper() in excluded:
            continue
        region = region_for(cc)
        reg = regions.get(region) or regions["other"]
        days = (ret - dep).days
        if days < reg.get("min_days", 12) or days > reg.get("max_days", 35):
            continue
        if any(s is not None and s > max_stops for s in (o.get("stops_out"), o.get("stops_back"))):
            continue
        o.update(city=name, country=country, country_code=cc, region=region,
                 region_label=reg.get("label", region), europe=bool(reg.get("europe")), days=days)
        kept.append(o)
    return kept


def find_deals(cfg: dict, offers: list[dict], history: dict, places: dict, state: dict, run_at: datetime) -> list[dict]:
    rule = cfg["deal_rule"]
    regions = cfg["regions"]
    seen = state.setdefault("seen", {})
    candidates = []
    for o in offers:
        reg = regions.get(o["region"]) or regions["other"]
        limit = reg.get("price_limit") or 0
        median, samples = period_median(history, f"{o['dest']}|{o['depart'][:7]}")
        discount = None
        if median and samples >= rule.get("min_samples_for_median", 6):
            discount = 1 - o["price"] / median
        cheap_vs_normal = discount is not None and discount >= rule["min_discount"]
        under_limit = limit > 0 and o["price"] <= limit
        if not (cheap_vs_normal or under_limit):
            continue
        level = "hot" if (cheap_vs_normal and under_limit) else "deal"
        why = "both" if level == "hot" else ("median" if cheap_vs_normal else "limit")
        score = max(discount or 0, (1 - o["price"] / limit) if limit else 0) + (0.25 if level == "hot" else 0)
        deal_id = f"{o['dest']}|{o['depart']}|{o['return']}"
        seen.setdefault(deal_id, iso(run_at))
        candidates.append({
            "id": deal_id,
            "dest": o["dest"], "dest_airport": o.get("dest_airport"),
            "city": o["city"], "country": o["country"], "country_code": o["country_code"],
            "region": o["region"], "region_label": o["region_label"], "europe": o["europe"],
            "depart": o["depart"], "return": o["return"], "days": o["days"],
            "price": o["price"],
            "median": median, "samples": samples,
            "discount": round(discount, 3) if discount is not None else None,
            "price_limit": limit or None,
            "level": level, "why": why, "score": round(score, 4),
            "stops_out": o.get("stops_out"), "stops_back": o.get("stops_back"),
            "airline": o.get("airline"), "airline_name": places["airlines"].get(o.get("airline") or "", None),
            "first_seen": seen[deal_id],
            "links": build_links(cfg["origin"], o),
        })

    # best deal per destination per month, then at most N per destination
    candidates.sort(key=lambda d: (-d["score"], d["price"]))
    best_per_month, per_dest, deals = set(), {}, []
    for d in candidates:
        month_key = (d["dest"], d["depart"][:7])
        if month_key in best_per_month:
            continue
        if per_dest.get(d["dest"], 0) >= rule.get("max_deals_per_destination", 3):
            continue
        best_per_month.add(month_key)
        per_dest[d["dest"]] = per_dest.get(d["dest"], 0) + 1
        deals.append(d)
    return deals


def prune_state(state: dict, today: date):
    t = today.isoformat()
    for bucket in ("seen", "alerted"):
        items = state.get(bucket) or {}
        state[bucket] = {k: v for k, v in items.items() if k.split("|")[1] >= t}


# --------------------------------------------------------------------------- alerts

def stops_text(d: dict) -> str:
    s = d.get("stops_out")
    if s is None:
        return "stops unknown"
    return "direct" if s == 0 else f"{s} stop{'s' if s > 1 else ''}"


def deal_reason(d: dict) -> str:
    if d.get("why") in ("median", "both"):
        reason = f"{round(d['discount'] * 100)}% under normal ({kr(d['median'])})"
        if d["why"] == "both":
            reason += f" and under your {kr(d['price_limit'])} limit"
        return reason
    return f"Under your {kr(d['price_limit'])} limit for {d['region_label']}"


def send_alerts(cfg: dict, deals: list[dict], state: dict, topic: str, page_url: str, run_at: datetime, errors: list) -> int:
    a = cfg.get("alerts") or {}
    if not (a.get("enabled") and topic):
        return 0
    server = (a.get("ntfy_server") or "https://ntfy.sh").rstrip("/") + "/"
    alerted = state.setdefault("alerted", {})
    drop = a.get("realert_if_price_drops", 0.05)

    eligible = [d for d in deals
                if (a.get("include_europe") or not d["europe"])
                and (a.get("min_level", "deal") == "deal" or d["level"] == "hot")]
    fresh = [d for d in eligible
             if d["id"] not in alerted or d["price"] <= alerted[d["id"]]["price"] * (1 - drop)]
    fresh.sort(key=lambda d: (-d["score"], d["price"]))
    one_per_city, cities = [], set()
    for d in fresh:  # only the best new deal per destination gets its own alert
        if d["dest"] not in cities:
            cities.add(d["dest"])
            one_per_city.append(d)

    def post(payload):
        try:
            http_post_json(server, payload)
            return True
        except Exception as e:
            errors.append(f"ntfy alert failed: {e}")
            return False

    def mark(ds):
        for d in ds:
            alerted[d["id"]] = {"price": d["price"], "at": iso(run_at)}

    actions = [{"action": "view", "label": "All deals", "url": page_url}] if page_url else []
    sent = 0

    if not state.get("alerts_initialized"):
        # first run with alerts on: one summary instead of a flood
        world = [d for d in deals if not d["europe"]]
        top = ", ".join(f"{d['city']} {kr(d['price'])}" for d in world[:3]) or "nothing yet"
        if post({"topic": topic, "title": "CPH deal radar is live",
                 "message": f"{len(world)} deals outside Europe right now. Top: {top}",
                 "tags": ["airplane", "tada"], "priority": 3,
                 **({"click": page_url} if page_url else {})}):
            sent += 1
            state["alerts_initialized"] = True
            mark(eligible)
        return sent

    for d in one_per_city[: a.get("max_per_run", 5)]:
        title = f"{'🔥 ' if d['level'] == 'hot' else ''}{d['city']} {kr(d['price'])} return"
        msg = (f"CPH → {d['city']}, {d['country']}\n"
               f"{nice_date(d['depart'])} → {nice_date(d['return'])} ({d['days']} days) · {stops_text(d)}\n"
               f"{deal_reason(d)}")
        if post({"topic": topic, "title": title, "message": msg,
                 "tags": ["airplane"], "priority": 4 if d["level"] == "hot" else 3,
                 "click": d["links"]["google"], "actions": actions}):
            sent += 1
    extra = len(fresh) - min(len(one_per_city), a.get("max_per_run", 5))
    if extra > 0:
        if post({"topic": topic, "title": f"+{extra} more new deals",
                 "message": "Open the deal page to see them all.", "tags": ["airplane"], "priority": 2,
                 **({"click": page_url} if page_url else {})}):
            sent += 1
    mark(fresh)
    return sent


# --------------------------------------------------------------------------- main

def page_url_from_env(cfg: dict) -> str:
    if cfg.get("page_url"):
        return cfg["page_url"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def base_output(cfg: dict, run_at: datetime, months: list[str]) -> dict:
    return {
        "generated_at": iso(run_at),
        "sample": os.environ.get("SCANNER_SAMPLE") == "1",
        "origin": cfg["origin"],
        "currency": cfg["currency"].upper(),
        "months_scanned": months,
        "rules": {
            "min_discount": cfg["deal_rule"]["min_discount"],
            "median_window_days": cfg["deal_rule"]["median_window_days"],
            "min_samples_for_median": cfg["deal_rule"]["min_samples_for_median"],
            "max_stops_each_way": cfg["deal_rule"].get("max_stops_each_way"),
        },
        "regions": [
            {"key": k, "label": v.get("label", k), "price_limit": v.get("price_limit") or None,
             "min_days": v.get("min_days"), "max_days": v.get("max_days"), "europe": bool(v.get("europe"))}
            for k, v in cfg["regions"].items()
        ],
        "stats": {},
        "errors": [],
        "deals": [],
    }


def main(run_at: datetime | None = None) -> int:
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        log("config.json missing or invalid")
        return 1
    run_at = run_at or datetime.now(timezone.utc)
    today = run_at.date()
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    months = months_to_scan(cfg, today)
    out = base_output(cfg, run_at, months)
    previous = load_json(OUTPUT_PATH, {})

    if not token:
        out["errors"].append("No API token yet. Add TRAVELPAYOUTS_TOKEN under Settings → "
                             "Secrets and variables → Actions in your GitHub repository.")
        out["deals"] = previous.get("deals", [])
        save_json(OUTPUT_PATH, out)
        log(out["errors"][0])
        return 0

    errors: list[str] = []
    state = load_json(STATE_PATH, {})
    history = load_json(HISTORY_PATH, {})

    places = load_places(errors)
    raw, calls = fetch_offers(cfg, token, months, errors)
    log(f"API calls: {calls}, raw offers: {len(raw)}")

    if not places["cities"]:
        raw = []  # without city/country data we can't tell Europe from the rest, so skip this run
        errors.append("City list missing, so destinations can't be sorted into regions.")

    if not raw:
        out["errors"] = errors[:20] + ["No prices came back this run; showing the previous deals."]
        out["deals"] = previous.get("deals", [])
        out["stale"] = True
        out["stats"] = {"api_calls": calls, "offers_seen": 0}
        save_json(OUTPUT_PATH, out)
        log("\n".join(out["errors"]))
        return 0

    offers = filter_offers(cfg, raw, places, today)
    history = update_history(history, offers, today, cfg["deal_rule"]["median_window_days"])
    deals = find_deals(cfg, offers, history, places, state, run_at)

    alerts_sent = send_alerts(cfg, deals, state, topic, page_url_from_env(cfg), run_at, errors)
    prune_state(state, today)

    out["deals"] = deals
    out["errors"] = errors[:20]
    out["stats"] = {
        "api_calls": calls,
        "offers_seen": len(raw),
        "offers_after_filters": len(offers),
        "destinations_seen": len({o["dest"] for o in offers}),
        "deals_world": sum(1 for d in deals if not d["europe"]),
        "deals_europe": sum(1 for d in deals if d["europe"]),
        "alerts_sent": alerts_sent,
    }
    save_json(OUTPUT_PATH, out)
    save_json(STATE_PATH, state)
    save_json(HISTORY_PATH, history)
    log(json.dumps(out["stats"]))
    for e in errors[:10]:
        log("warning:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
