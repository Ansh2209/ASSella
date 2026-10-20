---
layout: home

hero:
  name: "ASSella"
  text: "Steam Library & DLC Manager"
  tagline: "Fast, modern, and ACF-independent game & depot management for SteamOS and desktop Linux."
  image:
    src: /logo.png
    alt: ASSella Logo
  actions:
    - theme: brand
      text: Installation Guide
      link: /install
    - theme: brand
      text: Decky Plugin (Uran)
      link: /decky
    - theme: alt
      text: Arch / SteamOS Repo
      link: /arch-repo
    - theme: alt
      text: View on GitHub
      link: https://github.com/niwia/ASSella

features:
  - title: One-Line Installer & Updates
    details: Instant setup on Steam Deck and Linux desktops with automatic backup, delta-update zsync support, and rollback pins.
  - title: Steam Deck Companion Plugin
    details: Official Decky Loader plugin (Uran) for native game search, injection, and library management directly in Game Mode.
  - title: Modern Manifest Architecture
    details: ACF-independent metadata tracking with smart depot discovery, manifest repair, and multi-library discovery.
  - title: Theming & Audio Packs
    details: Rich custom QSS themes, modular color palettes, custom game banners, and selectable voice announcement packs.
---

## Quick Install

To install ASSella directly on Steam Deck or Linux terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

Read the full step-by-step setup in the [Installation Guide](/install).
