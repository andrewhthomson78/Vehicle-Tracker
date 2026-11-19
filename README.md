# Vehicle Tracker

Tracks MOT, road tax, insurance and servicing for all your vehicles. It runs locally on your Mac.

- **Servicing:** every 12 months or 5,000 miles, whichever comes first (you can change this per vehicle). Mileage deadlines
  are turned into dates using how much you actually drive, learned from your odometer readings, service records
  and MOT history.
- **Maintenance schedule:** each vehicle has a list of manufacturer items (spark plugs, cambelt, brake fluid and so on), each
  with its own interval. The **Next service** tab lists what's due, including items that would go overdue before the service
  after next, plus advisories from the last MOT.
- **Official data:** tax and MOT dates come from DVLA, and the full MOT history with mileages and advisories comes from DVSA.
- **Assistant:** ask questions, or tell it what work was done and it logs it. It can also research a manufacturer's
  schedule on the web and load it once you confirm.
- **Calendar feed:** `http://localhost:8765/calendar.ics` has every deadline with 14-day reminders. In Calendar, use
  File → New Calendar Subscription. It only refreshes while the server is running.

- **Inbox:** add records from anywhere, including from Claude on your phone. Anything dropped into the Google Drive
  folder `Vehicle Tracker Inbox` (in My Drive) is imported within a minute or so while the server is running. See below.

## Run

```bash
pip3 install -r requirements.txt   # only needed for the assistant
cp .env.example .env               # then add your keys
python3 server.py                  # http://localhost:8765
```

To have it start when you log in and keep running in the background, with no Terminal window:

```bash
bash tools/install-service.sh      # --uninstall to stop and remove it
```

It restarts itself if it ever stops, and writes to `data/server.log`. Do this if you use the inbox, since records are
only imported while the tracker is running.

`python3 server.py --demo` runs the app with made-up vehicles in a separate database (`data/demo.db`), so you can try it
without any API keys. Your real data is in `data/garage.db`.

## API keys

| What | Where | Gives you |
|---|---|---|
| DVLA Vehicle Enquiry Service (optional) | developer-portal.driver-vehicle-licensing.api.gov.uk. **Not taking new sign-ups while DVLA upgrades its system.** | Tax status and due date. Until you have this, enter tax dates by hand (check them at vehicleenquiry.service.gov.uk) |
| DVSA MOT History API | documentation.history.mot.api.gov.uk → register | Every MOT test, odometer readings, advisories and failures, recall flag |
| Anthropic API | console.anthropic.com | The assistant |

The DVSA key is the one that matters: it fills in make, model, MOT dates and history. Both government APIs are free. Approval can take a few days. Without keys, you can still type dates in by hand.

## Schedules

The generic templates in `schedules/` are only a starting point. For your actual vehicles, either:
- ask the assistant: *"research the manufacturer schedule for the van"*, or
- add a JSON file in `schedules/` (same format as the generic ones, plus
  `"match": {"make": "Ford", "model": "Transit Custom"}` so new vehicles pick it up automatically).
  `model` can be a list (`["MT-07", "XSR700"]`), and `"years": [2012, 2016]` limits it to vehicles built in those
  years (either end can be `null`), so a classic's schedule doesn't get applied to a modern car with the same name.

Items with no record are treated as **due from new** until you log them or set "last done" on the item.
Log your last service and any big jobs (cambelt, brake fluid and so on) so the dates are right.

## Inbox

With Google Drive for desktop installed, the server watches `My Drive/Vehicle Tracker Inbox` (or `INBOX_DIR` in `.env`)
every minute. Each `.json` file in it is one job: which vehicle, the work record, a mileage reading and any photos or
PDFs to attach (they sit next to it in the folder). The format is at the top of `tracker/inbox.py`.

- Imported jobs move to `processed/`, with what was added written into the file. Jobs that fail move to `failed/`
  with the reason.
- A job with a `ref` (an invoice number, say) is never imported twice.
- The server keeps `vehicles.json` in the folder up to date, so whoever writes jobs can see which vehicles you have.

## Layout

- `server.py`: HTTP server, routes and calendar feed (Python standard library only)
- `tracker/due.py`: due-date and mileage-projection logic
- `tracker/govapi.py`: DVLA and DVSA clients, plus demo data
- `tracker/agent.py`: assistant tools and the Claude tool-use loop
- `tracker/db.py`: SQLite storage
- `tracker/inbox.py`: imports jobs dropped into the inbox folder
- `web/`: front end (plain HTML/JS/CSS)
- `tools/install-service.sh`: runs the server in the background on macOS (launchd)
