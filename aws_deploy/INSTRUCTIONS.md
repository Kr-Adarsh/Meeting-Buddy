# Meeting Buddy - AWS EC2 Cloud Deployment Guide

This directory contains everything you need to transform a blank AWS `$0` Free Tier structure into a headless PulseAudio Selenium Bot environment.

## Step 1: Push code to AWS
Make sure this entire `MeetingBuddy` folder is located at `/home/ubuntu/MeetingBuddy` on your EC2 hard drive!

## Step 2: Build the Frontend
Log into your EC2 SSH and compile the React bundler:
```bash
cd /home/ubuntu/MeetingBuddy/frontend
npm install
npm run build
```

## Step 3: Run the Global Automator
I have written an `install.sh` bash script that structurally configures your OS! 
It dynamically establishes the `4GB Swap Space` so your Free Tier `t3.micro` doesn't crash from Memory Panics, installs `nginx`, grabs the `google-chrome` raw Debian files, and creates the headless `X11` & `PulseAudio` dependencies natively.

```bash
cd /home/ubuntu/MeetingBuddy/aws_deploy
sudo chmod +x install.sh
sudo ./install.sh
```

*(During execution, the script will literally pause and ask you to type in a Dashboard Username and Password. Whatever you type here becomes your Nginx Basic Auth barrier lock!)*

## Step 4: Verification
The bot is now completely running in the background as a `SystemD` daemon. 
Because `Nginx` is protecting the proxy, you can type your `Public IPv4 Address` straight into your browser! 
- It will popup asking for your Basic Auth credentials.
- Once entered, it will serve your compiled React UI.
- All requests to `/jobs` or `/deploy` will silently tunnel through Nginx straight down into the `Xvfb/FastAPI` background Daemon!

**To view your background python logs (errors, API limits, Mistral summarizer dumps):**
```bash
sudo journalctl -u meetingbuddy -f
```
