# Deploying to a Contabo VM

End-to-end checklist for going from zero to a daily cron job that scrapes
Upwork, writes to your sheet, and pokes n8n.

## 1. GCP project setup (one-time)

Working in GCP project `upwork-workflow-496808` (or whichever you choose).

1. **Enable the Google Sheets API**
   - GCP Console -> APIs & Services -> Library -> "Google Sheets API" -> Enable.
2. **Create a service account**
   - IAM & Admin -> Service Accounts -> Create.
   - Name: `upwork-scraper`. No project roles needed (the sheet itself gates
     access).
3. **Create a JSON key**
   - On the service account -> Keys -> Add key -> JSON. Save the download
     somewhere safe; we'll move it to the VM next.
4. **Share the target sheet with the service account**
   - Open the Google Sheet (default in `.env.example`:
     `1wsLPktPzfIdf0dSKX0Ghxa21mnI8kdJ2FaZLAmX27QQ`).
   - Share -> add the service account email
     (looks like `upwork-scraper@upwork-workflow-496808.iam.gserviceaccount.com`)
     with **Editor** permission.

## 2. VM bootstrap

```bash
ssh user@your-contabo-host

sudo mkdir -p /opt/upwork-vm/{state,logs,secrets}
sudo chown -R "$USER":"$USER" /opt/upwork-vm
```

Upload secrets and config (run locally):

```bash
scp /path/to/sa.json   user@vm:/opt/upwork-vm/sa.json
scp .env               user@vm:/opt/upwork-vm/.env
```

Tighten permissions on the VM:

```bash
chmod 600 /opt/upwork-vm/sa.json /opt/upwork-vm/.env
```

Inside `/opt/upwork-vm/.env`, make sure
`GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa.json` matches the mount path used
in the cron line (it does by default).

## 3. Build the image on the VM

Cheapest single-host workflow: build locally on the VM. No registry needed.

```bash
git clone <your-repo-url> /opt/upwork-vm/src
cd /opt/upwork-vm/src
docker build -t upwork-vm:latest .
```

Or upload a tarball if the repo isn't reachable from the VM:

```bash
tar --exclude='.git' --exclude='state' --exclude='logs' --exclude='secrets' \
    -czf /tmp/upwork-vm.tgz -C /path/to/local/repo .
scp /tmp/upwork-vm.tgz user@vm:/tmp/
ssh user@vm "mkdir -p /opt/upwork-vm/src && tar -xzf /tmp/upwork-vm.tgz -C /opt/upwork-vm/src && cd /opt/upwork-vm/src && docker build -t upwork-vm:latest ."
```

## 4. Manual dry run

Before installing the cron line, run once by hand:

```bash
docker run --rm \
  --env-file /opt/upwork-vm/.env \
  -v /opt/upwork-vm/sa.json:/secrets/sa.json:ro \
  -v /opt/upwork-vm/state:/app/state \
  -v /opt/upwork-vm/logs:/app/logs \
  upwork-vm:latest
```

Check:
- Log output: scraper progress, "appended N row(s)" line.
- Sheet: new rows under the `upwork_master` tab.
- (If `N8N_WEBHOOK_URL` is set) n8n: execution appears in the executions panel.

## 5. Install the cron line

Copy the line from [upwork-vm.cron](upwork-vm.cron) into your crontab:

```bash
crontab -e
# paste contents of upwork-vm.cron
```

The default fires at **06:00 in the VM's local timezone**. Confirm or override:

```bash
timedatectl                              # show current timezone
sudo timedatectl set-timezone Europe/Sofia   # example: set to your tz
```

Or pin the cron to UTC explicitly by adding `CRON_TZ=UTC` at the top of the
crontab.

## 6. Log rotation

Cron output appends to `/opt/upwork-vm/logs/cron.log`. Add to
`/etc/logrotate.d/upwork-vm`:

```
/opt/upwork-vm/logs/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

## 7. Updating the deployment

When code changes:

```bash
cd /opt/upwork-vm/src
git pull                                 # or re-upload tarball
docker build -t upwork-vm:latest .
```

The cron line picks up the new image on the next run.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `FileNotFoundError: secrets/sa.json` | Mount path mismatch between `-v` flag and `GOOGLE_APPLICATION_CREDENTIALS`. |
| `gspread.exceptions.APIError: 403` | Sheet not shared with the service account email. |
| `RuntimeError: Sheet header does not match expected schema` | Someone edited row 1 of the sheet, or the column list in `sheets_writer.py` changed. Either reset row 1 or update the code. |
| Cron runs but nothing happens | `docker` not on root's PATH inside cron. Use the full path `/usr/bin/docker` in the crontab. |
| Chromium crash inside container | The image runs as non-root; some hosts require `--shm-size=2g` on `docker run`. |
| n8n never fires | `N8N_WEBHOOK_URL` empty or wrong; check `cron.log` for the "n8n notify" line. |
