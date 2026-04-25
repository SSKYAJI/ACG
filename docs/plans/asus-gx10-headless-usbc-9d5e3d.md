# ASUS Ascent GX10 — Headless Setup Over USB-C from Your Mac

Use the rear USB-C "host link" on the GX10 as a USB-Ethernet gadget so your Mac can SSH straight into the box without monitor/keyboard, then configure venue Wi-Fi from inside the shell.

## Why this works

The GX10 (and its NVIDIA cousin, DGX Spark) ship with a **USB-Ethernet gadget** on one of the rear USB-C ports — when you plug it into a host machine, both sides see a private Ethernet link, the GX10 hands out a DHCP lease, and you can SSH it directly. This is the documented "headless setup" path, and your Apple USB-C charge cable is fine for it (USB 2.0 data is plenty for an SSH session).

> **Forget the hotspot path for now.** If the GX10 hasn't been through first-boot setup yet, it has no Wi-Fi profile to attach with — that's likely why your hotspot saw nothing.

---

## Step 0 — Things to find on the quick-start card

Before plugging anything in, get these from the leaflet (write them down):

- **Headless / setup IP** of the GX10 (commonly `10.42.0.1`, `192.168.55.1`, or similar — vendor-specific)
- **Default username** (often `nvidia`, `ubuntu`, or a custom hackathon user)
- **Default password** (or "first-boot will prompt" — note which)
- Which USB-C port is labeled for setup/host (if marked)

If the card doesn't list the IP, no problem — Step 2 walks through discovering it.

---

## Step 1 — Plug it in

