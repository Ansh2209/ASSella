# Arch Linux & SteamOS Repository

ASSella maintains a dedicated Arch Linux repository for automated updates and native pacman package management on Arch Linux and SteamOS.

---

## Automatic Updates via Pacman

To configure `pacman` to track ASSella updates automatically, add the following repository configuration to `/etc/pacman.conf`:

```ini
[assella]
SigLevel = Optional TrustAll
Server = https://github.com/niwia/ASSella/releases/download/arch-repo
```

After updating your configuration, synchronize your repositories and install or update ASSella:

```bash
sudo pacman -Sy assella
```

---

## Direct Package Installation

If you prefer to install a specific package release directly without modifying `/etc/pacman.conf`:

```bash
sudo pacman -U https://github.com/niwia/ASSella/releases/download/arch-repo/assella-2.7.1beta-1-x86_64.pkg.tar.zst
```

---

## Releases & Binaries

For AppImages, delta update `.zsync` files, and ARM64 packages, visit the official [ASSella GitHub Releases](https://github.com/niwia/ASSella/releases) page.
