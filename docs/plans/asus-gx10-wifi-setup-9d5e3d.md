# ASUS Ascent GX10 — SSH In, Switch to Venue Wi-Fi, Install Stack

Get the GX10 onto your phone hotspot, SSH in from your Mac, reconfigure it onto the hackathon's WPA2-PSK Wi-Fi, then install your AI packages/models — all without ever attaching a monitor.

## Assumptions

- GX10 is **pre-imaged** to auto-join an SSID/password printed on the card you were given.
- It runs an **Ubuntu/DGX-OS-style** stack with `NetworkManager` + `nmcli` (standard for these dev kits).
- You're driving from your **Mac**, both Mac and GX10 sharing your phone hotspot during setup.
- Venue Wi-Fi is **WPA2-PSK** (one SSID + one password) — confirmed.

---

## Step 1 — Get the GX10 onto your phone hotspot

1. On your iPhone, set hotspot to **exactly** the SSID + password from the GX10's card (case-sensitive).
2. Toggle **Settings → Personal Hotspot → Maximize Compatibility = ON** (forces 2.4 GHz; most embedded NICs need it).
3. **Keep the Personal Hotspot screen open** until you see "1 Connection" appear (iPhone sleeps the radio otherwise).
4. Power on the GX10. Allow **2–3 min** for first auto-join, up to 5–10 min if it's a true first boot doing OOBE.
5. Verify on the iPhone hotspot screen that a client connected.

**If still nothing after ~5 min, jump to "Troubleshooting" at the bottom before continuing.**

---

## Step 2 — Discover the GX10's IP from your Mac

Mac is also on the hotspot. Try in order (fastest to slowest):

```bash
# A. mDNS — many of these kits advertise as ascent.local / dgx.local / <hostname>.local
ping -c 2 ascent.local
ping -c 2 dgx.local
dns-sd -B _ssh._tcp .       # browses any SSH-advertising hosts; Ctrl-C when you see it

# B. iPhone hotspot subnet is usually 172.20.10.0/28 — quick sweep
for i in 2 3 4 5 6 7 8 9 10 11 12 13 14; do
  ping -c 1 -W 200 172.20.10.$i >/dev/null && echo "alive: 172.20.10.$i"
done
arp -a | grep 172.20.10

# C. Last resort: nmap (install via brew if needed)
nmap -sn 172.20.10.0/28
```

Note the GX10's IP — call it `$GX_IP` going forward.

---

## Step 3 — SSH in from your Mac

Use the username/password from the device card (commonly `nvidia`, `ubuntu`, or a custom hackathon user).

```bash
ssh <user>@$GX_IP
# or if mDNS works:
ssh <user>@ascent.local
```

Once in, **immediately start a `tmux` session** so the upcoming Wi-Fi switch doesn't kill long-running commands:

```bash
tmux new -s setup
```

