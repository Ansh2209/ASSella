---
layout: home

hero:
  name: "ASSella"
  text: "Make linux great again"
  tagline: "Depotdownloader made with AI on steroids."
  image:
    src: /logo.png
    alt: ASSella Logo
  actions:
    - theme: brand
      text: Installation Guide
      link: /install
    - theme: brand
      text: Uran (Decky Plugin)
      link: /uran
    - theme: alt
      text: View on GitHub
      link: https://github.com/niwia/ASSella
---

## Quick Install

To install ASSella directly on Steam Deck or Linux terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

Read the full step-by-step setup in the [Installation Guide](/install).

<div class="testers-section">
  <p class="testers-title">Special thanks to early testers &amp; contributors</p>
  <div class="testers-cloud">
    <span class="tester-tag" style="--dur: 13.5s; --delay: -3.2s; --y: 4px;">tr4nquility</span>
    <span class="tester-tag" style="--dur: 16.2s; --delay: -8.7s; --y: -6px;">da_real_dari</span>
    <span class="tester-tag" style="--dur: 14.0s; --delay: -1.4s; --y: 2px;">morrenus</span>
    <span class="tester-tag" style="--dur: 18.5s; --delay: -11.2s; --y: -4px;">feelingkindadizzy</span>
    <span class="tester-tag" style="--dur: 15.3s; --delay: -6.5s; --y: 6px;">ACEsls</span>
    <span class="tester-tag" style="--dur: 12.8s; --delay: -4.1s; --y: -2px;">kaiser_1804</span>
    <span class="tester-tag" style="--dur: 17.0s; --delay: -9.9s; --y: 5px;">drazy</span>
    <span class="tester-tag" style="--dur: 14.6s; --delay: -2.8s; --y: -5px;">Shinji257</span>
    <span class="tester-tag" style="--dur: 13.2s; --delay: -7.4s; --y: 3px;">bleibeidl</span>
    <span class="tester-tag" style="--dur: 16.8s; --delay: -12.1s; --y: -3px;">th3g3ntlem3n</span>
    <span class="tester-tag" style="--dur: 15.0s; --delay: -5.0s; --y: 4px;">gogovang</span>
    <span class="tester-tag" style="--dur: 17.4s; --delay: -10.5s; --y: -6px;">beastzy</span>
    <span class="tester-tag" style="--dur: 13.8s; --delay: -1.9s; --y: 2px;">p.t.r</span>
  </div>
</div>

<style scoped>
.testers-section {
  margin-top: 2rem;
  margin-bottom: 0;
  padding: 1rem 0 0;
  text-align: center;
  width: 100%;
}
.testers-title {
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--vp-c-text-3);
  margin-bottom: 1.25rem;
}
.testers-cloud {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-around;
  align-items: center;
  width: 100%;
  gap: 0.85rem 1.5rem;
  overflow: hidden;
  padding: 0.5rem 0;
}
.tester-tag {
  display: inline-block;
  font-family: var(--vp-font-family-mono);
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--vp-c-brand-1);
  will-change: transform, opacity;
  animation: slowDrift var(--dur) infinite ease-in-out var(--delay);
}
@keyframes slowDrift {
  0% {
    opacity: 0;
    transform: translate(-25px, var(--y, 0px)) scale(0.9);
    filter: blur(2px);
  }
  20% {
    opacity: 0.9;
    transform: translate(-5px, var(--y, 0px)) scale(1);
    filter: blur(0px);
  }
  75% {
    opacity: 0.9;
    transform: translate(15px, var(--y, 0px)) scale(1);
    filter: blur(0px);
  }
  100% {
    opacity: 0;
    transform: translate(35px, var(--y, 0px)) scale(0.9);
    filter: blur(2px);
  }
}
</style>

