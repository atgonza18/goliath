# GOLIATH — Complete Hetzner Setup Guide

This guide assumes you have zero server experience. Follow every step in order.

---

## Part 1: Create Your Hetzner Account and Server

### Step 1: Sign up for Hetzner

1. Go to https://www.hetzner.com/cloud
2. Click **"Get Started"** or **"Sign Up"**
3. Create an account with your email
4. You'll need to verify your identity (they may ask for a credit card or PayPal)
5. Once verified, you'll land on the **Hetzner Cloud Console** (a dashboard)

### Step 2: Create a server

1. In the Hetzner Cloud Console, click **"+ Create a Server"** (big button, top right area)
2. Fill in the options:

| Setting | What to Pick |
|---------|-------------|
| **Location** | Ashburn, VA (closest to US, lowest latency) — or any US location |
| **Image** | **Ubuntu 24.04** (under the "OS Images" tab) |
| **Type** | Shared vCPU → **CX22** (2 vCPU, 4 GB RAM, 40 GB disk) — costs ~$4.50/month. This is plenty. |
| **Networking** | Leave defaults (Public IPv4 + IPv6) |
| **SSH Key** | See Step 3 below — **do this before clicking Create** |
| **Name** | Type `goliath` (or whatever you want to call it) |

3. **DON'T click Create yet** — do Step 3 first.

### Step 3: Set up SSH key (this is how you log in securely)

SSH is how you talk to your server from your laptop. Instead of a password, you use a "key" — think of it like a digital fingerprint that proves it's you.

#### On Mac:
1. Open **Terminal** (press Cmd+Space, type "Terminal", hit Enter)
2. Type this command and press Enter:
   ```
   ssh-keygen -t ed25519 -C "your-email@example.com"
   ```
3. It will ask "Enter file in which to save the key" — just press **Enter** (use the default)
4. It will ask for a passphrase — you can press **Enter** twice to skip (or set one for extra security)
5. Now copy your public key:
   ```
   cat ~/.ssh/id_ed25519.pub
   ```
6. Select ALL the text it shows (starts with `ssh-ed25519`) and copy it (Cmd+C)

#### On Windows:
1. Open **PowerShell** (press Windows key, type "PowerShell", click it)
2. Type this command and press Enter:
   ```
   ssh-keygen -t ed25519 -C "your-email@example.com"
   ```