(If `tmux` isn't installed: `sudo apt-get install -y tmux`.)

---

## Step 4 — Switch to the venue Wi-Fi (WPA2-PSK)

Inside the `tmux` session on the GX10:

```bash
# 1. See what's around
nmcli dev wifi list

# 2. Connect (this will drop your SSH if you're on Wi-Fi — that's expected)
sudo nmcli dev wifi connect "<VENUE_SSID>" password "<VENUE_PASS>"

# 3. Make venue Wi-Fi the preferred network so it auto-reconnects on reboot
sudo nmcli connection modify "<VENUE_SSID>" connection.autoconnect yes \
                                            connection.autoconnect-priority 100

# 4. (Optional) Lower the hotspot's priority so it stays as a fallback
sudo nmcli connection modify "<HOTSPOT_SSID>" connection.autoconnect-priority 10
```

Your SSH session will die the moment the GX10 leaves the hotspot. That's fine — proceed to Step 5.

---

## Step 5 — Reconnect on venue Wi-Fi & verify

1. Switch your **Mac** to the venue Wi-Fi.
2. Re-SSH:

   ```bash
   ssh <user>@<new_ip_or_hostname>
   tmux attach -t setup
   ```

   New IP discovery: try `ascent.local` first; otherwise check the venue's DHCP/router page or run `arp -a | grep <prefix>` after pinging the subnet.

3. Sanity-check connectivity inside the session:

   ```bash
   ip -4 addr show          # confirm new IP
   ping -c 3 1.1.1.1        # raw internet
   ping -c 3 github.com     # DNS works
   curl -sI https://nvidia.com | head -1
   sudo timedatectl set-ntp true && timedatectl    # fix clock if it drifted
   ```

---

## Step 6 — Install packages & models

Update first, then install whatever you need. Common GX10 / DGX-Spark-class workloads:

```bash
# Base hygiene
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y git curl build-essential python3-venv tmux htop nvtop

# Verify GPU is visible (should already be, but confirms drivers + CUDA)
nvidia-smi

# Docker + NVIDIA Container Toolkit are typically pre-installed; confirm:
docker info | grep -i runtime
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

**Likely stacks for the hackathon (pick what you need):**

```bash
# Ollama (easiest local LLM serving)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b        # or whatever model fits your VRAM/UMA budget

# Hugging Face CLI + a venv for Python work
python3 -m venv ~/.venvs/ai && source ~/.venvs/ai/bin/activate
pip install --upgrade pip huggingface_hub transformers accelerate
huggingface-cli login          # paste your HF token

# vLLM (high-throughput serving) — only if you need batched inference
pip install vllm
```

For long downloads, **always run inside `tmux`** so a flaky venue Wi-Fi doesn't nuke a 30 GB pull.

---

## Troubleshooting

**Device never appears on hotspot:**

- Re-verify SSID/password char-by-char (most common failure).
- iPhone: **Maximize Compatibility ON**, hotspot screen kept open, Wi-Fi + Bluetooth both ON on the phone.
- Power-cycle the GX10 (hold power 5s, wait 10s, power on).
- If it has a status LED, check the device manual for what "connected" looks like.
- Worst case: the GX10 may need a **one-time headless setup over USB-C** (Spark-style: plug a USB-C cable from the GX10's rear port to your Mac, it appears as a USB-Ethernet device, SSH to a fixed IP like `192.168.0.1` — check the device's quick-start card for the exact address).

**SSH connects but dies during Wi-Fi switch and you can't find it again:**

- Venue network may **block client-to-client** traffic (common on guest Wi-Fi). If so, you can't SSH peer-to-peer; you'd need a Tailscale install before the switch:
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  sudo tailscale up
  ```
  Do this **while still on the hotspot**, then use the Tailscale IP after the switch — works regardless of client isolation.

**Captive portal surprise (if the venue Wi-Fi turns out to require browser login):**

- Run `curl -v http://neverssl.com` from the GX10 — if you get a redirect, it's a portal.
- Cleanest fix: tether the GX10 through your Mac (Mac on venue Wi-Fi handles the portal; share via Internet Sharing or a USB-C link), or use Tailscale exit-node from a portal-accepted device.

**`nvidia-smi` not found / driver missing:**

- Don't reinstall drivers blindly on a pre-imaged dev kit — you'll likely brick the CUDA stack. Reboot first; if still broken, contact the hackathon support desk before touching `apt`.

---

## Quick reference — copy-paste path (happy case)

```bash
# from Mac
ssh <user>@ascent.local
tmux new -s setup

# on GX10
sudo nmcli dev wifi connect "<VENUE_SSID>" password "<VENUE_PASS>"
sudo nmcli connection modify "<VENUE_SSID>" connection.autoconnect-priority 100

# Mac switches to venue Wi-Fi, then:
ssh <user>@ascent.local
tmux attach -t setup
ping -c 2 1.1.1.1 && sudo apt-get update
```
