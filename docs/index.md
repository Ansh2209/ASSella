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
  <p class="testers-title">Special thanks to our early testers &amp; contributors</p>
  <div class="testers-cloud">
    <span class="tester-tag" style="--dur: 5.2s; --delay: -0.5s">drazy</span>
    <span class="tester-tag" style="--dur: 6.7s; --delay: -2.3s">ACEsls</span>
    <span class="tester-tag" style="--dur: 4.8s; --delay: -3.7s">p.t.r</span>
    <span class="tester-tag" style="--dur: 7.1s; --delay: -1.4s">gogovang</span>
    <span class="tester-tag" style="--dur: 5.5s; --delay: -4.1s">morrenus</span>
    <span class="tester-tag" style="--dur: 6.3s; --delay: -0.9s">da_real_dari</span>
    <span class="tester-tag" style="--dur: 4.5s; --delay: -2.8s">bleibeidl</span>
    <span class="tester-tag" style="--dur: 5.9s; --delay: -5.0s">beastzy</span>
    <span class="tester-tag" style="--dur: 6.8s; --delay: -1.7s">Shinji257</span>
    <span class="tester-tag" style="--dur: 4.9s; --delay: -3.2s">th3g3ntlem3n</span>
    <span class="tester-tag" style="--dur: 6.1s; --delay: -4.6s">tr4nquility</span>
    <span class="tester-tag" style="--dur: 5.4s; --delay: -1.1s">kaiser_1804</span>
    <span class="tester-tag" style="--dur: 7.3s; --delay: -2.9s">feelingkindadizzy</span>
  </div>
</div>

<style scoped>
.testers-section {
  margin-top: 4rem;
  margin-bottom: 2rem;
  padding: 1.75rem 1.5rem;
  text-align: center;
  border-radius: 12px;
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-border);
}
.testers-title {
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--vp-c-text-2);
  margin-bottom: 1.25rem;
}
.testers-cloud {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 0.75rem 1rem;
  min-height: 80px;
}
.tester-tag {
  display: inline-block;
  padding: 0.35rem 0.85rem;
  border-radius: 20px;
  font-family: var(--vp-font-family-mono);
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--vp-c-brand-1);
  background: var(--vp-c-bg-mute);
  border: 1px solid var(--vp-c-brand-soft);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
  animation: randomFade var(--dur) infinite ease-in-out var(--delay);
}
@keyframes randomFade {
  0%, 100% {
    opacity: 0;
    transform: scale(0.85);
    filter: blur(2px);
  }
  30%, 70% {
    opacity: 1;
    transform: scale(1);
    filter: blur(0px);
  }
}
</style>