1. GX10 powered on, its own DC brick attached. Wait 60–90s after power-on so the USB gadget driver is up.
2. Plug your Mac's USB-C cable from the **GX10's rear USB-C port** (the setup-marked one if labeled — otherwise we'll try each in turn) to **any USB-C port on your Mac**.
3. macOS may pop a "Allow accessory to connect?" prompt — click **Allow**.

Don't worry about charging — the GX10 has its own power; the link is just data.

---

## Step 2 — Confirm your Mac sees the new network interface

In a Mac terminal:

```bash
# List all network interfaces; new one will appear as "enX"
networksetup -listallhardwareports

# Watch for a fresh enX that has an IPv4 address (not 169.254.*)
ifconfig | grep -A4 "^en"
```

You're looking for an interface (likely `en6`, `en7`, etc.) with:
- `status: active`
- An `inet` address like `10.42.0.123` or `192.168.55.x`

**If nothing shows up after 30s:**
- The GX10 has 2–3 USB-C ports — only one is the gadget link. **Move the cable to the next USB-C port** on the GX10 and wait 15s. Repeat until the new interface appears.
- If still nothing after trying every port: power-cycle the GX10 (hold power 5s, wait 10s, power back on), wait 2 min, retry.

---

## Step 3 — Find the GX10's IP on this link

Once you have the new `enX` interface up:

```bash
# Replace enX with the actual interface name from Step 2
IFACE=enX

# Your Mac's IP on the link (note the /24 subnet)
ifconfig $IFACE | grep "inet "

# The GX10 is the gateway — usually .1 of that subnet
# Examples: Mac on 10.42.0.50 → GX10 is 10.42.0.1
#           Mac on 192.168.55.100 → GX10 is 192.168.55.1
arp -a -i $IFACE
ping -c 2 <suspected-gx10-ip>
```

If the quick-start card listed an IP, just confirm it pings on this interface.

Save the result as `$GX_IP` for the next steps:

```bash
export GX_IP=10.42.0.1   # or whatever you found
```

---

## Step 4 — SSH into the GX10

```bash
ssh <user>@$GX_IP
```

- Username/password from the card.
- If it's a true first boot, you may be walked through a quick OOBE prompt (set password, accept EULA) — answer those, then re-`ssh`.
- Drop into `tmux` immediately so any flaky steps survive a reconnect:

  ```bash
  sudo apt-get install -y tmux 2>/dev/null   # if missing
  tmux new -s setup
  ```

---

## Step 5 — Bring up venue Wi-Fi (WPA2-PSK)

Inside the GX10 shell:

```bash
# Confirm Wi-Fi radio is on
nmcli radio wifi
sudo nmcli radio wifi on

# See what's around
nmcli dev wifi list

# Connect
sudo nmcli dev wifi connect "<VENUE_SSID>" password "<VENUE_PASS>"

# Make it stick across reboots and prefer it over anything else
sudo nmcli connection modify "<VENUE_SSID>" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100

# Verify
nmcli connection show --active
ip -4 addr show
ping -c 3 1.1.1.1
ping -c 3 github.com
```

**Critical:** because you're connected over USB-C (not Wi-Fi), bringing Wi-Fi up does **NOT** drop your SSH session. You can keep working in this same shell.

---

## Step 6 — (Optional) Drop the USB-C link and go fully wireless

Only when you've confirmed Wi-Fi works:

1. From the GX10, note its Wi-Fi IP: `ip -4 addr show wlan0` (or `wlp*` — whatever showed up).
2. Optionally enable mDNS hostname for easier reconnects:

   ```bash
   hostnamectl    # see current hostname; commonly resolvable as <name>.local
   ```

3. Move your Mac onto the venue Wi-Fi.
4. Re-SSH:

   ```bash
   ssh <user>@<gx10_wifi_ip>
   # or, if mDNS is up:
   ssh <user>@<hostname>.local
   tmux attach -t setup
   ```

5. Unplug the USB-C cable. You're now fully wireless.

---

## Step 7 — Install packages & models

```bash
# Hygiene
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y git curl build-essential python3-venv tmux htop nvtop

# Confirm GPU + container runtime (these should already be set up on a pre-imaged kit)
nvidia-smi
docker info | grep -i "Default Runtime"
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Common AI stacks (pick what your project needs):

```bash
# Ollama — fastest path to local LLMs
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b          # or qwen2.5, mistral, etc.

# Hugging Face + Python venv
python3 -m venv ~/.venvs/ai && source ~/.venvs/ai/bin/activate
pip install --upgrade pip huggingface_hub transformers accelerate
huggingface-cli login

# vLLM (only if you need batched serving)
pip install vllm
```

**Always run model pulls inside `tmux`** — venue Wi-Fi will eventually blip and you don't want a 30 GB download to die.

---

## Troubleshooting

**No new `enX` shows up on Mac, every GX10 port tried:**

- macOS sometimes silently blocks unsigned NCM gadgets. Open *System Settings → Privacy & Security* and look for a "Network extension was blocked" prompt at the bottom — click **Allow**.
- Try a different USB-C port on your Mac (some Macs have one port wired oddly).
- Try power-cycling both the GX10 and the cable (unplug, wait 10s, replug).

**Interface appears but only has a `169.254.x.x` address:**

- That's link-local — DHCP from the GX10 didn't complete. The GX10 may still be booting. Wait another minute and run `sudo ipconfig set $IFACE DHCP` on the Mac to retrigger.

**SSH "permission denied" with the password from the card:**

- First-boot OOBE may require setting a new password before SSH allows password login. Try the username with no password (just hit Enter) — some kits require a console-style "set password" flow that runs over the serial-style USB link. If your card mentions an "OOBE web URL" (e.g., `http://<gx_ip>`), open it in Safari first.

**`nmcli` says no Wi-Fi devices found:**

- `rfkill list` — if Wi-Fi is "Soft blocked", run `sudo rfkill unblock wifi`.
- Confirm with `lspci | grep -i network` that the Wi-Fi card is detected.
- If it's a freshly-flashed image, the Wi-Fi firmware blob may need a reboot: `sudo reboot`, then SSH back in via USB-C.

**Want a safety net before unplugging USB-C — guarantee remote access on venue Wi-Fi:**

While still connected over USB-C, install Tailscale on the GX10 so you can reach it later even if venue Wi-Fi has client isolation:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up    # follow the URL it prints, log in with your Tailscale account
tailscale ip -4      # save this IP — works from anywhere you're on the same tailnet
```

---

## Quick reference — minimum copy-paste path

```bash
# Mac side
ifconfig | grep -A4 "^en"                  # find new enX
export GX_IP=10.42.0.1                     # from card or arp -a
ssh <user>@$GX_IP

# GX10 side
tmux new -s setup
sudo nmcli dev wifi connect "<VENUE_SSID>" password "<VENUE_PASS>"
sudo nmcli connection modify "<VENUE_SSID>" connection.autoconnect-priority 100
ip -4 addr show && ping -c 2 1.1.1.1
sudo apt-get update
```
