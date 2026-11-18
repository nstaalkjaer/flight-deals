# CPH Deal Radar

A free, self-running flight deal finder for return flights from Copenhagen.

- **Scanner:** runs every 30 minutes on GitHub Actions (free for public repositories). It doesn't use Claude or any tokens.
- **Prices:** from the Travelpayouts / Aviasales Data API (free token).
- **Live page:** GitHub Pages (free) at `https://nstaalkjaer.github.io/flight-deals/`.
- **Phone alerts:** the free ntfy app.

## What counts as a deal

A flight counts if **either** of these is true:

1. it's at least **30% under the median price** for that destination in that departure month (the median uses the last 30 days of scans), or
2. it's under the **price limit** for its region (for example, 3.000 kr to Southeast Asia).

If both are true, it's marked 🔥 **hot**.

The best of the best are marked 💰 **BUY BUY BUY**: at least **45% under the median**, or at most **60% of the region's price limit** (for example, 1.800 kr or less to Southeast Asia). Only BUY BUY BUY deals outside Europe send phone alerts.

Trips outside Europe must be 12–35 days. Europe has its own tab with 3–21 day trips, and Europe deals don't send phone alerts. Layovers are fine, up to 2 each way.

The **All prices** tab shows the cheapest price to every destination, deal or not, grouped by country.

Turkey is left out completely (`exclude_countries` in `config.json`). The UK, Norway, Iceland, Switzerland and the Balkans are in the Europe tab.

You can change all of this in `config.json`. To move a country into a different region, edit `scanner/regions.py`.

## Setup (about 15 minutes)

### 1. Put the files on GitHub
1. Create a free account at github.com.
2. Click **New repository**, name it `flight-deals`, and choose **Public**. Free Actions and Pages need a public repository. Your API key stays secret either way.
3. Click **Add file → Upload files** and drag in everything from this folder.
   - The `.github` folder is hidden on Mac. In Finder, press **Cmd + Shift + .** to show it.
   - If it still doesn't upload, click **Add file → Create new file**, type `.github/workflows/scan.yml` as the name, and paste in the contents of that file.

### 2. Get a free Travelpayouts API token
1. Sign up at travelpayouts.com.
2. When it asks for your project or website, give your GitHub page address: `https://nstaalkjaer.github.io/flight-deals/`.
3. Join the **Aviasales** program.
4. Copy your token from **Profile → API token**.

### 3. Add two secrets on GitHub
In your repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `TRAVELPAYOUTS_TOKEN` | your token from step 2 |
| `NTFY_TOPIC` | a private, random topic name (don't share it) |

### 4. Turn on the web page
Go to **Settings → Pages**. Under Source, choose **Deploy from a branch**, then branch **main** and folder **/docs**, and click Save.
After a minute or two, the page is live at `https://nstaalkjaer.github.io/flight-deals/`.

### 5. Turn on phone alerts
Install **ntfy** (App Store or Google Play), tap **+**, and subscribe to the same topic name you used for `NTFY_TOPIC`.
Anyone who knows the topic name can read the alerts, so keep it random.

### 6. Run the first scan
Go to **Actions → Scan flights → Run workflow**. After about a minute:
- the page shows deals, and
- your phone gets one message, "CPH deal radar is live". After that, you only get alerts for new BUY BUY BUY deals (up to 5 per scan).

## Good to know
- Prices come from searches other people made in the last few days, so they can be out of date. Always confirm the price on Google Flights or Skyscanner before booking. Each deal card has buttons for both.
- The median needs a few days of scans to settle. In the first week, most deals come from the region price limits.
- GitHub sometimes starts scheduled runs 10–30 minutes late. That's normal.
- GitHub pauses scheduled jobs in repositories with no activity for 60 days. The scanner saves its results after every run, which should count as activity. If runs ever stop, open **Actions** and click **Enable workflow**.
- Alerts are set to BUY BUY BUY only (`"min_level": "buy"` in `config.json`). Use `"hot"` or `"deal"` for more alerts.

## Files
| File | What it does |
|---|---|
| `config.json` | Your settings: price limits, trip lengths, the 30% rule, alerts |
| `scanner/scan.py` | The scanner (plain Python, nothing to install) |
| `scanner/regions.py` | Which country belongs to which region |
| `.github/workflows/scan.yml` | The schedule (every 30 minutes) |
| `docs/index.html` | The live deal page |
| `docs/deals.json` | The latest deals (written by the scanner) |
| `data/` | Price history and alert memory (written by the scanner) |
