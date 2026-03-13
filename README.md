# Nosok Bot (Trial Version)

A powerful multifunctional Discord bot written in **Python** using the `discord.py` library.
The bot provides **moderation tools, utility commands, fun features, voice management, and server statistics**.

---

# ✨ Features

## 🛡 Moderation

* Kick / Ban members
* Timeout users
* Warning system with **SQLite database**
* Clear messages
* Mute / Unmute system
* Role management
* Channel lockdown
* Slowmode control
* Voice moderation (move, disconnect, deafen)
* Moderation logging system

---

## 🛠 Utility

* Poll creation
* Timer / reminders
* AFK system
* Math calculator
* Server information
* User information
* Server icon & banner viewer
* Temporary voice channel creator
* Voice channel info

---

## 🎮 Fun Commands

* Dice roll
* Coin flip
* Random jokes
* Magic 8-ball
* Rock Paper Scissors

---

## 📊 Information

* Bot statistics
* Server statistics
* User profile details
* Avatar viewer
* Invite generator

---

# ⚙️ Installation

### 1️⃣ Clone the repository

```bash
git clone https://github.com/lordnosok/Nosok-Bot-Trial-Version-.git
cd Nosok-Bot-Trial-Version-
```

---

### 2️⃣ Install dependencies

```bash
pip install discord.py
```

---

### 3️⃣ Create configuration file

Create the file:

```
config/config.cfg
```

Example configuration:

```
[DEFAULT]

TOKEN=YOUR_DISCORD_BOT_TOKEN
GUILD_ID=
BOT_NAME=Nosok Bot
BOT_VERSION=1.0
OWNER_NAME=YourName
OWNER_ID=YOUR_DISCORD_ID
BANNER_FILENAME=banner.png
LOGO_FILENAME=logo.png
```

---

### 4️⃣ Run the bot

```bash
python main.py
```

---

# 📂 Project Structure

```
Nosok-Bot-Trial-Version
│
├── main.py
├── config
│   └── config.cfg
│
├── data
│   └── warnings.db
│
├── res
│   ├── banner.png
│   └── logo.png
```

---

# 🗄 Database

The bot automatically creates a **SQLite database** to store:

* User warnings
* Guild moderation settings
* Log channel
* Mute role

---

# 🔐 Permissions

The bot requires permissions such as:

* Manage Messages
* Kick Members
* Ban Members
* Moderate Members
* Move Members
* Manage Roles
* Manage Channels

---

# 👤 Developer

Developer: **Nosok**

---

# ⚠️ Trial Version

This repository contains a **trial version** of the bot.
More features and improvements may be added in future updates.

---
