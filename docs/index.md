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
    <span class="tester-tag" style="--dur: 15.2s; --delay: -4.3s;">shisaya</span>
    <span class="tester-tag" style="--dur: 18.0s; --delay: -11.1s;">da_real_dari</span>
    <span class="tester-tag" style="--dur: 13.5s; --delay: -2.7s;">jayo</span>
    <span class="tester-tag" style="--dur: 16.4s; --delay: -8.5s;">morrenus</span>
    <span class="tester-tag" style="--dur: 14.8s; --delay: -1.2s;">aguga</span>
    <span class="tester-tag" style="--dur: 17.2s; --delay: -9.8s;">kaiser_1804</span>
    <span class="tester-tag" style="--dur: 13.8s; --delay: -5.4s;">pduran5</span>
    <span class="tester-tag" style="--dur: 19.5s; --delay: -13.2s;">feelingkindadizzy</span>
    <span class="tester-tag" style="--dur: 15.6s; --delay: -6.9s;">ACEsls</span>
    <span class="tester-tag" style="--dur: 14.2s; --delay: -3.8s;">ansh2209</span>
    <span class="tester-tag" style="--dur: 17.8s; --delay: -10.4s;">drazy</span>
    <span class="tester-tag" style="--dur: 16.0s; --delay: -7.6s;">MohandL3G</span>
    <span class="tester-tag" style="--dur: 14.5s; --delay: -2.1s;">Shinji257</span>
    <span class="tester-tag" style="--dur: 13.2s; --delay: -5.9s;">bleibeidl</span>
    <span class="tester-tag" style="--dur: 18.2s; --delay: -12.5s;">tr4nquility</span>
    <span class="tester-tag" style="--dur: 15.0s; --delay: -4.8s;">th3g3ntlem3n</span>
    <span class="tester-tag" style="--dur: 16.6s; --delay: -9.1s;">gogovang</span>
    <span class="tester-tag" style="--dur: 17.5s; --delay: -11.7s;">beastzy</span>
    <span class="tester-tag" style="--dur: 14.0s; --delay: -1.9s;">p.t.r</span>
  </div>
</div>

<style scoped>
.testers-section {
  margin-top: 3.5rem;
  margin-bottom: 0.5rem;
  padding: 0 1rem;
  text-align: center;
  width: 100%;
}
.testers-title {
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--vp-c-text-3);
  margin-bottom: 1.75rem;
}
.testers-cloud {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 1.5rem 3rem;
  max-width: 960px;
  margin: 0 auto;
  padding: 0.5rem 0 1.5rem;
  min-height: 140px;
}
.tester-tag {
  display: inline-block;
  font-family: var(--vp-font-family-mono);
  font-size: 0.92rem;
  font-weight: 500;
  color: var(--vp-c-brand-1);
  white-space: nowrap;
  user-select: none;
  will-change: transform, opacity;
  animation: slowDrift var(--dur) infinite ease-in-out var(--delay);
}
@keyframes slowDrift {
  0% {
    opacity: 0;
    transform: translateX(-15px) scale(0.92);
    filter: blur(1.5px);
  }
  25% {
    opacity: 0.95;
    transform: translateX(-3px) scale(1);
    filter: blur(0px);
  }
  75% {
    opacity: 0.95;
    transform: translateX(8px) scale(1);
    filter: blur(0px);
  }
  100% {
    opacity: 0;
    transform: translateX(20px) scale(0.92);
    filter: blur(1.5px);
  }
}
</style>