3. It will ask "Enter file in which to save the key" — just press **Enter** (use the default)
4. It will ask for a passphrase — you can press **Enter** twice to skip (or set one for extra security)
5. Now copy your public key:
   ```
   cat ~/.ssh/id_ed25519.pub
   ```
   (If that doesn't work, try: `type $env:USERPROFILE\.ssh\id_ed25519.pub`)
6. Select ALL the text it shows (starts with `ssh-ed25519`) and copy it (Ctrl+C)

#### Back in Hetzner:
1. In the server creation page, under **SSH Keys**, click **"Add SSH Key"**
2. Paste the key you just copied
3. Give it a name like "My Laptop"
4. Click **Add**

### Step 4: Create the server

1. Now click **"Create & Buy Now"**
2. Wait ~30 seconds. Your server will appear with an **IP address** (something like `65.109.xxx.xxx`)
3. **Write down that IP address** — you'll need it for everything below

---

## Part 2: Connect to Your Server

### On Mac:
1. Open **Terminal**
2. Type:
   ```
   ssh root@YOUR_IP_ADDRESS
   ```
   (Replace `YOUR_IP_ADDRESS` with the actual IP from Step 4)
3. First time it'll ask "Are you sure you want to continue connecting?" — type `yes` and press Enter
4. You're now logged into your server. You'll see something like `root@goliath:~#`

### On Windows:
1. Open **PowerShell**
2. Type:
   ```
   ssh root@YOUR_IP_ADDRESS
   ```
   (Replace `YOUR_IP_ADDRESS` with the actual IP from Step 4)
3. First time it'll ask "Are you sure you want to continue connecting?" — type `yes` and press Enter
4. You're now logged into your server. You'll see something like `root@goliath:~#`

If SSH doesn't work on Windows, install **PuTTY** from https://www.putty.org/ — it's a program that does the same thing with a graphical interface.

---

## Part 3: Install Goliath

You should now be connected to your server (you see `root@goliath:~#` or similar).

### Step 1: Run the setup script

Copy and paste this ONE command into the terminal and press Enter:

```bash
curl -sSL https://raw.githubusercontent.com/atgonza18/goliath/main/deploy/setup-hetzner.sh | bash
```

This will take 2-5 minutes. It installs everything: Python, Node.js, Claude CLI, the bot code, systemd service, log rotation, health checks.

Watch it run. When it finishes, you'll see a big summary with "Next steps."

### Step 2: Set your Telegram bot token

```bash
nano /opt/goliath/.env
```

This opens a simple text editor. Change the line that says:
```
TELEGRAM_BOT_TOKEN=your-token-here
```
to:
```
TELEGRAM_BOT_TOKEN=paste-your-actual-token-here
```

Also uncomment (remove the `#` from) and set these lines:
```
ALLOWED_CHAT_IDS=8226036883
REPORT_CHAT_ID=8226036883
```

To save: press **Ctrl+O**, then **Enter**, then **Ctrl+X** to exit.

### Step 3: Authenticate Claude CLI

```bash
sudo -u goliath claude auth login
```

This will give you a URL to open in your browser. Open it, sign in with your Anthropic account, and authorize the CLI. Come back to the terminal — it should say "authenticated" or similar.

### Step 4: Transfer your project data

This copies all your PDFs, Excel files, and project data to the new server.

Open a **NEW terminal window on your laptop** (don't close the server one). Run:

#### On Mac:
```bash
# From your laptop, push files to the server:
scp -r /path/to/your/local/project/files/* root@YOUR_IP_ADDRESS:/opt/goliath/projects/
```

**Simplest approach:** Once Syncthing is set up (Part 5), just drop your files in the sync folder. They'll transfer automatically.

For now, you can skip this step and set up Syncthing first.

### Step 5: Transfer memory database (optional)

If you want Goliath to remember everything from previous sessions:

From your laptop (wherever memory.db is):
```bash
scp /path/to/memory.db root@YOUR_IP_ADDRESS:/opt/goliath/telegram-bot/data/
```

### Step 6: Fix file ownership and start the bot

Back in your **server terminal**:
```bash
chown -R goliath:goliath /opt/goliath
systemctl start goliath-bot
```

### Step 7: Verify it's running

```bash
systemctl status goliath-bot
```

You should see **"active (running)"** in green. If you see "failed", check the logs:
```bash
journalctl -u goliath-bot -n 50
```

### Step 8: Test it

Open Telegram on your phone and message Goliath. If he responds, you're live on Hetzner!

---

## Part 4: Set Up SFTP (Drag-and-Drop File Access)

SFTP lets you browse and manage files on the server using a graphical app on your laptop.

### On Mac — Use Cyberduck (free):

1. Download from https://cyberduck.io/download/
2. Install it (drag to Applications)
3. Open Cyberduck
4. Click **"Open Connection"** (top left)
5. Change the dropdown at the top from "FTP" to **"SFTP (SSH File Transfer Protocol)"**
6. Fill in:
   - **Server**: `YOUR_IP_ADDRESS`
   - **Port**: `22`
   - **Username**: `root`
   - **SSH Private Key**: Click the dropdown, navigate to `~/.ssh/id_ed25519`
   - Leave password blank
7. Click **Connect**
8. Navigate to `/opt/goliath/projects/` — you can now drag files in and out!

**Tip:** Bookmark this connection (Bookmark menu → New Bookmark) so you don't have to type it every time.

### On Windows — Use WinSCP (free):

1. Download from https://winscp.net/eng/download.php
2. Install it
3. Open WinSCP
4. In the login window:
   - **File protocol**: SFTP
   - **Host name**: `YOUR_IP_ADDRESS`
   - **Port**: `22`
   - **User name**: `root`
   - Click **Advanced** → **SSH** → **Authentication** → under "Private key file", browse to `C:\Users\YourName\.ssh\id_ed25519`
     - WinSCP may ask to convert it to `.ppk` format — click **Yes**
   - Click **OK**, then **Login**
5. Left side = your laptop. Right side = the server.
6. Navigate on the right side to `/opt/goliath/projects/`
7. Drag files from left to right to upload!

**Tip:** Save this as a "Site" so you can reconnect with one click.

---

## Part 5: Set Up Syncthing (Auto-Sync Folder)

This creates a folder on your laptop that automatically syncs with the server. Drop a file in → it appears on the server in seconds.

### Step 1: Install Syncthing on the server

SSH into your server and run:
```bash
# Install Syncthing
curl -s https://syncthing.net/release-key.gpg | gpg --dearmor -o /usr/share/keyrings/syncthing-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable" > /etc/apt/sources.list.d/syncthing.list
apt-get update -qq && apt-get install -y -qq syncthing

# Create systemd service for Syncthing (runs as goliath user)
cat > /etc/systemd/system/syncthing@.service << 'STEOF'
[Unit]
Description=Syncthing for %i
After=network.target

[Service]
User=%i
ExecStart=/usr/bin/syncthing serve --no-browser --no-restart
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
STEOF

# Start Syncthing for the goliath user
systemctl enable --now syncthing@goliath

# Open the web UI port so you can configure it (temporarily)
# We'll lock this down later
```

Wait 10 seconds for Syncthing to start, then get the server's device ID:
```bash
sudo -u goliath syncthing cli show system | grep myID
```
**Write down this ID** — it looks like `XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX`

### Step 2: Install Syncthing on your laptop

#### On Mac:
1. Install via Homebrew (if you have it): `brew install syncthing`
   OR download from https://syncthing.net/downloads/
2. Run it: open Terminal and type `syncthing`
3. It will open a web page at http://localhost:8384 — this is Syncthing's dashboard

#### On Windows:
1. Download from https://syncthing.net/downloads/ (Windows 64-bit)
2. Unzip it to a folder (like `C:\Syncthing\`)
3. Run `syncthing.exe`
4. It will open a web page at http://localhost:8384 — this is Syncthing's dashboard

### Step 3: Pair your laptop with the server

1. In the Syncthing web UI on your laptop (http://localhost:8384):
2. Click **"Add Remote Device"** (bottom right)
3. Paste the server's Device ID from Step 1
4. Name it "Goliath Server"
5. Click **Save**

Now get YOUR laptop's Device ID:
- In the Syncthing web UI, click **Actions** (top right) → **Show ID**
- Copy the ID

Back on the server (via SSH):
```bash
# Add your laptop as a trusted device
sudo -u goliath syncthing cli config devices add --device-id YOUR_LAPTOP_DEVICE_ID --name "My Laptop"
```

### Step 4: Set up the shared folder

On the server:
```bash
# Share the projects folder via Syncthing
sudo -u goliath syncthing cli config folders add --id goliath-projects --label "Goliath Projects" --path /opt/goliath/projects
sudo -u goliath syncthing cli config folders goliath-projects devices add --device-id YOUR_LAPTOP_DEVICE_ID
```

On your laptop's Syncthing web UI:
1. The server should appear under "Remote Devices" as connected
2. You'll get a popup saying "Goliath Server wants to share folder 'Goliath Projects'"
3. Click **Add**
4. Set the **Folder Path** to wherever you want on your laptop, e.g.:
   - Mac: `/Users/yourname/Documents/Goliath Projects`
   - Windows: `C:\Users\yourname\Documents\Goliath Projects`
5. Click **Save**

### Step 5: Test it

1. Drop a test file into your `Goliath Projects` folder on your laptop
2. Wait 10-30 seconds
3. Check on the server: `ls /opt/goliath/projects/` — the file should be there
4. Delete the test file when done

From now on, to upload project files, just drop them in the right subfolder:
```
Goliath Projects/
  pecan-prairie/
    schedule/     ← drop schedule PDFs here
    constraints/  ← drop constraint files here
    pod/          ← drop POD reports here
  duff/
    schedule/
    ...
```

---

## Part 6: Daily Operations

### Check if the bot is running:
```bash
ssh root@YOUR_IP_ADDRESS
systemctl status goliath-bot
```

### View live logs:
```bash
ssh root@YOUR_IP_ADDRESS
journalctl -u goliath-bot -f
```
(Press Ctrl+C to stop watching)

### Restart the bot:
```bash
ssh root@YOUR_IP_ADDRESS
systemctl restart goliath-bot
```

### Update the code (after pushing changes to GitHub):
Either tell Goliath via Telegram:
> "Pull the latest code from GitHub and restart yourself"

Or manually:
```bash
ssh root@YOUR_IP_ADDRESS
cd /opt/goliath && git pull
systemctl restart goliath-bot
```

### Kill stuck Claude processes:
```bash
ssh root@YOUR_IP_ADDRESS
pkill -f "claude --print"
```

---

## Part 7: Costs

| Item | Monthly Cost |
|------|-------------|
| Hetzner CX22 (2 vCPU, 4GB RAM) | ~$4.50 |
| Claude Max plan (you already have this) | $0 extra |
| Syncthing | Free |
| Total | **~$4.50/month** |

---

## Troubleshooting

### Bot won't start
```bash
journalctl -u goliath-bot -n 100   # check logs
cat /opt/goliath/.env               # verify token is set
systemctl restart goliath-bot       # try restart
```

### Claude CLI not working
```bash
sudo -u goliath claude --version    # check it's installed
sudo -u goliath claude auth status  # check authentication
sudo -u goliath claude auth login   # re-authenticate if needed
```

### Syncthing not syncing
- Check laptop: http://localhost:8384 — both devices should show "Connected"
- Check server: `systemctl status syncthing@goliath`
- Restart: `systemctl restart syncthing@goliath`

### Server ran out of disk
```bash
df -h                                          # check disk usage
du -sh /opt/goliath/projects/*                 # see which project is biggest
du -sh /opt/goliath/telegram-bot/data/         # check memory.db size
journalctl --vacuum-size=100M                  # trim system logs
```

### Can't SSH in
- Double-check the IP address
- Make sure your SSH key exists: `ls ~/.ssh/id_ed25519`
- Try with verbose output: `ssh -v root@YOUR_IP_ADDRESS`
